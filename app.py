"""Conversational teaching prototype. Launch with: python -m streamlit run app.py"""

from __future__ import annotations

import json

import streamlit as st

from core import FIELD_LABELS
from memory import list_cases, list_teaching_presets, load_case, save_case
from report import calculation_markdown, standards_markdown, summary_markdown
from standards import STANDARDS
from tool_agent import calculate_confirmed, deepseek_tool_turn, get_api_key


st.set_page_config(page_title="挡土墙对话式智能体", page_icon="🧱", layout="wide")
st.title("🧱 挡土墙初算对话式智能体")
st.caption("直接和 DeepSeek 讨论挡土墙；它会自行决定是否调用本地计算、比选和案例工具。")


def empty_agent_state() -> dict:
    return {"values": {}, "trace": [], "answer": "", "result": None}


if "agent_state" not in st.session_state:
    st.session_state.agent_state = empty_agent_state()
if "messages" not in st.session_state:
    st.session_state.messages = []


def append_case(title: str, values: dict, selected_codes: list[str]) -> None:
    """Load a stored case into the same confirmation flow as chat input."""
    prior = empty_agent_state()
    prior["values"] = dict(values)
    prior["sources"] = {key: f"数据库案例：{title}" for key in values}
    prior["wall_shape"] = "rectangle" if values.get("top_width_m") == values.get("base_width_m") else "trapezoid"
    outcome = deepseek_tool_turn(
        f"我从侧栏载入了“{title}”案例。先帮我看看这些参数，等我提出要求再决定是否计算。",
        prior, history=st.session_state.messages, selected_standards=selected_codes,
        cases={title: values},
    )
    st.session_state.agent_state = outcome["state"]
    st.session_state.messages.append({"role": "user", "content": f"载入数据库案例：{title}"})
    st.session_state.messages.append({"role": "assistant", "content": outcome["content"], "trace": outcome.get("trace", [])})


with st.sidebar:
    st.header("对话设置")
    has_key = bool(get_api_key())
    st.caption("DeepSeek 自主决定是否调用计算、比选和案例工具。")
    if not has_key:
        st.warning(
            "尚未配置 DeepSeek API Key：请设置环境变量 DEEPSEEK_API_KEY，"
            "或复制 local_config.example.py 为 local_config.py 并填入密钥，然后重启应用。"
        )
    selected_codes = st.multiselect(
        "本轮展示的规范", list(STANDARDS),
        default=["GB 50330-2013", "GB 55003-2021"],
    )
    st.caption("规范选择从下一条消息或下一次载入案例起生效；未覆盖的验算会明确标出。")
    if st.button("新建对话", use_container_width=True):
        st.session_state.agent_state = empty_agent_state()
        st.session_state.messages = []

    st.divider()
    st.header("教学案例库")
    presets = list_teaching_presets()
    preset_by_title = {item["title"]: item for item in presets}
    preset_title = st.selectbox("数据库中的教学案例", list(preset_by_title))
    st.caption(preset_by_title[preset_title]["note"])
    if st.button("载入教学案例并核对", use_container_width=True):
        try:
            append_case(preset_title, preset_by_title[preset_title]["values"], selected_codes)
        except ValueError as exc:
            st.error(str(exc))

    saved = list_cases()
    if saved:
        labels = {f"{item['id']} · {item['title']}": item["id"] for item in saved}
        saved_title = st.selectbox("我保存的案例", list(labels))
        if st.button("载入保存案例并核对", use_container_width=True):
            old = load_case(labels[saved_title])
            try:
                append_case(old["title"], old["values"], selected_codes)
            except ValueError as exc:
                st.error(str(exc))

    current_result = st.session_state.agent_state.get("result")
    if current_result:
        case_name = st.text_input("保存案例名称", value="对话算例")
        if st.button("保存当前参数与结果", use_container_width=True):
            case_id = save_case(case_name, st.session_state.agent_state["values"], current_result)
            st.success(f"已保存案例 #{case_id}。")

    st.divider()
    st.caption("当前模型：干燥、水平无黏性填土；直立墙背、水平基底、非地震，墙高小于 5 m。")


