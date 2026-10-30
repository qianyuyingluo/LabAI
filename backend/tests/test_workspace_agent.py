import asyncio
import copy
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.base import AdapterStreamEvent
from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import Base, Chat, Message, ModelProfile
from app.main import app
from app.core.database import get_session
from app.services.agent_checkpoint import load_checkpoint, save_checkpoint
from app.services.agent_context import compact_messages
from app.services.run_manager import run_manager


class ScriptedModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    async def stream_chat(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        action = next(self.actions, "已完成并验证。")
        if isinstance(action, Exception):
            raise action
        if isinstance(action, str):
            yield AdapterStreamEvent("delta", action)
            yield AdapterStreamEvent("finish_reason", "stop")
        else:
            name, arguments = action
            yield AdapterStreamEvent("tool_call_delta", {"index": 0, "id": f"tool-{len(self.requests)}",
                "name": name, "arguments": json.dumps(arguments)})
            yield AdapterStreamEvent("finish_reason", "tool_calls")


def python_command(code):
    if os.name == "nt":
        return "& '" + sys.executable.replace("'", "''") + "' -c '" + code.replace("'", "''") + "'"
    import shlex
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)


class WorkspaceAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="labai-agent-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.settings = Settings(_env_file=None, backend_storage_dir=self.root / "storage")
        self.engine = create_engine(f"sqlite:///{(self.root / 'test.sqlite3').as_posix()}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            db.add(ModelProfile(id="test", name="test", model_name="test", supports_tools=True, supports_stream=True))
            db.commit()
        self.stack = ExitStack()
        for module in ("app.api.runs", "app.api.agent_runs", "app.services.run_manager", "app.db.session"):
            self.stack.enter_context(patch(module + ".SessionLocal", self.sessions))
        for module in ("app.services.agent_checkpoint", "app.services.agent_runtime", "app.services.workspace_tools"):
            self.stack.enter_context(patch(module + ".get_settings", return_value=self.settings))
        self.stack.enter_context(patch("app.main.init_db"))
        self.stack.enter_context(patch("app.main.cleanup_stale_workspaces"))
        def session_override():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_session] = session_override
        self.client = self.stack.enter_context(TestClient(app))

    def tearDown(self):
        self.stack.close()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp.cleanup()

    def start(self, adapter, mode="auto"):
        self.stack.enter_context(patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter))
        response = self.client.post("/api/runs/chat", json={"model_profile_id": "test", "message": "修复并验证项目",
            "workspace_path": str(self.project), "workspace_mode": mode})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_message(self, run, predicate=lambda m: m["status"] != "streaming"):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            data = self.client.get(f"/api/chats/{run['chat_id']}").json()
            message = next(m for m in data["messages"] if m["id"] == run["assistant_message"]["id"])
            if predicate(message):
                return message
            time.sleep(0.025)
        self.fail("Agent did not reach the expected state")

    def test_real_files_failure_repair_and_command_results_survive_reload(self):
        model = ScriptedModel([
            ("workspace_list", {}),
            ("workspace_write", {"path": "calc.py", "content": "result = 2 + 3\nassert result == 4\nprint(result)\n"}),
            ("run_command", {"command": python_command("exec(open('calc.py').read())")}),
            ("workspace_read", {"path": "calc.py"}),
            ("workspace_edit", {"path": "calc.py", "old_text": "2 + 3", "new_text": "2 + 2"}),
            ("run_command", {"command": python_command("exec(open('calc.py').read())")}),
        ])
        run = self.start(model)
        message = self.wait_message(run)
        self.assertEqual(message["status"], "done", message)
        calls = message["metadata"]["workspaceAgent"]["calls"]
        commands = [c for c in calls if c["tool"] == "run_command"]
        self.assertNotEqual(commands[0]["result"]["exit_code"], 0)
        self.assertEqual(commands[1]["result"]["exit_code"], 0)
        self.assertEqual(commands[1]["result"]["stdout"].strip(), "4")
        self.assertIn("2 + 2", (self.project / "calc.py").read_text())
        state = load_checkpoint(message["id"])
        self.assertEqual(state["status"], "done")
        self.assertEqual(len(state["metadata"]["workspaceAgent"]["calls"]), 6)
        with self.sessions() as db:
            self.assertEqual(db.get(Chat, run["chat_id"]).workspace_path, str(self.project))
        stream = self.client.get(f"/api/runs/messages/{message['id']}/stream")
        self.assertIn("workspaceAgent", stream.text)
        self.assertIn("event: done", stream.text)

    def test_approval_denial_and_acceptance(self):
        model = ScriptedModel([
            ("workspace_write", {"path": "denied.txt", "content": "no"}),
            ("workspace_write", {"path": "allowed.txt", "content": "yes"}),
        ])
        run = self.start(model, "approve")
        for index, approved in enumerate((False, True)):
            message = self.wait_message(run, lambda m: len(m.get("metadata", {}).get("workspaceAgent", {}).get("calls", [])) > index
                and m["metadata"]["workspaceAgent"]["status"] == "waiting_approval")
            call = message["metadata"]["workspaceAgent"]["calls"][-1]
            self.assertFalse((self.project / ("denied.txt" if index == 0 else "allowed.txt")).exists())
            response = self.client.post(f"/api/runs/messages/{message['id']}/approval", json={"call_id": call["id"], "approved": approved})
            self.assertEqual(response.status_code, 200, response.text)
        message = self.wait_message(run)
        self.assertEqual(message["status"], "done")
        self.assertFalse((self.project / "denied.txt").exists())
        self.assertEqual((self.project / "allowed.txt").read_text(), "yes")

    def test_failed_provider_can_resume_without_replaying_completed_write(self):
        model = ScriptedModel([
            ("workspace_write", {"path": "once.txt", "content": "once"}),
            AppError("TEST_PROVIDER_FAILURE", "Temporary provider failure", status_code=502),
            ("workspace_read", {"path": "once.txt"}),
        ])
        run = self.start(model)
        message = self.wait_message(run)
        self.assertEqual(message["status"], "error")
        response = self.client.post(f"/api/runs/messages/{message['id']}/resume")
        self.assertEqual(response.status_code, 200, response.text)
        message = self.wait_message(run)
        self.assertEqual(message["status"], "done")
        calls = message["metadata"]["workspaceAgent"]["calls"]
        self.assertEqual([c["tool"] for c in calls], ["workspace_write", "workspace_read"])

    def test_uncertain_command_is_not_replayed_on_resume(self):
        model = ScriptedModel([AppError("TEST_FAILURE", "offline")])
        run = self.start(model)
        message = self.wait_message(run)
        state = load_checkpoint(message["id"])
        call = {"id": "uncertain", "type": "function", "function": {"name": "run_command", "arguments": json.dumps({"command": python_command("open('replayed.txt','w').write('bad')")})}}
        state["messages"].append({"role": "assistant", "content": None, "tool_calls": [call]})
        state["pending"] = [{"call": call, "phase": "executing"}]
        save_checkpoint(message["id"], state)
        response = self.client.post(f"/api/runs/messages/{message['id']}/resume")
        self.assertEqual(response.status_code, 200, response.text)
        message = self.wait_message(run)
        self.assertEqual(message["status"], "done")
        self.assertFalse((self.project / "replayed.txt").exists())
        result = json.loads(model.requests[-1]["messages"][-1]["content"])
        self.assertTrue(result["interrupted"])

    def test_cancel_waiting_approval_and_resume_binding_protection(self):
        run = self.start(ScriptedModel([("workspace_write", {"path": "no.txt", "content": "no"})]), "approve")
        message = self.wait_message(run, lambda m: m.get("metadata", {}).get("workspaceAgent", {}).get("status") == "waiting_approval")
        response = self.client.post(f"/api/runs/messages/{message['id']}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        message = self.wait_message(run)
        self.assertEqual(message["status"], "cancelled")
        self.assertFalse((self.project / "no.txt").exists())
        self.assertEqual(self.client.post(f"/api/runs/messages/{message['id']}/resume").status_code, 409)


class AgentContextTests(unittest.TestCase):
    def test_compaction_preserves_request_and_tool_pairs(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "old" * 4000},
                    {"role": "user", "content": "CURRENT REQUIREMENT"}]
        for index in range(20):
            messages.extend([{"role": "assistant", "content": None, "tool_calls": [{"id": str(index)}]},
                             {"role": "tool", "tool_call_id": str(index), "content": "evidence" * 700}])
        result = compact_messages(messages, 18000)
        self.assertIn({"role": "user", "content": "CURRENT REQUIREMENT"}, result)
        self.assertLess(len(json.dumps(result)), len(json.dumps(messages)))
        ids = {c["id"] for m in result for c in m.get("tool_calls", [])}
        self.assertEqual(ids, {m["tool_call_id"] for m in result if m["role"] == "tool"})


if __name__ == "__main__":
    unittest.main()
