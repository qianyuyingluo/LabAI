from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.services.chat_service import run_chat, stream_chat
from app.services.model_service import get_model_profile
from app.skills.files_skill import FilesSkillService


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    chat_id: str | None = None
    model_profile_id: str | None = None
    message: str = ""
    image_file_ids: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    file_summaries: list[str] = Field(default_factory=list)
    extra_params: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_message_or_image(self) -> "ChatRequest":
        if not self.message.strip() and not self.image_file_ids and not self.file_ids and not self.file_summaries:
            raise ValueError("message or attachment is required")
        return self


class ChatResponse(BaseModel):
    chat_id: str
    user_message_id: str
    assistant_message_id: str
    content: str
    usage: dict[str, Any] | None = None


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session = Depends(get_session)) -> ChatResponse:
    file_summaries = await _resolve_file_contexts(
        db,
        request.chat_id,
        request.file_ids,
        request.file_summaries,
        request.model_profile_id,
        request.message,
        request.extra_params,
    )
    result = await run_chat(
        db,
        chat_id=request.chat_id,
        model_profile_id=request.model_profile_id,
        message=request.message,
        image_file_ids=request.image_file_ids,
        file_ids=request.file_ids,
        file_summaries=file_summaries,
        extra_params=request.extra_params,
    )
    return ChatResponse(**result)


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_session),
) -> StreamingResponse:
    file_summaries = await _resolve_file_contexts(
        db,
        body.chat_id,
        body.file_ids,
        body.file_summaries,
        body.model_profile_id,
        body.message,
        body.extra_params,
    )
    events = stream_chat(
        db,
        chat_id=body.chat_id,
        model_profile_id=body.model_profile_id,
        message=body.message,
        image_file_ids=body.image_file_ids,
        file_ids=body.file_ids,
        file_summaries=file_summaries,
        extra_params=body.extra_params,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _resolve_file_contexts(
    db: Session,
    chat_id: str | None,
    file_ids: list[str],
    provided: list[str],
    model_profile_id: str | None,
    user_message: str,
    extra_params: dict[str, Any] | None,
) -> list[str]:
    if provided:
        return provided
    profile = get_model_profile(db, model_profile_id)
    result = await FilesSkillService(db).run_with_model(
        chat_id=chat_id,
        file_ids=file_ids,
        profile=profile,
        user_message=user_message,
        extra_params=extra_params,
    )
    return [result.model_context] if result.has_context else []
