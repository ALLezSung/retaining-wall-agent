"""DeepSeek HTTP/configuration boundary; no design state or tool execution."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .protocol import Message, ModelError, ModelResponse, ToolCall
from ..config import ModelConfig, ProjectConfig


def provider_message(message: Message) -> dict:
    """DeepSeek accepts a system record for our near-input context projection."""
    output = {"role": "system" if message.role == "context" else message.role,
              "content": message.content}
    if message.tool_calls:
        output["tool_calls"] = [
            {"id": call.id, "type": "function", "function": {
                "name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        output["tool_call_id"] = message.tool_call_id
    return output


class DeepSeekModel:
    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ProjectConfig.load().llm

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    def complete(self, messages: list[Message], tools: list[dict]) -> ModelResponse:
        if not self.config.api_key:
            raise ModelError("请先配置 DeepSeek API Key。")
        payload = {"model": self.config.model,
                   "messages": [provider_message(item) for item in messages],
                   "thinking": {"type": "disabled"}, "max_tokens": self.config.max_tokens}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        try:
            request = Request(self.config.base_url.rstrip("/") + "/chat/completions",
                              data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers={"Authorization": f"Bearer {self.config.api_key}",
                                       "Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                envelope = json.load(response)
            choice = envelope["choices"][0]
            message = choice["message"]
            finish = choice.get("finish_reason")
            if finish == "length":
                raise ModelError("DeepSeek 回复被截断，请缩短问题重试。")
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise TypeError("invalid content")
            calls = []
            for call in message.get("tool_calls") or []:
                function = call["function"]
                if not all(isinstance(v, str) for v in (call["id"], function["name"], function["arguments"])):
                    raise TypeError("invalid tool call")
                calls.append(ToolCall(call["id"], function["name"], function["arguments"]))
            if any(not call.id for call in calls) or len({c.id for c in calls}) != len(calls):
                raise TypeError("invalid tool call ids")
            return ModelResponse(content, tuple(calls), finish)
        except HTTPError as exc:
            raise ModelError(f"DeepSeek 请求失败（HTTP {exc.code}）。") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelError("无法连接 DeepSeek，请检查网络。") from exc
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ModelError("DeepSeek 返回格式异常，请重试。") from exc
