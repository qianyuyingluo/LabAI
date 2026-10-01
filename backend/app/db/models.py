from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid4().hex


def now_utc() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class ModelProfile(Base):
    __tablename__ = "model_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default="openai_compat")
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_key_env: Mapped[str] = mapped_column(String(120), nullable=False, default="OPENAI_COMPAT_API_KEY")
    api_key_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    supports_stream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supports_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_utc, onupdate=now_utc
    )

    chats: Mapped[list["Chat"]] = relationship(back_populates="model_profile")
    messages: Mapped[list["Message"]] = relationship(back_populates="model_profile")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_utc, onupdate=now_utc
    )
    model_profile_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("model_profiles.id"), nullable=True
    )

    model_profile: Mapped[ModelProfile | None] = relationship(back_populates="chats")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    chat_id: Mapped[str] = mapped_column(String(64), ForeignKey("chats.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="done")
    message_type: Mapped[str] = mapped_column(String(40), nullable=False, default="text")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_profile_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("model_profiles.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    token_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    chat: Mapped[Chat] = relationship(back_populates="messages")
    model_profile: Mapped[ModelProfile | None] = relationship(back_populates="messages")


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    extension: Mapped[str] = mapped_column(String(20), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    local_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str] = mapped_column(String(40), nullable=False, default="image")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_key", "content_hash", name="uq_prompt_versions_key_hash"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    prompt_key: Mapped[str] = mapped_column(String(200), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    chat_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("chats.id"), nullable=True)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="planning")
    current_plan_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("analysis_plans.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=now_utc, onupdate=now_utc
    )


class AnalysisPlan(Base):
    __tablename__ = "analysis_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("analysis_tasks.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="awaiting_approval")
    plan_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_key: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_profile_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("model_profiles.id"), nullable=True
    )
    user_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_utc)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
