"""Provider-neutral messages. Context records are deliberately not user speech."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str = "{}"


@dataclass(frozen=True)
class Message:
    role: str
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None


class ModelError(RuntimeError):
    """Configuration, transport or response-protocol failure, not domain input."""


class Model(Protocol):
    def complete(self, messages: list[Message], tools: list[dict]) -> ModelResponse: ...
