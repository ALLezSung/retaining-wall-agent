"""Generic sequential model/tool loop; domain and streaming transports are injected."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable
from uuid import uuid4

from ..llm.protocol import Message, Model, ModelDelta, ModelError
from .events import Event, EventSink
from .tools import ToolRegistry


@dataclass(frozen=True)
class RunResult:
    content: str
    trace: list[dict]
    stop_reason: str | None = None
    run_messages: list[dict] = field(default_factory=list)


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
        trace, run_messages = [], []
        run_id, started = uuid4().hex, monotonic()
        round_number = 0

        def emit(name, **data):
            self.event_sink(Event(name, {"run_id": run_id, "run": round_number,
                                        "elapsed_seconds": round(monotonic() - started, 3), **data}))

        def delta(item: ModelDelta):
            if item.content:
                emit("message_delta", content=item.content)
            if item.reasoning:
                emit("model_reasoning")

        messages = [Message("system", self.system_prompt), *self.history,
                    Message("context", json.dumps(self.context_builder(), ensure_ascii=False)),
                    Message("user", text)]
        self.history.append(messages[-1])
        definitions = self.registry.definitions()
        emit("run_started")
        try:
            for round_index in range(self.max_rounds + 1):
                round_number = round_index + 1
                allow_tools = round_index < self.max_rounds
                if not allow_tools:
                    emit("run_limit_reached", max_rounds=self.max_rounds)
                    messages.append(Message("system",
                        "本轮已达到工具调用轮次上限。此请求仅用于汇总已有证据，不再执行工具。"
                        "请用自然语言说明已完成的操作、尚未验证的状态与下一步；不要输出工具标记，"
                        "不要把尚未调用的工具或尚未完成的验证说成已经完成。需要继续时明确告知用户发送‘继续’。"))
                emit("model_started", final_round=not allow_tools)
                stream = getattr(self.model, "complete_stream", None)
                finalize = getattr(self.model, "complete_final", None)
                if not allow_tools and callable(finalize):
                    response = finalize(list(messages), definitions, delta)
                elif callable(stream):
                    response = stream(list(messages), definitions if allow_tools else [], delta)
                else:
                    response = self.model.complete(list(messages), definitions if allow_tools else [])
                    if response.content:
                        delta(ModelDelta(content=response.content))
                if response.finish_reason == "length":
                    raise ModelError("模型回复被截断，请缩短问题重试。")
                emit("model_finished", tool_count=len(response.tool_calls), content=response.content)
                if response.tool_calls:
                    if not allow_tools:
                        raise ModelError("模型超过本轮工具调用上限。")
                    ids = [call.id for call in response.tool_calls]
                    if any(not item for item in ids) or len(set(ids)) != len(ids):
                        raise ModelError("模型返回了无效的工具调用标识。")
                    assistant = Message("assistant", response.content, response.tool_calls,
                                        reasoning_content=response.reasoning_content)
                    messages.append(assistant)
                    self.history.append(assistant)
                    if response.content and response.content.strip():
                        run_messages.append({"run": round_number, "content": response.content.strip()})
                    # Append the complete set of tool results before requesting another model run.
                    for call in response.tool_calls:
                        tool_started = monotonic()
                        emit("tool_started", tool=call.name, call_id=call.id, arguments=call.arguments)
                        output = self.registry.invoke(call.name, call.arguments)
                        item = {"run_id": run_id, "run": round_number, "call_id": call.id,
                                "tool": call.name, "arguments": call.arguments, "output": output,
                                "duration_seconds": round(monotonic() - tool_started, 3)}
                        trace.append(item)
                        tool_message = Message("tool", json.dumps(output, ensure_ascii=False), tool_call_id=call.id)
                        messages.append(tool_message)
                        self.history.append(tool_message)
                        emit("tool_finished", **item)
                    continue
                if not isinstance(response.content, str) or not response.content.strip():
                    raise ModelError("模型没有返回可显示的回答，请重试。")
                stop_reason = None if allow_tools else "round_limit"
                final_text = response.content.strip()
                if stop_reason:
                    notice = "本轮已达到工具调用轮次上限，已完成的操作已保留；后续操作可发送‘继续’接着执行。"
                    final_text += "\n\n" + notice
                self.history.append(Message("assistant", final_text,
                                            reasoning_content=response.reasoning_content))
                emit("message_completed", content=final_text)
                emit("run_stopped", reason=stop_reason) if stop_reason else emit("run_finished")
                return RunResult(final_text, trace, stop_reason, run_messages)
        except ModelError as exc:
            emit("run_failed", message=str(exc))
            raise
        raise ModelError("模型未完成本轮回答。")
