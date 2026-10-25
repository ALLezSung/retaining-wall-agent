"""OpenAI-compatible transport, model discovery and reasoning mapping."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .protocol import Message, ModelDelta, ModelError, ModelInfo, ModelResponse, ToolCall
from ..config import ModelConfig, ProjectConfig


# V4 / V4.1 and gateways can expose one or two full-width (or ASCII) bars.
_RAW_TOOL_MARKER = re.compile(r"<\s*/?\s*[|｜]+\s*DSML\s*[|｜]+", re.IGNORECASE)
_RAW_TOOL_ERROR = "模型把工具调用标记写入了正文，该调用未执行。请继续对话以完成后续操作。"


class _TextStreamGuard:
    """Hold a possible opening tag across chunks; never render protocol tokens."""

    def __init__(self, on_delta):
        self.on_delta = on_delta
        self.pending = ""

    def _emit(self, text):
        if text:
            self.on_delta(ModelDelta(content=text))

    def feed(self, text):
        self.pending += text
        match = _RAW_TOOL_MARKER.search(self.pending)
        if match:
            self._emit(self.pending[:match.start()])
            self.pending = ""
            raise ModelError(_RAW_TOOL_ERROR)
        opening = self.pending.rfind("<")
        # A comparison such as p < 60 is ordinary text and is released promptly.
        tail = self.pending[opening:] if opening >= 0 else ""
        possible_tag = bool(re.fullmatch(r"<\s*/?\s*[|｜]*\s*[Dd]?[Ss]?[Mm]?[Ll]?\s*[|｜]*", tail))
        if possible_tag:
            self._emit(self.pending[:opening])
            self.pending = tail
        else:
            self._emit(self.pending)
            self.pending = ""

    def finish(self):
        if re.match(r"<\s*/?\s*[|｜]+", self.pending):
            raise ModelError(_RAW_TOOL_ERROR)
        self._emit(self.pending)
        self.pending = ""


def provider_message(message: Message) -> dict:
    output = {"role": "system" if message.role == "context" else message.role,
              "content": message.content}
    if message.role == "assistant" and not message.tool_calls and _RAW_TOOL_MARKER.search(message.content or ""):
        # Recover an older saved transcript without replaying a bogus invocation.
        output["content"] = _RAW_TOOL_MARKER.split(message.content, maxsplit=1)[0].rstrip() + "\n先前回复曾返回未执行的工具标记；后续请以真实工具结果为准。"
    if message.tool_calls:
        output["tool_calls"] = [
            {"id": call.id, "type": "function", "function": {
                "name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        output["tool_call_id"] = message.tool_call_id
    if message.reasoning_content is not None:
        output["reasoning_content"] = message.reasoning_content
    return output


class OpenAICompatibleModel:
    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ProjectConfig.load().llm
        self._metadata: dict[str, dict] = {}
        self._models: list[ModelInfo] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    @property
    def reasoning_effort(self) -> str:
        return self.config.reasoning_effort

    @property
    def is_deepseek(self) -> bool:
        return urlsplit(self.config.base_url).hostname == "api.deepseek.com"

    def _info(self, identifier: str, metadata: dict | None = None) -> ModelInfo:
        metadata = metadata or {}
        mapping = self.config.reasoning_overrides.get(identifier)
        if mapping:
            return ModelInfo(identifier, metadata.get("name") or identifier,
                             ("default", *mapping), "使用配置中的思考深度映射。")
        declared = metadata.get("effort", {}).get("supported_levels", [])
        levels = tuple(dict.fromkeys(level for level in declared if level in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}))
        if levels:
            toggle = ("none",) if self.is_deepseek and "none" not in levels else ()
            return ModelInfo(identifier, metadata.get("name") or identifier,
                             ("default", *toggle, *levels),
                             "默认保持快速模式；其他档位来自服务声明。" if self.is_deepseek else "思考深度来自服务声明。")
        if self.is_deepseek and identifier.startswith("deepseek-"):
            return ModelInfo(identifier, identifier, ("default", "none", "low", "medium", "high", "max"),
                             "默认/关闭→禁用；低→low；中/高→high；最高→max。")
        if identifier.startswith(("o3", "o4-mini", "gpt-5")) and "chat" not in identifier:
            levels = ("default", "low", "medium", "high")
            if identifier.startswith("gpt-5."):
                levels = ("default", "none", "low", "medium", "high")
            return ModelInfo(identifier, identifier, levels, "使用已知模型族的保守映射，可在配置中覆盖。")
        return ModelInfo(identifier, metadata.get("name") or identifier)

    def current_model(self) -> ModelInfo:
        return self._info(self.config.model, self._metadata.get(self.config.model))

    def _open(self, request, timeout):
        return urlopen(request, timeout=timeout)

    def _request(self, path: str, payload: dict | None = None, *, discovery=False):
        if not self.configured:
            raise ModelError("请在 config.toml 的 [llm] 中配置 api_key 和 base_url。")
        request = Request(self.config.base_url.rstrip("/") + path,
                          data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.config.api_key}",
                                   "Content-Type": "application/json"})
        return self._open(request, min(10, self.config.timeout_seconds) if discovery else self.config.timeout_seconds)

    def discover_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        if self._models is not None and not refresh:
            return list(self._models)
        try:
            with self._request("/models", discovery=True) as response:
                data = json.load(response)["data"]
            if not isinstance(data, list):
                raise ValueError("invalid models")
            metadata = {}
            for item in data:
                identifier = item["id"]
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError("invalid model id")
                if item.get("output_modalities") and "text" not in item["output_modalities"]:
                    continue
                if any(part in identifier.lower() for part in ("embedding", "whisper", "tts", "dall-e", "moderation")):
                    continue
                # These families require Responses for tools, outside this transport.
                if identifier.startswith(("gpt-6-astra", "gpt-6.1-sol")):
                    continue
                capabilities = item.get("api_capabilities", {}).get("chat_completions")
                if capabilities is False or (isinstance(capabilities, dict) and capabilities.get("tools") is False):
                    continue
                metadata[identifier] = item
            if not metadata:
                raise ModelError("服务未返回可用于此智能体的聊天模型，可通过 llm.model 指定模型。")
            self._metadata = metadata
            self._models = [self._info(key, item) for key, item in metadata.items()]
            if self.config.model not in metadata:
                self.config = replace(self.config, model=self._models[0].id, reasoning_effort="default")
            return list(self._models)
        except HTTPError as exc:
            raise ModelError(f"模型发现失败（HTTP {exc.code}）；可使用已配置模型或刷新重试。") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelError("无法连接模型列表接口；可使用已配置模型或刷新重试。") from exc
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ModelError("模型列表格式不兼容；可通过 llm.model 指定模型。") from exc

    def select_model(self, identifier: str, effort: str = "default") -> None:
        info = self._info(identifier, self._metadata.get(identifier))
        if effort not in info.efforts:
            raise ValueError("该模型不支持所选思考深度。")
        self.config = replace(self.config, model=identifier, reasoning_effort=effort)

    def _reasoning_payload(self) -> dict:
        effort = self.config.reasoning_effort
        if effort == "default":
            return {"thinking": {"type": "disabled"}} if self.is_deepseek else {}
        if effort not in self.current_model().efforts:
            raise ModelError("当前模型的思考深度不受支持，请选择默认或配置映射。")
        mapped = self.config.reasoning_overrides.get(self.config.model, {}).get(effort, effort)
        if self.is_deepseek:
            if mapped == "none":
                return {"thinking": {"type": "disabled"}}
            mapped = {"minimal": "low", "medium": "high", "xhigh": "high", "ultra": "max"}.get(mapped, mapped)
            return {"thinking": {"type": "enabled"}, "reasoning_effort": mapped}
        return {"reasoning_effort": mapped}

    def _payload(self, messages: list[Message], tools: list[dict], *, stream=False, final=False) -> dict:
        records = [provider_message(item) for item in messages]
        if not self.is_deepseek:
            for record in records:
                record.pop("reasoning_content", None)
        limit_name = "max_completion_tokens" if self.config.model.startswith(("gpt-5", "o1", "o3", "o4")) else "max_tokens"
        limit = min(self.config.max_tokens, self._metadata.get(self.config.model, {}).get("max_output_tokens") or self.config.max_tokens)
        payload = {"model": self.config.model, "messages": records, limit_name: limit,
                   **self._reasoning_payload()}
        if stream:
            payload["stream"] = True
        if tools:
            payload.update(tools=tools, tool_choice="none" if final else "auto")
        return payload

    @staticmethod
    def _normalize(message: dict, finish: str | None) -> ModelResponse:
        if finish == "length":
            raise ModelError("模型回复被截断，请提高 llm.max_tokens 或降低思考深度后重试。")
        if finish in {"content_filter", "insufficient_system_resource"}:
            raise ModelError("模型未完成回复，请调整问题后重试。")
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        if any(value is not None and not isinstance(value, str) for value in (content, reasoning)):
            raise TypeError("invalid content")
        if _RAW_TOOL_MARKER.search(content or ""):
            raise ModelError(_RAW_TOOL_ERROR)
        calls = []
        for call in message.get("tool_calls") or []:
            function = call["function"]
            if not all(isinstance(v, str) for v in (call["id"], function["name"], function["arguments"])):
                raise TypeError("invalid tool call")
            calls.append(ToolCall(call["id"], function["name"], function["arguments"]))
        if any(not call.id or not call.name for call in calls) or len({c.id for c in calls}) != len(calls):
            raise TypeError("invalid tool call ids")
        return ModelResponse(content, tuple(calls), finish, reasoning)

    def complete(self, messages: list[Message], tools: list[dict]) -> ModelResponse:
        return self._complete(messages, tools)

    def complete_stream(self, messages: list[Message], tools: list[dict],
                        on_delta: Callable[[ModelDelta], None]) -> ModelResponse:
        return self._complete(messages, tools, on_delta)

    def complete_final(self, messages: list[Message], tools: list[dict],
                       on_delta: Callable[[ModelDelta], None]) -> ModelResponse:
        return self._complete(messages, tools, on_delta, final=True)

    def _complete(self, messages, tools, on_delta=None, *, final=False) -> ModelResponse:
        try:
            with self._request("/chat/completions", self._payload(messages, tools, stream=on_delta is not None, final=final)) as response:
                if on_delta is None:
                    choice = json.load(response)["choices"][0]
                    return self._normalize(choice["message"], choice.get("finish_reason"))
                return self._read_stream(response, on_delta)
        except HTTPError as exc:
            raise ModelError(f"模型请求失败（HTTP {exc.code}），请检查端点、模型权限和参数。") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelError("模型连接中断，请检查网络后重试。") from exc
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ModelError("模型返回格式异常，请重试。") from exc

    def _read_stream(self, response, on_delta) -> ModelResponse:
        content, reasoning, calls = [], [], {}
        finish = None
        done = False
        data_lines = []
        text_guard = _TextStreamGuard(on_delta)

        def consume(data):
            nonlocal finish, done
            if data.strip() == "[DONE]":
                done = True
                return
            packet = json.loads(data)
            if "error" in packet:
                raise ModelError("模型流返回错误，请稍后重试。")
            for choice in packet.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta", {})
                text = delta.get("content")
                thought = delta.get("reasoning_content")
                if text is not None:
                    if not isinstance(text, str):
                        raise TypeError("invalid text delta")
                    content.append(text)
                    if text:
                        text_guard.feed(text)
                if thought:
                    if not isinstance(thought, str):
                        raise TypeError("invalid reasoning delta")
                    reasoning.append(thought)
                    on_delta(ModelDelta(reasoning=True))
                for part in delta.get("tool_calls") or []:
                    index = part["index"]
                    if not isinstance(index, int) or index < 0:
                        raise ValueError("invalid tool index")
                    call = calls.setdefault(index, {"id": "", "function": {"name": "", "arguments": ""}})
                    if part.get("id"):
                        call["id"] += part["id"]
                    for key in ("name", "arguments"):
                        value = part.get("function", {}).get(key)
                        if value is not None:
                            call["function"][key] += value
                if choice.get("finish_reason") is not None:
                    finish = choice["finish_reason"]

        for raw in response:
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if data_lines:
                    consume("\n".join(data_lines))
                    data_lines.clear()
                if done:
                    break
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines and not done:
            consume("\n".join(data_lines))
        if finish is None:
            raise ModelError("模型输出流提前中断，未执行未完成的工具调用，请重试。")
        text_guard.finish()
        return self._normalize({"content": "".join(content) or None,
                                "reasoning_content": "".join(reasoning) or None,
                                "tool_calls": [calls[key] for key in sorted(calls)]}, finish)
