from pathlib import Path
import sys
from time import sleep
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import streamlit as st
from src.config import ProjectConfig, ModelConfig, StorageConfig
from src.design_agent import DesignAgent
from src.llm.protocol import ModelDelta, ModelResponse, ToolCall
from src.webui.app import main

class PreviewModel:
    configured=True
    count=0
    def complete_stream(self,messages,tools,on_delta):
        index=self.count % 4
        self.count+=1
        content=["我先读取核算技能说明，再核对方案参数。", "先计算当前方案，检查基底最大压力与稳定性。", "当前方案最大压力为 70.83 kPa，超过 60 kPa。我将底宽调整为 2.7 m 后重新核算。", "**调整后的方案满足基底最大压力小于 60 kPa 的要求。**\n\n底宽采用 **2.7 m**，最大压力约 **57.81 kPa**；抗滑系数为 **1.760**，抗倾覆系数为 **4.258**。计算书中保留了逐项结果与待补充资料。"] [index]
        calls=[(ToolCall("read","read_skill",'{"name":"retaining_wall"}'),), (ToolCall("calc","calculate_design","{}"),), (ToolCall("edit","set_parameters",'{"base_width_m":2.7}'),ToolCall("recalc","calculate_design","{}")), ()][index]
        for start in range(0,len(content),4):
            on_delta(ModelDelta(content[start:start+4]))
            sleep(.035)
        return ModelResponse(content,calls,"tool_calls" if calls else "stop")

if "design_agent" not in st.session_state:
    location=Path(__file__).resolve().parents[2] / ".runtime/visual"
    app=DesignAgent(PreviewModel(),max_rounds=6,config=ProjectConfig(llm=ModelConfig(api_key=""),storage=StorageConfig(location/"run-notes-cases.sqlite3",location/"run-notes-conversations.sqlite3")))
    app.load_teaching_case("基础干燥填土")
    app.chat("核算该方案，需要满足基底最大压力小于60kPa，如果不行请优化。")
    st.session_state.design_agent=app
main()
