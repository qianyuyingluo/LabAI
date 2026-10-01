import json
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import Base, Message, ModelProfile


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
            message.status = "error"
            if not message.metadata_json:
                continue
            try:
                metadata = json.loads(message.metadata_json)
            except json.JSONDecodeError:
                continue
            plan = metadata.get("plan") if isinstance(metadata, dict) else None
            if not isinstance(plan, dict):
                continue
            plan["status"] = "error"
            if not plan.get("markdown"):
                plan["markdown"] = message.content
            message.metadata_json = json.dumps(metadata, ensure_ascii=False)
        db.commit()


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
