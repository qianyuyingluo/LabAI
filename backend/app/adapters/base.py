from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


OpenAIMessage = dict[str, Any]
OpenAITool = dict[str, Any]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    type: str = "function"

    def to_openai_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass(slots=True)
class AdapterResponse:
    content: str
    usage: dict[str, Any] | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


@dataclass(slots=True)
class AdapterStreamEvent:
    event: str
    data: Any


class ModelAdapter(ABC):
    @abstractmethod
    async def chat(
        self,
        *,
        model: str,
        messages: list[OpenAIMessage],
        extra_params: dict[str, Any] | None = None,
        tools: list[OpenAITool] | None = None,
        tool_choice: Any | None = None,
    ) -> AdapterResponse:
        raise NotImplementedError

    @abstractmethod
    def stream_chat(
        self,
        *,
        model: str,
        messages: list[OpenAIMessage],
        extra_params: dict[str, Any] | None = None,
        tools: list[OpenAITool] | None = None,
        tool_choice: Any | None = None,
    ) -> AsyncIterator[AdapterStreamEvent]:
        raise NotImplementedError
