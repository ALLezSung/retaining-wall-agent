"""Design application assembly and state boundary. No formulas, HTTP or UI."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ProjectConfig
from ..agent.events import Event, EventSink
from ..agent.loop import AgentLoop
from ..agent.skills import SkillCatalog
from ..agent.tools import ToolRegistry, ToolError
from ..llm.deepseek import DeepSeekModel
from ..llm.protocol import Model, ModelError
from ..skills.retaining_wall.report import calculation_markdown, standards_markdown, summary_markdown
from ..skills.retaining_wall.tools import WallTools, protected_dimensions
from ..skills.standards.index import STANDARDS, selected_standard_report
from ..skills.standards.tools import tools as standard_tools
from .session import DesignSession


SYSTEM_IDENTITY = """你是设计任务的对话助手，使用本地工具提供可复核的结果。自然回应用户，通常用2到5句中文，表格由界面展示。
只使用用户明确给出的条件，不猜测参数；工程数字、方案和覆盖结论必须来自工具证据。条件不足时追问，工具失败时解释下一步，不把教学结果说成完整工程设计。
下列是可用 skill 的稳定目录。首次开展相应业务前，使用 read_skill 按 id 读取完整说明；同一会话已读取的说明在历史中，无需重复添加。工具启动时已注册，读取说明不会改变工具列表。
临近本轮输入的内部上下文是应用权威状态的快照，不是用户亲口陈述；本轮以工具返回的更新状态为准。"""


@dataclass(frozen=True)
class DesignTurn:
    content: str
    trace: list[dict]
    artifacts: dict = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    failed: bool = False


class DesignAgent:
    def __init__(self, model: Model | None = None, *, db_path: Path | None = None,
                 event_sink: EventSink | None = None, max_rounds: int | None = None,
                 config: ProjectConfig | None = None):
        config = config or ProjectConfig.load()
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
        self.loop = AgentLoop(model if model is not None else DeepSeekModel(config.llm), registry, prompt,
                              self.context_snapshot, self._emit,
                              max_rounds if max_rounds is not None else config.agent.max_rounds)

    @property
    def model_configured(self) -> bool:
        return getattr(self.loop.model, "configured", True)

    def read_session(self) -> DesignSession:
        """Consumers get copies; all authoritative mutations use application methods."""
        return deepcopy(self._session)

    def _emit(self, event: Event) -> None:
        self._events.append(deepcopy(event))
        if self._event_sink:
            self._event_sink(deepcopy(event))

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
                "loaded_skills": sorted(self.catalog.loaded)}

    def select_standards(self, selected: list[str]) -> None:
        selected_standard_report(selected)  # Skill owns identifier validation.
        self._session.selected_standards = list(selected)

    def standard_choices(self) -> list[str]:
        return list(STANDARDS)

    def reset(self) -> None:
        self._session = DesignSession(selected_standards=list(self._session.selected_standards))
        self.loop.reset()
        self.catalog.loaded.clear()
        self.protected_dimensions.clear()
        self._turn_artifacts.clear()
        self._events.clear()
        self._updated()

    def teaching_cases(self) -> list[dict]:
        return self.wall.teaching_cases()

    def saved_cases(self) -> list[dict]:
        return self.wall.saved_cases()

    def load_teaching_case(self, title: str) -> dict:
        return self.wall.load_case(title)

    def load_saved_case(self, case_id: int) -> dict:
        return self.wall.load_saved(case_id)

    def save_case(self, title: str) -> int:
        return self.wall.save_current(title)

    @staticmethod
    def present_result(result: dict) -> dict:
        summary = summary_markdown(result)
        calculation = calculation_markdown(result)
        standards = standards_markdown(result)
        return {"summary": summary, "calculation": calculation, "standards": standards,
                "markdown": summary + "\n\n" + calculation + "\n\n### 规范覆盖\n" + standards,
                "json": json.dumps(result, ensure_ascii=False, indent=2)}

    def current_artifacts(self) -> dict:
        artifacts = deepcopy(self._turn_artifacts)
        if "result" in artifacts:
            artifacts["report"] = self.present_result(artifacts["result"])
        return artifacts

    def chat(self, text: str) -> DesignTurn:
        self._events.clear()
        self._turn_artifacts.clear()
        self.protected_dimensions = protected_dimensions(text)
        try:
            result = self.loop.run(text)
            return DesignTurn(result.content, result.trace, self.current_artifacts(), deepcopy(self._events))
        except ModelError as exc:
            trace = [deepcopy(event.data) for event in self._events if event.name == "tool_finished"]
            return DesignTurn(f"本轮模型请求未完成：{exc} 已完成的操作已保留，可继续对话。",
                              trace, self.current_artifacts(), deepcopy(self._events), failed=True)
        finally:
            self.protected_dimensions.clear()
