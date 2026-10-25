from pathlib import Path
import sys
from time import sleep
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import streamlit as st
from src.config import ProjectConfig, ModelConfig, StorageConfig
from src.design_agent import DesignAgent
from src.llm.protocol import ModelInfo, ModelResponse, ModelDelta, ToolCall
from src.webui.app import main

class PreviewModel:
    configured = True
    reasoning_effort = "default"
    selected = "deepseek-flash"
    def current_model(self):
        return next(item for item in self.discover_models() if item.id == self.selected)
    def discover_models(self, **kwargs):
        return [ModelInfo("deepseek-flash", "DeepSeek Flash", ("default", "low", "high", "max")), ModelInfo("demo-pro", "Demo Pro", ("default", "high"))]
    def select_model(self, identifier, effort="default"):
        self.selected, self.reasoning_effort = identifier, effort
    def complete_stream(self, messages, tools, on_delta):
        if messages[-1].role == "user":
            sleep(1.5)
            on_delta(ModelDelta(reasoning=True))
            sleep(2)
            return ModelResponse(tool_calls=(ToolCall("read", "read_skill", '{"name":"retaining_wall"}'),
                ToolCall("check", "validate_parameters", "{}"), ToolCall("calc", "calculate_design", "{}")))
        text = "已完成这组参数的教学初算。抗滑系数为 1.595，抗倾覆系数为 3.418，均达到已选 GB 50330 的教学对照值。地基承载力与整体稳定仍需补充资料；可在计算书中逐项复核。"
        for position in range(0, len(text), 3):
            on_delta(ModelDelta(text[position:position+3]))
            sleep(.14)
        return ModelResponse(text, finish_reason="stop")

if "design_agent" not in st.session_state:
    location = Path(__file__).resolve().parents[2] / ".runtime/visual"
    app = DesignAgent(PreviewModel(), config=ProjectConfig(llm=ModelConfig(api_key=""),
        storage=StorageConfig(location / "review-cases.sqlite3", location / "review-conversations.sqlite3")))
    for index in range(8):
        app.load_teaching_case("基础干燥填土")
        app.rename_conversation(app.conversation_id, f"教学方案 {index+1:02d}")
        app.reset()
    app.load_teaching_case("基础干燥填土")
    app.rename_conversation(app.conversation_id, "基础挡墙方案")
    st.session_state.design_agent = app
main()
