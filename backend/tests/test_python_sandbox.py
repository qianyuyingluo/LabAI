import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.base import AdapterStreamEvent
from app.api.files import read_uploaded_file
from app.core.config import get_settings
from app.db.models import Base, Chat, Message, ModelProfile, UploadedFile
from app.services.python_sandbox import (
    PythonSandboxService,
    PythonSandboxWorkspace,
    _reset_sandbox_readiness_cache,
    sandbox_ready,
)
from app.services.tool_orchestrator import ToolOrchestrator


class PythonSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="labai-python-sandbox-test-"))
        self.settings = get_settings()
        self.original = {
            "backend_storage_dir": self.settings.backend_storage_dir,
            "labai_sandbox_python": self.settings.labai_sandbox_python,
            "sandbox_timeout_seconds": self.settings.sandbox_timeout_seconds,
            "sandbox_max_output_file_mb": self.settings.sandbox_max_output_file_mb,
            "sandbox_max_output_total_mb": self.settings.sandbox_max_output_total_mb,
            "sandbox_max_memory_mb": self.settings.sandbox_max_memory_mb,
        }
        self.settings.backend_storage_dir = self.temp_dir / "storage"
        self.settings.labai_sandbox_python = Path(sys.executable)
        self.settings.sandbox_timeout_seconds = 2

    def tearDown(self) -> None:
        _reset_sandbox_readiness_cache()
        for key, value in self.original.items():
            setattr(self.settings, key, value)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _workspace(self) -> PythonSandboxWorkspace:
        root = self.temp_dir / "workspace"
        for name in ("inputs", "outputs", "tmp"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return PythonSandboxWorkspace(message_id="test", root=root, inputs=[])

    def test_executes_code_and_validates_json_output(self) -> None:
        workspace = self._workspace()
        result = asyncio.run(
            workspace.execute(
                "from pathlib import Path\n"
                "Path('outputs/result.json').write_text('{\"answer\": 42}', encoding='utf-8')\n"
                "print('calculated')\n"
            )
        )

        self.assertTrue(result.ok, result.to_tool_payload())
        self.assertIn("calculated", result.stdout)
        self.assertEqual(result.outputs, [{"name": "result.json", "path": "outputs/result.json", "size": 14}])

    def test_rejects_network_import_before_execution(self) -> None:
        result = asyncio.run(self._workspace().execute("import socket\nprint(socket.gethostname())"))

        self.assertFalse(result.ok)
        self.assertIn("blocked", result.policy_error or "")

    def test_blocks_reading_files_outside_workspace(self) -> None:
        secret = self.temp_dir / "outside-secret.txt"
        secret.write_text("must stay private", encoding="utf-8")
        workspace = self._workspace()
        result = asyncio.run(
            workspace.execute(
                "from pathlib import Path\n"
                f"print(Path({str(secret)!r}).read_text(encoding='utf-8'))\n"
            )
        )

        self.assertFalse(result.ok)
        self.assertNotIn("must stay private", result.stdout)
        self.assertIn("outside the sandbox workspace", result.stderr)

    def test_blocks_changing_directory_outside_workspace(self) -> None:
        result = asyncio.run(
            self._workspace().execute("import os\nimport sys\nos.chdir(sys.prefix)\n")
        )

        self.assertFalse(result.ok)
        self.assertIn("Changing directory outside the sandbox workspace", result.stderr)

    def test_blocks_dynamic_import_calls(self) -> None:
        result = asyncio.run(
            self._workspace().execute("import builtins\nbuiltins.__import__('subprocess')\n")
        )

        self.assertFalse(result.ok)
        self.assertIn("blocked", result.policy_error or "")

    def test_runtime_network_guard_does_not_depend_on_ast_imports(self) -> None:
        code = (
            "import builtins\n"
            "loader = getattr(builtins, '_' + '_import' + '__')\n"
            "socket_module = loader('socket')\n"
            "socket_module.socket()\n"
        )
        result = asyncio.run(self._workspace().execute(code))

        self.assertFalse(result.ok)
        self.assertIn("Network access is disabled", result.stderr)

    def test_stops_process_when_output_file_exceeds_limit(self) -> None:
        self.settings.sandbox_max_output_file_mb = 1
        self.settings.sandbox_max_output_total_mb = 2
        result = asyncio.run(
            self._workspace().execute(
                "from pathlib import Path\n"
                "import time\n"
                "Path('outputs/large.txt').write_bytes(b'x' * (3 * 1024 * 1024))\n"
                "time.sleep(5)\n"
            )
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.output_limited)

    def test_stops_process_when_memory_limit_is_exceeded(self) -> None:
        self.settings.sandbox_max_memory_mb = 1
        result = asyncio.run(
            self._workspace().execute("import time\ntime.sleep(5)\n")
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.memory_limited)

    def test_sensitive_parent_environment_is_not_inherited(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak"}):
            result = asyncio.run(
                self._workspace().execute(
                    "import os\nprint(os.environ.get('OPENAI_API_KEY', 'not-present'))\n"
                )
            )

        self.assertTrue(result.ok, result.to_tool_payload())
        self.assertEqual(result.stdout.strip(), "not-present")

    def test_svg_scripts_and_external_references_are_removed(self) -> None:
        workspace = self._workspace()
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<script>alert(1)</script>'
            '<image xlink:href="https://example.test/image.png" />'
            '<rect width="10" height="10" onclick="alert(1)" style="fill:red" />'
            '</svg>'
        )
        result = asyncio.run(
            workspace.execute(
                "from pathlib import Path\n"
                f"Path('outputs/safe.svg').write_text({svg!r}, encoding='utf-8')\n"
            )
        )
        sanitized = (workspace.outputs_dir / "safe.svg").read_text(encoding="utf-8")

        self.assertTrue(result.ok, result.to_tool_payload())
        self.assertNotIn("script", sanitized)
        self.assertNotIn("example.test", sanitized)
        self.assertNotIn("onclick", sanitized)
        self.assertIn("fill:red", sanitized)

    def test_times_out_runaway_code(self) -> None:
        self.settings.sandbox_timeout_seconds = 1
        result = asyncio.run(self._workspace().execute("while True:\n    pass\n"))

        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)

    def test_readiness_probe_is_cached_and_invalidated_by_interpreter_mtime(self) -> None:
        python = self.temp_dir / ".sandbox-venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"fake-python")
        completed = subprocess.CompletedProcess(args=[], returncode=0)

        with (
            patch("app.services.python_sandbox.sandbox_python_path", return_value=python),
            patch("app.services.python_sandbox.subprocess.run", return_value=completed) as run,
        ):
            _reset_sandbox_readiness_cache()
            self.assertTrue(sandbox_ready())
            self.assertTrue(sandbox_ready())
            self.assertEqual(run.call_count, 1)

            current = python.stat()
            os.utime(python, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000))
            self.assertTrue(sandbox_ready())
            self.assertEqual(run.call_count, 2)
            self.assertIn("numpy", run.call_args.args[0][2])

    def test_failed_readiness_probe_is_briefly_cached(self) -> None:
        python = self.temp_dir / ".sandbox-venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"fake-python")
        completed = subprocess.CompletedProcess(args=[], returncode=1)

        with (
            patch("app.services.python_sandbox.sandbox_python_path", return_value=python),
            patch("app.services.python_sandbox.subprocess.run", return_value=completed) as run,
        ):
            _reset_sandbox_readiness_cache()
            self.assertFalse(sandbox_ready())
            self.assertFalse(sandbox_ready())
            self.assertEqual(run.call_count, 1)


class ToolOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="labai-tool-orchestrator-test-"))
        self.settings = get_settings()
        self.original_storage = self.settings.backend_storage_dir
        self.original_python = self.settings.labai_sandbox_python
        self.settings.backend_storage_dir = self.temp_dir / "storage"
        self.settings.labai_sandbox_python = Path(sys.executable)
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, future=True)

    def tearDown(self) -> None:
        self.settings.backend_storage_dir = self.original_storage
        self.settings.labai_sandbox_python = self.original_python
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tool_loop_registers_files_and_persists_metadata(self) -> None:
        async def run_case() -> tuple[list[str], str, int]:
            with self.Session() as db:
                profile = ModelProfile(
                    name="tool model",
                    provider="openai_compat",
                    model_name="fake",
                    api_key_secret="fake",
                    supports_stream=True,
                    supports_tools=True,
                )
                chat = Chat(title="sandbox")
                db.add_all([profile, chat])
                db.flush()
                message = Message(
                    chat_id=chat.id,
                    role="assistant",
                    content="",
                    status="streaming",
                    model_profile_id=profile.id,
                )
                db.add(message)
                db.commit()
                fake = _FakeToolAdapter()
                orchestrator = ToolOrchestrator(
                    db,
                    profile=profile,
                    assistant_message_id=message.id,
                    chat_id=chat.id,
                    current_file_ids=[],
                )
                events = []
                with patch("app.services.tool_orchestrator.get_adapter_for_profile", return_value=fake):
                    async for event in orchestrator.stream(
                        messages=[{"role": "user", "content": "create data"}],
                        extra_params=None,
                    ):
                        events.append(event.event)
                db.refresh(message)
                metadata = json.loads(message.metadata_json or "{}")
                files = list(db.query(UploadedFile).all())
                uploaded = files[0]
                download = await read_uploaded_file(uploaded.id, db)
                self.assertEqual(uploaded.mime_type, "application/json")
                self.assertEqual(len(uploaded.sha256), 64)
                self.assertTrue(Path(uploaded.local_path).is_file())
                self.assertEqual(download.media_type, "application/json")
                self.assertEqual(download.filename, "analysis.json")
                self.assertFalse((self.settings.sandbox_runs_dir / message.id).exists())
                return events, metadata["generatedFiles"][0]["name"], len(files)

        events, filename, file_count = asyncio.run(run_case())
        self.assertIn("python_sandbox_status", events)
        self.assertIn("generated_files", events)
        self.assertEqual(filename, "analysis.json")
        self.assertEqual(file_count, 1)

    def test_historical_generated_file_is_mounted_for_followup(self) -> None:
        with self.Session() as db:
            profile = ModelProfile(
                name="followup model",
                provider="openai_compat",
                model_name="fake",
                api_key_secret="fake",
                supports_tools=True,
            )
            chat = Chat(title="followup", model_profile=profile)
            source = self.settings.generated_file_dir / "data" / "prior.json"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('{"prior": 1}', encoding="utf-8")
            uploaded = UploadedFile(
                original_filename="上次结果.json",
                stored_filename="prior.json",
                mime_type="application/json",
                extension="json",
                size=source.stat().st_size,
                sha256="1" * 64,
                local_path=str(source),
                file_type="data",
            )
            db.add_all([profile, chat, uploaded])
            db.flush()
            db.add(
                Message(
                    chat_id=chat.id,
                    role="assistant",
                    content="created",
                    status="done",
                    metadata_json=json.dumps(
                        {
                            "generatedFiles": [
                                {
                                    "fileId": uploaded.id,
                                    "name": uploaded.original_filename,
                                    "status": "ready",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()

            workspace = PythonSandboxService(db).create_workspace(
                message_id="followup-message",
                chat_id=chat.id,
                current_file_ids=[],
            )
            try:
                self.assertEqual([item.file_id for item in workspace.inputs], [uploaded.id])
                self.assertEqual(workspace.inputs[0].name, "上次结果.json")
                self.assertTrue((workspace.root / workspace.inputs[0].path).is_file())
            finally:
                workspace.cleanup()

    def test_fragmented_tool_name_is_merged(self) -> None:
        async def run_case() -> tuple[list[str], int]:
            with self.Session() as db:
                profile = ModelProfile(
                    name="fragmented tool model",
                    provider="openai_compat",
                    model_name="fake",
                    api_key_secret="fake",
                    supports_stream=True,
                    supports_tools=True,
                )
                chat = Chat(title="fragmented")
                db.add_all([profile, chat])
                db.flush()
                message = Message(
                    chat_id=chat.id,
                    role="assistant",
                    content="",
                    status="streaming",
                    model_profile_id=profile.id,
                )
                db.add(message)
                db.commit()
                orchestrator = ToolOrchestrator(
                    db,
                    profile=profile,
                    assistant_message_id=message.id,
                    chat_id=chat.id,
                    current_file_ids=[],
                )
                events: list[str] = []
                with patch(
                    "app.services.tool_orchestrator.get_adapter_for_profile",
                    return_value=_FragmentedToolAdapter(),
                ):
                    async for event in orchestrator.stream(
                        messages=[{"role": "user", "content": "run fragmented tool"}],
                        extra_params=None,
                    ):
                        events.append(event.event)
                return events, orchestrator.call_count

        events, call_count = asyncio.run(run_case())
        self.assertEqual(call_count, 1)
        self.assertIn("python_sandbox_status", events)

    def test_provider_failure_publishes_persisted_error_state_and_files(self) -> None:
        async def run_case() -> tuple[list[str], dict[str, object], int]:
            with self.Session() as db:
                profile = ModelProfile(
                    name="failing tool model",
                    provider="openai_compat",
                    model_name="fake",
                    api_key_secret="fake",
                    supports_stream=True,
                    supports_tools=True,
                )
                chat = Chat(title="sandbox failure")
                db.add_all([profile, chat])
                db.flush()
                message = Message(
                    chat_id=chat.id,
                    role="assistant",
                    content="",
                    status="streaming",
                    model_profile_id=profile.id,
                )
                db.add(message)
                db.commit()
                orchestrator = ToolOrchestrator(
                    db,
                    profile=profile,
                    assistant_message_id=message.id,
                    chat_id=chat.id,
                    current_file_ids=[],
                )
                events: list[str] = []
                with patch(
                    "app.services.tool_orchestrator.get_adapter_for_profile",
                    return_value=_FailingAfterToolAdapter(),
                ):
                    with self.assertRaisesRegex(RuntimeError, "provider failed"):
                        async for event in orchestrator.stream(
                            messages=[{"role": "user", "content": "create partial data"}],
                            extra_params=None,
                        ):
                            events.append(event.event)
                db.refresh(message)
                metadata = json.loads(message.metadata_json or "{}")
                return events, metadata, db.query(UploadedFile).count()

        events, metadata, file_count = asyncio.run(run_case())
        self.assertEqual(events[-2:], ["python_sandbox_status", "generated_files"])
        self.assertEqual(metadata["pythonSandbox"]["status"], "error")
        self.assertEqual(metadata["generatedFiles"][0]["name"], "partial.json")
        self.assertEqual(file_count, 1)


class _FakeToolAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(self, **_: object):
        self.calls += 1
        if self.calls == 1:
            code = (
                "from pathlib import Path\n"
                "Path('outputs/analysis.json').write_text('{\"value\": 7}', encoding='utf-8')\n"
            )
            arguments = json.dumps({"code": code}, ensure_ascii=False)
            yield AdapterStreamEvent(
                "tool_call_delta",
                {
                    "index": 0,
                    "id": "call-1",
                    "name": "execute_python",
                    "arguments": arguments,
                },
            )
            yield AdapterStreamEvent("finish_reason", "tool_calls")
            return
        yield AdapterStreamEvent("delta", "Completed.")
        yield AdapterStreamEvent("usage", {"total_tokens": 12})
        yield AdapterStreamEvent("finish_reason", "stop")


class _FailingAfterToolAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(self, **_: object):
        self.calls += 1
        if self.calls == 1:
            code = (
                "from pathlib import Path\n"
                "Path('outputs/partial.json').write_text('{\"partial\": true}', encoding='utf-8')\n"
            )
            yield AdapterStreamEvent(
                "tool_call_delta",
                {
                    "index": 0,
                    "id": "call-partial",
                    "name": "execute_python",
                    "arguments": json.dumps({"code": code}),
                },
            )
            yield AdapterStreamEvent("finish_reason", "tool_calls")
            return
        raise RuntimeError("provider failed")


class _FragmentedToolAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(self, **_: object):
        self.calls += 1
        if self.calls == 1:
            arguments = json.dumps({"code": "print('fragmented tool works')"})
            yield AdapterStreamEvent(
                "tool_call_delta",
                {"index": 0, "id": "call-", "name": "execute_", "arguments": arguments[:8]},
            )
            yield AdapterStreamEvent(
                "tool_call_delta",
                {"index": 0, "id": "fragment", "name": "python", "arguments": arguments[8:]},
            )
            yield AdapterStreamEvent("finish_reason", "tool_calls")
            return
        yield AdapterStreamEvent("delta", "Completed.")
        yield AdapterStreamEvent("finish_reason", "stop")


if __name__ == "__main__":
    unittest.main()
