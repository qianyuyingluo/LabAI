import json
import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.registry import get_adapter_for_profile
from app.core.database import get_session
from app.core.errors import (
    CHAT_RUNNING,
    MODEL_CAPABILITY_MISMATCH,
    PROVIDER_STREAM_ERROR,
    AppError,
)
from app.db.models import AnalysisTask, Chat, Message, ModelProfile, UploadedFile, now_utc
from app.db.session import SessionLocal
from app.services.message_builder import MessageBuilder
from app.services.model_service import get_model_profile, system_prompt_messages
from app.services.plan_execution_service import PLAN_APPROVAL_USER_MESSAGE, PlanExecutionService
from app.services.plan_service import (
    PLAN_INVALID_STATE,
    PLAN_STATUS_APPROVED,
    PLAN_STATUS_AWAITING_APPROVAL,
    PLAN_STATUS_REJECTED,
    PlanService,
    TASK_STATUS_ERROR,
    TASK_STATUS_EXECUTING,
)
from app.services.run_manager import run_manager
from app.services.tool_orchestrator import ToolOrchestrator
from app.skills.files_skill import FilesSkillService
from app.skills.files_skill.schemas import FilesSkillResult
from app.skills.plan_skill import PLAN_SKILL_NAME, PlanSkillService


router = APIRouter(prefix="/api/runs", tags=["runs"])

SAVE_INTERVAL_SECONDS = 0.35


class StartChatRunRequest(BaseModel):
    chat_id: str | None = None
    model_profile_id: str | None = None
    message: str = ""
    image_file_ids: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    file_summaries: list[str] = Field(default_factory=list)
    extra_params: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_message_or_image(self) -> "StartChatRunRequest":
        if not self.message.strip() and not self.image_file_ids and not self.file_ids and not self.file_summaries:
            raise ValueError("message or attachment is required")
        return self


class StartPlanRunRequest(StartChatRunRequest):
    pass


class ApprovePlanRunRequest(BaseModel):
    plan_message_id: str | None = None
    extra_params: dict[str, Any] | None = None


class RevisePlanRunRequest(BaseModel):
    plan_message_id: str | None = None
    user_feedback: str = Field(min_length=1)
    extra_params: dict[str, Any] | None = None


