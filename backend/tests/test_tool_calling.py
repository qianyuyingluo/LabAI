import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIStatusError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.adapters.base import AdapterResponse, ToolCall
from app.adapters.openai_compat import OpenAICompatAdapter
from app.api.models import ModelProfileResponse, ModelProfileUpsertRequest
from app.core.errors import MODEL_TOOL_CALLING_UNSUPPORTED, AppError
from app.db import session as db_session
from app.db.models import Base
from app.services.model_service import create_model_profile, test_model_profile, update_model_profile


def _tool_definition(name: str = "python_sandbox") -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Run a test tool.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class FakeCompletions:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def __aiter__(self) -> "FakeStream":
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def close(self) -> None:
        self.closed = True


def _adapter_with_completions(
    completions: FakeCompletions,
    *,
    supports_tools: bool = True,
) -> OpenAICompatAdapter:
    adapter = OpenAICompatAdapter(api_key="test-key", supports_tools=supports_tools)
    adapter.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return adapter


class OpenAICompatToolCallingTests(unittest.TestCase):
    def test_non_stream_tool_calls_and_reserved_params(self) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call-1",
                                type="function",
                                function=SimpleNamespace(
                                    name="python_sandbox",
                                    arguments='{"code":"print(1)"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 12}),
        )
        completions = FakeCompletions(response)
        adapter = _adapter_with_completions(completions)
        tools = [_tool_definition()]
        tool_choice = {"type": "function", "function": {"name": "python_sandbox"}}

        result = asyncio.run(
            adapter.chat(
                model="model-a",
                messages=[{"role": "user", "content": "run"}],
                tools=tools,
                tool_choice=tool_choice,
                extra_params={
                    "model": "wrong-model",
                    "messages": [],
                    "stream": True,
                    "tools": [_tool_definition("wrong_tool")],
                    "tool_choice": "none",
                    "temperature": 0.25,
                },
            )
        )

        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(result.usage, {"total_tokens": 12})
        self.assertEqual(
            result.tool_calls,
            [ToolCall(id="call-1", name="python_sandbox", arguments='{"code":"print(1)"}')],
        )
        request = completions.calls[0]
        self.assertEqual(request["model"], "model-a")
        self.assertEqual(request["stream"], False)
        self.assertIs(request["tools"], tools)
        self.assertIs(request["tool_choice"], tool_choice)
        self.assertEqual(request["temperature"], 0.25)

    def test_stream_emits_tool_call_fragments_and_finish_reason(self) -> None:
        chunks = [
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    type="function",
                                    function=SimpleNamespace(
                                        name="python_",
                                        arguments='{"co',
                                    ),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ],
            ),
            SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    type=None,
                                    function=SimpleNamespace(
                                        name="sandbox",
                                        arguments='de":"x"}',
                                    ),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
            ),
            SimpleNamespace(
                usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 8}),
                choices=[],
            ),
        ]
        stream = FakeStream(chunks)
        completions = FakeCompletions(stream)
        adapter = _adapter_with_completions(completions)

        async def collect() -> list[tuple[str, object]]:
            return [
                (event.event, event.data)
                async for event in adapter.stream_chat(
                    model="model-a",
                    messages=[{"role": "user", "content": "run"}],
                    tools=[_tool_definition()],
                    extra_params={"stream": False, "tools": [], "temperature": 0.1},
                )
            ]

        events = asyncio.run(collect())

        self.assertEqual(
            events,
            [
                (
                    "tool_call_delta",
                    {
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "name": "python_",
                        "arguments": '{"co',
                    },
                ),
                (
                    "tool_call_delta",
                    {"index": 0, "name": "sandbox", "arguments": 'de":"x"}'},
                ),
                ("finish_reason", "tool_calls"),
                ("usage", {"total_tokens": 8}),
            ],
        )
        self.assertTrue(stream.closed)
        request = completions.calls[0]
        self.assertEqual(request["stream"], True)
        self.assertNotEqual(request["tools"], [])
        self.assertEqual(request["temperature"], 0.1)

    def test_profile_can_disable_tools_before_provider_call(self) -> None:
        completions = FakeCompletions()
        adapter = _adapter_with_completions(completions, supports_tools=False)

        with self.assertRaises(AppError) as raised:
            asyncio.run(
                adapter.chat(
                    model="model-a",
                    messages=[],
                    tools=[_tool_definition()],
                )
            )

        self.assertEqual(raised.exception.code, MODEL_TOOL_CALLING_UNSUPPORTED)
        self.assertEqual(completions.calls, [])

    def test_provider_tool_rejection_has_specific_error_code(self) -> None:
        request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
        response = httpx.Response(400, request=request)
        provider_error = APIStatusError(
            "tools are not supported",
            response=response,
            body={"error": {"message": "tools are not supported"}},
        )
        adapter = _adapter_with_completions(FakeCompletions(error=provider_error))

        with self.assertRaises(AppError) as raised:
            asyncio.run(
                adapter.chat(
                    model="model-a",
                    messages=[],
                    tools=[_tool_definition()],
                )
            )

        self.assertEqual(raised.exception.code, MODEL_TOOL_CALLING_UNSUPPORTED)
        self.assertEqual(raised.exception.details["status_code"], 400)


class ModelToolCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_profile_crud_and_api_default_support_tools(self) -> None:
        request = ModelProfileUpsertRequest(name="test")
        self.assertTrue(request.supports_tools)

        profile = create_model_profile(
            self.db,
            name="test",
            base_url=None,
            model_name="model-a",
            api_key="secret",
            system_prompt=None,
            supports_stream=True,
            supports_vision=False,
        )
        self.assertTrue(profile.supports_tools)
        self.assertTrue(ModelProfileResponse.from_profile(profile).supports_tools)

        updated = update_model_profile(self.db, profile.id, {"supports_tools": False})
        self.assertFalse(updated.supports_tools)

    def test_model_test_probes_tools_when_enabled(self) -> None:
        profile = create_model_profile(
            self.db,
            name="test",
            base_url=None,
            model_name="model-a",
            api_key="secret",
            system_prompt=None,
            supports_stream=True,
            supports_vision=False,
            supports_tools=True,
        )

        class ProbeAdapter:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] | None = None

            async def chat(self, **kwargs: object) -> AdapterResponse:
                self.kwargs = kwargs
                return AdapterResponse(
                    content="",
                    tool_calls=[ToolCall("probe-1", "labai_capability_probe", "{}")],
                    finish_reason="tool_calls",
                )

        adapter = ProbeAdapter()
        with patch("app.services.model_service.get_adapter_for_profile", return_value=adapter):
            result = asyncio.run(test_model_profile(self.db, model_profile_id=profile.id))

        self.assertTrue(result["tool_calling_supported"])
        self.assertEqual(result["response"], "Tool calling probe succeeded.")
        self.assertEqual(result["tool_calls"][0]["name"], "labai_capability_probe")
        self.assertEqual(adapter.kwargs["tool_choice"]["function"]["name"], "labai_capability_probe")

    def test_model_test_keeps_text_probe_when_tools_disabled(self) -> None:
        profile = create_model_profile(
            self.db,
            name="test",
            base_url=None,
            model_name="model-a",
            api_key="secret",
            system_prompt=None,
            supports_stream=True,
            supports_vision=False,
            supports_tools=False,
        )

        class TextAdapter:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] | None = None

            async def chat(self, **kwargs: object) -> AdapterResponse:
                self.kwargs = kwargs
                return AdapterResponse(content="OK", finish_reason="stop")

        adapter = TextAdapter()
        with patch("app.services.model_service.get_adapter_for_profile", return_value=adapter):
            result = asyncio.run(test_model_profile(self.db, model_profile_id=profile.id))

        self.assertFalse(result["tool_calling_supported"])
        self.assertEqual(result["response"], "OK")
        self.assertNotIn("tools", adapter.kwargs)

    def test_sqlite_compatibility_adds_supports_tools_true(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE model_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO model_profiles (id, name) VALUES ('existing', 'Existing')"
            )

        with (
            patch.object(db_session, "engine", engine),
            patch.object(
                db_session,
                "settings",
                SimpleNamespace(database_url="sqlite:///:memory:"),
            ),
        ):
            db_session.ensure_schema_compatibility()

        with engine.connect() as connection:
            columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(model_profiles)")
            }
            value = connection.exec_driver_sql(
                "SELECT supports_tools FROM model_profiles WHERE id = 'existing'"
            ).scalar_one()

        self.assertIn("supports_tools", columns)
        self.assertEqual(value, 1)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
