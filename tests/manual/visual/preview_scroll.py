"""Offline report + comparison fixture for expander/tab scrolling regressions."""
from collections import deque
from pathlib import Path
import json
import sys
from time import sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import streamlit as st
from src.bootstrap import create_agent
from src.llm.protocol import ModelDelta, ModelInfo, ModelResponse, ToolCall
from src.webui.app import main


class Model:
    configured = True

    def __init__(self):
        self.responses = deque([
            ModelResponse(tool_calls=tuple(ToolCall(action, "run_skill_action", json.dumps({
                "skill_id": "retaining_wall", "action_id": action, "input": {}}))
                for action in ("calculate_design", "compare_designs"))),
            ModelResponse("已计算当前方案，并找到可选的教学候选。可在计算书内核对各章节和方案表。\n\n"
                          "地基承载力、沉降、整体稳定、墙身强度及实际造价仍待补充资料。"),
        ])

    def complete(self, messages, tools):
        return self.responses.popleft() if self.responses else ModelResponse("已保留本次输入。")

    def complete_stream(self, messages, tools, on_delta):
        if self.responses:
            return self.complete(messages, tools)
        content = ""
        for index in range(1, 61):
            delta = f"滚动跟随验证第 {index} 行：生成新内容时可以向上阅读已有计算书。\n\n"
            content += delta
            on_delta(ModelDelta(delta))
            sleep(0.1)
        return ModelResponse(content)

    def current_model(self):
        return ModelInfo("offline-scroll", "离线滚动复核")


if "design_agent" not in st.session_state:
    folder = Path(__file__).resolve().parents[2] / ".runtime/visual/scroll"
    agent = create_agent(Model(), db_path=folder / "preview.sqlite3")
    agent.execute_action("retaining_wall", "load_case", {"title": "坡顶均布荷载"})
    agent.chat("请计算并给出不同方案")
    st.session_state.design_agent = agent

main()
