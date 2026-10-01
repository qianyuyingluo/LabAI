import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.chats import delete_chat
from app.api.plans import _approve_and_execute_events
from app.api.runs import _fail_message
from app.core.config import get_settings
from app.core.errors import PROVIDER_STREAM_ERROR, AppError
from app.db.models import (
    AnalysisPlan,
    AnalysisTask,
    Base,
    Chat,
    Message,
    ModelProfile,
    UploadedFile,
)
from app.services.chat_service import run_chat, stream_chat
from app.services.plan_execution_service import PlanExecutionService
from app.services.plan_service import (
    PLAN_STATUS_AWAITING_APPROVAL,
    TASK_STATUS_AWAITING_APPROVAL,
    TASK_STATUS_ERROR,
    TASK_STATUS_EXECUTING,
)
from app.services.tool_orchestrator import OrchestratorEvent


class ExecutionStateTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, future=True)

    def test_run_plan_failure_marks_execution_task_error(self) -> None:
        with self.Session() as db:
            chat = Chat(title="plan run")
            task = AnalysisTask(
                chat_id=chat.id,
                user_message="analyze",
                status=TASK_STATUS_EXECUTING,
            )
            db.add_all([chat, task])
            db.flush()
            message = Message(
                chat_id=chat.id,
                role="assistant",
                content="",
                status="streaming",
                message_type="execution_result",
                metadata_json=json.dumps({"pythonSandbox": {"status": "running"}}),
            )
            db.add(message)
            db.commit()
            chat_id = chat.id
            task_id = task.id
            message_id = message.id

        with patch("app.api.runs.SessionLocal", self.Session):
            asyncio.run(
                _fail_message(
                    message_id,
                    chat_id,
                    {"message": "execution failed"},
                    task_id=task_id,
                )
            )

        with self.Session() as db:
            self.assertEqual(db.get(AnalysisTask, task_id).status, TASK_STATUS_ERROR)
            self.assertEqual(db.get(Message, message_id).status, "error")

    def test_legacy_non_stream_chat_failure_finishes_assistant_error(self) -> None:
        with self.Session() as db:
            profile, chat = _profile_and_chat(db, "legacy non-stream")
            with patch("app.services.chat_service.ToolOrchestrator", _FailingOrchestrator):
                with self.assertRaises(AppError):
                    asyncio.run(
                        run_chat(
                            db,
                            chat_id=chat.id,
                            model_profile_id=profile.id,
                            message="fail",
                            image_file_ids=[],
                        )
                    )

            assistant = db.scalars(
                select(Message).where(Message.chat_id == chat.id, Message.role == "assistant")
            ).one()
            self.assertEqual(assistant.status, "error")
            self.assertEqual(json.loads(assistant.metadata_json)["pythonSandbox"]["status"], "error")

    def test_legacy_chat_disconnect_and_close_do_not_mark_done(self) -> None:
        async def disconnected() -> bool:
            return True

        with self.Session() as db:
            profile, chat = _profile_and_chat(db, "legacy disconnect")
            with patch("app.services.chat_service.ToolOrchestrator", _SingleEventOrchestrator):
                events = asyncio.run(
                    _collect(
                        stream_chat(
                            db,
                            chat_id=chat.id,
                            model_profile_id=profile.id,
                            message="disconnect",
                            image_file_ids=[],
                            is_disconnected=disconnected,
                        )
                    )
                )
            self.assertEqual(events, [])
            disconnected_message = _assistant_message(db, chat.id)
            self.assertEqual(disconnected_message.status, "error")

            second_chat = Chat(title="legacy close", model_profile_id=profile.id)
            db.add(second_chat)
            db.commit()

            async def close_after_first_event() -> None:
                with patch("app.services.chat_service.ToolOrchestrator", _SingleEventOrchestrator):
                    generator = stream_chat(
                        db,
                        chat_id=second_chat.id,
                        model_profile_id=profile.id,
                        message="close",
                        image_file_ids=[],
                    )
                    await generator.__anext__()
                    await generator.aclose()

            asyncio.run(close_after_first_event())
            self.assertEqual(_assistant_message(db, second_chat.id).status, "error")

    def test_legacy_plan_failure_and_disconnect_finish_task_and_message(self) -> None:
        for orchestrator, disconnected in (
            (_FailingOrchestrator, False),
            (_SingleEventOrchestrator, True),
        ):
            with self.subTest(disconnected=disconnected):
                with self.Session() as db:
                    profile, chat = _profile_and_chat(db, f"plan {disconnected}")
                    task = AnalysisTask(
                        chat_id=chat.id,
                        user_message="analyze",
                        status=TASK_STATUS_AWAITING_APPROVAL,
                    )
                    db.add(task)
                    db.flush()
                    plan = AnalysisPlan(
                        task_id=task.id,
                        version=1,
                        status=PLAN_STATUS_AWAITING_APPROVAL,
                        plan_markdown="Do the analysis",
                        prompt_key="plan_skill",
                        prompt_version_hash="hash",
                        model_profile_id=profile.id,
                    )
                    db.add(plan)
                    db.flush()
                    task.current_plan_id = plan.id
                    db.commit()
                    task_id = task.id

                    request = _Request(disconnected)
                    with (
                        patch("app.api.plans.ToolOrchestrator", orchestrator),
                        patch.object(PlanExecutionService, "build_provider_messages", return_value=[]),
                    ):
                        asyncio.run(_collect(_approve_and_execute_events(db, request, plan.id)))

                    self.assertEqual(db.get(AnalysisTask, task_id).status, TASK_STATUS_ERROR)
                    execution = db.scalars(
                        select(Message).where(
                            Message.chat_id == chat.id,
                            Message.message_type == "execution_result",
                        )
                    ).one()
                    self.assertEqual(execution.status, "error")
                    metadata = json.loads(execution.metadata_json)
                    self.assertEqual(metadata["pythonSandbox"]["status"], "error")

    def test_legacy_plan_stream_close_finishes_task_and_message_error(self) -> None:
        with self.Session() as db:
            profile, chat = _profile_and_chat(db, "plan close")
            task = AnalysisTask(
                chat_id=chat.id,
                user_message="analyze",
                status=TASK_STATUS_AWAITING_APPROVAL,
            )
            db.add(task)
            db.flush()
            plan = AnalysisPlan(
                task_id=task.id,
                version=1,
                status=PLAN_STATUS_AWAITING_APPROVAL,
                plan_markdown="Do the analysis",
                prompt_key="plan_skill",
                prompt_version_hash="hash",
                model_profile_id=profile.id,
            )
            db.add(plan)
            db.flush()
            task.current_plan_id = plan.id
            db.commit()
            task_id = task.id

            async def close_after_first_event() -> None:
                generator = _approve_and_execute_events(db, _Request(False), plan.id)
                await generator.__anext__()
                await generator.aclose()

            with (
                patch("app.api.plans.ToolOrchestrator", _SingleEventOrchestrator),
                patch.object(PlanExecutionService, "build_provider_messages", return_value=[]),
            ):
                asyncio.run(close_after_first_event())

            self.assertEqual(db.get(AnalysisTask, task_id).status, TASK_STATUS_ERROR)
            execution = db.scalars(
                select(Message).where(
                    Message.chat_id == chat.id,
                    Message.message_type == "execution_result",
                )
            ).one()
            self.assertEqual(execution.status, "error")


class GeneratedFileCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="labai-cleanup-test-"))
        self.settings = get_settings()
        self.original_storage = self.settings.backend_storage_dir
        self.settings.backend_storage_dir = self.temp_dir / "storage"
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, future=True)

    def tearDown(self) -> None:
        self.settings.backend_storage_dir = self.original_storage
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_chat_delete_commits_before_unlink_and_rejects_outside_path(self) -> None:
        generated = self.settings.generated_file_dir / "safe.txt"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("safe", encoding="utf-8")
        outside = self.temp_dir / "outside.txt"
        outside.write_text("keep", encoding="utf-8")

        with self.Session() as db:
            chat = Chat(title="cleanup")
            safe_file = _uploaded_file(generated, "safe.txt")
            outside_file = _uploaded_file(outside, "outside.txt")
            db.add_all([chat, safe_file, outside_file])
            db.flush()
            safe_id = safe_file.id
            outside_id = outside_file.id
            message = Message(
                chat_id=chat.id,
                role="assistant",
                content="files",
                status="done",
                metadata_json=json.dumps(
                    {
                        "generatedFiles": [
                            {"fileId": safe_id},
                            {"fileId": outside_id},
                        ]
                    }
                ),
            )
            db.add(message)
            db.commit()
            chat_id = chat.id
            cleaned: list[Path] = []

            def cleanup_after_commit(paths: list[Path]) -> None:
                self.assertIsNone(db.get(UploadedFile, safe_id))
                self.assertIsNone(db.get(UploadedFile, outside_id))
                cleaned.extend(paths)
                for path in paths:
                    path.unlink(missing_ok=True)

            with patch("app.api.chats.unlink_managed_files", cleanup_after_commit):
                asyncio.run(delete_chat(chat_id, db))

            self.assertEqual(cleaned, [generated.resolve()])
            self.assertFalse(generated.exists())
            self.assertTrue(outside.exists())


async def _collect(iterator):
    return [item async for item in iterator]


def _profile_and_chat(db, title: str) -> tuple[ModelProfile, Chat]:
    profile = ModelProfile(
        name=title,
        provider="openai_compat",
        model_name="fake",
        api_key_secret="fake",
        supports_stream=True,
        supports_tools=True,
    )
    chat = Chat(title=title, model_profile=profile)
    db.add_all([profile, chat])
    db.commit()
    return profile, chat


def _assistant_message(db, chat_id: str) -> Message:
    return db.scalars(
        select(Message).where(Message.chat_id == chat_id, Message.role == "assistant")
    ).one()


def _uploaded_file(path: Path, name: str) -> UploadedFile:
    return UploadedFile(
        original_filename=name,
        stored_filename=f"stored-{name}",
        mime_type="text/plain",
        extension="txt",
        size=path.stat().st_size,
        sha256="0" * 64,
        local_path=str(path),
        file_type="data",
    )


class _Request:
    def __init__(self, disconnected: bool) -> None:
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self.disconnected


class _FailingOrchestrator:
    def __init__(self, *_: object, **__: object) -> None:
        self.metadata = {"pythonSandbox": {"status": "running"}}
        self.usage = None

    async def stream(self, **_: object):
        if False:
            yield None
        raise AppError(PROVIDER_STREAM_ERROR, "provider failed", status_code=502)


class _SingleEventOrchestrator:
    def __init__(self, *_: object, **kwargs: object) -> None:
        self.metadata = {"pythonSandbox": {"status": "running"}}
        self.usage = None
        self.text_event = str(kwargs.get("text_event") or "delta")

    async def stream(self, **_: object):
        yield OrchestratorEvent(self.text_event, {"text": "partial"})


if __name__ == "__main__":
    unittest.main()
