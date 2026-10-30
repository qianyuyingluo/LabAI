import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.runs import StartChatRunRequest, _bind_requested_workspace, _load_done_history
from app.api.workspaces import bind_workspace
from app.core.database import get_session
from app.core.errors import AppError
from app.db.models import AnalysisPlan, AnalysisTask, Base, Chat, Message, ModelProfile
from app.db.session import ensure_schema_compatibility, mark_orphaned_streaming_messages
from app.main import app
from app.services.run_manager import RunManager


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="labai-workspace-api-")
        self.root = Path(self.temp.name)
        (self.root / "子目录").mkdir()
        (self.root / "hello.txt").write_text("hello 世界\nsecond line\n", encoding="utf-8")
        self.engine = create_engine(
            "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            chat = Chat(title="Workspace", workspace_path=str(self.root.resolve()))
            profile = ModelProfile(name="test", model_name="fake", supports_stream=True, api_key_secret="fake")
            db.add_all([chat, profile])
            db.commit()
            self.chat_id, self.profile_id = chat.id, profile.id

        def session():
            with self.Session() as db:
                yield db

        self.old_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_session] = session
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.old_overrides)
        self.engine.dispose()
        self.temp.cleanup()

    def test_inspect_bind_browse_read_and_unbind(self):
        inspected = self.client.post("/api/workspaces/inspect", json={"path": str(self.root)})
        self.assertEqual(inspected.status_code, 200, inspected.text)
        self.assertEqual(inspected.json()["relative_path"], ".")
        self.assertTrue(any(item["path"] == "hello.txt" for item in inspected.json()["entries"]))
        bound = self.client.patch(f"/api/chats/{self.chat_id}/workspace", json={"path": str(self.root), "mode": "auto"})
        self.assertEqual(bound.json(), {"workspace_path": str(self.root.resolve()), "workspace_mode": "auto"})
        detail = self.client.get(f"/api/chats/{self.chat_id}").json()
        self.assertEqual(detail["workspace_mode"], "auto")
        history = self.client.get("/api/chats").json()
        self.assertEqual(history[0]["workspace_path"], str(self.root.resolve()))
        child = self.client.get(f"/api/chats/{self.chat_id}/workspace", params={"path": "子目录"})
        self.assertEqual(child.json()["relative_path"], "子目录")
        read = self.client.get(f"/api/chats/{self.chat_id}/workspace/file", params={"path": "hello.txt", "end_line": 1})
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()["content"].replace("\r\n", "\n"), "hello 世界\n")
        self.client.patch(f"/api/chats/{self.chat_id}/workspace", json={"path": None})
        self.assertEqual(self.client.get(f"/api/chats/{self.chat_id}/workspace").status_code, 409)

    def test_browse_rejects_escape_ads_and_nonexistent(self):
        for path in ["..", "hello.txt:secret", "missing", str(self.root.parent)]:
            with self.subTest(path=path):
                response = self.client.get(f"/api/chats/{self.chat_id}/workspace", params={"path": path})
                self.assertEqual(response.status_code, 400, response.text)
        response = self.client.get(f"/api/chats/{self.chat_id}/workspace/file", params={"path": "../outside.txt"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/workspaces/inspect", json={"path": "relative"}).status_code, 400)
        self.assertEqual(self.client.post("/api/workspaces/inspect", json={"path": str(self.root / "hello.txt")}).status_code, 400)

    def test_binding_running_chat_is_rejected(self):
        with self.Session() as db:
            db.add(Message(chat_id=self.chat_id, role="assistant", content="", status="streaming"))
            db.commit()
        response = self.client.patch(f"/api/chats/{self.chat_id}/workspace", json={"path": None})
        self.assertEqual(response.status_code, 409)

    def test_omitted_binding_preserves_explicit_null_unbinds(self):
        with self.Session() as db:
            chat = db.get(Chat, self.chat_id)
            _bind_requested_workspace(db, chat, StartChatRunRequest(message="hello"))
            self.assertEqual(chat.workspace_path, str(self.root.resolve()))
            _bind_requested_workspace(db, chat, StartChatRunRequest(message="hello", workspace_mode="auto"))
            self.assertEqual(chat.workspace_mode, "auto")
            self.assertEqual(chat.workspace_path, str(self.root.resolve()))
            _bind_requested_workspace(db, chat, StartChatRunRequest(message="hello", workspace_path=None))
            self.assertIsNone(chat.workspace_path)

    def test_start_run_binds_workspace_in_response(self):
        with patch("app.api.runs.run_manager.start") as start:
            response = self.client.post("/api/runs/chat", json={
                "model_profile_id": self.profile_id, "message": "inspect project",
                "workspace_path": str(self.root), "workspace_mode": "auto",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["chat"]["workspace_path"], str(self.root.resolve()))
        self.assertEqual(response.json()["chat"]["workspace_mode"], "auto")
        start.assert_called_once()

    def test_cross_origin_and_rebinding_are_rejected(self):
        for headers in [
            {"Origin": "https://attacker.example"}, {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"}, {"Host": "attacker.example"},
        ]:
            with self.subTest(headers=headers):
                response = self.client.post("/api/workspaces/inspect", json={"path": str(self.root)}, headers=headers)
                self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/workspaces/inspect", json={"path": str(self.root)}, headers={"Origin": "http://localhost:3000"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:3000")

    def test_run_control_endpoints_validate_run_and_pending_call(self):
        self.assertEqual(self.client.post("/api/runs/messages/missing/cancel").status_code, 404)
        self.assertEqual(self.client.post("/api/runs/messages/missing/approval", json={"call_id": "tool-1", "approved": True}).status_code, 404)
        with self.Session() as db:
            message = Message(chat_id=self.chat_id, role="assistant", content="finished", status="done")
            db.add(message)
            db.commit()
            message_id = message.id
        self.assertEqual(self.client.post(f"/api/runs/messages/{message_id}/approval", json={"call_id": "tool-1", "approved": True}).status_code, 409)
        response = self.client.post(f"/api/runs/messages/{message_id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "done")

    def test_legacy_chat_and_plan_routes_reject_workspace_before_mutations(self):
        with self.Session() as db:
            task = AnalysisTask(chat_id=self.chat_id, user_message="inspect", status="awaiting_approval")
            db.add(task)
            db.flush()
            plan = AnalysisPlan(task_id=task.id, plan_markdown="Read project", prompt_key="test", prompt_version_hash="hash",
                                model_profile_id=self.profile_id, status="awaiting_approval")
            db.add(plan)
            db.commit()
            plan_id = plan.id
        payload = {"chat_id": self.chat_id, "model_profile_id": self.profile_id, "message": "work here"}
        requests = [
            ("/api/chat", payload), ("/api/chat/stream", payload),
            ("/api/plans/generate/stream", payload),
            (f"/api/plans/{plan_id}/approve/stream", {}),
            (f"/api/plans/{plan_id}/revise/stream", {"user_feedback": "revise"}),
        ]
        for url, body in requests:
            with self.subTest(url=url):
                response = self.client.post(url, json=body)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["code"], "WORKSPACE_MANAGED_RUN_REQUIRED")
                self.assertIn("/api/runs", response.json()["message"])
        with self.Session() as db:
            self.assertEqual(db.query(Message).count(), 0)
            self.assertEqual(db.get(AnalysisPlan, plan_id).status, "awaiting_approval")

    def test_history_includes_interrupted_workspace_evidence_only(self):
        with self.Session() as db:
            db.add_all([
                Message(chat_id=self.chat_id, role="assistant", content="old plain failure", status="error"),
                Message(chat_id=self.chat_id, role="assistant", content="partial work", status="cancelled",
                        metadata_json=json.dumps({"workspaceAgent": {"calls": [{"tool": "run_command", "status": "done",
                            "arguments": {"command": "python -m unittest"}, "result": {"ok": True, "exit_code": 0}}]}})),
            ])
            db.commit()
            history = _load_done_history(db, self.chat_id)
        self.assertEqual(len(history), 1)
        self.assertIn("python -m unittest", history[0]["content"])
        self.assertIn('"exit_code": 0', history[0]["content"])
        self.assertNotIn("old plain failure", str(history))

    def test_startup_marks_workspace_runs_and_tasks_interrupted(self):
        with self.Session() as db:
            message = Message(chat_id=self.chat_id, role="assistant", content="partial", status="streaming",
                              metadata_json=json.dumps({"workspaceAgent": {"status": "running", "calls": [{"status": "running"}]}}))
            task = AnalysisTask(chat_id=self.chat_id, user_message="do it", status="executing")
            db.add_all([message, task])
            db.commit()
            message_id, task_id = message.id, task.id
        with patch("app.db.session.SessionLocal", self.Session):
            mark_orphaned_streaming_messages()
        with self.Session() as db:
            message = db.get(Message, message_id)
            self.assertEqual(message.status, "interrupted")
            self.assertEqual(json.loads(message.metadata_json)["workspaceAgent"]["calls"][0]["status"], "interrupted")
            self.assertEqual(db.get(AnalysisTask, task_id).status, "interrupted")


class WorkspaceMigrationTests(unittest.TestCase):
    def test_existing_sqlite_gets_nullable_workspace_and_default_mode(self):
        engine = create_engine("sqlite+pysqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE chats (id TEXT PRIMARY KEY, title TEXT)")
                connection.exec_driver_sql("INSERT INTO chats VALUES ('old-chat', 'existing')")
            with patch("app.db.session.engine", engine):
                ensure_schema_compatibility()
                ensure_schema_compatibility()
            with engine.connect() as connection:
                row = connection.exec_driver_sql("SELECT workspace_path, workspace_mode FROM chats").one()
            self.assertEqual(tuple(row), (None, "approve"))
        finally:
            engine.dispose()


class AgentRunControlTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            chat = Chat(title="cancel")
            db.add(chat)
            db.flush()
            message = Message(chat_id=chat.id, role="assistant", content="kept partial", status="streaming", metadata_json=json.dumps({
                "workspaceAgent": {"status": "waiting_approval", "calls": [{"id": "call-1", "status": "waiting_approval"}]},
            }))
            task = AnalysisTask(chat_id=chat.id, user_message="execute", status="executing")
            db.add_all([message, task])
            db.commit()
            self.message_id, self.chat_id, self.task_id = message.id, chat.id, task.id
        self.manager = RunManager()

    def tearDown(self):
        self.engine.dispose()

    def test_approval_registration_before_request_handles_fast_decision(self):
        async def scenario():
            hold = asyncio.Event()
            async def runner():
                await hold.wait()
            self.manager.start(message_id=self.message_id, chat_id=self.chat_id, runner=runner)
            self.manager.prepare_approval(self.message_id, "call-1")
            self.assertFalse(self.manager.resolve_approval(self.message_id, "wrong-id", True))
            self.assertTrue(self.manager.resolve_approval(self.message_id, "call-1", False))
            self.assertFalse(self.manager.resolve_approval(self.message_id, "call-1", True))
            self.assertFalse(await self.manager.request_approval(self.message_id, "call-1"))
            hold.set()
            await self.manager._runs_by_message[self.message_id].task
        asyncio.run(scenario())

    def test_cancel_waits_for_cleanup_persists_and_closes_stream(self):
        async def scenario():
            started = asyncio.Event()
            cleaned = asyncio.Event()
            async def runner():
                started.set()
                try:
                    await self.manager.request_approval(self.message_id, "call-1")
                finally:
                    await asyncio.sleep(0.01)
                    cleaned.set()
            self.manager.start(message_id=self.message_id, chat_id=self.chat_id, runner=runner)
            await started.wait()
            queue = asyncio.Queue()
            self.manager._runs_by_message[self.message_id].queues.add(queue)
            self.assertTrue(await self.manager.cancel(self.message_id))
            self.assertTrue(cleaned.is_set())
            self.assertFalse(self.manager.is_chat_running(self.chat_id))
            self.assertFalse(await self.manager.cancel(self.message_id))
            self.assertEqual((await queue.get())[0], "snapshot")
            event, data = await queue.get()
            self.assertEqual(event, "done")
            self.assertEqual(data["status"], "cancelled")
        with patch("app.services.run_manager.SessionLocal", self.Session):
            asyncio.run(scenario())
        with self.Session() as db:
            message = db.get(Message, self.message_id)
            self.assertEqual(message.status, "cancelled")
            self.assertEqual(message.content, "kept partial")
            self.assertEqual(json.loads(message.metadata_json)["workspaceAgent"]["calls"][0]["status"], "cancelled")
            self.assertEqual(db.get(AnalysisTask, self.task_id).status, "cancelled")

    def test_cancel_before_task_starts_persists_terminal_state(self):
        async def scenario():
            async def runner():
                self.fail("The cancelled task must never start")
            self.manager.start(message_id=self.message_id, chat_id=self.chat_id, runner=runner)
            await self.manager.cancel(self.message_id)
        with patch("app.services.run_manager.SessionLocal", self.Session):
            asyncio.run(scenario())
        with self.Session() as db:
            self.assertEqual(db.get(Message, self.message_id).status, "cancelled")


if __name__ == "__main__":
    unittest.main()
