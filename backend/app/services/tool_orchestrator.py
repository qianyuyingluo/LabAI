from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.base import ToolCall
from app.adapters.registry import get_adapter_for_profile
from app.core.config import get_settings
from app.core.errors import PROVIDER_STREAM_ERROR, AppError
from app.db.models import Message, ModelProfile
from app.services.file_service import generated_file_payload, register_generated_file
from app.services.prompt_service import PromptService
from app.services.python_sandbox import PythonSandboxService, PythonSandboxWorkspace


EXECUTE_PYTHON_TOOL = {
    "type": "function",
    "function": {
        "name": "execute_python",
        "description": (
            "Execute Python for real data processing and generation of downloadable files. "
            "Read only listed inputs and write user-facing files under outputs/."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Complete Python source code to execute in the current sandbox workspace.",
                }
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}

TOOLS_DISABLED_PROMPT = (
    "The selected model profile has Python tool calling disabled. "
    "Never claim that code ran or that a downloadable file was created. "
    "If the user requests real calculation or file generation, tell them to enable tool calling "
    "for this model or select a compatible model."
)


@dataclass(slots=True)
class OrchestratorEvent:
    event: str
    data: dict[str, Any]


class ToolOrchestrator:
    def __init__(
        self,
        db: Session,
        *,
        profile: ModelProfile,
        assistant_message_id: str,
        chat_id: str,
        current_file_ids: list[str],
        metadata: dict[str, Any] | None = None,
        text_event: str = "delta",
    ) -> None:
        self.db = db
        self.profile = profile
        self.assistant_message_id = assistant_message_id
        self.chat_id = chat_id
        self.current_file_ids = current_file_ids
        self.metadata = dict(metadata or {})
        self.text_event = text_event
        self.usage: dict[str, Any] | None = None
        self.call_count = 0
        self.generated_files: list[dict[str, object]] = []
        self._workspace: PythonSandboxWorkspace | None = None
        self._outputs_registered = False
        self._last_execution_ok = False

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        extra_params: dict[str, Any] | None,
    ) -> AsyncIterator[OrchestratorEvent]:
        adapter = get_adapter_for_profile(self.profile)
        supports_tools = bool(getattr(self.profile, "supports_tools", True))
        if not supports_tools:
            provider_messages = _inject_system_message(messages, TOOLS_DISABLED_PROMPT)
            async for provider_event in adapter.stream_chat(
                model=self.profile.model_name or "",
                messages=provider_messages,
                extra_params=extra_params,
            ):
                if provider_event.event == "delta":
                    yield self._text_event(str(provider_event.data))
                elif provider_event.event == "usage" and isinstance(provider_event.data, dict):
                    self.usage = _merge_usage(self.usage, provider_event.data)
                    yield self._usage_event()
            return

        self._workspace = PythonSandboxService(self.db).create_workspace(
            message_id=self.assistant_message_id,
            chat_id=self.chat_id,
            current_file_ids=self.current_file_ids,
        )
        sandbox_prompt = PromptService(self.db).load_prompt("shared/python_sandbox").content
        provider_messages = _inject_system_message(
            messages,
            "\n\n".join([sandbox_prompt.strip(), self._workspace.prompt_context]),
        )

        try:
            while True:
                calls_by_index: dict[int, dict[str, str]] = {}
                turn_content = ""
                finish_reason: str | None = None
                async for provider_event in adapter.stream_chat(
                    model=self.profile.model_name or "",
                    messages=provider_messages,
                    extra_params=extra_params,
                    tools=[EXECUTE_PYTHON_TOOL],
                    tool_choice="auto",
                ):
                    if provider_event.event == "delta":
                        text = str(provider_event.data)
                        turn_content += text
                        yield self._text_event(text)
                    elif provider_event.event == "usage" and isinstance(provider_event.data, dict):
                        self.usage = _merge_usage(self.usage, provider_event.data)
                        yield self._usage_event()
                    elif provider_event.event == "tool_call_delta" and isinstance(provider_event.data, dict):
                        _merge_tool_call_delta(calls_by_index, provider_event.data)
                    elif provider_event.event == "finish_reason":
                        finish_reason = str(provider_event.data)

                tool_calls = _materialize_tool_calls(calls_by_index, self.call_count)
                if not tool_calls:
                    if finish_reason == "tool_calls":
                        raise AppError(
                            PROVIDER_STREAM_ERROR,
                            "Model returned an incomplete tool call.",
                            status_code=502,
                        )
                    if self.call_count:
                        self._register_outputs()
                        final_status = "done" if self._last_execution_ok else "error"
                        error = None if self._last_execution_ok else "The final Python execution failed."
                        self._set_sandbox_status(final_status, error=error)
                        yield self._sandbox_status_event()
                        if self.generated_files:
                            yield self._generated_files_event()
                    return

                provider_messages.append(
                    {
                        "role": "assistant",
                        "content": turn_content or None,
                        "tool_calls": [call.to_openai_dict() for call in tool_calls],
                    }
                )
                for call in tool_calls:
                    self.call_count += 1
                    if self.call_count > get_settings().sandbox_max_tool_calls:
                        raise AppError(
                            "SANDBOX_TOOL_LIMIT",
                            f"Python sandbox exceeded {get_settings().sandbox_max_tool_calls} tool calls.",
                            status_code=400,
                        )
                    self._set_sandbox_status("running")
                    yield self._sandbox_status_event()
                    result = await self._execute_call(call)
                    self._last_execution_ok = bool(result.get("ok"))
                    error = _tool_error_message(result)
                    self._set_sandbox_status("running", error=error)
                    yield self._sandbox_status_event()
                    provider_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
        except Exception as exc:
            if self.call_count:
                try:
                    self._register_outputs()
                except Exception:
                    pass
                self._set_sandbox_status("error", error=_exception_message(exc))
                # Metadata is committed before these full-state events. Connected
                # clients update immediately, while reconnecting clients recover
                # the same values from the snapshot.
                yield self._sandbox_status_event()
                if self.generated_files:
                    yield self._generated_files_event()
            raise
        finally:
            if self._workspace is not None:
                self._workspace.cleanup()

    async def _execute_call(self, call: ToolCall) -> dict[str, object]:
        if call.name != "execute_python":
            return {
                "ok": False,
                "error": f"Unknown tool '{call.name}'. Only execute_python is available.",
            }
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Tool arguments are not valid JSON: {exc.msg}"}
        if not isinstance(arguments, dict) or not isinstance(arguments.get("code"), str):
            return {"ok": False, "error": "execute_python requires a string 'code' argument."}
        if self._workspace is None:
            return {"ok": False, "error": "Python sandbox workspace is unavailable."}
        try:
            result = await self._workspace.execute(arguments["code"])
            return result.to_tool_payload()
        except AppError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "details": exc.details}
        except Exception as exc:
            return {
                "ok": False,
                "error": "Unexpected sandbox execution failure.",
                "details": {"type": exc.__class__.__name__},
            }

    def _register_outputs(self) -> None:
        if self._outputs_registered or self._workspace is None:
            return
        outputs = self._workspace.collect_outputs()
        registered_paths: list[Path] = []
        pending_files: list[dict[str, object]] = []
        previous_count = len(self.generated_files)
        previous_metadata_files = self.metadata.get("generatedFiles")
        try:
            for output in outputs:
                uploaded = register_generated_file(
                    self.db,
                    output.path,
                    original_filename=output.name,
                )
                registered_paths.append(Path(uploaded.local_path))
                pending_files.append(generated_file_payload(uploaded))
            self.generated_files.extend(pending_files)
            if self.generated_files:
                self.metadata["generatedFiles"] = list(self.generated_files)
            self._persist_metadata()
            self.db.commit()
            self._outputs_registered = True
        except Exception:
            self.db.rollback()
            del self.generated_files[previous_count:]
            if previous_metadata_files is None:
                self.metadata.pop("generatedFiles", None)
            else:
                self.metadata["generatedFiles"] = previous_metadata_files
            for path in registered_paths:
                path.unlink(missing_ok=True)
            raise

    def _set_sandbox_status(self, status: str, *, error: str | None = None) -> None:
        value: dict[str, object] = {
            "status": status,
            "callCount": self.call_count,
        }
        if error:
            value["error"] = error
        self.metadata["pythonSandbox"] = value
        self._persist_metadata()
        self.db.commit()

    def _persist_metadata(self) -> None:
        message = self.db.get(Message, self.assistant_message_id)
        if message is not None:
            message.metadata_json = json.dumps(self.metadata, ensure_ascii=False)

    def _text_event(self, text: str) -> OrchestratorEvent:
        return OrchestratorEvent(
            self.text_event,
            {"text": text, "message_id": self.assistant_message_id, "chat_id": self.chat_id},
        )

    def _usage_event(self) -> OrchestratorEvent:
        return OrchestratorEvent(
            "usage",
            {"usage": self.usage, "message_id": self.assistant_message_id, "chat_id": self.chat_id},
        )

    def _sandbox_status_event(self) -> OrchestratorEvent:
        return OrchestratorEvent(
            "python_sandbox_status",
            {
                "pythonSandbox": self.metadata.get("pythonSandbox"),
                "message_id": self.assistant_message_id,
                "chat_id": self.chat_id,
            },
        )

    def _generated_files_event(self) -> OrchestratorEvent:
        return OrchestratorEvent(
            "generated_files",
            {
                "generatedFiles": self.generated_files,
                "message_id": self.assistant_message_id,
                "chat_id": self.chat_id,
            },
        )


