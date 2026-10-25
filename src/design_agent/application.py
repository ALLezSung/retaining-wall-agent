"""Design application assembly and state boundary. No formulas, HTTP or UI."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ..config import ProjectConfig
from ..agent.events import Event, EventSink
from ..agent.loop import AgentLoop
from ..agent.skills import SkillCatalog
from ..agent.tools import ToolRegistry, ToolError
from ..llm.protocol import Message, Model, ModelError, ModelInfo, ToolCall
from ..skills.retaining_wall.report import book_sections, calculation_markdown, standards_markdown, summary_markdown
from ..skills.retaining_wall.tools import WallTools, protected_dimensions
from ..skills.standards.index import STANDARDS, selected_standard_report
from ..skills.standards.tools import tools as standard_tools
from .session import DesignSession
from .conversations import ConversationStore


SYSTEM_IDENTITY = """你是设计任务的对话助手，使用本地工具提供可复核的结果。自然回应用户，通常用2到5句中文，表格由界面展示。
只使用用户明确给出的条件，不猜测参数；工程数字、方案和覆盖结论必须来自工具证据。条件不足时追问，工具失败时解释下一步，不把教学结果说成完整工程设计。
下列是可用 skill 的稳定目录。首次开展相应业务前，使用 read_skill 按 id 读取完整说明；同一会话已读取的说明在历史中，无需重复添加。工具启动时已注册，读取说明不会改变工具列表。
临近本轮输入的内部上下文是应用权威状态的快照，不是用户亲口陈述；本轮以工具返回的更新状态为准。
核算时必须考虑所选规范的已实现校核和待补充要求；未覆盖的项目明确列为待验算，不能宣称全面合规。"""


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
    def __init__(self, model: Model | None = None, *, db_path: Path | None = None,
                 event_sink: EventSink | None = None, max_rounds: int | None = None,
                 config: ProjectConfig | None = None,
                 conversation_store: ConversationStore | None = None):
        config = config or ProjectConfig.load()
        # Defaults are assembled by the composition root; runtime uses only ports.
        from ..bootstrap import configured_model, conversation_store as default_store
        self._conversations = conversation_store if conversation_store is not None else default_store(
            db_path if db_path is not None else config.storage.conversations_database)
        self._messages: list[dict] = []
        self._conversation_saved = False
        self._turn_sink: EventSink | None = None
        self._partial_segments: list[str] = []
        self._model_choices: list[ModelInfo] | None = None
        self.model_discovery_error = ""
        self.persistence_error = ""
        self.conversation_id = uuid4().hex
        self.created_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        self.conversation_title = datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d %H:%M:%S")
        self._session = DesignSession()
        self._events: list[Event] = []
        self._turn_artifacts: dict = {}
        self._event_sink = event_sink
        self.protected_dimensions: set[str] = set()
        self.catalog = SkillCatalog(Path(__file__).resolve().parents[1] / "skills")
        self.wall = WallTools(self, db_path if db_path is not None else config.storage.cases_database)
        registry = ToolRegistry()
        registry.register(self.catalog.tool())
        for tool in [*self.wall.definitions(), *standard_tools(lambda: self.read_session().selected_standards)]:
            registry.register(tool)
        prompt = SYSTEM_IDENTITY + "\n" + json.dumps(self.catalog.directory(), ensure_ascii=False)
        self.loop = AgentLoop(model if model is not None else configured_model(config.llm), registry, prompt,
                              self.context_snapshot, self._emit,
                              max_rounds if max_rounds is not None else config.agent.max_rounds)
        self._persist()

    @property
    def model_configured(self) -> bool:
        return getattr(self.loop.model, "configured", True)

    def read_session(self) -> DesignSession:
        """Consumers get copies; all authoritative mutations use application methods."""
        return deepcopy(self._session)

    @property
    def max_runs(self) -> int:
        return self.loop.max_rounds + 1

    def parameter_table(self) -> str:
        from ..skills.retaining_wall.models import FIELD_LABELS
        return "| 参数 | 当前值 |\n|---|---:|\n" + "\n".join(
            f"| {label} | {self._session.parameters.get(key, '待补充')} |" for key, label in FIELD_LABELS.items())

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
        try:
            self._conversations.save(self.conversation_id, self.conversation_title, self.created_at, {
                "session": asdict(self._session), "messages": self._messages,
                "history": [asdict(message) for message in self.loop.history],
                "loaded_skills": sorted(self.catalog.loaded),
                "model": self.current_model().id, "reasoning_effort": self.reasoning_effort,
            })
            self._conversation_saved = True
            self.persistence_error = ""
        except (RuntimeError, OSError, ValueError):
            self.persistence_error = "本次对话尚未保存成功，请检查对话数据库路径与文件权限。"
        return not self.persistence_error

    def load_conversation(self, identifier: str) -> None:
        if not self._persist():
            raise RuntimeError(self.persistence_error + " 当前对话仍保留在内存，暂未切换。")
        record = self._conversations.load(identifier)
        payload = record["payload"]
        try:
            session = DesignSession(**payload["session"])
            selected_standard_report(session.selected_standards)
            history = [Message(**{**message, "tool_calls": tuple(ToolCall(**call) for call in message.get("tool_calls", []))})
                       for message in payload["history"]]
            messages = deepcopy(payload["messages"])
            if not isinstance(messages, list):
                raise ValueError("invalid transcript")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("对话记录损坏或格式不兼容，未切换当前对话。") from exc
        self._session, self.loop.history, self._messages = session, history, messages
        self.catalog.loaded = set(payload.get("loaded_skills", [])) & set(self.catalog.entries)
        self.conversation_id, self.conversation_title = identifier, record["title"]
        self._conversation_saved = True
        self.created_at = record["created_at"]
        self._turn_artifacts.clear()
        self._events.clear()
        self.protected_dimensions.clear()
        select = getattr(self.loop.model, "select_model", None)
        if select and payload.get("model"):
            try:
                select(payload["model"], payload.get("reasoning_effort", "default"))
            except ValueError:
                select(payload["model"], "default")
        self._persist()

    def delete_conversation(self, identifier: str) -> None:
        if identifier != self.conversation_id or self._conversation_saved:
            self._conversations.delete(identifier)
        if identifier == self.conversation_id:
            self.reset(save_current=False)

    def _updated(self) -> None:
        self._emit(Event("design_updated", {
            "current_parameters": deepcopy(self._session.parameters),
            "has_calculated_result": self._session.current_result is not None,
            "has_options": self._session.pending_options is not None,
            "wall_shape": self._session.wall_shape,
        }))

    def commit_parameters(self, values: dict, sources: dict, shape: str | None,
                          *, replace: bool = False) -> None:
        session = self._session
        values = deepcopy(values)
        changed = values != session.parameters or shape != session.wall_shape
        session.parameters = values
        session.parameter_sources = deepcopy(sources) if replace else {**session.parameter_sources, **deepcopy(sources)}
        session.wall_shape = shape
        if changed or replace:
            session.current_result = None
            session.pending_options = None
            self._turn_artifacts.clear()
        self._updated()

    def store_result(self, result: dict) -> None:
        if result["input"] != self._session.parameters:
            raise ToolError("INVALID_STATE", "计算结果与当前参数不一致。")
        self._session.current_result = deepcopy(result)
        self._turn_artifacts["result"] = deepcopy(result)
        self._updated()
        self._emit(Event("artifact_created", {"kind": "result", "artifact": deepcopy(result)}))

    def store_options(self, optimization: dict) -> None:
        self._session.pending_options = deepcopy(optimization)
        self._turn_artifacts["optimization"] = deepcopy(optimization)
        self._updated()
        self._emit(Event("artifact_created", {"kind": "optimization", "artifact": deepcopy(optimization)}))

    def context_snapshot(self) -> dict:
        session = self._session
        return {"current_parameters": deepcopy(session.parameters),
                "wall_shape": session.wall_shape,
                "has_calculated_result": session.current_result is not None,
                "result_summary": {key: session.current_result[key] for key in (
                    "sliding_ratio", "overturning_ratio", "base_contact")} if session.current_result else None,
                "last_options": deepcopy((session.pending_options or {}).get("alternatives", [])),
                "selected_standards": list(session.selected_standards),
                "standard_requirements": selected_standard_report(session.selected_standards),
                "loaded_skills": sorted(self.catalog.loaded)}

    def select_standards(self, selected: list[str]) -> None:
        selected_standard_report(selected)  # Skill owns identifier validation.
        if self._session.selected_standards == selected:
            return
        self._session.selected_standards = list(selected)
        # A result computed against another set of requirements is no longer current.
        self._session.current_result = None
        self._session.pending_options = None
        self._turn_artifacts.clear()
        self._persist()

    def standard_choices(self) -> list[str]:
        return list(STANDARDS)

    def reset(self, *, save_current: bool = True) -> None:
        if save_current and not self._persist():
            raise RuntimeError(self.persistence_error + " 当前对话仍保留在内存，暂未新建。")
        self._session = DesignSession(selected_standards=list(self._session.selected_standards))
        self.conversation_id = uuid4().hex
        self._conversation_saved = False
        self.created_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        self.conversation_title = datetime.fromisoformat(self.created_at).strftime("%Y-%m-%d %H:%M:%S")
        self._messages.clear()
        self.loop.reset()
        self.catalog.loaded.clear()
        self.protected_dimensions.clear()
        self._turn_artifacts.clear()
        self._events.clear()
        self._updated()
        self._persist()

    def teaching_cases(self) -> list[dict]:
        return self.wall.teaching_cases()

    def saved_cases(self) -> list[dict]:
        return self.wall.saved_cases()

    def load_teaching_case(self, title: str) -> dict:
        output = self.wall.load_case(title)
        self._persist()
        return output

    def load_saved_case(self, case_id: int) -> dict:
        output = self.wall.load_saved(case_id)
        self._persist()
        return output

    def save_case(self, title: str) -> int:
        return self.wall.save_current(title)

    @staticmethod
    def present_result(result: dict) -> dict:
        summary = summary_markdown(result)
        calculation = calculation_markdown(result)
        standards = standards_markdown(result)
        return {"summary": summary, "calculation": calculation, "standards": standards,
                "sections": book_sections(result),
                "markdown": summary + "\n\n" + calculation + "\n\n### 规范覆盖\n" + standards,
                "json": json.dumps(result, ensure_ascii=False, indent=2)}

    def current_artifacts(self) -> dict:
        artifacts = deepcopy(self._turn_artifacts)
        if "result" in artifacts:
            artifacts["report"] = self.present_result(artifacts["result"])
        return artifacts

    def chat(self, text: str, *, event_sink: EventSink | None = None) -> DesignTurn:
        self._events.clear()
        self._turn_artifacts.clear()
        self._partial_segments.clear()
        self._turn_sink = event_sink
        self._messages.append({"role": "user", "content": text})
        self.protected_dimensions = protected_dimensions(text)
        try:
            result = self.loop.run(text)
            turn = DesignTurn(result.content, result.trace, self.current_artifacts(), deepcopy(self._events),
                              stop_reason=result.stop_reason, run_messages=result.run_messages)
        except ModelError as exc:
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
            self.protected_dimensions.clear()
            self._turn_sink = None
        self._messages.append({"role": "assistant", "content": turn.content, "trace": turn.trace,
                               "artifacts": turn.artifacts, "failed": turn.failed,
                               "stop_reason": turn.stop_reason,
                               "run_messages": deepcopy(turn.run_messages),
                               "model": self.current_model().name, "reasoning_effort": self.reasoning_effort,
                               "events": [{"event": event.name, **event.data} for event in turn.events]})
        self._persist()
        return turn
