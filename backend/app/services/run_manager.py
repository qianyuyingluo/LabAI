import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.db.models import AnalysisTask, Message, now_utc
from app.db.session import SessionLocal


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


RunFactory = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class ActiveRun:
    message_id: str
    chat_id: str
    queues: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    cancel_requested: bool = False


class RunManager:
    def __init__(self) -> None:
        self._runs_by_message: dict[str, ActiveRun] = {}
        self._message_by_chat: dict[str, str] = {}

    def is_message_running(self, message_id: str) -> bool:
        return message_id in self._runs_by_message

    def is_chat_running(self, chat_id: str) -> bool:
        return chat_id in self._message_by_chat

    def start(self, *, message_id: str, chat_id: str, runner: RunFactory) -> None:
        if chat_id in self._message_by_chat:
            raise RuntimeError("chat already has an active run")

        active = ActiveRun(message_id=message_id, chat_id=chat_id)
        self._runs_by_message[message_id] = active
        self._message_by_chat[chat_id] = message_id
        active.task = asyncio.create_task(self._wrap(active, runner))

    def prepare_approval(self, message_id: str, call_id: str) -> None:
        """Register before publishing waiting_approval so a fast UI cannot race us."""
        active = self._runs_by_message.get(message_id)
        if active is None or active.cancel_requested:
            raise asyncio.CancelledError()
        if call_id not in active.approvals:
            active.approvals[call_id] = asyncio.get_running_loop().create_future()

    async def request_approval(self, message_id: str, call_id: str) -> bool:
        self.prepare_approval(message_id, call_id)
        active = self._runs_by_message[message_id]
        try:
            return await active.approvals[call_id]
        finally:
            active.approvals.pop(call_id, None)

    def resolve_approval(self, message_id: str, call_id: str, approved: bool) -> bool:
        active = self._runs_by_message.get(message_id)
        if active is None or active.cancel_requested:
            return False
        future = active.approvals.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    async def cancel(self, message_id: str) -> bool:
        active = self._runs_by_message.get(message_id)
        if active is None or active.task is None:
            return False
        if not active.cancel_requested:
            active.cancel_requested = True
            active.task.cancel()
        try:
            # A disconnected HTTP caller must not send a second cancellation into
            # the run's subprocess-cleanup block.
            await asyncio.shield(active.task)
        except asyncio.CancelledError:
            if not active.task.done():
                raise
        # A task cancelled before its coroutine starts never enters _wrap.
        if message_id in self._runs_by_message:
            await self._mark_cancelled(active)
            self._remove(message_id)
        return True

    async def publish(self, message_id: str, event: str, data: dict[str, Any]) -> None:
        active = self._runs_by_message.get(message_id)
        if active is None:
            return

        payload = (event, data)
        stale: list[asyncio.Queue[tuple[str, dict[str, Any]]]] = []
        for queue in active.queues:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            active.queues.discard(queue)

    async def subscribe(self, message_id: str) -> AsyncIterator[str]:
        active = self._runs_by_message.get(message_id)
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        if active is not None:
            # Register before loading the database snapshot. Otherwise a fast run
            # can publish its final events and disappear while this subscriber is
            # between the snapshot read and queue registration.
            queue = asyncio.Queue(maxsize=400)
            active.queues.add(queue)
        try:
            snapshot = self._load_snapshot(message_id)
            if snapshot is not None:
                yield format_sse("snapshot", snapshot)

            if active is None or queue is None:
                if snapshot is not None:
                    yield format_sse(
                        "done",
                        {
                            "chat_id": snapshot.get("chat_id"),
                            "message_id": message_id,
                            "status": snapshot.get("status"),
                        },
                    )
                return

            while True:
                event, data = await queue.get()
                yield format_sse(event, data)
                if event == "done":
                    break
        finally:
            if active is not None and queue is not None:
                active.queues.discard(queue)

    async def finish(self, message_id: str, data: dict[str, Any]) -> None:
        await self.publish(message_id, "done", data)
        self._remove(message_id)

    async def fail(self, message_id: str, error_payload: dict[str, Any]) -> None:
        await self.publish(message_id, "error", error_payload)
        await self.publish(message_id, "done", {"ok": False, "message_id": message_id})
        self._remove(message_id)

    async def _wrap(self, active: ActiveRun, runner: RunFactory) -> None:
        try:
            await runner()
        except asyncio.CancelledError:
            await self._mark_cancelled(active)
            raise
        finally:
            self._remove(active.message_id)

    async def _mark_cancelled(self, active: ActiveRun) -> None:
        with SessionLocal() as db:
            message = db.get(Message, active.message_id)
            if message is not None and message.status == "streaming":
                message.status = "cancelled"
                try:
                    metadata = json.loads(message.metadata_json or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                for key in ("workspaceAgent", "pythonSandbox", "plan", "filesSkill"):
                    state = metadata.get(key)
                    if isinstance(state, dict) and state.get("status") in {"running", "streaming", "waiting_approval"}:
                        state["status"] = "cancelled"
                    if key == "workspaceAgent" and isinstance(state, dict):
                        state["status"] = "cancelled"
                        for call in state.get("calls", []):
                            if isinstance(call, dict) and call.get("status") in {"running", "waiting_approval"}:
                                call["status"] = "cancelled"
                message.metadata_json = json.dumps(metadata, ensure_ascii=False)
                tasks = db.scalars(select(AnalysisTask).where(
                    AnalysisTask.chat_id == active.chat_id,
                    AnalysisTask.status.in_(["planning", "executing"]),
                )).all()
                for task in tasks:
                    task.status = "cancelled"
                    task.updated_at = now_utc()
                db.commit()
        snapshot = self._load_snapshot(active.message_id)
        if snapshot is not None:
            await self.publish(active.message_id, "snapshot", snapshot)
        await self.publish(active.message_id, "done", {
            "ok": False, "message_id": active.message_id, "chat_id": active.chat_id,
            "status": snapshot.get("status", "cancelled") if snapshot else "cancelled",
        })

    def _remove(self, message_id: str) -> None:
        active = self._runs_by_message.pop(message_id, None)
        if active is not None:
            self._message_by_chat.pop(active.chat_id, None)
            for future in active.approvals.values():
                if not future.done():
                    future.cancel()
            active.approvals.clear()

    @staticmethod
    def _load_snapshot(message_id: str) -> dict[str, Any] | None:
        with SessionLocal() as db:
            message = db.get(Message, message_id)
            if message is None:
                return None
            metadata = None
            if message.metadata_json:
                try:
                    metadata = json.loads(message.metadata_json)
                except json.JSONDecodeError:
                    metadata = None
            return {
                "message_id": message.id,
                "chat_id": message.chat_id,
                "content": message.content,
                "status": message.status,
                "type": message.message_type,
                "metadata": metadata,
            }


run_manager = RunManager()
