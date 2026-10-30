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
from app.db.models import Chat, Message, ModelProfile
from app.services.document_tools import DOCUMENT_TOOLS, DOCUMENT_TOOLS_PROMPT, execute_document_tool
from app.services.file_service import generated_file_payload, register_generated_file
from app.services.prompt_service import PromptService
from app.services.python_sandbox import SANDBOX_NOT_READY, PythonSandboxService, PythonSandboxWorkspace


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
    "The selected model profile has tool calling disabled. "
    "You cannot search or read any file content omitted from the provided context. "
    "State that limitation when the supplied evidence is insufficient, and never claim to have read the whole file. "
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
        resume_state: dict[str, Any] | None = None,
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
        self.document_call_count = 0
        self.generated_files: list[dict[str, object]] = []
        self._workspace: PythonSandboxWorkspace | None = None
        self._outputs_registered = False
        self._last_execution_ok = False
        self.resume_state = resume_state

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        extra_params: dict[str, Any] | None,
    ) -> AsyncIterator[OrchestratorEvent]:
        adapter = get_adapter_for_profile(self.profile)
        supports_tools = bool(getattr(self.profile, "supports_tools", True))
        chat = self.db.get(Chat, self.chat_id)
        if chat is not None and getattr(chat, "workspace_path", None):
            if not supports_tools:
                raise AppError("MODEL_TOOL_CALLING_UNSUPPORTED", "Working in a folder requires a model with tool calling enabled.", status_code=400)
            from app.services.agent_runtime import WorkspaceAgentRuntime
            runtime = WorkspaceAgentRuntime(self, chat, resume=self.resume_state)
            async for event in runtime.stream(messages=messages, extra_params=extra_params, adapter=adapter):
                yield event
            return
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

        available_tools = list(DOCUMENT_TOOLS)
        tool_prompts = [DOCUMENT_TOOLS_PROMPT]
        try:
            self._workspace = PythonSandboxService(self.db).create_workspace(
                message_id=self.assistant_message_id,
                chat_id=self.chat_id,
                current_file_ids=self.current_file_ids,
            )
        except AppError as exc:
            if exc.code != SANDBOX_NOT_READY:
                raise
            tool_prompts.append(
                "The Python sandbox is unavailable. You can still search and read document evidence. "
                "Do not claim to execute calculations or create files. For those tasks, ask the user "
                "to start LabAI with npm start to configure the sandbox."
            )
        if self._workspace is not None:
            sandbox_prompt = PromptService(self.db).load_prompt("shared/python_sandbox").content
            tool_prompts.extend([sandbox_prompt.strip(), self._workspace.prompt_context])
            available_tools.append(EXECUTE_PYTHON_TOOL)
        provider_messages = _inject_system_message(
            messages,
            "\n\n".join(tool_prompts),
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
                    tools=available_tools,
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

                tool_calls = _materialize_tool_calls(calls_by_index, self.call_count + self.document_call_count)
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
                    if call.name == "execute_python":
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
                    else:
                        # Invalid tool names also consume this budget, so a provider
                        # cannot retry an unknown function indefinitely.
                        self.document_call_count += 1
                        limit = get_settings().document_max_tool_calls
                        if self.document_call_count > limit:
                            raise AppError(
                                "DOCUMENT_TOOL_LIMIT",
                                f"Document reading exceeded {limit} tool calls. Narrow the request or continue in a new turn.",
                                status_code=400,
                            )
                        result = await self._execute_document_call(call)
                        self._record_document_call(call, result)
                        yield OrchestratorEvent(
                            "document_tools_status",
                            {
                                "documentTools": self.metadata["documentTools"],
                                "message_id": self.assistant_message_id,
                                "chat_id": self.chat_id,
                            },
                        )
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
                # Exhausting document reads does not turn a completed Python
                # calculation into a failed one.
                document_limit = isinstance(exc, AppError) and exc.code == "DOCUMENT_TOOL_LIMIT"
                if document_limit and self._last_execution_ok:
                    self._set_sandbox_status("done")
                else:
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

    async def _execute_document_call(self, call: ToolCall) -> dict[str, object]:
        if call.name not in {"read_file", "search_file"}:
            return {"ok": False, "error": f"Unknown tool '{call.name}'."}
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Tool arguments are not valid JSON: {exc.msg}"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "Document tool arguments must be an object."}
        try:
            return await execute_document_tool(
                self.db,
                chat_id=self.chat_id,
                current_file_ids=self.current_file_ids,
                name=call.name,
                arguments=arguments,
            )
        except AppError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {
                "ok": False,
                "error": "Document reading failed.",
                "details": {"type": exc.__class__.__name__},
            }

    def _record_document_call(self, call: ToolCall, result: dict[str, object]) -> None:
        state = self.metadata.setdefault("documentTools", {"calls": []})
        if not isinstance(state, dict):
            state = {"calls": []}
            self.metadata["documentTools"] = state
        calls = state.setdefault("calls", [])
        if not isinstance(calls, list):
            calls = []
            state["calls"] = calls
        record: dict[str, object] = {"tool": call.name, "ok": result.get("ok") is True}
        field_names = {
            "file_id": "fileId", "fileId": "fileId", "filename": "filename",
            "parse_status": "parseStatus", "parseStatus": "parseStatus",
            "truncated": "truncated", "next_block": "nextBlock", "nextBlock": "nextBlock",
            "next_offset": "nextOffset", "nextOffset": "nextOffset", "error": "error",
        }
        for key, destination in field_names.items():
            if key in result:
                record[destination] = result[key]
        blocks = result.get("blocks", [])
        if isinstance(blocks, list):
            record["blockIds"] = [
                block.get("block_id") or block.get("id")
                for block in blocks
                if isinstance(block, dict) and (block.get("block_id") or block.get("id"))
            ]
        calls.append(record)
        state["callCount"] = self.document_call_count
        self._persist_metadata()
        self.db.commit()

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
