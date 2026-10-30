"""Local, atomic execution checkpoints. Never replay an uncertain side effect."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import AppError


def checkpoint_path(message_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", message_id):
        raise AppError("INVALID_RUN_ID", "Invalid run identifier.", status_code=400)
    return get_settings().storage_dir / "agent_runs" / message_id / "checkpoint.json"


def save_checkpoint(message_id: str, state: dict[str, Any]) -> None:
    path = checkpoint_path(message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_checkpoint(message_id: str) -> dict[str, Any]:
    try:
        state = json.loads(checkpoint_path(message_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("CHECKPOINT_UNAVAILABLE", "This run has no readable checkpoint.", status_code=409) from exc
    if not isinstance(state, dict) or state.get("version") != 1:
        raise AppError("CHECKPOINT_UNAVAILABLE", "Unsupported run checkpoint.", status_code=409)
    return state
