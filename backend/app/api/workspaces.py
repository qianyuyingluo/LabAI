"""Local workspace binding and read-only browsing for the desktop UI."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import ipaddress
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.errors import CHAT_RUNNING, AppError
from app.db.models import Chat, Message, now_utc
from app.services.run_manager import run_manager
from app.services.folder_picker import pick_folder
from app.services.workspace_tools import WorkspaceTools, validate_workspace_root


router = APIRouter(tags=["workspaces"])


class InspectWorkspaceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class PickFolderRequest(BaseModel):
    initial_path: str | None = Field(default=None, max_length=4096)


class BindWorkspaceRequest(BaseModel):
    path: str | None
    mode: Literal["approve", "auto"] = "approve"


def canonical_workspace(path: str) -> Path:
    try:
        return validate_workspace_root(path)
    except (ValueError, OSError) as exc:
        raise AppError("INVALID_WORKSPACE", str(exc), status_code=400) from exc


def bind_workspace(db: Session, chat: Chat, *, path: str | None, mode: str) -> None:
    running = run_manager.is_chat_running(chat.id) or db.scalar(
        select(Message.id).where(Message.chat_id == chat.id, Message.status == "streaming").limit(1)
    )
    if running:
        raise AppError(CHAT_RUNNING, "Stop the current run before changing its workspace.", status_code=409)
    chat.workspace_path = str(canonical_workspace(path)) if path is not None else None
    chat.workspace_mode = mode
    chat.updated_at = now_utc()


def _get_workspace_chat(db: Session, chat_id: str) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise AppError("CHAT_NOT_FOUND", "Chat was not found.", status_code=404)
    return chat


def _directory_payload(root: Path, relative_path: str = ".") -> dict[str, object]:
    try:
        requested = Path(relative_path)
        if requested.is_absolute() or requested.drive or "\x00" in relative_path or ":" in relative_path:
            raise ValueError("A relative workspace path is required.")
        directory = WorkspaceTools(root, run_id="browse").resolve(relative_path, must_exist=True)
        if not directory.is_dir():
            raise ValueError("The requested path is not a directory.")
        entries = []
        truncated = False
        # A bounded directory walk avoids sorting arbitrarily large directories.
        for scanned, entry in enumerate(directory.iterdir()):
            if len(entries) >= 500 or scanned >= 2000:
                truncated = True
                break
            try:
                resolved = entry.resolve(strict=True)
                resolved.relative_to(root)
                kind = "directory" if entry.is_dir() else "file"
                entries.append({
                    "name": entry.name,
                    "path": entry.relative_to(root).as_posix(),
                    "kind": kind,
                    "size": entry.stat().st_size if kind == "file" else None,
                })
            except (ValueError, OSError):
                # A link pointing outside the selected root is not browsable.
                continue
        entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
        return {
            "path": str(root),
            "name": root.name or str(root),
            "relative_path": directory.relative_to(root).as_posix(),
            "entries": entries,
            "truncated": truncated,
        }
    except (ValueError, OSError) as exc:
        raise AppError("INVALID_WORKSPACE_PATH", str(exc), status_code=400) from exc


@router.post("/api/workspaces/inspect")
async def inspect_workspace(body: InspectWorkspaceRequest) -> dict[str, object]:
    return _directory_payload(canonical_workspace(body.path))


@router.post("/api/workspaces/pick-folder")
async def pick_workspace_folder(body: PickFolderRequest, request: Request) -> dict[str, object]:
    host = request.client.host if request.client is not None else ""
    try:
        local = host == "testclient" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise AppError("FOLDER_PICKER_LOCAL_ONLY", "Folder selection is available only from the backend computer. Enter its folder path manually.", status_code=403)
    task = asyncio.create_task(pick_folder(body.initial_path))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=0.2)
            if done:
                break
            if await request.is_disconnected():
                raise AppError("FOLDER_PICKER_DISCONNECTED", "Folder selection was cancelled because the client disconnected.", status_code=499)
        selected = await task
        return {"path": selected, "cancelled": selected is None}
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@router.patch("/api/chats/{chat_id}/workspace")
async def update_workspace(
    chat_id: str, body: BindWorkspaceRequest, db: Session = Depends(get_session),
) -> dict[str, object]:
    chat = _get_workspace_chat(db, chat_id)
    bind_workspace(db, chat, path=body.path, mode=body.mode)
    db.commit()
    return {"workspace_path": chat.workspace_path, "workspace_mode": chat.workspace_mode}


@router.get("/api/chats/{chat_id}/workspace")
async def list_workspace(
    chat_id: str, path: str = ".", db: Session = Depends(get_session),
) -> dict[str, object]:
    chat = _get_workspace_chat(db, chat_id)
    if not chat.workspace_path:
        raise AppError("WORKSPACE_NOT_BOUND", "Select a workspace first.", status_code=409)
    return _directory_payload(canonical_workspace(chat.workspace_path), path)


@router.get("/api/chats/{chat_id}/workspace/file")
async def read_workspace_file(
    chat_id: str,
    path: str,
    start_line: int = Query(default=1, ge=1),
    end_line: int = Query(default=200, ge=1, le=1000000),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    chat = _get_workspace_chat(db, chat_id)
    if not chat.workspace_path:
        raise AppError("WORKSPACE_NOT_BOUND", "Select a workspace first.", status_code=409)
    workspace = WorkspaceTools(canonical_workspace(chat.workspace_path), run_id=f"browse-{chat.id}")
    result = await workspace.execute(
        "workspace_read", {"path": path, "start_line": start_line, "end_line": end_line},
    )
    if not result.get("ok"):
        raise AppError("WORKSPACE_READ_FAILED", str(result.get("error", "Cannot read file.")), status_code=400)
    return result
