from collections.abc import AsyncIterator
from typing import Any

from openai import APIStatusError, AuthenticationError, AsyncOpenAI, PermissionDeniedError

from app.adapters.base import (
    AdapterResponse,
    AdapterStreamEvent,
    ModelAdapter,
    OpenAIMessage,
    OpenAITool,
    ToolCall,
)
from app.core.errors import (
    MODEL_AUTH_FAILED,
    MODEL_NOT_CONFIGURED,
    MODEL_TOOL_CALLING_UNSUPPORTED,
    PROVIDER_ERROR,
    PROVIDER_STREAM_ERROR,
    AppError,
)


RESERVED_EXTRA_PARAMS = frozenset({"model", "messages", "stream", "tools", "tool_choice"})


class OpenAICompatAdapter(ModelAdapter):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None = None,
        supports_tools: bool = True,
    ) -> None:
        if not api_key:
            raise AppError(
                MODEL_NOT_CONFIGURED,
                "Model API key is not configured. Set OPENAI_COMPAT_API_KEY.",
                status_code=400,
            )

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**client_kwargs)
        self.supports_tools = supports_tools

    async def chat(
        self,
        *,
        model: str,
        messages: list[OpenAIMessage],
        extra_params: dict[str, Any] | None = None,
        tools: list[OpenAITool] | None = None,
        tool_choice: Any | None = None,
    ) -> AdapterResponse:
        if not model:
            raise AppError(
                MODEL_NOT_CONFIGURED,
                "Model name is not configured. Set OPENAI_COMPAT_DEFAULT_MODEL.",
                status_code=400,
            )

        tools_requested = bool(tools) or tool_choice is not None
        self._ensure_tool_support(tools_requested)
        params = _safe_extra_params(extra_params)
        if tools is not None:
            params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = tool_choice

        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
                **params,
            )
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise _auth_error(exc) from exc
        except APIStatusError as exc:
            if tools_requested and _is_tool_calling_rejection(exc):
                raise _tool_calling_unsupported_error(exc, stream=False) from exc
            raise AppError(
                PROVIDER_ERROR,
                "Model provider rejected the chat request.",
                status_code=502,
                details={"status_code": exc.status_code},
            ) from exc
        except Exception as exc:
            raise AppError(
                PROVIDER_ERROR,
                "Model provider request failed.",
                status_code=502,
                details={"type": exc.__class__.__name__},
            ) from exc

        choice = response.choices[0] if response.choices else None
        message = choice.message if choice is not None else None
        content = getattr(message, "content", None) if message is not None else ""
        tool_calls = [
            _parse_tool_call(tool_call)
            for tool_call in (getattr(message, "tool_calls", None) or [])
        ]
        finish_reason = getattr(choice, "finish_reason", None) if choice is not None else None
        usage = response.usage.model_dump() if response.usage else None
        return AdapterResponse(
            content=content if isinstance(content, str) else "",
            usage=usage,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    async def stream_chat(
        self,
        *,
        model: str,
        messages: list[OpenAIMessage],
        extra_params: dict[str, Any] | None = None,
        tools: list[OpenAITool] | None = None,
        tool_choice: Any | None = None,
    ) -> AsyncIterator[AdapterStreamEvent]:
        if not model:
            raise AppError(
                MODEL_NOT_CONFIGURED,
                "Model name is not configured. Set OPENAI_COMPAT_DEFAULT_MODEL.",
                status_code=400,
            )

        tools_requested = bool(tools) or tool_choice is not None
        self._ensure_tool_support(tools_requested)
        stream = None
        params = _safe_extra_params(extra_params)
        if tools is not None:
            params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = tool_choice

        try:
            stream = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **params,
            )
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    yield AdapterStreamEvent("usage", usage.model_dump())

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    yield AdapterStreamEvent("delta", text)

                for tool_call_delta in getattr(delta, "tool_calls", None) or []:
                    yield AdapterStreamEvent(
                        "tool_call_delta",
                        _parse_tool_call_delta(tool_call_delta),
                    )

                finish_reason = getattr(chunk.choices[0], "finish_reason", None)
                if finish_reason is not None:
                    yield AdapterStreamEvent("finish_reason", str(finish_reason))
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise _auth_error(exc) from exc
        except APIStatusError as exc:
            if tools_requested and _is_tool_calling_rejection(exc):
                raise _tool_calling_unsupported_error(exc, stream=True) from exc
            raise AppError(
                PROVIDER_STREAM_ERROR,
                "Model provider rejected the stream request.",
                status_code=502,
                details={"status_code": exc.status_code},
            ) from exc
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                PROVIDER_STREAM_ERROR,
                "Model provider stream failed.",
                status_code=502,
                details={"type": exc.__class__.__name__},
            ) from exc
        finally:
            if stream is not None and hasattr(stream, "close"):
                await stream.close()

    def _ensure_tool_support(self, tools_requested: bool) -> None:
        if not tools_requested or self.supports_tools:
            return
        raise AppError(
            MODEL_TOOL_CALLING_UNSUPPORTED,
            "This model profile does not support OpenAI-compatible tool calling.",
            status_code=400,
            details={"supports_tools": False},
        )


def _safe_extra_params(extra_params: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: value
        for key, value in (extra_params or {}).items()
        if key not in RESERVED_EXTRA_PARAMS
    }


def _parse_tool_call(tool_call: Any) -> ToolCall:
    function = _read_value(tool_call, "function")
    return ToolCall(
        id=str(_read_value(tool_call, "id") or ""),
        type=str(_read_value(tool_call, "type") or "function"),
        name=str(_read_value(function, "name") or ""),
        arguments=str(_read_value(function, "arguments") or ""),
    )


def _parse_tool_call_delta(tool_call: Any) -> dict[str, Any]:
    function = _read_value(tool_call, "function")
    payload: dict[str, Any] = {
        "index": int(_read_value(tool_call, "index") or 0),
    }
    call_id = _read_value(tool_call, "id")
    call_type = _read_value(tool_call, "type")
    name = _read_value(function, "name")
    arguments = _read_value(function, "arguments")
    if call_id is not None:
        payload["id"] = str(call_id)
    if call_type is not None:
        payload["type"] = str(call_type)
    if name is not None:
        payload["name"] = str(name)
    if arguments is not None:
        payload["arguments"] = str(arguments)
    return payload


def _read_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _is_tool_calling_rejection(exc: APIStatusError) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {400, 422, 501}:
        return True
    body = getattr(exc, "body", None)
    message = f"{exc} {body}".lower()
    return "tool" in message or "function call" in message or "function_call" in message


def _tool_calling_unsupported_error(exc: APIStatusError, *, stream: bool) -> AppError:
    return AppError(
        MODEL_TOOL_CALLING_UNSUPPORTED,
        "Model provider rejected OpenAI-compatible tool calling for this model.",
        status_code=400,
        details={
            "status_code": getattr(exc, "status_code", None),
            "stream": stream,
        },
    )


def _auth_error(exc: Exception) -> AppError:
    return AppError(
        MODEL_AUTH_FAILED,
        "Model provider authentication failed. Check the API key environment variable.",
        status_code=401,
        details={"type": exc.__class__.__name__},
    )
