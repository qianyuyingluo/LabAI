import asyncio
import json
from collections.abc import Awaitable, Callable, AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import (
    DATABASE_ERROR,
    MODEL_CAPABILITY_MISMATCH,
    PROVIDER_STREAM_ERROR,
    AppError,
)
from app.db.models import Chat, Message, ModelProfile, UploadedFile, now_utc
from app.services.message_builder import MessageBuilder
from app.services.model_service import get_model_profile, system_prompt_messages
from app.services.tool_orchestrator import ToolOrchestrator


async def run_chat(
    db: Session,
    *,
    chat_id: str | None,
    model_profile_id: str | None,
    message: str,
    image_file_ids: list[str],
    file_ids: list[str] | None = None,
    file_summaries: list[str] | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = get_model_profile(db, model_profile_id)
    chat, history = _prepare_chat(db, chat_id, profile, message)
    history = [*system_prompt_messages(profile), *history]
    builder = MessageBuilder(db)
    provider_messages = builder.build_messages(
        history=history,
        user_text=message,
        image_file_ids=image_file_ids,
        file_summaries=file_summaries or [],
        supports_vision=profile.supports_vision,
    )

    all_file_ids = _ordered_file_ids(file_ids or [], image_file_ids)
    user_message = _save_message(
        db,
        chat.id,
        "user",
        message,
        "done",
        profile.id,
        metadata=_attachment_metadata(db, all_file_ids),
    )
    assistant_message = _save_message(
        db,
        chat.id,
        "assistant",
        "",
        "streaming",
        profile.id,
    )
    content_parts: list[str] = []
    orchestrator = ToolOrchestrator(
        db,
        profile=profile,
        assistant_message_id=assistant_message.id,
        chat_id=chat.id,
        current_file_ids=all_file_ids,
    )
    try:
        async for event in orchestrator.stream(messages=provider_messages, extra_params=extra_params):
            if event.event == "delta":
                content_parts.append(str(event.data.get("text", "")))
    except asyncio.CancelledError:
        _finish_interrupted_message(
            db,
            assistant_message,
            content="".join(content_parts),
            error="Chat run was cancelled.",
            metadata=orchestrator.metadata,
            token_usage=orchestrator.usage,
        )
        raise
    except Exception as exc:
        _finish_interrupted_message(
            db,
            assistant_message,
            content="".join(content_parts),
            error=exc.message if isinstance(exc, AppError) else "Chat run failed.",
            metadata=orchestrator.metadata,
            token_usage=orchestrator.usage,
        )
        raise
    content = "".join(content_parts)
    _finish_existing_message(
        db,
        assistant_message,
        content=content,
        status="done",
        metadata=orchestrator.metadata,
        token_usage=orchestrator.usage,
    )

    return {
        "chat_id": chat.id,
        "user_message_id": user_message.id,
        "assistant_message_id": assistant_message.id,
        "content": content,
        "usage": orchestrator.usage,
    }


async def stream_chat(
    db: Session,
    *,
    chat_id: str | None,
    model_profile_id: str | None,
    message: str,
    image_file_ids: list[str],
    file_ids: list[str] | None = None,
    file_summaries: list[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    assistant_parts: list[str] = []
    usage: dict[str, Any] | None = None
    chat: Chat | None = None
    profile: ModelProfile | None = None
    assistant_message: Message | None = None
    orchestrator: ToolOrchestrator | None = None
    terminalized = False

    try:
        profile = get_model_profile(db, model_profile_id)
        if not profile.supports_stream:
            raise AppError(
                MODEL_CAPABILITY_MISMATCH,
                "This model profile does not support streaming. Enable supports_stream or use /api/chat.",
                status_code=400,
            )

        chat, history = _prepare_chat(db, chat_id, profile, message)
        history = [*system_prompt_messages(profile), *history]
        provider_messages = MessageBuilder(db).build_messages(
            history=history,
            user_text=message,
            image_file_ids=image_file_ids,
            file_summaries=file_summaries or [],
            supports_vision=profile.supports_vision,
        )
        all_file_ids = _ordered_file_ids(file_ids or [], image_file_ids)
        _save_message(
            db,
            chat.id,
            "user",
            message,
            "done",
            profile.id,
            metadata=_attachment_metadata(db, all_file_ids),
        )
        assistant_message = _save_message(
            db,
            chat.id,
            "assistant",
            "",
            "streaming",
            profile.id,
        )
        orchestrator = ToolOrchestrator(
            db,
            profile=profile,
            assistant_message_id=assistant_message.id,
            chat_id=chat.id,
            current_file_ids=all_file_ids,
        )
        async for event in orchestrator.stream(
            messages=provider_messages,
            extra_params=extra_params,
        ):
            if is_disconnected and await is_disconnected():
                _finish_interrupted_message(
                    db,
                    assistant_message,
                    content="".join(assistant_parts),
                    error="Chat stream disconnected before completion.",
                    metadata=orchestrator.metadata,
                    token_usage=orchestrator.usage,
                )
                terminalized = True
                return

            if event.event == "delta":
                text = str(event.data.get("text", ""))
                assistant_parts.append(text)
                yield format_sse("delta", event.data)
            elif event.event == "usage":
                usage = orchestrator.usage
                yield format_sse("usage", event.data)
            else:
                yield format_sse(event.event, event.data)

        if chat is not None and profile is not None:
            assistant_content = "".join(assistant_parts)
            usage = orchestrator.usage
            _finish_existing_message(
                db,
                assistant_message,
                content=assistant_content,
                status="done",
                metadata=orchestrator.metadata,
                token_usage=usage,
            )
            terminalized = True
            yield format_sse(
                "done",
                {
                    "chat_id": chat.id,
                    "assistant_message_id": assistant_message.id,
                },
            )
    except AppError as exc:
        if assistant_message is not None:
            _finish_interrupted_message(
                db,
                assistant_message,
                content="".join(assistant_parts) or exc.message,
                error=exc.message,
                metadata=orchestrator.metadata if orchestrator is not None else None,
                token_usage=usage,
            )
            terminalized = True
        yield format_sse("error", exc.to_payload())
        yield format_sse("done", {"ok": False})
    except Exception as exc:
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected streaming error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        if assistant_message is not None:
            _finish_interrupted_message(
                db,
                assistant_message,
                content="".join(assistant_parts) or error.message,
                error=error.message,
                metadata=orchestrator.metadata if orchestrator is not None else None,
                token_usage=usage,
            )
            terminalized = True
        yield format_sse("error", error.to_payload())
        yield format_sse("done", {"ok": False})
    finally:
        if assistant_message is not None and not terminalized:
            try:
                _finish_interrupted_message(
                    db,
                    assistant_message,
                    content="".join(assistant_parts),
                    error="Chat stream was cancelled before completion.",
                    metadata=orchestrator.metadata if orchestrator is not None else None,
                    token_usage=usage,
                )
            except SQLAlchemyError:
                db.rollback()


def format_sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _prepare_chat(
    db: Session,
    chat_id: str | None,
    profile: ModelProfile,
    message: str,
) -> tuple[Chat, list[dict[str, Any]]]:
    if chat_id:
        chat = db.get(Chat, chat_id)
        if chat is None:
            raise AppError("CHAT_NOT_FOUND", "Chat was not found.", status_code=404)
    else:
        title = (message.strip() or "New chat")[:80]
        chat = Chat(title=title, model_profile_id=profile.id)
        try:
            db.add(chat)
            db.commit()
            db.refresh(chat)
        except SQLAlchemyError as exc:
            db.rollback()
            raise AppError(DATABASE_ERROR, "Failed to create chat.", status_code=500) from exc

    history_rows = list(
        db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id, Message.status == "done")
            .order_by(Message.created_at)
        ).all()
    )
    history = [
        {"role": row.role, "content": row.content}
        for row in history_rows
        if row.role in {"system", "user", "assistant"}
    ]
    return chat, history


def _save_message(
    db: Session,
    chat_id: str,
    role: str,
    content: str,
    status: str,
    model_profile_id: str | None,
    token_usage: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        chat_id=chat_id,
        role=role,
        content=content,
        status=status,
        model_profile_id=model_profile_id,
        token_usage_json=json.dumps(token_usage, ensure_ascii=False) if token_usage else None,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    )
    try:
        chat = db.get(Chat, chat_id)
        if chat is not None:
            chat.updated_at = now_utc()
        db.add(message)
        db.commit()
        db.refresh(message)
    except SQLAlchemyError as exc:
        db.rollback()
        raise AppError(DATABASE_ERROR, "Failed to save chat message.", status_code=500) from exc
    return message


def _finish_existing_message(
    db: Session,
    message: Message,
    *,
    content: str,
    status: str,
    metadata: dict[str, Any] | None,
    token_usage: dict[str, Any] | None,
) -> None:
    message.content = content
    message.status = status
    if metadata:
        message.metadata_json = json.dumps(metadata, ensure_ascii=False)
    if token_usage:
        message.token_usage_json = json.dumps(token_usage, ensure_ascii=False)
    chat = db.get(Chat, message.chat_id)
    if chat is not None:
        chat.updated_at = now_utc()
    db.commit()


def _finish_interrupted_message(
    db: Session,
    message: Message,
    *,
    content: str,
    error: str,
    metadata: dict[str, Any] | None,
    token_usage: dict[str, Any] | None,
) -> None:
    db.rollback()
    _finish_existing_message(
        db,
        message,
        content=content or error,
        status="error",
        metadata=_mark_sandbox_interrupted(metadata, error),
        token_usage=token_usage,
    )


def _mark_sandbox_interrupted(
    metadata: dict[str, Any] | None,
    error: str,
) -> dict[str, Any] | None:
    if not metadata:
        return metadata
    merged = dict(metadata)
    sandbox = merged.get("pythonSandbox")
    if isinstance(sandbox, dict) and sandbox.get("status") in {"pending", "running"}:
        sandbox_state = dict(sandbox)
        sandbox_state["status"] = "error"
        sandbox_state["error"] = error
        merged["pythonSandbox"] = sandbox_state
    return merged


def _ordered_file_ids(file_ids: list[str], image_file_ids: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for file_id in [*file_ids, *image_file_ids]:
        if file_id and file_id not in seen:
            ordered.append(file_id)
            seen.add(file_id)
    return ordered


def _attachment_metadata(db: Session, file_ids: list[str]) -> dict[str, Any] | None:
    attachments: list[dict[str, object]] = []
    for file_id in file_ids:
        uploaded = db.get(UploadedFile, file_id)
        if uploaded is None:
            continue
        attachments.append(
            {
                "fileId": uploaded.id,
                "name": uploaded.original_filename,
                "size": uploaded.size,
                "type": uploaded.mime_type,
            }
        )
    return {"attachments": attachments} if attachments else None
