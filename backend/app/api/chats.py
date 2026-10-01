import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.errors import CHAT_RUNNING, AppError
from app.db.models import AnalysisPlan, AnalysisTask, Chat, Message, UploadedFile
from app.services.file_service import delete_managed_file, unlink_managed_files
from app.services.run_manager import run_manager


router = APIRouter(prefix="/api/chats", tags=["chats"])


class ChatListItem(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    model_profile_id: str | None
    message_count: int
    last_message_preview: str | None = None
    has_active_run: bool = False


class ChatMessageItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_id: str
    role: str
    content: str
    status: str
    type: str
    metadata: dict[str, object] | None = None
    model_profile_id: str | None
    created_at: datetime

    @classmethod
    def from_message(cls, message: Message, db: Session | None = None) -> "ChatMessageItem":
        metadata = _parse_metadata(message.metadata_json)
        if db is not None:
            metadata = _mark_unavailable_generated_files(db, metadata)
        return cls(
            id=message.id,
            chat_id=message.chat_id,
            role=message.role,
            content=message.content,
            status=message.status,
            type=message.message_type,
            metadata=metadata,
            model_profile_id=message.model_profile_id,
            created_at=message.created_at,
        )


class ChatDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    model_profile_id: str | None
    messages: list[ChatMessageItem]


@router.get("", response_model=list[ChatListItem])
async def list_chats(db: Session = Depends(get_session)) -> list[ChatListItem]:
    chats = list(db.scalars(select(Chat).order_by(Chat.updated_at.desc())).all())
    items: list[ChatListItem] = []

    for chat in chats:
        message_count = db.scalar(
            select(func.count()).select_from(Message).where(Message.chat_id == chat.id)
        )
        last_message = db.scalar(
            select(Message)
            .where(Message.chat_id == chat.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        preview = last_message.content[:120] if last_message else None
        has_active_run = bool(
            db.scalar(
                select(func.count()).select_from(Message).where(
                    Message.chat_id == chat.id,
                    Message.status == "streaming",
                )
            )
        ) or run_manager.is_chat_running(chat.id)
        items.append(
            ChatListItem(
                id=chat.id,
                title=chat.title,
                created_at=chat.created_at,
                updated_at=chat.updated_at,
                model_profile_id=chat.model_profile_id,
                message_count=message_count or 0,
                last_message_preview=preview,
                has_active_run=has_active_run,
            )
        )

    return items


@router.get("/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: str, db: Session = Depends(get_session)) -> ChatDetail:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise AppError("CHAT_NOT_FOUND", "Chat was not found.", status_code=404)

    messages = list(
        db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id)
            .order_by(Message.created_at)
        ).all()
    )
    _backfill_plan_message_metadata(db, chat.id, messages)
    return ChatDetail(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        model_profile_id=chat.model_profile_id,
        messages=[ChatMessageItem.from_message(message, db) for message in messages],
    )


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str, db: Session = Depends(get_session)) -> dict[str, bool]:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise AppError("CHAT_NOT_FOUND", "Chat was not found.", status_code=404)
    has_active_run = bool(
        db.scalar(
            select(func.count()).select_from(Message).where(
                Message.chat_id == chat_id,
                Message.status == "streaming",
            )
        )
    ) or run_manager.is_chat_running(chat_id)
    if has_active_run:
        raise AppError(
            CHAT_RUNNING,
            "This chat has a running response and cannot be deleted yet.",
            status_code=409,
        )

    task_ids = list(
        db.scalars(select(AnalysisTask.id).where(AnalysisTask.chat_id == chat_id)).all()
    )
    if task_ids:
        tasks = list(db.scalars(select(AnalysisTask).where(AnalysisTask.id.in_(task_ids))).all())
        for task in tasks:
            task.current_plan_id = None
        db.flush()
        db.execute(delete(AnalysisPlan).where(AnalysisPlan.task_id.in_(task_ids)))
        db.execute(delete(AnalysisTask).where(AnalysisTask.id.in_(task_ids)))

    generated_ids: set[str] = set()
    messages = list(db.scalars(select(Message).where(Message.chat_id == chat_id)).all())
    for message in messages:
        metadata = _parse_metadata(message.metadata_json) or {}
        generated = metadata.get("generatedFiles")
        if not isinstance(generated, list):
            continue
        for item in generated:
            if isinstance(item, dict) and isinstance(item.get("fileId"), str):
                generated_ids.add(str(item["fileId"]))
    cleanup_paths: list[Path] = []
    for file_id in generated_ids:
        uploaded = db.get(UploadedFile, file_id)
        if uploaded is not None:
            cleanup_path = delete_managed_file(db, uploaded)
            if cleanup_path is not None:
                cleanup_paths.append(cleanup_path)

    db.execute(delete(Message).where(Message.chat_id == chat_id))
    db.delete(chat)
    db.commit()
    unlink_managed_files(cleanup_paths)
    return {"ok": True}


def _parse_metadata(raw: str | None) -> dict[str, object] | None:
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _mark_unavailable_generated_files(
    db: Session,
    metadata: dict[str, object] | None,
) -> dict[str, object] | None:
    if not metadata or not isinstance(metadata.get("generatedFiles"), list):
        return metadata
    normalized = dict(metadata)
    files: list[object] = []
    for value in metadata["generatedFiles"]:  # type: ignore[index]
        if not isinstance(value, dict):
            continue
        item = dict(value)
        file_id = item.get("fileId")
        uploaded = db.get(UploadedFile, file_id) if isinstance(file_id, str) else None
        if uploaded is None or not Path(uploaded.local_path).is_file():
            item["status"] = "unavailable"
        files.append(item)
    normalized["generatedFiles"] = files
    return normalized


def _backfill_plan_message_metadata(
    db: Session,
    chat_id: str,
    messages: list[Message],
) -> None:
    plan_rows = list(
        db.execute(
            select(AnalysisPlan, AnalysisTask)
            .join(AnalysisTask, AnalysisPlan.task_id == AnalysisTask.id)
            .where(AnalysisTask.chat_id == chat_id)
        ).all()
    )
    if not plan_rows:
        return

    plans_by_content: dict[str, tuple[AnalysisPlan, AnalysisTask]] = {}
    for plan, task in plan_rows:
        key = (plan.plan_markdown or "").strip()
        if key:
            plans_by_content[key] = (plan, task)

    changed = False
    for message in messages:
        if (
            message.role != "assistant"
            or message.message_type == "plan"
            or message.metadata_json
        ):
            continue
        match = plans_by_content.get((message.content or "").strip())
        if match is None:
            continue
        plan, task = match
        message.message_type = "plan"
        message.metadata_json = json.dumps(
            {
                "plan": {
                    "planId": plan.id,
                    "taskId": task.id,
                    "version": plan.version,
                    "status": plan.status,
                    "markdown": plan.plan_markdown,
                }
            },
            ensure_ascii=False,
        )
        changed = True

    if changed:
        db.commit()
