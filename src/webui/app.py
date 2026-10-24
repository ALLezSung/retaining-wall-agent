"""Streamlit presentation. All design mutations go through DesignAgent."""
from __future__ import annotations

import streamlit as st

from ..design_agent import DesignAgent, DesignTurn
from ..config import ConfigError


def append_turn(turn: DesignTurn) -> None:
    st.session_state.messages.append({
        "role": "assistant", "content": turn.content, "artifacts": turn.artifacts,
        "trace": turn.trace,
        "events": [{"event": item.name, **({"tool": item.data["tool"]} if "tool" in item.data else {})}
                   for item in turn.events],
    })


def discuss_loaded(agent: DesignAgent, title: str) -> None:
    text = f"我从侧栏载入了“{title}”案例。先帮我看看这些参数，等我提出要求再决定是否计算。"
    st.session_state.messages.append({"role": "user", "content": f"载入数据库案例：{title}"})
    append_turn(agent.chat(text))


def show_message(item: dict, index: int) -> None:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
        artifacts = item.get("artifacts", {})
        report = artifacts.get("report")
        if report:
            st.caption("此卡片保留本条消息生成时的计算记录；参数修改后需重新计算。")
            st.markdown(report["summary"])
            with st.expander("查看完整计算过程与所需补充资料"):
                st.markdown(report["calculation"])
            with st.expander("查看所选规范的覆盖状态"):
                st.markdown(report["standards"])
            st.download_button("下载本次计算 JSON", report["json"],
                               file_name=f"wall_result_{index}.json", mime="application/json", key=f"download_{index}")
            st.download_button("下载计算说明 Markdown", report["markdown"],
                               file_name=f"wall_report_{index}.md", mime="text/markdown", key=f"report_{index}")
        optimization = artifacts.get("optimization")
        if optimization:
            if optimization["recommended"] is None:
                st.warning(optimization["reason"])
            st.table([{
                "方案": row["label"],
                "墙底宽 B (m)": f"{row['base_width_m']:.2f}",
                "墙顶宽 t (m)": f"{row['top_width_m']:.2f}",
                "墙体自重 (kN/m)": f"{row['wall_weight_kn_m']:.2f}",
                "用量变化": f"{row['weight_change_percent']:+.1f}%",
                "抗滑": f"{row['sliding_ratio']:.3f}",
                "抗倾覆": f"{row['overturning_ratio']:.3f}",
                "基底接触": row["base_contact"],
                "教学筛选": "达到" if row["passes_teaching_screen"] else "未达到",
            } for row in optimization["candidates"]])
            st.caption(optimization["scope"])
        if item.get("trace"):
            with st.expander("查看本轮工具调用记录"):
                for trace in item["trace"]:
                    st.json(trace, expanded=False)
        if item.get("events"):
            with st.expander("查看本轮执行事件"):
                st.json(item["events"], expanded=False)


def main() -> None:
    st.set_page_config(page_title="挡土墙对话式智能体", page_icon="🧱", layout="wide")
    st.title("🧱 挡土墙初算对话式智能体")
    st.caption("直接讨论挡土墙；助手会按需读取技能说明并调用本地计算、比选和案例工具。")
    if "design_agent" not in st.session_state:
        try:
            st.session_state.design_agent = DesignAgent()
        except ConfigError as exc:
            st.error(str(exc))
            st.stop()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    agent = st.session_state.design_agent

    with st.sidebar:
        st.header("对话设置")
        if not agent.model_configured:
            st.warning("尚未配置 DeepSeek API Key：请设置 DEEPSEEK_API_KEY，或在 config.toml 的 [llm] 中填写 api_key，然后重启应用。")
        selected = st.multiselect("本轮展示的规范", agent.standard_choices(),
                                 default=agent.read_session().selected_standards)
        agent.select_standards(selected)
        st.caption("所选规范用于下一轮查询及计算覆盖展示；历史报告保持原记录。")
        if st.button("新建对话", use_container_width=True):
            agent.reset()
            st.session_state.messages = []

        st.divider()
        st.header("教学案例库")
        presets = agent.teaching_cases()
        by_title = {item["title"]: item for item in presets}
        title = st.selectbox("数据库中的教学案例", list(by_title))
        if title:
            st.caption(by_title[title]["note"])
        if st.button("载入教学案例并核对", use_container_width=True):
            try:
                agent.load_teaching_case(title)
                discuss_loaded(agent, title)
            except (ValueError, OSError) as exc:
                st.error(str(exc))

        saved = agent.saved_cases()
        if saved:
            labels = {f"{item['id']} · {item['title']}": item["id"] for item in saved}
            saved_title = st.selectbox("我保存的案例", list(labels))
            if st.button("载入保存案例并核对", use_container_width=True):
                try:
                    loaded = agent.load_saved_case(labels[saved_title])
                    discuss_loaded(agent, loaded["data"]["loaded_case"])
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
        if agent.read_session().current_result:
            case_name = st.text_input("保存案例名称", value="对话算例")
            if st.button("保存当前参数与结果", use_container_width=True):
                try:
                    case_id = agent.save_case(case_name)
                    st.success(f"已保存案例 #{case_id}。")
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
        st.divider()
        st.caption("当前模型：干燥、水平无黏性填土；直立墙背、水平基底、非地震，墙高小于 5 m。")

    prompt = st.chat_input("说出设计条件、修改要求，或直接问计算结果")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.spinner("正在处理…"):
            append_turn(agent.chat(prompt))
        st.rerun()  # Refresh sidebar actions against the new authoritative state.

    st.sidebar.caption("当前方案：已有有效计算结果。" if agent.read_session().current_result
                       else "当前方案：尚无有效计算结果。")
    for index, message in enumerate(st.session_state.messages):
        show_message(message, index)
