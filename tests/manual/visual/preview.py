from pathlib import Path
import sys
from time import sleep
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import streamlit as st
from dataclasses import replace
import json
from src.config import ProjectConfig, ModelConfig, StorageConfig
from src.bootstrap import create_agent
from src.config import SkillsConfig

def skill_call(identifier, name, arguments="{}"):
    if name == "read_skill":
        return ToolCall(identifier, name, arguments)
    return ToolCall(identifier, "run_skill_action", json.dumps({"skill_id":"retaining_wall", "action_id":name, "input":json.loads(arguments)}))

from src.llm.protocol import ModelInfo, ModelResponse, ModelDelta, ToolCall
from src.webui.app import main

class PreviewModel:
    configured = True
    reasoning_effort = "default"
    selected = "deepseek-flash"
    def current_model(self):
        return ModelInfo(self.selected, self.selected, ("default", "low", "high", "max"))
    def discover_models(self, **kwargs):
        return [self.current_model(), ModelInfo("demo-pro", "Demo Pro", ("default", "high"))]
    def select_model(self, identifier, effort="default"):
        self.selected, self.reasoning_effort = identifier, effort
    def complete_stream(self, messages, tools, on_delta):
        if messages[-1].role == "user":
            sleep(1.5)
            on_delta(ModelDelta(reasoning=True))
            sleep(2)
            return ModelResponse(tool_calls=(skill_call("read", "read_skill", '{"name":"retaining_wall"}'),
                skill_call("check", "validate_parameters", "{}"), skill_call("calc", "calculate_design", "{}")))
        text = "已完成这组参数的教学初算。抗滑系数为 1.595，抗倾覆系数为 3.418，均达到已选 GB 50330 的教学对照值。地基承载力与整体稳定仍需补充资料；可在计算书中逐项复核。"
        for position in range(0, len(text), 3):
            on_delta(ModelDelta(text[position:position+3]))
            sleep(.14)
        return ModelResponse(text, finish_reason="stop")

if "design_agent" not in st.session_state:
    location = Path(__file__).resolve().parents[2] / ".runtime/visual"
    app = create_agent(PreviewModel(), config=ProjectConfig(skills=replace(SkillsConfig(), workspace=Path(__file__).resolve().parents[2] / ".runtime/visual/workspace"),llm=ModelConfig(api_key=""),
        storage=StorageConfig(location / "cases.sqlite3", location / "conversations.sqlite3")))
    app.execute_action("retaining_wall", "load_case", {"title":"基础干燥填土"})
    st.session_state.design_agent = app
main()
