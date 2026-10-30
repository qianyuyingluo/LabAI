import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.agent_runs import _resume_background, resume_agent_run
from app.api.runs import ApprovePlanRunRequest, start_plan_approve_run
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import AnalysisPlan, AnalysisTask, Base, Chat, Message, ModelProfile
from app.db.session import mark_orphaned_streaming_messages
from app.services.agent_checkpoint import checkpoint_path, save_checkpoint
from app.services.tool_orchestrator import OrchestratorEvent


class AgentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="labai-agent-recovery-")
        self.root = Path(self.temp.name)
        self.settings = get_settings()
        self.previous_storage = self.settings.backend_storage_dir
        self.settings.backend_storage_dir = self.root / "storage"
        self.engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            profile = ModelProfile(name="test", model_name="test", api_key_secret="fake", supports_tools=True, supports_stream=True)
            chat = Chat(title="resume", workspace_path=str(self.root))
            db.add_all([profile, chat])
            db.flush()
            task = AnalysisTask(chat_id=chat.id, user_message="execute", status="executing")
            db.add(task)
            db.flush()
            plan = AnalysisPlan(task_id=task.id, status="approved", plan_markdown="Execute", prompt_key="test",
                                prompt_version_hash="hash", model_profile_id=profile.id)
            db.add(plan)
            db.flush()
            self.metadata = {"workspaceAgent": {"status": "running", "calls": []},
                             "execution": {"taskId": task.id, "planId": plan.id}}
            message = Message(chat_id=chat.id, role="assistant", content="stale", status="streaming",
                              message_type="execution_result", model_profile_id=profile.id,
                              metadata_json=json.dumps(self.metadata))
            db.add(message)
            db.commit()
            self.chat_id, self.profile_id = chat.id, profile.id
            self.task_id, self.plan_id, self.message_id = task.id, plan.id, message.id
        self.state = {"version": 1, "status": "running", "chat_id": self.chat_id, "message_id": self.message_id,
                      "model_profile_id": self.profile_id, "workspace_path": str(self.root), "metadata": self.metadata,
                      "content": "checkpoint content", "usage": {"total_tokens": 42}, "messages": [], "pending": []}

    def tearDown(self):
        self.settings.backend_storage_dir = self.previous_storage
        self.engine.dispose()
        self.temp.cleanup()

    def test_startup_restores_done_and_cancelled_checkpoints_before_marking_orphans(self):
        for status in ("done", "cancelled"):
            with self.subTest(status=status):
                with self.Session() as db:
                    message = db.get(Message, self.message_id)
                    message.status = "streaming"
                    message.content = "stale database"
                    # Covers a crash after checkpoint replace but before even
                    # the metadata database write has committed.
                    message.metadata_json = None
                    db.get(AnalysisTask, self.task_id).status = "executing"
                    db.commit()
                save_checkpoint(self.message_id, {**self.state, "status": status})
                with patch("app.db.session.SessionLocal", self.Session):
                    mark_orphaned_streaming_messages()
                with self.Session() as db:
                    message = db.get(Message, self.message_id)
                    self.assertEqual(message.status, status)
                    self.assertEqual(message.content, "checkpoint content")
                    self.assertEqual(json.loads(message.token_usage_json), {"total_tokens": 42})
                    self.assertEqual(json.loads(message.metadata_json)["workspaceAgent"]["status"], status)
                    self.assertEqual(db.get(AnalysisTask, self.task_id).status, status)

    def test_unreadable_or_mismatched_checkpoint_falls_back_to_interrupted(self):
        for corrupt in (True, False):
            with self.subTest(corrupt=corrupt):
                with self.Session() as db:
                    message = db.get(Message, self.message_id)
                    message.status = "streaming"
                    message.metadata_json = json.dumps(self.metadata)
                    db.commit()
                save_checkpoint(self.message_id, {**self.state, "status": "done", "message_id": "wrong"})
                if corrupt:
                    checkpoint_path(self.message_id).write_text("broken-json", encoding="utf-8")
                with patch("app.db.session.SessionLocal", self.Session):
                    mark_orphaned_streaming_messages()
                with self.Session() as db:
                    self.assertEqual(db.get(Message, self.message_id).status, "interrupted")
                    self.assertEqual(db.get(Message, self.message_id).content, "stale")

    def test_resume_marks_execution_task_executing_then_done_and_preserves_event_type(self):
        owner = self
        events = []
        class Orchestrator:
            def __init__(self, db, **kwargs):
                self.metadata = kwargs["metadata"]
                self.usage = {"total_tokens": 50}
                owner.assertEqual(kwargs["text_event"], "execution_delta")
            async def stream(self, **kwargs):
                with owner.Session() as db:
                    owner.assertEqual(db.get(AnalysisTask, owner.task_id).status, "executing")
                yield OrchestratorEvent("execution_delta", {"text": " final"})
        async def publish(message_id, event, data):
            events.append(event)
        with self.Session() as db:
            db.get(AnalysisTask, self.task_id).status = "interrupted"
            db.commit()
        with (patch("app.api.agent_runs.SessionLocal", self.Session),
              patch("app.api.agent_runs.ToolOrchestrator", Orchestrator),
              patch("app.api.agent_runs.run_manager.publish", publish)):
            asyncio.run(_resume_background(self.message_id, self.chat_id, self.state))
        with self.Session() as db:
            self.assertEqual(db.get(AnalysisTask, self.task_id).status, "done")
            message = db.get(Message, self.message_id)
            self.assertEqual(message.status, "done")
            self.assertEqual(message.content, "checkpoint content final")
        self.assertIn("execution_delta", events)

    def test_resume_failures_update_linked_task_to_error(self):
        for error in (AppError("TEST", "failed"), RuntimeError("failed")):
            with self.subTest(error=type(error).__name__):
                class Orchestrator:
                    def __init__(self, db, **kwargs):
                        self.metadata = kwargs["metadata"]
                        self.usage = None
                    async def stream(self, **kwargs):
                        if False:
                            yield None
                        raise error
                with (patch("app.api.agent_runs.SessionLocal", self.Session),
                      patch("app.api.runs.SessionLocal", self.Session),
                      patch("app.api.agent_runs.ToolOrchestrator", Orchestrator)):
                    asyncio.run(_resume_background(self.message_id, self.chat_id, self.state))
                with self.Session() as db:
                    self.assertEqual(db.get(AnalysisTask, self.task_id).status, "error")
                    self.assertEqual(db.get(Message, self.message_id).status, "error")

    def test_resume_endpoint_marks_task_executing_before_return(self):
        save_checkpoint(self.message_id, self.state)
        with self.Session() as db:
            db.get(Message, self.message_id).status = "interrupted"
            db.get(AnalysisTask, self.task_id).status = "interrupted"
            db.commit()
            with patch("app.api.agent_runs.run_manager.start"):
                result = asyncio.run(resume_agent_run(self.message_id, db))
            self.assertEqual(result["assistant_message"]["status"], "streaming")
            self.assertEqual(db.get(AnalysisTask, self.task_id).status, "executing")

    def test_plan_approval_stores_execution_link_before_background_starts(self):
        with self.Session() as db:
            db.get(Message, self.message_id).status = "done"
            db.get(AnalysisPlan, self.plan_id).status = "awaiting_approval"
            db.get(AnalysisTask, self.task_id).status = "awaiting_approval"
            db.commit()
            with patch("app.api.runs.run_manager.start"):
                result = asyncio.run(start_plan_approve_run(self.plan_id, ApprovePlanRunRequest(), db))
            self.assertEqual(result["assistant_message"]["metadata"]["execution"],
                             {"taskId": self.task_id, "planId": self.plan_id})


if __name__ == "__main__":
    unittest.main()
