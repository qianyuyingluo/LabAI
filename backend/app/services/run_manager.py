import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.db.models import Message
from app.db.session import SessionLocal
from app.services.chat_service import format_sse


RunFactory = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class ActiveRun:
    message_id: str
    chat_id: str
    queues: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


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
        finally:
            self._remove(active.message_id)

    def _remove(self, message_id: str) -> None:
        active = self._runs_by_message.pop(message_id, None)
        if active is not None:
            self._message_by_chat.pop(active.chat_id, None)

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