@router.post("/chat")
async def start_chat_run(
    body: StartChatRunRequest,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    profile = _get_streaming_profile(db, body.model_profile_id)
    chat = _get_or_create_chat(db, body.chat_id, profile, body.message or "New chat")
    _ensure_chat_can_start(db, chat.id)
    history = [
        *system_prompt_messages(profile),
        *_load_done_history(db, chat.id),
    ]
    user_metadata = _message_input_metadata(
        db,
        _attachment_file_ids(body.file_ids, body.image_file_ids),
    )

    user_message = _create_message(
        db,
        chat.id,
        "user",
        body.message,
        "done",
        profile.id,
        "text",
        user_metadata,
    )
    assistant_message = _create_message(
        db,
        chat.id,
        "assistant",
        "",
        "streaming",
        profile.id,
        "text",
        None,
    )
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    db.refresh(chat)

    run_manager.start(
        message_id=assistant_message.id,
        chat_id=chat.id,
        runner=lambda: _run_chat_background(
            assistant_message_id=assistant_message.id,
            chat_id=chat.id,
            model_profile_id=profile.id,
            history=history,
            user_message=body.message,
            image_file_ids=body.image_file_ids,
            file_ids=body.file_ids,
            file_summaries=body.file_summaries,
            extra_params=body.extra_params,
        ),
    )

    return _run_response(chat, user_message, assistant_message)


@router.post("/plan/generate")
async def start_plan_generate_run(
    body: StartPlanRunRequest,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    profile = _get_streaming_profile(db, body.model_profile_id)
    image_infos = PlanSkillService(db).load_image_infos(body.image_file_ids, profile)
    chat = _get_or_create_chat(db, body.chat_id, profile, body.message or "Plan Mode analysis")
    _ensure_chat_can_start(db, chat.id)
    user_metadata = _message_input_metadata(
        db,
        _attachment_file_ids(body.file_ids, body.image_file_ids),
        plan_mode=True,
    )

    user_message = _create_message(
        db,
        chat.id,
        "user",
        body.message,
        "done",
        profile.id,
        "text",
        user_metadata,
    )
    task = PlanService(db).create_task(chat_id=chat.id, user_message=body.message)
    metadata = _plan_metadata(
        markdown="",
        status="streaming",
        task_id=task.id,
        version=1,
    )
    assistant_message = _create_message(
        db,
        chat.id,
        "assistant",
        "",
        "streaming",
        profile.id,
        "plan",
        metadata,
    )
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    db.refresh(chat)
    db.refresh(task)

    run_manager.start(
        message_id=assistant_message.id,
        chat_id=chat.id,
        runner=lambda: _run_plan_generate_background(
            assistant_message_id=assistant_message.id,
            chat_id=chat.id,
            task_id=task.id,
            model_profile_id=profile.id,
            user_message=body.message,
            image_file_ids=body.image_file_ids,
            image_infos=image_infos,
            file_ids=body.file_ids,
            file_summaries=body.file_summaries,
            extra_params=body.extra_params,
        ),
    )

    return _run_response(chat, user_message, assistant_message)


@router.post("/plans/{plan_id}/approve")
async def start_plan_approve_run(
    plan_id: str,
    body: ApprovePlanRunRequest,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PlanService(db)
    plan = service.get_plan(plan_id)
    if plan.status != PLAN_STATUS_AWAITING_APPROVAL:
        raise AppError(
            PLAN_INVALID_STATE,
            "Only an awaiting-approval plan can be approved.",
            status_code=409,
            details={"plan_id": plan_id, "status": plan.status},
        )
    task = db.get(AnalysisTask, plan.task_id)
    if task is None or not task.chat_id:
        raise AppError("TASK_NOT_FOUND", "Analysis task was not found.", status_code=404)
    _ensure_chat_can_start(db, task.chat_id)
    profile = _get_streaming_profile(db, plan.model_profile_id)

    plan = service.approve_plan(plan_id)
    if body.plan_message_id:
        _update_plan_message_metadata(
            db,
            body.plan_message_id,
            status=PLAN_STATUS_APPROVED,
            markdown=plan.plan_markdown,
            plan_id=plan.id,
            task_id=task.id,
            version=plan.version,
        )
    approval_message = _create_message(
        db,
        task.chat_id,
        "user",
        PLAN_APPROVAL_USER_MESSAGE,
        "done",
        profile.id,
        "text",
        {
            "planApproval": {
                "planId": plan.id,
                "taskId": task.id,
                "version": plan.version,
            }
        },
    )
    execution_message = _create_message(
        db,
        task.chat_id,
        "assistant",
        "",
        "streaming",
        profile.id,
        "execution_result",
        None,
    )
    db.commit()
    db.refresh(approval_message)
    db.refresh(execution_message)
    chat = db.get(Chat, task.chat_id)

    run_manager.start(
        message_id=execution_message.id,
        chat_id=task.chat_id,
        runner=lambda: _run_plan_execute_background(
            execution_message_id=execution_message.id,
            chat_id=task.chat_id or "",
            plan_id=plan.id,
            extra_params=body.extra_params,
        ),
    )

    return {
        "chat_id": task.chat_id,
        "chat": _serialize_chat(chat) if chat else None,
        "user_message": _serialize_message(approval_message),
        "assistant_message": _serialize_message(execution_message),
    }


@router.post("/plans/{plan_id}/revise")
async def start_plan_revise_run(
    plan_id: str,
    body: RevisePlanRunRequest,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    service = PlanService(db)
    previous_plan = service.get_plan(plan_id)
    if previous_plan.status != PLAN_STATUS_AWAITING_APPROVAL:
        raise AppError(
            PLAN_INVALID_STATE,
            "Only an awaiting-approval plan can be revised.",
            status_code=409,
            details={"plan_id": plan_id, "status": previous_plan.status},
        )
    task = db.get(AnalysisTask, previous_plan.task_id)
    if task is None or not task.chat_id:
        raise AppError("TASK_NOT_FOUND", "Analysis task was not found.", status_code=404)
    _ensure_chat_can_start(db, task.chat_id)
    profile = _get_streaming_profile(db, previous_plan.model_profile_id)

    previous_plan, task = service.reject_plan_for_revision(plan_id, body.user_feedback)
    if body.plan_message_id:
        _update_plan_message_metadata(
            db,
            body.plan_message_id,
            status=PLAN_STATUS_REJECTED,
            markdown=previous_plan.plan_markdown,
            plan_id=previous_plan.id,
            task_id=task.id,
            version=previous_plan.version,
        )

    feedback_message = _create_message(
        db,
        task.chat_id,
        "user",
        body.user_feedback,
        "done",
        profile.id,
        "text",
        {"planMode": True},
    )
    new_plan_metadata = _plan_metadata(
        markdown="",
        status="streaming",
        task_id=task.id,
        version=previous_plan.version + 1,
    )
    new_plan_message = _create_message(
        db,
        task.chat_id,
        "assistant",
        "",
        "streaming",
        profile.id,
        "plan",
        new_plan_metadata,
    )
    db.commit()
    db.refresh(feedback_message)
    db.refresh(new_plan_message)
    chat = db.get(Chat, task.chat_id)

    run_manager.start(
        message_id=new_plan_message.id,
        chat_id=task.chat_id,
        runner=lambda: _run_plan_revise_background(
            new_plan_message_id=new_plan_message.id,
            chat_id=task.chat_id or "",
            previous_plan_id=previous_plan.id,
            task_id=task.id,
            user_feedback=body.user_feedback,
            extra_params=body.extra_params,
        ),
    )

    return {
        "chat_id": task.chat_id,
        "chat": _serialize_chat(chat) if chat else None,
        "user_message": _serialize_message(feedback_message),
        "assistant_message": _serialize_message(new_plan_message),
    }


@router.get("/messages/{message_id}/stream")
async def stream_run_message(message_id: str) -> StreamingResponse:
    return StreamingResponse(
        run_manager.subscribe(message_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_chat_background(
    *,
    assistant_message_id: str,
    chat_id: str,
    model_profile_id: str,
    history: list[dict[str, Any]],
    user_message: str,
    image_file_ids: list[str],
    file_ids: list[str],
    file_summaries: list[str],
    extra_params: dict[str, Any] | None,
) -> None:
    content = ""
    usage: dict[str, Any] | None = None
    last_save = 0.0
    metadata: dict[str, Any] | None = None
    try:
        with SessionLocal() as db:
            profile = get_model_profile(db, model_profile_id)
            files_skill_result = await _run_files_skill_for_message(
                db,
                assistant_message_id=assistant_message_id,
                chat_id=chat_id,
                file_ids=file_ids,
                profile=profile,
                user_message=user_message,
                extra_params=extra_params,
            )
            if files_skill_result.status != "skipped":
                metadata = _merge_files_skill_metadata(None, files_skill_result)
                _finish_message(db, assistant_message_id, chat_id, content, "streaming", metadata, None)
                db.commit()
            file_contexts = (
                [files_skill_result.model_context] if files_skill_result.has_context else file_summaries
            )
            provider_messages = MessageBuilder(db).build_messages(
                history=history,
                user_text=user_message,
                image_file_ids=image_file_ids,
                file_summaries=file_contexts,
                supports_vision=profile.supports_vision,
            )
            orchestrator = ToolOrchestrator(
                db,
                profile=profile,
                assistant_message_id=assistant_message_id,
                chat_id=chat_id,
                current_file_ids=_attachment_file_ids(file_ids, image_file_ids),
                metadata=metadata,
                text_event="delta",
            )
            async for event in orchestrator.stream(
                messages=provider_messages,
                extra_params=extra_params,
            ):
                metadata = orchestrator.metadata
                if event.event == "delta":
                    text = str(event.data.get("text", ""))
                    content += text
                    await run_manager.publish(
                        assistant_message_id,
                        "delta",
                        event.data,
                    )
                    last_save = _persist_streaming_message(
                        db,
                        assistant_message_id,
                        chat_id,
                        content,
                        metadata,
                        last_save,
                    )
                elif event.event == "usage":
                    usage = orchestrator.usage
                    await run_manager.publish(
                        assistant_message_id,
                        "usage",
                        event.data,
                    )
                else:
                    await run_manager.publish(assistant_message_id, event.event, event.data)

            metadata = orchestrator.metadata
            usage = orchestrator.usage

            _finish_message(
                db,
                assistant_message_id,
                chat_id,
                content,
                "done",
                metadata,
                usage,
            )
            db.commit()
        await run_manager.finish(
            assistant_message_id,
            {"ok": True, "chat_id": chat_id, "message_id": assistant_message_id},
        )
    except AppError as exc:
        await _fail_message(assistant_message_id, chat_id, exc.to_payload())
    except Exception as exc:
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected streaming error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        await _fail_message(assistant_message_id, chat_id, error.to_payload())


async def _run_plan_generate_background(
    *,
    assistant_message_id: str,
    chat_id: str,
    task_id: str,
    model_profile_id: str,
    user_message: str,
    image_file_ids: list[str],
    image_infos: list[dict[str, Any]],
    file_ids: list[str],
    file_summaries: list[str],
    extra_params: dict[str, Any] | None,
) -> None:
    plan_content = ""
    usage: dict[str, Any] | None = None
    last_save = 0.0
    metadata: dict[str, Any] | None = None
    try:
        with SessionLocal() as db:
            plan_service = PlanService(db)
            plan_skill = PlanSkillService(db)
            profile = get_model_profile(db, model_profile_id)
            task = plan_service.get_task(task_id)
            prompt_bundle = plan_skill.load_generation_prompt_bundle()
            files_skill_result = await _run_files_skill_for_message(
                db,
                assistant_message_id=assistant_message_id,
                chat_id=chat_id,
                file_ids=file_ids,
                profile=profile,
                user_message=user_message,
                extra_params=extra_params,
            )
            file_contexts = (
                [files_skill_result.model_context] if files_skill_result.has_context else file_summaries
            )
            if files_skill_result.status != "skipped":
                metadata = _merge_files_skill_metadata(
                    _plan_metadata(
                        markdown=plan_content,
                        status="streaming",
                        task_id=task.id,
                        version=1,
                    ),
                    files_skill_result,
                )
                _finish_message(db, assistant_message_id, chat_id, plan_content, "streaming", metadata, None)
                db.commit()
            async for event_name, event_data in plan_skill.stream_generate_plan(
                profile=profile,
                prompt_bundle=prompt_bundle,
                task=task,
                user_message=user_message,
                file_summaries=file_contexts,
                image_infos=image_infos,
                image_file_ids=image_file_ids,
                extra_params=extra_params,
                is_disconnected=None,
            ):
                if event_name == "plan_delta":
                    text = str(event_data.get("text", ""))
                    plan_content += text
                    metadata = _plan_metadata(
                        markdown=plan_content,
                        status="streaming",
                        task_id=task.id,
                        version=1,
                    )
                    metadata = _merge_files_skill_metadata(metadata, files_skill_result)
                    await run_manager.publish(
                        assistant_message_id,
                        "plan_delta",
                        {"text": text, "message_id": assistant_message_id, "chat_id": chat_id},
                    )
                    last_save = _persist_streaming_message(
                        db,
                        assistant_message_id,
                        chat_id,
                        plan_content,
                        metadata,
                        last_save,
                    )
                elif event_name == "usage":
                    usage = event_data.get("usage") if isinstance(event_data.get("usage"), dict) else None
                    await run_manager.publish(
                        assistant_message_id,
                        "usage",
                        {"usage": usage, "message_id": assistant_message_id, "chat_id": chat_id},
                    )

            plan = plan_service.create_plan(
                task=task,
                version=1,
                status=PLAN_STATUS_AWAITING_APPROVAL,
                plan_markdown=plan_content,
                prompt_key=prompt_bundle.prompt_key,
                prompt_version_hash=prompt_bundle.prompt_version_hash,
                model_profile_id=profile.id,
                user_feedback=None,
                metadata={
                    "skill_name": PLAN_SKILL_NAME,
                    "prompt_hashes": prompt_bundle.prompt_hashes,
                    "file_summaries": file_contexts,
                    "files_skill": files_skill_result.to_metadata() if files_skill_result.status != "skipped" else None,
                    "image_inputs": image_infos,
                    "usage": usage,
                },
            )
            plan_service.mark_task_awaiting_approval(task=task, plan=plan)
            metadata = _plan_metadata(
                markdown=plan_content,
                status=PLAN_STATUS_AWAITING_APPROVAL,
                plan_id=plan.id,
                task_id=task.id,
                version=plan.version,
            )
            metadata = _merge_files_skill_metadata(metadata, files_skill_result)
            plan_id = plan.id
            plan_version = plan.version
            plan_status = plan.status
            _finish_message(db, assistant_message_id, chat_id, plan_content, "done", metadata, usage)
            db.commit()
        await run_manager.publish(
            assistant_message_id,
            "plan_created",
            {
                "plan_id": plan_id,
                "task_id": task_id,
                "chat_id": chat_id,
                "version": plan_version,
                "status": plan_status,
                "message_id": assistant_message_id,
            },
        )
        await run_manager.publish(
            assistant_message_id,
            "approval_required",
            {"plan_id": plan_id, "task_id": task_id, "status": plan_status},
        )
        await run_manager.finish(
            assistant_message_id,
            {"ok": True, "chat_id": chat_id, "message_id": assistant_message_id, "plan_id": plan_id},
        )
    except AppError as exc:
        await _fail_message(assistant_message_id, chat_id, exc.to_payload())
    except Exception as exc:
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected plan generation error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        await _fail_message(assistant_message_id, chat_id, error.to_payload())


async def _run_plan_execute_background(
    *,
    execution_message_id: str,
    chat_id: str,
    plan_id: str,
    extra_params: dict[str, Any] | None,
) -> None:
    execution_content = ""
    usage: dict[str, Any] | None = None
    last_save = 0.0
    metadata: dict[str, Any] | None = None
    task_id: str | None = None
    try:
        with SessionLocal() as db:
            service = PlanService(db)
            plan = service.get_plan(plan_id)
            task = service.get_task(plan.task_id)
            task_id = task.id
            profile = get_model_profile(db, plan.model_profile_id)
            if plan.status != PLAN_STATUS_APPROVED:
                raise AppError(
                    PLAN_INVALID_STATE,
                    "Only an approved plan can be executed.",
                    status_code=409,
                    details={"plan_id": plan_id, "status": plan.status},
                )
            execution_service = PlanExecutionService(db)
            provider_messages = execution_service.build_provider_messages(
                profile=profile,
                task=task,
                plan=plan,
            )
            orchestrator = ToolOrchestrator(
                db,
                profile=profile,
                assistant_message_id=execution_message_id,
                chat_id=chat_id,
                current_file_ids=[],
                metadata=metadata,
                text_event="execution_delta",
            )
            async for event in orchestrator.stream(
                messages=provider_messages,
                extra_params=extra_params,
            ):
                metadata = orchestrator.metadata
                if event.event == "execution_delta":
                    text = str(event.data.get("text", ""))
                    execution_content += text
                    await run_manager.publish(
                        execution_message_id,
                        "execution_delta",
                        event.data,
                    )
                    last_save = _persist_streaming_message(
                        db,
                        execution_message_id,
                        chat_id,
                        execution_content,
                        metadata,
                        last_save,
                    )
                elif event.event == "usage":
                    usage = orchestrator.usage
                    await run_manager.publish(
                        execution_message_id,
                        "usage",
                        event.data,
                    )
                else:
                    await run_manager.publish(execution_message_id, event.event, event.data)

            metadata = orchestrator.metadata
            usage = orchestrator.usage

            task.status = TASK_STATUS_EXECUTING
            task.updated_at = now_utc()
            _finish_message(db, execution_message_id, chat_id, execution_content, "done", metadata, usage)
            db.commit()
        await run_manager.finish(
            execution_message_id,
            {"ok": True, "chat_id": chat_id, "message_id": execution_message_id, "plan_id": plan_id},
        )
    except AppError as exc:
        await _fail_message(
            execution_message_id,
            chat_id,
            exc.to_payload(),
            task_id=task_id,
        )
    except Exception as exc:
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected approved-plan execution error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        await _fail_message(
            execution_message_id,
            chat_id,
            error.to_payload(),
            task_id=task_id,
        )


async def _run_plan_revise_background(
    *,
    new_plan_message_id: str,
    chat_id: str,
    previous_plan_id: str,
    task_id: str,
    user_feedback: str,
    extra_params: dict[str, Any] | None,
) -> None:
    plan_content = ""
    usage: dict[str, Any] | None = None
    last_save = 0.0
    try:
        with SessionLocal() as db:
            plan_service = PlanService(db)
            plan_skill = PlanSkillService(db)
            previous_plan = plan_service.get_plan(previous_plan_id)
            task = plan_service.get_task(task_id)
            profile = get_model_profile(db, previous_plan.model_profile_id)
            prompt_bundle = plan_skill.load_revision_prompt_bundle()
            async for event_name, event_data in plan_skill.stream_revise_plan(
                profile=profile,
                prompt_bundle=prompt_bundle,
                task=task,
                previous_plan=previous_plan,
                user_feedback=user_feedback,
                extra_params=extra_params,
                is_disconnected=None,
            ):
                if event_name == "plan_delta":
                    text = str(event_data.get("text", ""))
                    plan_content += text
                    metadata = _plan_metadata(
                        markdown=plan_content,
                        status="streaming",
                        task_id=task.id,
                        version=previous_plan.version + 1,
                    )
                    await run_manager.publish(
                        new_plan_message_id,
                        "plan_delta",
                        {"text": text, "message_id": new_plan_message_id, "chat_id": chat_id},
                    )
                    last_save = _persist_streaming_message(
                        db,
                        new_plan_message_id,
                        chat_id,
                        plan_content,
                        metadata,
                        last_save,
                    )
                elif event_name == "usage":
                    usage = event_data.get("usage") if isinstance(event_data.get("usage"), dict) else None
                    await run_manager.publish(
                        new_plan_message_id,
                        "usage",
                        {"usage": usage, "message_id": new_plan_message_id, "chat_id": chat_id},
                    )

            plan = plan_service.create_plan(
                task=task,
                version=previous_plan.version + 1,
                status=PLAN_STATUS_AWAITING_APPROVAL,
                plan_markdown=plan_content,
                prompt_key=prompt_bundle.prompt_key,
                prompt_version_hash=prompt_bundle.prompt_version_hash,
                model_profile_id=profile.id,
                user_feedback=user_feedback,
                metadata={
                    "skill_name": PLAN_SKILL_NAME,
                    "previous_plan_id": previous_plan.id,
                    "prompt_hashes": prompt_bundle.prompt_hashes,
                    "usage": usage,
                },
            )
            plan_service.mark_task_awaiting_approval(task=task, plan=plan)
            metadata = _plan_metadata(
                markdown=plan_content,
                status=PLAN_STATUS_AWAITING_APPROVAL,
                plan_id=plan.id,
                task_id=task.id,
                version=plan.version,
            )
            plan_id = plan.id
            plan_version = plan.version
            plan_status = plan.status
            _finish_message(db, new_plan_message_id, chat_id, plan_content, "done", metadata, usage)
            db.commit()
        await run_manager.publish(
            new_plan_message_id,
            "plan_created",
            {
                "plan_id": plan_id,
                "task_id": task_id,
                "chat_id": chat_id,
                "version": plan_version,
                "status": plan_status,
                "message_id": new_plan_message_id,
            },
        )
        await run_manager.publish(
            new_plan_message_id,
            "approval_required",
            {"plan_id": plan_id, "task_id": task_id, "status": plan_status},
        )
        await run_manager.finish(
            new_plan_message_id,
            {"ok": True, "chat_id": chat_id, "message_id": new_plan_message_id, "plan_id": plan_id},
        )
    except AppError as exc:
        await _fail_message(new_plan_message_id, chat_id, exc.to_payload())
    except Exception as exc:
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected plan revision error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        await _fail_message(new_plan_message_id, chat_id, error.to_payload())


async def _run_files_skill_for_message(
    db: Session,
    *,
    assistant_message_id: str,
    chat_id: str,
    file_ids: list[str],
    profile: ModelProfile,
    user_message: str,
    extra_params: dict[str, Any] | None,
) -> FilesSkillResult:
    service = FilesSkillService(db)
    if service.has_context_files(chat_id=chat_id, file_ids=file_ids):
        await run_manager.publish(
            assistant_message_id,
            "files_skill_status",
            {
                "status": "running",
                "message_id": assistant_message_id,
                "chat_id": chat_id,
            },
        )
    result = await service.run_with_model(
        chat_id=chat_id,
        file_ids=file_ids,
        profile=profile,
        user_message=user_message,
        extra_params=extra_params,
    )
    if result.status != "skipped":
        await run_manager.publish(
            assistant_message_id,
            "files_skill_status",
            {
                "status": result.status,
                "message_id": assistant_message_id,
                "chat_id": chat_id,
                "filesSkill": result.to_metadata(),
            },
        )
    return result


def _merge_files_skill_metadata(
    metadata: dict[str, Any] | None,
    result: FilesSkillResult,
) -> dict[str, Any] | None:
    if result.status == "skipped":
        return metadata
    merged = dict(metadata or {})
    merged["filesSkill"] = result.to_metadata()
    return merged


async def _fail_message(
    message_id: str,
    chat_id: str,
    payload: dict[str, Any],
    *,
    task_id: str | None = None,
) -> None:
    message = str(payload.get("message") or "Run failed.")
    with SessionLocal() as db:
        metadata = None
        linked_task_id = task_id
        existing = db.get(Message, message_id)
        if existing is not None:
            metadata = _metadata_from_json(existing.metadata_json)
            plan = metadata.get("plan") if metadata else None
            if isinstance(plan, dict):
                plan["status"] = "error"
                if not plan.get("markdown"):
                    plan["markdown"] = existing.content or message
                metadata_task_id = plan.get("taskId")
                if linked_task_id is None and isinstance(metadata_task_id, str):
                    linked_task_id = metadata_task_id
        if linked_task_id is not None:
            task = db.get(AnalysisTask, linked_task_id)
            if task is not None:
                task.status = TASK_STATUS_ERROR
                task.updated_at = now_utc()
        _finish_message(db, message_id, chat_id, message, "error", metadata, None)
        db.commit()
    await run_manager.fail(message_id, payload)


def _get_streaming_profile(db: Session, model_profile_id: str | None) -> ModelProfile:
    profile = get_model_profile(db, model_profile_id)
    if not profile.supports_stream:
        raise AppError(
            MODEL_CAPABILITY_MISMATCH,
            "This model profile does not support streaming.",
            status_code=400,
        )
    return profile


def _get_or_create_chat(
    db: Session,
    chat_id: str | None,
    profile: ModelProfile,
    title_source: str,
) -> Chat:
    if chat_id:
        chat = db.get(Chat, chat_id)
        if chat is None:
            raise AppError("CHAT_NOT_FOUND", "Chat was not found.", status_code=404)
        return chat

    chat = Chat(title=(title_source.strip() or "New chat")[:80], model_profile_id=profile.id)
    db.add(chat)
    db.flush()
    return chat


def _ensure_chat_can_start(db: Session, chat_id: str) -> None:
    if run_manager.is_chat_running(chat_id):
        raise AppError(
            CHAT_RUNNING,
            "This chat already has a running response.",
            status_code=409,
        )
    streaming_count = db.scalar(
        select(func.count()).select_from(Message).where(
            Message.chat_id == chat_id,
            Message.status == "streaming",
        )
    )
    if streaming_count:
        raise AppError(
            CHAT_RUNNING,
            "This chat already has a running response.",
            status_code=409,
        )


def _load_done_history(db: Session, chat_id: str) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.chat_id == chat_id, Message.status == "done")
            .order_by(Message.created_at)
        ).all()
    )
    return [
        {"role": row.role, "content": row.content}
        for row in rows
        if row.role in {"system", "user", "assistant"}
    ]


def _create_message(
    db: Session,
    chat_id: str,
    role: str,
    content: str,
    status: str,
    model_profile_id: str | None,
    message_type: str,
    metadata: dict[str, Any] | None,
) -> Message:
    chat = db.get(Chat, chat_id)
    if chat is not None:
        chat.updated_at = now_utc()
    message = Message(
        chat_id=chat_id,
        role=role,
        content=content,
        status=status,
        model_profile_id=model_profile_id,
        message_type=message_type,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    )
    db.add(message)
    db.flush()
    return message


def _persist_streaming_message(
    db: Session,
    message_id: str,
    chat_id: str,
    content: str,
    metadata: dict[str, Any] | None,
    last_save: float,
) -> float:
    now = time.monotonic()
    if now - last_save < SAVE_INTERVAL_SECONDS:
        return last_save
    _finish_message(db, message_id, chat_id, content, "streaming", metadata, None)
    db.commit()
    return now


def _finish_message(
    db: Session,
    message_id: str,
    chat_id: str,
    content: str,
    status: str,
    metadata: dict[str, Any] | None,
    token_usage: dict[str, Any] | None,
) -> None:
    message = db.get(Message, message_id)
    if message is None:
        return
    message.content = content
    message.status = status
    if metadata is not None:
        message.metadata_json = json.dumps(metadata, ensure_ascii=False)
    if token_usage:
        message.token_usage_json = json.dumps(token_usage, ensure_ascii=False)
    chat = db.get(Chat, chat_id)
    if chat is not None:
        chat.updated_at = now_utc()


def _update_plan_message_metadata(
    db: Session,
    message_id: str,
    *,
    status: str,
    markdown: str,
    plan_id: str | None,
    task_id: str | None,
    version: int,
) -> None:
    message = db.get(Message, message_id)
    if message is None:
        return
    existing_metadata = _metadata_from_json(message.metadata_json) or {}
    metadata = _plan_metadata(
        markdown=markdown,
        status=status,
        plan_id=plan_id,
        task_id=task_id,
        version=version,
    )
    if "filesSkill" in existing_metadata:
        metadata["filesSkill"] = existing_metadata["filesSkill"]
    message.metadata_json = json.dumps(metadata, ensure_ascii=False)


def _message_input_metadata(
    db: Session,
    file_ids: list[str],
    *,
    plan_mode: bool = False,
) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    if plan_mode:
        metadata["planMode"] = True

    attachments = []
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
    if attachments:
        metadata["attachments"] = attachments

    return metadata or None


def _attachment_file_ids(file_ids: list[str], image_file_ids: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for file_id in [*file_ids, *image_file_ids]:
        if file_id in seen:
            continue
        ordered.append(file_id)
        seen.add(file_id)
    return ordered


def _plan_metadata(
    *,
    markdown: str,
    status: str,
    version: int,
    plan_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "plan": {
            "planId": plan_id,
            "taskId": task_id,
            "version": version,
            "status": status,
            "markdown": markdown,
        }
    }


def _metadata_from_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _serialize_message(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "chat_id": message.chat_id,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "type": message.message_type,
        "metadata": _metadata_from_json(message.metadata_json),
        "model_profile_id": message.model_profile_id,
        "created_at": message.created_at,
    }


def _serialize_chat(chat: Chat | None) -> dict[str, Any] | None:
    if chat is None:
        return None
    return {
        "id": chat.id,
        "title": chat.title,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "model_profile_id": chat.model_profile_id,
    }


def _run_response(chat: Chat, user_message: Message, assistant_message: Message) -> dict[str, Any]:
    return {
        "chat_id": chat.id,
        "chat": _serialize_chat(chat),
        "user_message": _serialize_message(user_message),
        "assistant_message": _serialize_message(assistant_message),
    }
