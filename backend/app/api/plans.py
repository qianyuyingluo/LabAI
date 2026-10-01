import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.errors import (
    CHAT_RUNNING,
    MODEL_CAPABILITY_MISMATCH,
    PROVIDER_STREAM_ERROR,
    AppError,
)
from app.db.models import AnalysisPlan, AnalysisTask, Chat, Message, ModelProfile, now_utc
from app.services.chat_service import format_sse
from app.services.model_service import get_model_profile
from app.services.plan_execution_service import PLAN_APPROVAL_USER_MESSAGE, PlanExecutionService
from app.services.plan_service import (
    PLAN_INVALID_STATE,
    PLAN_STATUS_AWAITING_APPROVAL,
    PlanService,
    TASK_STATUS_EXECUTING,
)
from app.services.prompt_service import PromptService
from app.services.tool_orchestrator import ToolOrchestrator
from app.skills.files_skill import FilesSkillService
from app.skills.files_skill.schemas import FilesSkillResult
from app.skills.plan_skill import PLAN_SKILL_NAME, PlanSkillService


router = APIRouter(prefix="/api/plans", tags=["plans"])


class GeneratePlanRequest(BaseModel):
    chat_id: str | None = None
    model_profile_id: str | None = None
    message: str = Field(min_length=1)
    image_file_ids: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    file_summaries: list[str] = Field(default_factory=list)
    extra_params: dict[str, Any] | None = None


class RevisePlanRequest(BaseModel):
    user_feedback: str = Field(min_length=1)
    extra_params: dict[str, Any] | None = None


@router.post("/generate/stream")
async def generate_plan_stream(
    request: Request,
    body: GeneratePlanRequest,
    db: Session = Depends(get_session),
) -> StreamingResponse:
    return _sse_response(_generate_plan_events(db, request, body))