def _inject_system_message(messages: list[dict[str, Any]], content: str) -> list[dict[str, Any]]:
    result = list(messages)
    index = 0
    while index < len(result) and result[index].get("role") == "system":
        index += 1
    result.insert(index, {"role": "system", "content": content})
    return result


def _merge_tool_call_delta(calls: dict[int, dict[str, str]], data: dict[str, Any]) -> None:
    try:
        index = int(data.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    current = calls.setdefault(index, {"id": "", "name": "", "arguments": "", "type": "function"})
    for key in ("id", "name", "type"):
        value = data.get(key)
        if isinstance(value, str) and value:
            current[key] = _merge_text_fragment(current[key], value)
    arguments = data.get("arguments")
    if isinstance(arguments, str):
        current["arguments"] += arguments


def _merge_text_fragment(current: str, fragment: str) -> str:
    """Merge provider fragments while tolerating repeated or cumulative chunks."""
    if not current:
        return fragment
    if fragment == current or current.startswith(fragment):
        return current
    if fragment.startswith(current):
        return fragment
    max_overlap = min(len(current), len(fragment))
    for overlap in range(max_overlap, 0, -1):
        if current[-overlap:] == fragment[:overlap]:
            return current + fragment[overlap:]
    return current + fragment


def _materialize_tool_calls(calls: dict[int, dict[str, str]], offset: int) -> list[ToolCall]:
    result: list[ToolCall] = []
    for index in sorted(calls):
        value = calls[index]
        result.append(
            ToolCall(
                id=value["id"] or f"call_{offset + index + 1}",
                name=value["name"],
                arguments=value["arguments"],
                type=value["type"] or "function",
            )
        )
    return result


def _merge_usage(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current or {})
    for key, value in incoming.items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key, 0), (int, float)):
            merged[key] = merged.get(key, 0) + value
        else:
            merged[key] = value
    return merged


def _tool_error_message(result: dict[str, object]) -> str | None:
    if result.get("ok") is True:
        return None
    for key in ("error", "policy_error", "stderr"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:1000]
    if result.get("timed_out"):
        return "Python execution timed out."
    return "Python execution failed."


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.message
    return f"Unexpected sandbox failure ({exc.__class__.__name__})."
