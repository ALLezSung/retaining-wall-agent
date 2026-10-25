from pathlib import Path
import sys
from time import sleep
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import streamlit as st
from src.config import ProjectConfig, ModelConfig, StorageConfig, AgentConfig
from src.design_agent import DesignAgent
from src.llm.protocol import ModelDelta, ModelResponse, ModelError, ToolCall
from src.webui.app import main

class FakeModel:
    configured=True
    total_requests=0
    step=0
    mode="normal"
    def complete_stream(self,messages,tools,on_delta):
        if messages[-1].role=="user":
            self.mode="interrupt" if "中断" in messages[-1].content else "limit" if "上限" in messages[-1].content else "normal"
            self.step=0
        step=self.step
        self.step+=1
        self.total_requests+=1
        if step==0:
            text="先读取技能说明，再核对方案参数。"
            calls=(ToolCall("read","read_skill",'{"name":"retaining_wall"}'),)
        elif step==1:
            text="核对已载入的教学参数是否齐全。"
            calls=(ToolCall("check","validate_parameters","{}"),)
        elif step==2 and self.mode=="limit":
            text="继续计算当前方案，随后汇总结果。"
            calls=(ToolCall("calc","calculate_design","{}"),)
        else:
            text="中断回答：已保留当前输出片段。" if self.mode=="interrupt" else "最终回答：**参数核对完成。**\n\n可以使用当前参数继续核算，并逐项检查所选规范的要求。"
            calls=()
        for position in range(0,len(text),1 if not calls else 3):
            on_delta(ModelDelta(text[position:position+(1 if not calls else 3)]))
            sleep(.065)
        sleep(.35)
        if self.mode=="interrupt" and not calls:
            raise ModelError("假模型模拟连接中断")
        return ModelResponse(text,calls,"tool_calls" if calls else "stop")

if "design_agent" not in st.session_state:
    folder=Path(__file__).resolve().parents[2] / ".runtime/visual"
    agent=DesignAgent(FakeModel(),config=ProjectConfig(llm=ModelConfig(api_key=""),agent=AgentConfig(3),storage=StorageConfig(folder/"live-slots-cases.sqlite3",folder/"live-slots-chats.sqlite3")))
    agent.load_teaching_case("基础干燥填土")
    st.session_state.design_agent=agent
main()
st.caption(f"FAKE_MODEL_REQUESTS: {st.session_state.design_agent.loop.model.total_requests}")
