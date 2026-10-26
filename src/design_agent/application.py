"""Design application assembly and state boundary. No formulas, HTTP or UI."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..config import ProjectConfig
from ..agent.events import Event, EventSink
from ..agent.loop import AgentLoop
from ..agent.tools import ToolRegistry, RUN_CONTEXT
from ..llm.protocol import Message, Model, ModelError, ModelInfo, ToolCall
from .errors import CapabilityError
from .ports import ApplicationPorts
from .skills.capabilities import Capabilities
from .workspace.models import StateEnvelope


SYSTEM_IDENTITY = """你是使用本地技能完成任务的对话助手，通常用2到5句中文回应，表格和成果由界面展示。
只使用用户明确提供的条件，不猜测输入。数值和完成结论必须来自执行证据。条件不足时追问；失败或尚未持久化时如实说明。
首次开展相关业务前按目录 id 调用 read_skill，遵循技能说明，通过 run_skill_action 提供本次输入。
临近用户输入的上下文由各状态所有者提供，是权威快照的投影；本轮以工具的新结果为准。不得自行构造、覆盖或提交权威快照。
成果默认只有摘要，需要内容时使用 read_artifact。技能目录启动时固定；读取说明不改变工具定义。"""


@dataclass(frozen=True)
class DesignTurn:
    content: str
    trace: list[dict]
    artifacts: dict = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    failed: bool = False
    stop_reason: str | None = None
    run_messages: list[dict] = field(default_factory=list)


class DesignAgent:
    def __init__(self, model: Model, *, ports: ApplicationPorts, config: ProjectConfig,
                 event_sink: EventSink | None = None, max_rounds: int | None = None):
        self._conversations = ports.conversations
        self._messages: list[dict] = []
        self._conversation_saved = False
        self._turn_sink: EventSink | None = None
        self._partial_segments: list[str] = []
        self._model_choices: list[ModelInfo] | None = None
        self.model_discovery_error = ""
        self.persistence_error = ""
        self.persistence_code = ""
        self.conversation_id = uuid4().hex
        self.created_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        self.conversation_title = datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d %H:%M:%S")
        self.config = config
        self._storage_revision = None
        self._events: list[Event] = []
        self._event_sink = event_sink
        self.catalog = ports.catalog
        self.capabilities = Capabilities(ports.catalog, ports.executor, ports.resources, ports.artifacts,
                                         self.conversation_id, self._emit)
        self.capabilities.initialize()
        registry = ToolRegistry()
        for tool in self.capabilities.tools():
            registry.register(tool)
        prompt = SYSTEM_IDENTITY + "\n" + json.dumps(self.catalog.directory(), ensure_ascii=False)
        self.loop = AgentLoop(model, registry, prompt,
                              self.context_snapshot, self._emit,
                              max_rounds if max_rounds is not None else config.agent.max_rounds,
                              checkpoint=self._checkpoint)
        self._persist()

    @property
    def model_configured(self) -> bool:
        return getattr(self.loop.model, "configured", True)

    @property
    def workspace(self):
        return self.capabilities.workspace

    @property
    def max_runs(self) -> int:
        return self.loop.max_rounds + 1

    def state_snapshot(self, owner: str) -> dict:
        return deepcopy(self.workspace.snapshot(owner))

    def application_view(self, skill_id: str | None = None) -> dict:
        return self.capabilities.view(skill_id or self.config.skills.default_skill)

    def execute_action(self, skill_id: str, action_id: str, input: dict, *, new_conversation=False) -> dict:
        if new_conversation:
            self.reset()
        token = RUN_CONTEXT.set({"source": "interface", "user_text": "", "operation_id": uuid4().hex})
        try:
            output = self.capabilities.run_skill_action(skill_id, action_id, input)
        finally:
            RUN_CONTEXT.reset(token)
        if output["ok"]:
            self._persist()
        return output

    def artifact_bytes(self, attachment: dict) -> bytes:
        return self.workspace.attachment_bytes(attachment)

    def supplementary_views(self) -> dict:
        used = {op["skill_id"] for op in self.workspace.operations
                if op["action_id"] in self.catalog.get(op["skill_id"]).manifest.get("actions", {})}
        return {skill: self.application_view(skill) for skill in sorted(used) if skill != self.config.skills.default_skill}

    def _checkpoint(self):
        """Persist a complete tool batch with a recoverable interrupted message."""
        if not self._messages or self._messages[-1]["role"] != "user":
            return
        self._messages.append({"role": "assistant", "content": "上次执行在工具批次完成后中断，可继续对话。",
                               "failed": True, "trace": [deepcopy(e.data) for e in self._events if e.name == "tool_finished"],
                               "artifacts": self.current_artifacts(),
                               "events": [{"event": e.name, **e.data} for e in self._events]})
        try:
            self._persist()
        finally:
            self._messages.pop()

    def _emit(self, event: Event) -> None:
        if event.name == "model_started":
            self._partial_segments.append("")
        if event.name == "message_delta":
            if not self._partial_segments:
                self._partial_segments.append("")
            self._partial_segments[-1] += event.data["content"]
        # Delta text and private reasoning are not duplicated in persisted events.
        if event.name not in {"message_delta", "model_reasoning"}:
            self._events.append(deepcopy(event))
        if self._event_sink:
            self._event_sink(deepcopy(event))
        if self._turn_sink:
            self._turn_sink(event)

    def available_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        if self._model_choices is None or refresh:
            discover = getattr(self.loop.model, "discover_models", None)
            self.model_discovery_error = ""
            try:
                self._model_choices = discover(refresh=refresh) if discover and self.model_configured else [self.current_model()]
            except ModelError as exc:
                self.model_discovery_error = str(exc)
                self._model_choices = [self.current_model()]
        return list(self._model_choices)

    def current_model(self) -> ModelInfo:
        read = getattr(self.loop.model, "current_model", None)
        return read() if read else ModelInfo("injected", "已注入的模型")

    @property
    def reasoning_effort(self) -> str:
        return getattr(self.loop.model, "reasoning_effort", "default")

    def select_model(self, identifier: str, effort: str) -> None:
        select = getattr(self.loop.model, "select_model", None)
        if select:
            before = (self.current_model().id, self.reasoning_effort)
            select(identifier, effort)
            if before != (identifier, effort):
                self._persist()

    def conversation_messages(self) -> list[dict]:
        messages = deepcopy(self._messages)
        # Older snapshots joined tool-run preambles and the final answer. Use
        # the actual transcript and an exact prefix match to separate them for
        # presentation, without rewriting stored history or guessing paragraphs.
        turns: list[list[dict]] = []
        run = 0
        for message in self.loop.history:
            if message.role == "user":
                turns.append([])
                run = 0
            elif message.role == "assistant" and turns:
                run += 1
                if message.tool_calls and message.content and message.content.strip():
                    turns[-1].append({"run": run, "content": message.content.strip()})
        turn_index = -1
        for message in messages:
            if message["role"] == "user":
                turn_index += 1
            elif message["role"] == "assistant" and "run_messages" not in message:
                notes = turns[turn_index] if 0 <= turn_index < len(turns) else []
                prefix = "\n\n".join(note["content"] for note in notes) + "\n\n"
                if notes and message["content"].startswith(prefix):
                    message["run_messages"] = deepcopy(notes)
                    message["content"] = message["content"][len(prefix):]
        return messages

    def recent_conversations(self) -> list[dict]:
        return self._conversations.recent(5)

    def all_conversations(self) -> list[dict]:
        return self._conversations.recent(None)

    def rename_conversation(self, identifier: str, title: str) -> None:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("对话标题不能为空。")
        title = title.strip()
        if len(title) > 80:
            raise ValueError("对话标题不能超过 80 个字符。")
        if identifier != self.conversation_id or self._conversation_saved:
            self._conversations.rename(identifier, title)
        if identifier == self.conversation_id:
            self.conversation_title = title

    def save_conversation(self) -> bool:
        return self._persist()

    def _persist(self) -> bool:
        # A new conversation is an in-memory draft until the first chat message.
        # Parameter presets, model settings and title changes do not create rows.
        if not self._conversation_saved and not self._messages:
            self.persistence_error = ""
            return True
        next_revision = uuid4().hex
        try:
            self._conversations.save(self.conversation_id, self.conversation_title, self.created_at, {
                "version": 2, "workspace": self.workspace.serialize(), "messages": self._messages,
                "expected_revision": self._storage_revision, "storage_revision": next_revision,
                "execution_logs": self.capabilities.logs,
                "history": [asdict(message) for message in self.loop.history],
                "loaded_skills": sorted(self.catalog.loaded),
                "model": self.current_model().id, "reasoning_effort": self.reasoning_effort,
            })
            self._conversation_saved = True
            self._storage_revision = next_revision
            self.persistence_error = ""
            self.persistence_code = ""
        except (RuntimeError, OSError, ValueError) as exc:
            self.persistence_code = getattr(exc, "code", "PERSISTENCE_FAILED")
            self.persistence_error = ("对话已由其他实例更新，内存内容仍保留；未覆盖数据库记录。" if self.persistence_code == "STALE_STATE"
                                      else "本次对话尚未保存成功，请检查对话数据库路径与文件权限。")
        return not self.persistence_error

    def load_conversation(self, identifier: str) -> None:
        if not self._persist():
            raise RuntimeError(self.persistence_error + " 当前对话仍保留在内存，暂未切换。")
        record = self._conversations.load(identifier)
        payload = record["payload"]
        candidate = self.capabilities.for_session(identifier, lambda event: None)
        try:
            messages = deepcopy(payload["messages"])
            if not isinstance(messages, list) or not all(isinstance(m, dict) and isinstance(m.get("content"), str) and m.get("role") in {"user", "assistant"} for m in messages):
                raise ValueError("invalid transcript")
            history = [Message(**{**m, "tool_calls": tuple(ToolCall(**c) for c in m.get("tool_calls", []))}) for m in payload["history"]]
            if payload.get("version") == 2:
                candidate.workspace.restore(payload["workspace"])
                for owner in list(candidate.workspace.states):
                    if owner not in self.catalog.owners:
                        raise ValueError("状态所有者技能不可用。")
                    candidate.lifecycle(owner, "restore")
            else:
                owner = self.config.skills.legacy_owner
                candidate.lifecycle(owner, "restore", {"legacy_state": payload["session"]})
                for message in messages:
                    old = message.get("artifacts")
                    if old:
                        converted = self.capabilities.for_session(identifier, lambda event: None)
                        converted.lifecycle(owner, "restore", {"legacy_artifacts": old})
                        message["artifacts"] = {"items": [converted.workspace.read_artifact(ref) for ref in converted.workspace.turn_refs]}
                        candidate.workspace.artifacts.update(converted.workspace.artifacts)
            candidate.initialize()
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise ValueError("对话记录损坏或格式不兼容，未切换当前对话。") from exc
        self.capabilities.workspace = candidate.workspace
        self.capabilities.logs = deepcopy(payload.get("execution_logs", []))
        self.loop.history, self._messages = history, messages
        self.catalog.loaded = set(payload.get("loaded_skills", [])) & set(self.catalog.entries)
        self.conversation_id, self.conversation_title = identifier, record["title"]
        self._storage_revision = payload.get("storage_revision", "legacy")
        self._conversation_saved = True
        self.created_at = record["created_at"]
        self._events.clear()
        select = getattr(self.loop.model, "select_model", None)
        if select and payload.get("model"):
            try:
                select(payload["model"], payload.get("reasoning_effort", "default"))
            except ValueError:
                select(payload["model"], "default")

    def delete_conversation(self, identifier: str) -> None:
        if identifier == self.conversation_id and self.persistence_error:
            raise RuntimeError(self.persistence_error + " 未保存内容仍保留在内存。")
        if identifier != self.conversation_id or self._conversation_saved:
            self._conversations.delete(identifier)
        if identifier == self.conversation_id:
            self.reset(save_current=False)

    def context_snapshot(self) -> dict:
        return self.capabilities.context()

    def reset(self, *, save_current: bool = True) -> None:
        if save_current and not self._persist():
            raise RuntimeError(self.persistence_error + " 当前对话仍保留在内存，暂未新建。")
        identifier = uuid4().hex
        candidate = self.capabilities.for_session(identifier, lambda event: None)
        # The owner decides what preferences survive a new conversation.
        for owner, state in self.workspace.states.items():
            candidate.workspace.states[owner] = StateEnvelope(identifier, owner, data_version=state.data_version, payload=deepcopy(state.payload))
            candidate.lifecycle(owner, "reset")
        candidate.initialize()
        self.capabilities.workspace = candidate.workspace
        self.capabilities.logs.clear()
        self.conversation_id = identifier
        self._storage_revision = None
        self._conversation_saved = False
        self.created_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        self.conversation_title = datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d %H:%M:%S")
        self._messages.clear()
        self.loop.reset()
        self.catalog.loaded.clear()
        self._events.clear()
        self.persistence_error = ""

    def current_artifacts(self) -> dict:
        items = [self.workspace.read_artifact(ref) for ref in self.workspace.turn_refs]
        return {"items": items} if items else {}

    def chat(self, text: str, *, event_sink: EventSink | None = None) -> DesignTurn:
        self._events.clear()
        self.workspace.turn_refs.clear()
        self._partial_segments.clear()
        self._turn_sink = event_sink
        self._messages.append({"role": "user", "content": text})
        try:
            result = self.loop.run(text)
            turn = DesignTurn(result.content, result.trace, self.current_artifacts(), deepcopy(self._events),
                              stop_reason=result.stop_reason, run_messages=result.run_messages)
        except (ModelError, CapabilityError) as exc:
            trace = [deepcopy(event.data) for event in self._events if event.name == "tool_finished"]
            notes = [{"run": event.data["run"], "content": event.data["content"].strip()}
                     for event in self._events if event.name == "model_finished"
                     and event.data.get("tool_count") and event.data.get("content", "")]
            current_run = max((event.data["run"] for event in self._events if event.name == "model_started"), default=0)
            partial = self._partial_segments[-1] if self._partial_segments and not any(note["run"] == current_run for note in notes) else ""
            content = f"本轮模型请求未完成：{exc} 已完成的操作已保留，可继续对话。"
            turn = DesignTurn((partial + "\n\n" if partial else "") + content,
                              trace, self.current_artifacts(), deepcopy(self._events), failed=True, run_messages=notes)
        finally:
            self._turn_sink = None
        self._messages.append({"role": "assistant", "content": turn.content, "trace": turn.trace,
                               "artifacts": turn.artifacts, "failed": turn.failed,
                               "stop_reason": turn.stop_reason,
                               "run_messages": deepcopy(turn.run_messages),
                               "model": self.current_model().name, "reasoning_effort": self.reasoning_effort,
                               "events": [{"event": event.name, **event.data} for event in turn.events]})
        self._persist()
        return turn