@router.post("/{plan_id}/approve/stream")
async def approve_plan_stream(
    plan_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> StreamingResponse:
    return _sse_response(_approve_and_execute_events(db, request, plan_id))


@router.post("/{plan_id}/revise/stream")
async def revise_plan_stream(
    plan_id: str,
    request: Request,
    body: RevisePlanRequest,
    db: Session = Depends(get_session),
) -> StreamingResponse:
    return _sse_response(_revise_plan_events(db, request, plan_id, body))


@router.get("/{plan_id}")
async def get_plan(plan_id: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    service = PlanService(db)
    plan = service.get_plan(plan_id)
    task = db.get(AnalysisTask, plan.task_id)
    return _serialize_plan(plan, task)


@router.get("/prompts/plain")
async def get_plain_mode_prompts(db: Session = Depends(get_session)) -> dict[str, Any]:
    return _serialize_plan_mode_prompts(db)


@router.get("/prompts/plan")
async def get_plan_mode_prompts(db: Session = Depends(get_session)) -> dict[str, Any]:
    return _serialize_plan_mode_prompts(db)


def _serialize_plan_mode_prompts(db: Session) -> dict[str, Any]:
    prompts = PromptService(db).get_plan_mode_prompts()
    return {
        "prompts": [
            {
                "key": prompt.key,
                "relative_path": prompt.relative_path,
                "content": prompt.content,
                "content_hash": prompt.content_hash,
            }
            for prompt in prompts
        ]
    }


async def _generate_plan_events(
    db: Session,
    request: Request,
    body: GeneratePlanRequest,
) -> AsyncIterator[str]:
    task: AnalysisTask | None = None
    try:
        profile = _get_streaming_profile(db, body.model_profile_id)
        plan_skill = PlanSkillService(db)
        image_infos = plan_skill.load_image_infos(body.image_file_ids, profile)
        chat = _get_or_create_chat(db, body.chat_id, profile, body.message or "Plan Mode analysis")
        _ensure_chat_can_start(db, chat.id)
        _create_message(db, chat.id, "user", body.message, "done", profile.id, "text", {"planMode": True})
        plan_service = PlanService(db)
        task = plan_service.create_task(chat_id=chat.id, user_message=body.message)
        db.commit()

        files_skill_result = await _run_files_skill_stream(
            db,
            chat_id=chat.id,
            file_ids=body.file_ids,
            profile=profile,
            user_message=body.message,
            extra_params=body.extra_params,
            event_context={"chat_id": chat.id, "task_id": task.id},
        )
        for event in files_skill_result["events"]:
            yield event
        files_skill: FilesSkillResult = files_skill_result["result"]
        file_contexts = [files_skill.model_context] if files_skill.has_context else body.file_summaries
        prompt_bundle = plan_skill.load_generation_prompt_bundle()
        plan_parts: list[str] = []
        usage: dict[str, Any] | None = None

        async for event_name, event_data in plan_skill.stream_generate_plan(
            profile=profile,
            prompt_bundle=prompt_bundle,
            task=task,
            user_message=body.message,
            file_summaries=file_contexts,
            image_infos=image_infos,
            image_file_ids=body.image_file_ids,
            extra_params=body.extra_params,
            is_disconnected=request.is_disconnected,
        ):
            if event_name == "plan_delta":
                plan_parts.append(str(event_data.get("text", "")))
            elif event_name == "usage":
                usage = event_data.get("usage") if isinstance(event_data.get("usage"), dict) else None
            yield format_sse(event_name, event_data)

        if await request.is_disconnected():
            return

        plan_markdown = "".join(plan_parts)
        plan = plan_service.create_plan(
            task=task,
            version=1,
            status=PLAN_STATUS_AWAITING_APPROVAL,
            plan_markdown=plan_markdown,
            prompt_key=prompt_bundle.prompt_key,
            prompt_version_hash=prompt_bundle.prompt_version_hash,
            model_profile_id=profile.id,
            user_feedback=None,
            metadata={
                "skill_name": PLAN_SKILL_NAME,
                "prompt_hashes": prompt_bundle.prompt_hashes,
                "file_summaries": file_contexts,
                "files_skill": files_skill.to_metadata() if files_skill.status != "skipped" else None,
                "image_inputs": image_infos,
                "usage": usage,
            },
        )
        plan_service.mark_task_awaiting_approval(task=task, plan=plan)
        _create_message(
            db,
            chat.id,
            "assistant",
            plan_markdown,
            "done",
            profile.id,
            "plan",
            _merge_files_skill_metadata(
                _plan_metadata(
                    markdown=plan_markdown,
                    status=PLAN_STATUS_AWAITING_APPROVAL,
                    plan_id=plan.id,
                    task_id=task.id,
                    version=plan.version,
                ),
                files_skill,
            ),
            usage,
        )
        db.commit()

        yield format_sse(
            "plan_created",
            {
                "plan_id": plan.id,
                "task_id": task.id,
                "chat_id": chat.id,
                "version": plan.version,
                "status": plan.status,
                "prompt_key": plan.prompt_key,
                "prompt_version_hash": plan.prompt_version_hash,
            },
        )
        yield format_sse("approval_required", {"plan_id": plan.id, "task_id": task.id, "status": plan.status})
        yield format_sse("done", {"ok": True, "plan_id": plan.id, "task_id": task.id})
    except AppError as exc:
        _rollback_and_mark_task_error(db, task)
        yield format_sse("error", exc.to_payload())
        yield format_sse("done", {"ok": False})
    except Exception as exc:
        _rollback_and_mark_task_error(db, task)
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected plan generation error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        yield format_sse("error", error.to_payload())
        yield format_sse("done", {"ok": False})


async def _revise_plan_events(
    db: Session,
    request: Request,
    plan_id: str,
    body: RevisePlanRequest,
) -> AsyncIterator[str]:
    task: AnalysisTask | None = None
    try:
        plan_service = PlanService(db)
        previous_plan = plan_service.get_plan(plan_id)
        if previous_plan.status != PLAN_STATUS_AWAITING_APPROVAL:
            raise AppError(
                PLAN_INVALID_STATE,
                "Only an awaiting-approval plan can be revised.",
                status_code=409,
                details={"plan_id": plan_id, "status": previous_plan.status},
            )
        task = plan_service.get_task(previous_plan.task_id)
        if not task.chat_id:
            raise AppError("TASK_NOT_FOUND", "Analysis task was not found.", status_code=404)
        _ensure_chat_can_start(db, task.chat_id)
        profile = _get_streaming_profile(db, previous_plan.model_profile_id)
        previous_plan, task = plan_service.reject_plan_for_revision(plan_id, body.user_feedback)
        _create_message(db, task.chat_id, "user", body.user_feedback, "done", profile.id, "text", {"planMode": True})
        db.commit()

        plan_skill = PlanSkillService(db)
        prompt_bundle = plan_skill.load_revision_prompt_bundle()
        new_parts: list[str] = []
        usage: dict[str, Any] | None = None
        async for event_name, event_data in plan_skill.stream_revise_plan(
            profile=profile,
            prompt_bundle=prompt_bundle,
            task=task,
            previous_plan=previous_plan,
            user_feedback=body.user_feedback,
            extra_params=body.extra_params,
            is_disconnected=request.is_disconnected,
        ):
            if event_name == "plan_delta":
                new_parts.append(str(event_data.get("text", "")))
            elif event_name == "usage":
                usage = event_data.get("usage") if isinstance(event_data.get("usage"), dict) else None
            yield format_sse(event_name, event_data)

        if await request.is_disconnected():
            return

        new_markdown = "".join(new_parts)
        plan = plan_service.create_plan(
            task=task,
            version=previous_plan.version + 1,
            status=PLAN_STATUS_AWAITING_APPROVAL,
            plan_markdown=new_markdown,
            prompt_key=prompt_bundle.prompt_key,
            prompt_version_hash=prompt_bundle.prompt_version_hash,
            model_profile_id=profile.id,
            user_feedback=body.user_feedback,
            metadata={
                "skill_name": PLAN_SKILL_NAME,
                "previous_plan_id": previous_plan.id,
                "prompt_hashes": prompt_bundle.prompt_hashes,
                "usage": usage,
            },
        )
        plan_service.mark_task_awaiting_approval(task=task, plan=plan)
        _create_message(
            db,
            task.chat_id,
            "assistant",
            new_markdown,
            "done",
            profile.id,
            "plan",
            _plan_metadata(
                markdown=new_markdown,
                status=PLAN_STATUS_AWAITING_APPROVAL,
                plan_id=plan.id,
                task_id=task.id,
                version=plan.version,
            ),
            usage,
        )
        db.commit()

        yield format_sse(
            "plan_created",
            {
                "plan_id": plan.id,
                "task_id": task.id,
                "chat_id": task.chat_id,
                "version": plan.version,
                "status": plan.status,
                "prompt_key": plan.prompt_key,
                "prompt_version_hash": plan.prompt_version_hash,
            },
        )
        yield format_sse("approval_required", {"plan_id": plan.id, "task_id": task.id, "status": plan.status})
        yield format_sse("done", {"ok": True, "plan_id": plan.id, "task_id": task.id})
    except AppError as exc:
        _rollback_and_mark_task_error(db, task)
        yield format_sse("error", exc.to_payload())
        yield format_sse("done", {"ok": False})
    except Exception as exc:
        _rollback_and_mark_task_error(db, task)
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected plan revision error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        yield format_sse("error", error.to_payload())
        yield format_sse("done", {"ok": False})


async def _approve_and_execute_events(
    db: Session,
    request: Request,
    plan_id: str,
) -> AsyncIterator[str]:
    task_id: str | None = None
    execution_message_id: str | None = None
    execution_parts: list[str] = []
    orchestrator: ToolOrchestrator | None = None
    terminalized = False
    try:
        service = PlanService(db)
        plan = service.approve_plan(plan_id)
        task = service.get_task(plan.task_id)
        task_id = task.id
        if not task.chat_id:
            raise AppError("TASK_NOT_FOUND", "Analysis task was not found.", status_code=404)
        _ensure_chat_can_start(db, task.chat_id)
        profile = _get_streaming_profile(db, plan.model_profile_id)
        _create_message(
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
        execution_message_id = execution_message.id
        db.commit()

        execution_service = PlanExecutionService(db)
        provider_messages = execution_service.build_provider_messages(
            profile=profile,
            task=task,
            plan=plan,
        )
        orchestrator = ToolOrchestrator(
            db,
            profile=profile,
            assistant_message_id=execution_message.id,
            chat_id=task.chat_id,
            current_file_ids=[],
            text_event="execution_delta",
        )
        async for event in orchestrator.stream(messages=provider_messages, extra_params=None):
            if await request.is_disconnected():
                _finish_plan_execution_error(
                    db,
                    task_id=task.id,
                    message_id=execution_message.id,
                    content="".join(execution_parts),
                    metadata=orchestrator.metadata,
                    token_usage=orchestrator.usage,
                    error="Plan execution disconnected before completion.",
                )
                terminalized = True
                return
            if event.event == "execution_delta":
                execution_parts.append(str(event.data.get("text", "")))
            yield format_sse(event.event, event.data)

        task.status = TASK_STATUS_EXECUTING
        task.updated_at = now_utc()
        execution_message.content = "".join(execution_parts)
        execution_message.status = "done"
        execution_message.metadata_json = _json_dumps(orchestrator.metadata)
        execution_message.token_usage_json = _json_dumps(orchestrator.usage)
        db.commit()
        terminalized = True
        if await request.is_disconnected():
            return
        yield format_sse("done", {"ok": True, "plan_id": plan.id, "task_id": task.id, "chat_id": task.chat_id})
    except AppError as exc:
        if task_id is not None and execution_message_id is not None:
            _finish_plan_execution_error(
                db,
                task_id=task_id,
                message_id=execution_message_id,
                content="".join(execution_parts),
                metadata=orchestrator.metadata if orchestrator is not None else None,
                token_usage=orchestrator.usage if orchestrator is not None else None,
                error=exc.message,
            )
            terminalized = True
        else:
            db.rollback()
        yield format_sse("error", exc.to_payload())
        yield format_sse("done", {"ok": False})
    except Exception as exc:
        error = AppError(
            PROVIDER_STREAM_ERROR,
            "Unexpected approved-plan execution error.",
            status_code=502,
            details={"type": exc.__class__.__name__},
        )
        if task_id is not None and execution_message_id is not None:
            _finish_plan_execution_error(
                db,
                task_id=task_id,
                message_id=execution_message_id,
                content="".join(execution_parts),
                metadata=orchestrator.metadata if orchestrator is not None else None,
                token_usage=orchestrator.usage if orchestrator is not None else None,
                error=error.message,
            )
            terminalized = True
        else:
            db.rollback()
        yield format_sse("error", error.to_payload())
        yield format_sse("done", {"ok": False})
    finally:
        if task_id is not None and execution_message_id is not None and not terminalized:
            try:
                _finish_plan_execution_error(
                    db,
                    task_id=task_id,
                    message_id=execution_message_id,
                    content="".join(execution_parts),
                    metadata=orchestrator.metadata if orchestrator is not None else None,
                    token_usage=orchestrator.usage if orchestrator is not None else None,
                    error="Plan execution was cancelled before completion.",
                )
            except Exception:
                db.rollback()


def _sse_response(events: Any) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_files_skill_stream(
    db: Session,
    *,
    chat_id: str,
    file_ids: list[str],
    profile: ModelProfile,
    user_message: str,
    extra_params: dict[str, Any] | None,
    event_context: dict[str, str],
) -> dict[str, Any]:
    service = FilesSkillService(db)
    events: list[str] = []
    if service.has_context_files(chat_id=chat_id, file_ids=file_ids):
        events.append(format_sse("files_skill_status", {"status": "running", **event_context}))
    result = await service.run_with_model(
        chat_id=chat_id,
        file_ids=file_ids,
        profile=profile,
        user_message=user_message,
        extra_params=extra_params,
    )
    if result.status != "skipped":
        events.append(
            format_sse(
                "files_skill_status",
                {
                    "status": result.status,
                    **event_context,
                    "filesSkill": result.to_metadata(),
                },
            )
        )
    return {"events": events, "result": result}


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

    chat = Chat(title=(title_source.strip() or "Plan Mode analysis")[:80], model_profile_id=profile.id)
    db.add(chat)
    db.flush()
    return chat


def _ensure_chat_can_start(db: Session, chat_id: str) -> None:
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


def _create_message(
    db: Session,
    chat_id: str,
    role: str,
    content: str,
    status: str,
    model_profile_id: str | None,
    message_type: str,
    metadata: dict[str, Any] | None,
    token_usage: dict[str, Any] | None = None,
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
        metadata_json=_json_dumps(metadata),
        token_usage_json=_json_dumps(token_usage),
    )
    db.add(message)
    db.flush()
    return message


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


def _merge_files_skill_metadata(
    metadata: dict[str, Any] | None,
    result: FilesSkillResult,
) -> dict[str, Any] | None:
    if result.status == "skipped":
        return metadata
    merged = dict(metadata or {})
    merged["filesSkill"] = result.to_metadata()
    return merged


def _rollback_and_mark_task_error(db: Session, task: AnalysisTask | None) -> None:
    task_id = task.id if task is not None else None
    db.rollback()
    if not task_id:
        return
    existing = db.get(AnalysisTask, task_id)
    if existing is None:
        return
    PlanService(db).mark_task_error(existing)
    db.commit()


def _finish_plan_execution_error(
    db: Session,
    *,
    task_id: str,
    message_id: str,
    content: str,
    metadata: dict[str, Any] | None,
    token_usage: dict[str, Any] | None,
    error: str,
) -> None:
    db.rollback()
    task = db.get(AnalysisTask, task_id)
    if task is not None:
        PlanService(db).mark_task_error(task)

    message = db.get(Message, message_id)
    if message is not None:
        current_metadata = metadata or _json_loads(message.metadata_json)
        message.content = content or error
        message.status = "error"
        message.metadata_json = _json_dumps(_mark_sandbox_interrupted(current_metadata, error))
        if token_usage:
            message.token_usage_json = _json_dumps(token_usage)
        chat = db.get(Chat, message.chat_id)
        if chat is not None:
            chat.updated_at = now_utc()
    db.commit()


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


def _json_loads(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_dumps(value: dict[str, Any] | None) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value else None


def _serialize_plan(plan: AnalysisPlan, task: AnalysisTask | None) -> dict[str, Any]:
    return {
        "id": plan.id,
        "task_id": plan.task_id,
        "version": plan.version,
        "status": plan.status,
        "plan_markdown": plan.plan_markdown,
        "plan_json": plan.plan_json,
        "prompt_key": plan.prompt_key,
        "prompt_version_hash": plan.prompt_version_hash,
        "model_profile_id": plan.model_profile_id,
        "user_feedback": plan.user_feedback,
        "created_at": plan.created_at.isoformat(),
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "rejected_at": plan.rejected_at.isoformat() if plan.rejected_at else None,
        "task": {
            "id": task.id,
            "chat_id": task.chat_id,
            "user_message": task.user_message,
            "status": task.status,
            "current_plan_id": task.current_plan_id,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }
        if task
        else None,
    }
