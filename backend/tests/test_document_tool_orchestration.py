import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.adapters.base import AdapterStreamEvent
from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import Base, Chat, Message, ModelProfile, UploadedFile
from app.services.python_sandbox import SANDBOX_NOT_READY
from app.services.tool_orchestrator import ToolOrchestrator


class ScriptedAdapter:
    def __init__(self, calls, *, repeat=False):
        self.calls = calls
        self.repeat = repeat
        self.requests = []

    async def stream_chat(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        index = len(self.requests) - 1
        if index < len(self.calls) or self.repeat:
            name, arguments = self.calls[min(index, len(self.calls) - 1)]
            yield AdapterStreamEvent("tool_call_delta", {
                "index": 0, "name": name, "id": f"call-{index}",
                "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
            })
            yield AdapterStreamEvent("finish_reason", "tool_calls")
        else:
            yield AdapterStreamEvent("delta", "Answer from evidence.")
            yield AdapterStreamEvent("finish_reason", "stop")


class DocumentToolOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.profile = ModelProfile(name="mock", provider="openai_compat", model_name="mock", supports_tools=True)
        self.chat = Chat(title="Document tools")
        self.db.add_all([self.profile, self.chat])
        self.db.flush()
        self.message = Message(chat_id=self.chat.id, role="assistant", content="", status="streaming")
        self.db.add(self.message)
        self.db.commit()
        self.orchestrator = ToolOrchestrator(
            self.db, profile=self.profile, assistant_message_id=self.message.id,
            chat_id=self.chat.id, current_file_ids=["attached-file"],
        )
        self.settings = SimpleNamespace(document_max_tool_calls=3, sandbox_max_tool_calls=1)
        self.document_result = {
            "ok": True, "file_id": "attached-file", "filename": "long.txt",
            "blocks": [{"id": "b000101", "text": "TAIL_EVIDENCE", "location": {"line": 101}}],
            "truncated": False, "next_block": None,
        }

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    async def collect(self):
        return [event async for event in self.orchestrator.stream(
            messages=[{"role": "user", "content": "Read the final result."}], extra_params=None,
        )]

    def test_missing_sandbox_still_reads_documents_and_forwards_evidence(self):
        adapter = ScriptedAdapter([("read_file", {"file_id": "attached-file", "start_block": 101})])
        execute = AsyncMock(return_value=self.document_result)
        with (
            patch("app.services.tool_orchestrator.PythonSandboxService.create_workspace",
                  side_effect=AppError(SANDBOX_NOT_READY, "Sandbox unavailable")),
            patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter),
            patch("app.services.tool_orchestrator.execute_document_tool", execute),
            patch("app.services.tool_orchestrator.get_settings", return_value=self.settings),
        ):
            events = asyncio.run(self.collect())
        self.assertEqual(self.orchestrator.call_count, 0)
        self.assertEqual(self.orchestrator.document_call_count, 1)
        self.assertNotIn("python_sandbox_status", [event.event for event in events])
        self.assertNotIn("generated_files", [event.event for event in events])
        tool_names = {tool["function"]["name"] for tool in adapter.requests[0]["tools"]}
        self.assertEqual(tool_names, {"read_file", "search_file"})
        self.assertIn("TAIL_EVIDENCE", adapter.requests[1]["messages"][-1]["content"])
        self.assertEqual(execute.await_args.kwargs["chat_id"], self.chat.id)
        self.assertEqual(execute.await_args.kwargs["current_file_ids"], ["attached-file"])
        self.db.refresh(self.message)
        metadata = json.loads(self.message.metadata_json)
        self.assertNotIn("pythonSandbox", metadata)
        record = metadata["documentTools"]["calls"][0]
        self.assertEqual(record["fileId"], "attached-file")
        self.assertEqual(record["blockIds"], ["b000101"])
        self.assertNotIn("TAIL_EVIDENCE", json.dumps(metadata))

    def test_document_reads_do_not_collect_python_outputs(self):
        adapter = ScriptedAdapter([("search_file", {"file_id": "attached-file", "query": "result"})])
        workspace = MagicMock(prompt_context="Input manifest")
        with (
            patch("app.services.tool_orchestrator.PythonSandboxService.create_workspace", return_value=workspace),
            patch("app.services.tool_orchestrator.PromptService.load_prompt", return_value=SimpleNamespace(content="Python rules")),
            patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter),
            patch("app.services.tool_orchestrator.execute_document_tool", AsyncMock(return_value=self.document_result)),
            patch("app.services.tool_orchestrator.get_settings", return_value=self.settings),
        ):
            events = asyncio.run(self.collect())
        workspace.collect_outputs.assert_not_called()
        workspace.execute.assert_not_called()
        workspace.cleanup.assert_called_once()
        self.assertNotIn("python_sandbox_status", [event.event for event in events])

    def test_model_can_search_and_read_real_long_file_tail(self):
        class EvidenceAdapter:
            def __init__(self, file_id):
                self.file_id = file_id
                self.requests = []
                self.tail = ""

            async def stream_chat(self, **kwargs):
                self.requests.append(copy.deepcopy(kwargs))
                if len(self.requests) == 1:
                    name = "search_file"
                    arguments = {"file_id": self.file_id, "query": "TAIL_EVIDENCE", "neighbors": 0}
                elif len(self.requests) == 2:
                    evidence = json.loads(kwargs["messages"][-1]["content"])
                    block = next(block for block in evidence["blocks"] if "TAIL_EVIDENCE" in block["text"])
                    name = "read_file"
                    arguments = {
                        "file_id": self.file_id, "start_block": block["block_index"],
                        "start_offset": block["start_offset"], "limit": 1,
                    }
                else:
                    evidence = json.loads(kwargs["messages"][-1]["content"])
                    self.tail = "\n".join(block["text"] for block in evidence["blocks"])
                    yield AdapterStreamEvent("delta", self.tail)
                    yield AdapterStreamEvent("finish_reason", "stop")
                    return
                yield AdapterStreamEvent("tool_call_delta", {
                    "index": 0, "id": f"real-{len(self.requests)}", "name": name,
                    "arguments": json.dumps(arguments),
                })
                yield AdapterStreamEvent("finish_reason", "tool_calls")

        with tempfile.TemporaryDirectory(prefix="labai-document-tool-test-") as directory:
            source = Path(directory) / "long.txt"
            body = "Reference background. " * 2500 + "\n\nTAIL_EVIDENCE result=73."
            source.write_text(body, encoding="utf-8")
            uploaded = UploadedFile(
                original_filename=source.name, stored_filename=source.name, extension="txt",
                mime_type="text/plain", file_type="text", size=source.stat().st_size,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(), local_path=str(source),
            )
            self.db.add(uploaded)
            self.db.commit()
            self.orchestrator.current_file_ids = [uploaded.id]
            adapter = EvidenceAdapter(uploaded.id)
            parser_settings = Settings(_env_file=None, backend_storage_dir=Path(directory) / "storage")
            with (
                patch("app.services.tool_orchestrator.PythonSandboxService.create_workspace",
                      side_effect=AppError(SANDBOX_NOT_READY, "Sandbox unavailable")),
                patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter),
                patch("app.services.document_service.get_settings", return_value=parser_settings),
                patch("app.services.tool_orchestrator.get_settings", return_value=self.settings),
            ):
                asyncio.run(self.collect())
        self.assertIn("TAIL_EVIDENCE result=73", adapter.tail)
        self.assertEqual(self.orchestrator.call_count, 0)
        self.assertEqual(self.orchestrator.document_call_count, 2)
        self.assertNotIn("pythonSandbox", self.orchestrator.metadata)

    def test_read_and_python_budgets_are_independent(self):
        adapter = ScriptedAdapter([
            ("read_file", {"file_id": "attached-file"}),
            ("execute_python", {"code": "print(2)"}),
            ("search_file", {"file_id": "attached-file", "query": "result"}),
        ])
        workspace = MagicMock(prompt_context="Input manifest")
        workspace.execute = AsyncMock(return_value=SimpleNamespace(to_tool_payload=lambda: {"ok": True, "stdout": "2"}))
        workspace.collect_outputs.return_value = []
        with (
            patch("app.services.tool_orchestrator.PythonSandboxService.create_workspace", return_value=workspace),
            patch("app.services.tool_orchestrator.PromptService.load_prompt", return_value=SimpleNamespace(content="Python rules")),
            patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter),
            patch("app.services.tool_orchestrator.execute_document_tool", AsyncMock(return_value=self.document_result)),
            patch("app.services.tool_orchestrator.get_settings", return_value=self.settings),
        ):
            events = asyncio.run(self.collect())
        self.assertEqual(self.orchestrator.call_count, 1)
        self.assertEqual(self.orchestrator.document_call_count, 2)
        self.assertEqual(self.orchestrator.metadata["pythonSandbox"], {"status": "done", "callCount": 1})
        self.assertEqual([event.event for event in events].count("python_sandbox_status"), 3)
        workspace.collect_outputs.assert_called_once()

    def test_document_and_unknown_tool_retry_loops_are_bounded(self):
        for name in ("read_file", "invented_tool"):
            with self.subTest(name=name):
                self.orchestrator.document_call_count = 0
                adapter = ScriptedAdapter([(name, {"file_id": "attached-file"})], repeat=True)
                with (
                    patch("app.services.tool_orchestrator.PythonSandboxService.create_workspace",
                          side_effect=AppError(SANDBOX_NOT_READY, "Sandbox unavailable")),
                    patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter),
                    patch("app.services.tool_orchestrator.execute_document_tool", AsyncMock(return_value=self.document_result)),
                    patch("app.services.tool_orchestrator.get_settings", return_value=self.settings),
                ):
                    with self.assertRaises(AppError) as raised:
                        asyncio.run(self.collect())
                self.assertEqual(raised.exception.code, "DOCUMENT_TOOL_LIMIT")
                self.assertEqual(len(adapter.requests), 4)
                self.assertEqual(self.orchestrator.call_count, 0)
                self.assertNotIn("pythonSandbox", self.orchestrator.metadata)

    def test_invalid_document_arguments_are_reported_without_python_state(self):
        adapter = ScriptedAdapter([("read_file", "[]")])
        execute = AsyncMock()
        with (
            patch("app.services.tool_orchestrator.PythonSandboxService.create_workspace",
                  side_effect=AppError(SANDBOX_NOT_READY, "Sandbox unavailable")),
            patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter),
            patch("app.services.tool_orchestrator.execute_document_tool", execute),
            patch("app.services.tool_orchestrator.get_settings", return_value=self.settings),
        ):
            asyncio.run(self.collect())
        execute.assert_not_awaited()
        result = json.loads(adapter.requests[1]["messages"][-1]["content"])
        self.assertFalse(result["ok"])
        self.assertIn("object", result["error"])
        self.assertNotIn("pythonSandbox", self.orchestrator.metadata)

    def test_disabled_tools_disclose_unread_content_limit(self):
        self.profile.supports_tools = False
        adapter = ScriptedAdapter([])
        with (
            patch("app.services.tool_orchestrator.PythonSandboxService.create_workspace") as workspace,
            patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=adapter),
        ):
            asyncio.run(self.collect())
        workspace.assert_not_called()
        request = adapter.requests[0]
        self.assertNotIn("tools", request)
        self.assertIn("cannot search or read", request["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
