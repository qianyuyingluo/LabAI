import asyncio
import unittest
from unittest.mock import patch

from app.services.run_manager import ActiveRun, RunManager


class RunManagerTests(unittest.TestCase):
    def test_subscriber_registers_before_loading_snapshot(self) -> None:
        async def run_case() -> list[str]:
            manager = RunManager()
            active = ActiveRun(message_id="message-1", chat_id="chat-1")
            manager._runs_by_message[active.message_id] = active
            manager._message_by_chat[active.chat_id] = active.message_id

            def load_snapshot(_: str) -> dict[str, object]:
                self.assertEqual(len(active.queues), 1)
                queue = next(iter(active.queues))
                queue.put_nowait(("done", {"message_id": active.message_id, "ok": True}))
                return {
                    "message_id": active.message_id,
                    "chat_id": active.chat_id,
                    "content": "complete",
                    "status": "done",
                    "type": "reply",
                    "metadata": None,
                }

            with patch.object(manager, "_load_snapshot", side_effect=load_snapshot):
                return [event async for event in manager.subscribe(active.message_id)]

        events = asyncio.run(run_case())
        self.assertIn("event: snapshot", events[0])
        self.assertIn("event: done", events[1])


if __name__ == "__main__":
    unittest.main()
