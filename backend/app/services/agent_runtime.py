"""Persistent local workspace agent built around the existing model adapters."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Chat, Message
from app.services.agent_checkpoint import save_checkpoint
from app.services.agent_context import compact_messages
from app.services.document_tools import DOCUMENT_TOOLS, DOCUMENT_TOOLS_PROMPT, execute_document_tool
from app.services.run_manager import run_manager
from app.services.workspace_tools import WORKSPACE_TOOLS, WORKSPACE_TOOLS_PROMPT, WorkspaceTools, workspace_context


AGENT_PROMPT = """You are LabAI, a local workspace agent. Complete the user's task using the available tools.
Inspect the workspace before editing. Honor applicable AGENTS.md instructions (read nested instructions before
editing their directory), preserve unrelated user changes, and use small, verifiable edits. Use workspace_read
and workspace_edit for source changes; run_command is a real host shell whose current directory is the workspace.
Commands are not an OS sandbox. Never change files outside the bound folder unless the user explicitly requests it.
Do not install dependencies, delete data, contact external services or run privileged commands without a task need.
Use the user's language. Give short progress updates and continue working after tool results or recoverable errors.
Before finishing, verify changed files with relevant tests or by reading the result; report what was actually tested,
what failed, and any remaining work. A tool error is not success. Never invent command output or claim unexecuted work.
If an action is denied, respect that decision; do not recreate the same side effect through a different tool or command.
For files produced in this workspace, report the actual relative path; do not invent attachment/download URLs.
Prefer the workspace tools and run_command over any old prompt referring to a temporary Python sandbox.
"""
MUTATING_TOOLS = {"workspace_write", "workspace_edit", "run_command"}


class WorkspaceAgentRuntime:
    def __init__(self, orchestrator, chat: Chat, *, resume: dict[str, Any] | None = None):
        self.owner = orchestrator
        self.db = orchestrator.db
        self.chat = chat
        self.tools = WorkspaceTools(chat.workspace_path, run_id=orchestrator.assistant_message_id)
        self.resume = resume
        self.state: dict[str, Any] = {}
        self.messages: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.content = ""

    def _save(self, status: str) -> None:
        save_checkpoint(self.owner.assistant_message_id, {
            "version": 1, "status": status, "chat_id": self.owner.chat_id,
            "message_id": self.owner.assistant_message_id, "model_profile_id": self.owner.profile.id,
            "workspace_path": str(self.tools.root), "workspace_mode": self.chat.workspace_mode,
            "messages": self.messages, "pending": self.pending, "content": self.content,
            "metadata": self.owner.metadata, "usage": self.owner.usage,
            "current_file_ids": self.owner.current_file_ids, "extra_params": self.extra_params,
        })
        self.owner._persist_metadata()
        message = self.db.get(Message, self.owner.assistant_message_id)
        if message is not None:
            message.content = self.content
        self.db.commit()

    def _event(self):
        from app.services.tool_orchestrator import OrchestratorEvent
        return OrchestratorEvent("workspace_agent_status", {
            "workspaceAgent": self.state, "message_id": self.owner.assistant_message_id,
            "chat_id": self.owner.chat_id,
        })

    async def stream(self, *, messages, extra_params, adapter):
        from app.services.tool_orchestrator import _merge_tool_call_delta, _materialize_tool_calls, _merge_usage
        self.extra_params = extra_params
        if self.resume:
            self.messages = self.resume["messages"]
            self.pending = self.resume.get("pending", [])
            self.content = self.resume.get("content", "")
            self.owner.metadata = self.resume.get("metadata", {})
            self.owner.usage = self.resume.get("usage")
            self.state = self.owner.metadata["workspaceAgent"]
            # A process may have died after an external side effect but before the
            # checkpoint. Feed an uncertain result back instead of replaying it.
            uncertain_batch = any(p.get("phase") == "executing" for p in self.pending)
            for pending in self.pending:
                if pending.get("phase") == "executing":
                    pending["uncertain"] = True
                elif uncertain_batch:
                    pending["dependency_interrupted"] = True
        else:
            self.state = {"workspacePath": str(self.tools.root), "status": "running", "calls": []}
            self.owner.metadata["workspaceAgent"] = self.state
            self.messages = [{"role": "system", "content": "\n\n".join([
                AGENT_PROMPT, WORKSPACE_TOOLS_PROMPT, DOCUMENT_TOOLS_PROMPT,
                workspace_context(self.tools.root),
            ])}] + messages
        self.state["status"] = "running"
        self._save("running")
        yield self._event()
        available_tools = [*WORKSPACE_TOOLS, *DOCUMENT_TOOLS]
        workspace_names = {tool["function"]["name"] for tool in WORKSPACE_TOOLS}
        calls_this_attempt = 0
        try:
            while True:
                while self.pending:
                    calls_this_attempt += 1
                    if calls_this_attempt > get_settings().agent_max_tool_calls:
                        raise AppError("AGENT_TOOL_LIMIT", "Tool budget reached. Continue from the saved checkpoint.", status_code=400)
                    pending = self.pending[0]
                    call = pending["call"]
                    name = call["function"]["name"]
                    record = next((r for r in self.state["calls"] if r["id"] == call["id"]), None)
                    if record is None:
                        try:
                            arguments = json.loads(call["function"]["arguments"] or "{}")
                            if not isinstance(arguments, dict):
                                raise ValueError("Arguments must be an object")
                        except (ValueError, TypeError) as exc:
                            arguments = {}
                            pending["argument_error"] = str(exc)
                        record = {"id": call["id"], "tool": name, "arguments": arguments, "status": "running"}
                        self.state["calls"].append(record)
                    arguments = record["arguments"]
                    result = None
                    if pending.get("uncertain"):
                        result = {"ok": False, "interrupted": True, "error":
                                  "Execution was interrupted. Its outcome is unknown; this action was NOT replayed. "
                                  "Inspect files/process state before deciding the next action."}
                    elif pending.get("dependency_interrupted"):
                        result = {"ok": False, "not_executed": True, "error":
                                  "This queued action was not executed because an earlier action in the batch has an unknown outcome. "
                                  "Inspect state and re-plan before requesting further actions."}
                    elif pending.get("argument_error"):
                        result = {"ok": False, "error": pending["argument_error"]}
                    elif name in MUTATING_TOOLS and self.chat.workspace_mode != "auto":
                        record["status"] = "waiting_approval"
                        self.state["status"] = "waiting_approval"
                        pending["phase"] = "waiting_approval"
                        run_manager.prepare_approval(self.owner.assistant_message_id, call["id"])
                        self._save("waiting_approval")
                        yield self._event()
                        approved = await run_manager.request_approval(self.owner.assistant_message_id, call["id"])
                        if not approved:
                            record["status"] = "denied"
                            result = {"ok": False, "denied": True, "error": "The user denied this action. Respect their decision."}
                    if result is None:
                        record["status"] = "running"
                        self.state["status"] = "running"
                        pending["phase"] = "executing"
                        self._save("running")
                        yield self._event()
                        try:
                            if name in workspace_names:
                                async def on_output(snapshot):
                                    record["result"] = snapshot
                                    self.owner._persist_metadata()
                                    self.db.commit()
                                    event = self._event()
                                    await run_manager.publish(self.owner.assistant_message_id, event.event, event.data)
                                self.tools.on_output = on_output
                                result = await self.tools.execute(name, arguments)
                            elif name in {"read_file", "search_file"}:
                                result = await execute_document_tool(self.db, chat_id=self.owner.chat_id,
                                    current_file_ids=self.owner.current_file_ids, name=name, arguments=arguments)
                            else:
                                result = {"ok": False, "error": f"Unknown tool: {name}"}
                        except AppError as exc:
                            result = {"ok": False, "error": exc.message, "code": exc.code}
                        except (OSError, ValueError, TypeError) as exc:
                            result = {"ok": False, "error": str(exc)}
                        finally:
                            self.tools.on_output = None
                    record["result"] = result
                    if record["status"] != "denied":
                        record["status"] = "done" if result.get("ok") else "error"
                    self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                          "content": json.dumps(result, ensure_ascii=False)})
                    self.pending.pop(0)
                    self.state["status"] = "running"
                    self._save("running")
                    yield self._event()

                self.messages = compact_messages(self.messages, get_settings().agent_context_chars)
                if self.content and not self.content.endswith("\n\n"):
                    self.content += "\n\n"
                    yield self.owner._text_event("\n\n")
                self._save("running")
                fragments: dict[int, dict[str, str]] = {}
                text = ""
                finish_reason = None
                async for event in adapter.stream_chat(model=self.owner.profile.model_name or "",
                        messages=self.messages, extra_params=self.extra_params, tools=available_tools, tool_choice="auto"):
                    if event.event == "delta":
                        chunk = str(event.data)
                        text += chunk
                        self.content += chunk
                        yield self.owner._text_event(chunk)
                    elif event.event == "tool_call_delta" and isinstance(event.data, dict):
                        _merge_tool_call_delta(fragments, event.data)
                    elif event.event == "usage" and isinstance(event.data, dict):
                        self.owner.usage = _merge_usage(self.owner.usage, event.data)
                        yield self.owner._usage_event()
                    elif event.event == "finish_reason":
                        finish_reason = str(event.data)
                calls = _materialize_tool_calls(fragments, len(self.state["calls"]))
                if finish_reason not in {"stop", "tool_calls"}:
                    raise AppError("AGENT_RESPONSE_INCOMPLETE", "The model response did not finish cleanly. No partial tool calls were executed; resume to retry.", status_code=502)
                if not calls:
                    if finish_reason == "tool_calls" or not text.strip():
                        raise AppError("AGENT_RESPONSE_INCOMPLETE", "The model response was interrupted or incomplete. Resume to continue.", status_code=502)
                    self.messages.append({"role": "assistant", "content": text})
                    self.state["status"] = "done"
                    self._save("done")
                    yield self._event()
                    return
                # Ensure record identity survives providers reusing a call id.
                used = {r["id"] for r in self.state["calls"]}
                payloads = []
                for index, call in enumerate(calls):
                    payload = call.to_openai_dict()
                    if payload["id"] in used:
                        payload["id"] = "call_" + uuid4().hex
                    used.add(payload["id"])
                    payloads.append(payload)
                self.messages.append({"role": "assistant", "content": text or None, "tool_calls": payloads})
                self.pending = [{"call": call, "phase": "pending"} for call in payloads]
                self._save("running")
        except asyncio.CancelledError:
            self.state["status"] = "cancelled"
            for record in self.state["calls"]:
                if record["status"] in {"running", "waiting_approval"}:
                    record["status"] = "cancelled"
            self._save("cancelled")
            raise
        except Exception:
            self.state["status"] = "error"
            self._save("error")
            yield self._event()
            raise
