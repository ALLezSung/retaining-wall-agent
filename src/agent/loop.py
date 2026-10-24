"""Generic sequential model/tool loop; all domain behavior is injected."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ..llm.protocol import Message, Model, ModelError
from .events import Event, EventSink
from .tools import ToolRegistry


@dataclass(frozen=True)
class RunResult:
    content: str
    trace: list[dict]


class AgentLoop:
    def __init__(self, model: Model, registry: ToolRegistry, system_prompt: str,
                 context_builder: Callable[[], dict], event_sink: EventSink | None = None,
                 max_rounds: int = 6):
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        self.model = model
        self.registry = registry
        self.system_prompt = system_prompt
        self.context_builder = context_builder
        self.event_sink = event_sink or (lambda event: None)
        self.max_rounds = max_rounds
        self.history: list[Message] = []

    def reset(self) -> None:
        self.history.clear()

    def run(self, text: str) -> RunResult:
        trace = []
        # Exactly one temporary snapshot, never persisted in conversation history.
        messages = [Message("system", self.system_prompt), *self.history,
                    Message("context", json.dumps(self.context_builder(), ensure_ascii=False)),
                    Message("user", text)]
        self.history.append(messages[-1])
        definitions = self.registry.definitions()
        try:
            for round_index in range(self.max_rounds + 1):
                allow_tools = round_index < self.max_rounds
                response = self.model.complete(list(messages), definitions if allow_tools else [])
                if response.finish_reason == "length":
                    raise ModelError("模型回复被截断，请缩短问题重试。")
                if response.tool_calls:
                    if not allow_tools:
                        raise ModelError("模型超过本轮工具调用上限。")
                    ids = [call.id for call in response.tool_calls]
                    if any(not item for item in ids) or len(set(ids)) != len(ids):
                        raise ModelError("模型返回了无效的工具调用标识。")
                    assistant = Message("assistant", response.content, response.tool_calls)
                    messages.append(assistant)
                    self.history.append(assistant)
                    for call in response.tool_calls:
                        self.event_sink(Event("tool_started", {"tool": call.name, "arguments": call.arguments}))
                        output = self.registry.invoke(call.name, call.arguments)
                        item = {"tool": call.name, "arguments": call.arguments, "output": output}
                        trace.append(item)
                        tool_message = Message("tool", json.dumps(output, ensure_ascii=False), tool_call_id=call.id)
                        messages.append(tool_message)
                        self.history.append(tool_message)
                        self.event_sink(Event("tool_finished", item))
                    continue
                if not isinstance(response.content, str) or not response.content.strip():
                    raise ModelError("模型没有返回可显示的回答，请重试。")
                content = response.content.strip()
                self.history.append(Message("assistant", content))
                self.event_sink(Event("message_completed", {"content": content}))
                self.event_sink(Event("run_finished"))
                return RunResult(content, trace)
        except ModelError as exc:
            self.event_sink(Event("run_failed", {"message": str(exc)}))
            raise
        raise ModelError("模型未完成本轮回答。")