def show_message(item: dict, index: int) -> None:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
        result = item.get("result")
        if result:
            st.markdown(summary_markdown(result))
            with st.expander("查看完整计算过程与所需补充资料"):
                st.markdown(calculation_markdown(result))
            with st.expander("查看所选规范的覆盖状态"):
                st.markdown(standards_markdown(result))
            st.download_button(
                "下载本次计算 JSON",
                json.dumps(result, ensure_ascii=False, indent=2),
                file_name=f"wall_result_{index}.json", mime="application/json",
                key=f"download_{index}",
            )
            st.download_button(
                "下载计算说明 Markdown",
                summary_markdown(result) + "\n\n" + calculation_markdown(result) + "\n\n### 规范覆盖\n" + standards_markdown(result),
                file_name=f"wall_report_{index}.md", mime="text/markdown",
                key=f"report_{index}",
            )
        comparison = item.get("comparison")
        if comparison:
            st.table([{
                "方案": row["label"],
                "墙底宽 B (m)": f"{row['base_width_m']:.2f}",
                "墙体自重 (kN/m)": f"{row['wall_weight_kn_m']:.2f}",
                "用量变化": f"{row['weight_change_percent']:+.1f}%",
                "抗滑": f"{row['sliding_ratio']:.3f}",
                "抗倾覆": f"{row['overturning_ratio']:.3f}",
                "基底最大压力 (kPa)": f"{row['base_pressure_max_kpa']:.2f}" if row["base_pressure_max_kpa"] is not None else "无法估算",
                "教学筛选": "达到" if row["passes_teaching_screen"] else "未达到",
            } for row in comparison["candidates"]])
            st.caption(comparison["scope"])
        optimization = item.get("optimization")
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
                for trace_item in item["trace"]:
                    st.json(trace_item, expanded=False)


prompt = st.chat_input("说出设计条件、修改要求，或直接问计算结果")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    try:
        cases = {item["title"]: item["values"] for item in presets}
        for item in saved:
            cases[f"{item['id']} · {item['title']}"] = load_case(item["id"])["values"]
        outcome = deepseek_tool_turn(prompt, st.session_state.agent_state,
                                     history=st.session_state.messages[:-1],
                                     selected_standards=selected_codes, cases=cases)
        st.session_state.agent_state = outcome["state"]
        reply = {"role": "assistant", "content": outcome["content"],
                 "result": outcome.get("result"), "comparison": outcome.get("comparison"),
                 "optimization": outcome.get("optimization"),
                 "trace": outcome.get("trace", [])}
    except Exception as exc:
        reply = {"role": "assistant", "content": f"本轮没有完成：{exc}。已保留此前的对话，请检查输入后再试。"}
    st.session_state.messages.append(reply)

for index, message in enumerate(st.session_state.messages):
    show_message(message, index)

pending = st.session_state.agent_state
if pending.get("awaiting_confirmation"):
    st.subheader("核对本次计算参数")
    st.caption("请检查数值、单位和来源；可直接改数值。确认后才会生成计算结果。")
    with st.form("confirm_parameters"):
        columns = st.columns(4)
        confirmed = {}
        for i, (key, label) in enumerate(FIELD_LABELS.items()):
            box = columns[i % 4]
            if key == "top_width_m" and pending.get("wall_shape") == "rectangle":
                box.write(f"{label}：等于墙底宽（矩形墙）")
                continue
            confirmed[key] = box.number_input(
                label, min_value=0.0, value=float(pending["values"][key]),
                step=0.1, key=f"confirm_{len(pending['trace'])}_{key}",
            )
            box.caption("来源：" + pending.get("sources", {}).get(key, "待确认"))
        submitted = st.form_submit_button("确认并计算", type="primary")
    if submitted:
        if pending.get("wall_shape") == "rectangle":
            confirmed["top_width_m"] = confirmed["base_width_m"]
        ready = dict(pending)
        ready["values"] = confirmed
        ready["sources"] = dict(pending.get("sources", {}))
        for key, value in confirmed.items():
            if value != pending["values"].get(key):
                ready["sources"][key] = "确认界面修改"
        ready["trace"] = list(pending.get("trace", [])) + [{"step": "参数确认", "output": confirmed.copy()}]
        try:
            next_state = calculate_confirmed(ready, selected_codes)
            st.session_state.agent_state = next_state
            content = next_state["answer"]
            try:
                outcome = deepseek_tool_turn("我已在表单确认参数，计算已经完成。请结合结果回答我。",
                                             next_state, history=st.session_state.messages,
                                             selected_standards=selected_codes)
                content = outcome["content"]
            except ValueError as exc:
                content = f"DeepSeek 暂时无法生成回答：{exc}。计算结果见下方。"
                next_state["trace"].append({"step": "自然语言回复", "output": str(exc)})
            reply = {
                "role": "assistant", "content": content,
                "result": next_state["result"], "trace": next_state["trace"][-4:],
            }
            st.session_state.messages.append(reply)
            show_message(reply, len(st.session_state.messages) - 1)
        except ValueError as exc:
            st.error(f"请修改参数：{exc}")
