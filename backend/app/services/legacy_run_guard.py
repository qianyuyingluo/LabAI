from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Chat


def reject_legacy_workspace_run(db: Session, chat_id: str | None) -> None:
    """Workspace runs need the durable manager for approval and cancellation."""
    chat = db.get(Chat, chat_id) if chat_id else None
    if chat is not None and chat.workspace_path:
        raise AppError(
            "WORKSPACE_MANAGED_RUN_REQUIRED",
            "Workspace chats must use /api/runs/chat or /api/runs/plan endpoints so tool approval and cancellation remain available.",
            status_code=409,
        )
