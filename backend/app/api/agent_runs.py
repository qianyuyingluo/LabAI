from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.errors import AppError
from app.db.models import AnalysisTask, Chat, Message, now_utc
from app.db.session import SessionLocal
from app.services.agent_checkpoint import load_checkpoint
from app.services.model_service import get_model_profile
from app.services.run_manager import run_manager
from app.services.tool_orchestrator import ToolOrchestrator


router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("/messages/{message_id}/resume")
async def resume_agent_run(message_id: str, db: Session = Depends(get_session)):
    from app.api.runs import _ensure_chat_can_start, _serialize_chat, _serialize_message
    message = db.get(Message, message_id)
    if message is None:
        raise AppError("MESSAGE_NOT_FOUND", "Run was not found.", status_code=404)
    chat = db.get(Chat, message.chat_id)
    if chat is None or not chat.workspace_path:
        raise AppError("WORKSPACE_REQUIRED", "Bind the original workspace before resuming.", status_code=409)
    _ensure_chat_can_start(db, chat.id)
    if message.role != "assistant" or message.status not in {"interrupted", "error"}:
        raise AppError("RUN_NOT_RESUMABLE", "Only interrupted or failed workspace runs can be resumed.", status_code=409)
    state = load_checkpoint(message.id)
    if state["chat_id"] != chat.id or state["workspace_path"] != chat.workspace_path:
        raise AppError("WORKSPACE_CHANGED", "Restore the original workspace before resuming this run.", status_code=409)
    if state.get("status") in {"done", "cancelled"}:
        raise AppError("RUN_NOT_RESUMABLE", "This checkpoint is already finished.", status_code=409)
    profile = get_model_profile(db, state["model_profile_id"])
    if not profile.supports_tools:
        raise AppError("MODEL_TOOL_CALLING_UNSUPPORTED", "Enable model tool calling before resuming.", status_code=400)
    # Use the current workspace approval policy, never a stale automatic grant.
    message.status = "streaming"
    message.content = state.get("content", "")
    metadata = state.get("metadata", {})
    metadata.get("workspaceAgent", {})["status"] = "running"
    message.metadata_json = json.dumps(metadata, ensure_ascii=False)
    task = _linked_execution_task(db, state, chat.id)
    if task is not None:
        task.status = "executing"
        task.updated_at = now_utc()
    db.commit()
    run_manager.start(message_id=message.id, chat_id=chat.id,
                      runner=lambda: _resume_background(message.id, chat.id, state))
    return {"chat_id": chat.id, "chat": _serialize_chat(chat), "assistant_message": _serialize_message(message)}


async def _resume_background(message_id: str, chat_id: str, state: dict) -> None:
    from app.api.runs import _fail_message, _finish_message
    content = state.get("content", "")
    task_id: str | None = None
    try:
        with SessionLocal() as db:
            task = _linked_execution_task(db, state, chat_id)
            if task is not None:
                task_id = task.id
                task.status = "executing"
                task.updated_at = now_utc()
                db.commit()
            profile = get_model_profile(db, state["model_profile_id"])
            orchestrator = ToolOrchestrator(db, profile=profile,
                assistant_message_id=message_id, chat_id=chat_id,
                current_file_ids=state.get("current_file_ids", []),
                metadata=state.get("metadata", {}), resume_state=state,
                text_event="execution_delta" if task is not None else "delta")
            async for event in orchestrator.stream(messages=[], extra_params=state.get("extra_params")):
                if event.event in {"delta", "execution_delta"}:
                    content += str(event.data.get("text", ""))
                    _finish_message(db, message_id, chat_id, content, "streaming", orchestrator.metadata, orchestrator.usage)
                    db.commit()
                await run_manager.publish(message_id, event.event, event.data)
            _finish_message(db, message_id, chat_id, content, "done", orchestrator.metadata, orchestrator.usage)
            if task is not None:
                task.status = "done"
                task.updated_at = now_utc()
            db.commit()
        await run_manager.finish(message_id, {"ok": True, "message_id": message_id, "chat_id": chat_id})
    except AppError as exc:
        await _fail_message(message_id, chat_id, exc.to_payload(), task_id=task_id)
    except Exception as exc:
        await _fail_message(message_id, chat_id, {"code": "AGENT_RESUME_FAILED",
            "message": "Resumed run failed.", "details": {"type": type(exc).__name__}}, task_id=task_id)


def _linked_execution_task(db: Session, state: dict, chat_id: str) -> AnalysisTask | None:
    metadata = state.get("metadata", {})
    execution = metadata.get("execution") if isinstance(metadata, dict) else None
    task_id = execution.get("taskId") if isinstance(execution, dict) else None
    if not isinstance(task_id, str):
        return None
    task = db.get(AnalysisTask, task_id)
    if task is None or task.chat_id != chat_id:
        raise AppError("TASK_NOT_FOUND", "The checkpoint's analysis task is unavailable.", status_code=409)
    return task
