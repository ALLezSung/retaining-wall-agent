"""Provider-neutral messages. Context records are deliberately not user speech."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol


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
    reasoning_content: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ModelResponse:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    reasoning_content: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ModelDelta:
    """Incremental visible text, or an opaque reasoning activity notification."""

    content: str = ""
    reasoning: bool = False


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    efforts: tuple[str, ...] = ("default",)
    effort_hint: str = "服务未声明思考深度，使用模型默认设置。"


class StreamingModel(Protocol):
    def complete_stream(self, messages: list[Message], tools: list[dict],
                        on_delta: Callable[[ModelDelta], None]) -> ModelResponse: ...


class SelectableModel(Protocol):
    @property
    def reasoning_effort(self) -> str: ...
    def current_model(self) -> ModelInfo: ...
    def discover_models(self, *, refresh: bool = False) -> list[ModelInfo]: ...
    def select_model(self, identifier: str, effort: str = "default") -> None: ...


class FinalizingModel(Protocol):
    """Optional final response with schemas retained and execution disabled."""

    def complete_final(self, messages: list[Message], tools: list[dict],
                       on_delta: Callable[[ModelDelta], None]) -> ModelResponse: ...


class ModelError(RuntimeError):
    """Configuration, transport or response-protocol failure, not domain input."""


class Model(Protocol):
    def complete(self, messages: list[Message], tools: list[dict]) -> ModelResponse: ...
