import json
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import AnalysisTask, Base, Message, ModelProfile, now_utc
from app.services.agent_checkpoint import load_checkpoint


settings = get_settings()
DEFAULT_MODEL_PROFILE_ID = "default-openai-compatible"


def _connect_args() -> dict[str, bool]:
    if settings.database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(settings.database_url, connect_args=_connect_args(), future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.image_upload_dir.mkdir(parents=True, exist_ok=True)
    settings.file_upload_dir.mkdir(parents=True, exist_ok=True)
    settings.generated_file_dir.mkdir(parents=True, exist_ok=True)
    settings.sandbox_runs_dir.mkdir(parents=True, exist_ok=True)

    if settings.database_url.startswith("sqlite:///"):
        sqlite_path = Path(settings.database_url.removeprefix("sqlite:///"))
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()
    mark_orphaned_streaming_messages()
    seed_default_model_profile()


def ensure_schema_compatibility() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    with engine.begin() as connection:
        table_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "model_profiles" in table_names:
            existing_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(model_profiles)")
            }
            if "api_key_secret" not in existing_columns:
                connection.exec_driver_sql("ALTER TABLE model_profiles ADD COLUMN api_key_secret TEXT")
            if "system_prompt" not in existing_columns:
                connection.exec_driver_sql("ALTER TABLE model_profiles ADD COLUMN system_prompt TEXT")
            if "supports_tools" not in existing_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE model_profiles ADD COLUMN supports_tools BOOLEAN NOT NULL DEFAULT 1"
                )

        if "chats" in table_names:
            chat_columns = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(chats)")
            }
            if "workspace_path" not in chat_columns:
                connection.exec_driver_sql("ALTER TABLE chats ADD COLUMN workspace_path TEXT")
            if "workspace_mode" not in chat_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE chats ADD COLUMN workspace_mode TEXT NOT NULL DEFAULT 'approve'"
                )

        if "messages" not in table_names:
            return

        message_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(messages)")
        }
        if "message_type" not in message_columns:
            connection.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN message_type TEXT NOT NULL DEFAULT 'text'"
            )
        if "metadata_json" not in message_columns:
            connection.exec_driver_sql("ALTER TABLE messages ADD COLUMN metadata_json TEXT")


def mark_orphaned_streaming_messages() -> None:
    with SessionLocal() as db:
        messages = list(db.scalars(select(Message).where(Message.status == "streaming")).all())
        for message in messages:
            # The atomic checkpoint is written before the final database update.
            # A crash between these writes must not turn completed work into an
            # interrupted, non-resumable run.
            if _restore_terminal_checkpoint(db, message):
                continue
            message.status = "error"
            if not message.metadata_json:
                continue
            try:
                metadata = json.loads(message.metadata_json)
            except json.JSONDecodeError:
                continue
            agent = metadata.get("workspaceAgent") if isinstance(metadata, dict) else None
            if isinstance(agent, dict):
                message.status = "interrupted"
                agent["status"] = "interrupted"
                for call in agent.get("calls", []):
                    if isinstance(call, dict) and call.get("status") in {"running", "waiting_approval"}:
                        call["status"] = "interrupted"
                message.metadata_json = json.dumps(metadata, ensure_ascii=False)
            plan = metadata.get("plan") if isinstance(metadata, dict) else None
            if not isinstance(plan, dict):
                continue
            plan["status"] = "error"
            if not plan.get("markdown"):
                plan["markdown"] = message.content
            message.metadata_json = json.dumps(metadata, ensure_ascii=False)
        db.flush()
        tasks = db.scalars(select(AnalysisTask).where(AnalysisTask.status == "executing")).all()
        for task in tasks:
            task.status = "interrupted"
            task.updated_at = now_utc()
        db.commit()


def _restore_terminal_checkpoint(db: Session, message: Message) -> bool:
    try:
        checkpoint = load_checkpoint(message.id)
    except (AppError, OSError, ValueError):
        return False
    metadata = checkpoint.get("metadata")
    content = checkpoint.get("content")
    usage = checkpoint.get("usage")
    status = checkpoint.get("status")
    if (
        status not in {"done", "cancelled"}
        or checkpoint.get("message_id") != message.id
        or checkpoint.get("chat_id") != message.chat_id
        or not isinstance(metadata, dict)
        or not isinstance(metadata.get("workspaceAgent"), dict)
        or not isinstance(content, str)
        or (usage is not None and not isinstance(usage, dict))
    ):
        return False
    message.status = status
    message.content = content
    metadata["workspaceAgent"]["status"] = status
    message.metadata_json = json.dumps(metadata, ensure_ascii=False)
    message.token_usage_json = json.dumps(usage, ensure_ascii=False) if usage is not None else None
    execution = metadata.get("execution")
    task_id = execution.get("taskId") if isinstance(execution, dict) else None
    task = db.get(AnalysisTask, task_id) if isinstance(task_id, str) else None
    if task is not None and task.chat_id == message.chat_id:
        task.status = status
        task.updated_at = now_utc()
    return True


def seed_default_model_profile() -> None:
    with SessionLocal() as db:
        profile = db.scalar(select(ModelProfile).where(ModelProfile.id == DEFAULT_MODEL_PROFILE_ID))
        if profile is not None:
            return

        profile = ModelProfile(
            id=DEFAULT_MODEL_PROFILE_ID,
            name="OpenAI Compatible Default",
            provider="openai_compat",
            api_key_env="OPENAI_COMPAT_API_KEY",
            base_url=settings.openai_compat_base_url or None,
            model_name=settings.openai_compat_default_model or None,
            supports_stream=settings.openai_compat_supports_stream,
            supports_vision=settings.openai_compat_supports_vision,
            supports_tools=True,
        )
        db.add(profile)
        db.commit()
