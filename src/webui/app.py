"""Streamlit presentation; authoritative changes go through the application port."""
from __future__ import annotations

from html import escape
from pathlib import Path
from time import monotonic

import streamlit as st

from ..bootstrap import create_agent
from ..design_agent import DesignAgent
from ..config import ConfigError


EFFORT_LABELS = {"default": "Default", "none": "OFF", "minimal": "Minimal", "low": "Light",
                 "medium": "Medium", "high": "High", "xhigh": "xHigh", "max": "Max", "ultra": "Ultra"}
TOOL_LABELS = {"read_skill": "读取技能说明", "read_skill_resource": "读取参考资源", "run_skill_action": "执行技能操作", "read_artifact": "读取成果"}
EVENT_LABELS = {"run_started": "开始执行", "model_started": "请求模型", "model_finished": "模型返回",
                "tool_started": "开始工具", "tool_finished": "工具完成", "state_updated": "状态更新",
                "skill_started": "开始业务操作", "skill_finished": "业务操作结束", "artifact_created": "生成成果",
                "message_completed": "回复完成", "run_finished": "本轮完成", "run_failed": "本轮中断",
                "run_limit_reached": "达到工具轮次上限", "run_stopped": "本轮暂停"}
STYLE = """
<style>
:root { --wall-accent: #bd684b; }
.stApp { background: #faf9f6; }
[data-testid="stSidebar"] { background: #f0eee8; border-right: 1px solid #e5e1d8; }
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .65rem; }
[data-testid="stMainBlockContainer"] { max-width: 1080px; padding-top: 4.5rem; padding-bottom: 5rem; }
/* Expanded reports retain their intrinsic height above the bottom composer. */
[data-testid="stMainBlockContainer"], [data-testid="stBottom"] { flex-shrink: 0; }
main.stMain { overflow-anchor: none; }
[data-testid="stLayoutWrapper"]:has(> .st-key-scroll_controller) { display:none; }
h1 { font-size: 1.9rem !important; letter-spacing: -.04em; }
[data-testid="stChatMessage"] { background: transparent; padding: 1.1rem .25rem; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { background: #f0eee8; border-radius: 12px; padding: 1rem; }
[data-testid="stExpander"] { background: #fffdfa; border: 1px solid #e4dfd5; border-radius: 10px; }
[data-testid="stExpander"] details summary { min-height: 2.8rem; }
[data-testid="stExpander"] details summary p { font-weight: 600; }
.stButton button, .stDownloadButton button { border-radius: 8px; }
[data-testid="stSidebar"] .stButton button { font-size: .88rem; }
[data-testid="stTabs"] button { font-size: .85rem; }
[data-testid="stChatInput"] { border-radius: 14px; }
.eyebrow { color: #99816c; font-size: .72rem; letter-spacing: .16em; margin-bottom: .35rem; }
.welcome { padding: 2.2rem 0 1rem; color: #766e63; line-height: 1.9; }
.agent-live { display:flex; align-items:flex-start; gap:12px; padding:12px 0; font-size:.9rem; color:#74695e; }
.agent-spark { display:inline-block; color:#bd684b; font-size:25px; line-height:26px; animation:agent-turn 2.6s linear infinite, agent-breathe 1.3s ease-in-out infinite alternate; }
.agent-live strong { color:#a6553b; font-weight:600; }
.agent-live small { display:block; color:#8e867a; margin-top:4px; }
[class*="st-key-run_notes_"] [data-testid="stMarkdownContainer"] { color:#77736c; font-size:.9rem; line-height:1.65; }
[class*="st-key-run_notes_"] [data-testid="stMarkdownContainer"] :is(strong,b,h1,h2,h3,h4,h5,h6) { color:inherit; font-size:inherit !important; font-weight:400; }
[class*="st-key-run_notes_"] ul { padding-left:1.25rem; margin:.15rem 0 .7rem; }
[class*="st-key-run_notes_"] li::marker { color:#aaa398; }
[class*="st-key-run_notes_"] li > p { margin-bottom:.25rem; }
.run-track { display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; }
.run-chip { font-size:.72rem; background:#eeebe4; border-radius:5px; padding:2px 7px; }
.run-chip.active { color:#a6553b; background:#f5e6dd; }
@keyframes agent-turn { to { transform:rotate(360deg); } }
@keyframes agent-breathe { from { opacity:.45; } to { opacity:1; } }
@media (prefers-reduced-motion:reduce) { .agent-spark { animation:none; } }
@media (max-width:700px) { [data-testid="stMainBlockContainer"] { padding-top:4rem; } h1 {font-size:1.5rem !important;} }

[data-testid="stSidebarUserContent"] { height: calc(100dvh - 6rem); padding-bottom: 0; }
[data-testid="stSidebarUserContent"] > div { height:100%; }
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] { height:100%; }
[data-testid="stLayoutWrapper"]:has(> .st-key-sidebar_content) { flex:1 1 0; min-height:0; overflow:hidden; }
[data-testid="stLayoutWrapper"]:has(> .st-key-sidebar_model_controls) { flex:0 0 auto; }
.st-key-sidebar_content { flex:1 1 auto; min-height:0; overflow-y:auto; padding-right:3px; }
.st-key-sidebar_model_controls { flex:0 0 auto; padding-top:12px; padding-bottom:12px; border-top:1px solid #dcd6cb; background:#f0eee8; }
.st-key-conversation_history [data-testid="stHorizontalBlock"] { align-items:center; }
.st-key-conversation_history .stButton button { min-height:2.25rem; padding:.25rem .3rem; }
.st-key-conversation_history .stButton button p { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.st-key-conversation_history [data-testid="stTextInput"] input { border-color:#bd684b; }
.st-key-conversation_history [class*="st-key-conversation_"][class*="_active"] { background:#f4e2d7; border-radius:8px; }
.st-key-conversation_history [data-testid="stHorizontalBlock"], .st-key-sidebar_model_controls [data-testid="stHorizontalBlock"] { flex-wrap:nowrap; }
.st-key-conversation_history [data-testid="stColumn"], .st-key-sidebar_model_controls [data-testid="stColumn"] { min-width:0 !important; }
</style>
"""


class LiveTurn:
    """Synchronous UI subscriber. No provider requests or business mutations."""
    def __init__(self, notes_slot, body_slot, status_slot, max_runs):
        self.notes_slot, self.body_slot, self.status_slot = notes_slot, body_slot, status_slot
        self.max_runs = max_runs
        self.run_messages: list[dict] = []
        self.current_text = ""
        self.current_is_final = False
        self.run = 0
        self.steps: list[str] = []
        self.tool_label = ""
        self.stage = "准备执行"
        self.last_paint = 0.0
        self.last_status = ""
        self.finished_runs: set[int] = set()
        self.last_notes = ""
        self.last_body = ""
        self.paint_status()

    def paint_status(self):
        chips = "".join(f'<span class="run-chip">✓ {escape(step)}</span>' for step in self.steps[-4:])
        chips += f'<span class="run-chip active">Run {self.run or 1}</span>'
        rendered = (f'<div class="agent-live" role="status" aria-live="polite">'
                    f'<span class="agent-spark" aria-hidden="true">✳</span><div>'
                    f'<strong>Run {self.run or 1}</strong> · {escape(self.stage)}'
                    f'<small>工具执行结果会继续交给 agent，完成后收录在系统状态中。</small>'
                    f'<div class="run-track">{chips}</div></div></div>')
        if rendered != self.last_status:
            self.status_slot.markdown(rendered, unsafe_allow_html=True)
            self.last_status = rendered

    def paint_text(self):
        # Clear the body before moving a confirmed tool-run note to the list,
        # so the same text is never displayed in both slots at once.
        if self.current_text != self.last_body:
            if self.current_text:
                self.body_slot.markdown(self.current_text)
            else:
                self.body_slot.empty()
            self.last_body = self.current_text
        notes = run_notes_markdown(self.run_messages)
        if notes != self.last_notes:
            if notes:
                self.notes_slot.markdown(notes)
            else:
                self.notes_slot.empty()
            self.last_notes = notes

    def finish(self, turn):
        """Reconcile final/failed output in the same slots used for streaming."""
        self.run_messages = list(turn.run_messages)
        self.current_text = turn.content
        self.current_is_final = True
        self.paint_text()

    def __call__(self, event):
        data = event.data
        if event.name == "model_started":
            self.run = data["run"]
            self.current_text = ""
            self.current_is_final = False
            self.paint_text()
            self.stage = "达到轮次上限，正在汇总已有结果" if data.get("final_round") else "等待模型响应"
        elif event.name == "model_reasoning":
            self.stage = "正在思考"
        elif event.name == "message_delta":
            self.current_text += data["content"]
            self.stage = "正在生成回复"
            if monotonic() - self.last_paint > .035:
                self.paint_text()
                self.last_paint = monotonic()
        elif event.name == "skill_started":
            self.stage = data["label"]
            self.tool_label = data["label"]
        elif event.name == "tool_started":
            self.stage = TOOL_LABELS.get(data["tool"], data["tool"])
            self.tool_label = self.stage
        elif event.name == "tool_finished":
            label = self.tool_label or TOOL_LABELS.get(data["tool"], data["tool"])
            self.tool_label = ""
            self.steps.append(label if data["output"].get("ok") else label + "（待处理）")
            self.stage = "整理工具结果"
        elif event.name == "model_finished":
            if data.get("run", self.run) != self.run or self.run in self.finished_runs:
                return
            self.finished_runs.add(self.run)
            text = (data.get("content") or self.current_text).strip()
            if data.get("tool_count"):
                if text:
                    self.run_messages.append({"run": self.run, "content": text})
                self.current_text = ""
            else:
                self.current_text = text
                self.current_is_final = True
            self.paint_text()
        elif event.name == "message_completed":
            self.current_text = data["content"]
            self.current_is_final = True
            self.paint_text()
        elif event.name == "run_failed":
            self.stage = "本轮中断，已完成操作保留"
        self.paint_status()


def show_details(item: dict, key: str, agent=None):
    artifacts = item.get("artifacts", {}).get("items", [])
    groups = {}
    for artifact in artifacts:
        groups.setdefault(artifact.get("group", artifact["title"]), []).append(artifact)
    for title, members in groups.items():
        with st.expander(title, expanded=False):
            for artifact in members:
                if artifact.get("summary"):
                    st.caption(artifact["summary"])
                if artifact.get("content"):
                    st.markdown(artifact["content"])
                if artifact.get("sections"):
                    tabs = st.tabs([s["title"] for s in artifact["sections"]])
                    for tab, section in zip(tabs, artifact["sections"]):
                        with tab:
                            st.markdown(section["content"])
                if artifact["kind"] == "table":
                    st.table(artifact.get("rows", []))
                attachments = artifact.get("attachments", [])
                if attachments and agent:
                    columns = st.columns(len(attachments))
                    for index, (column, attachment) in enumerate(zip(columns, attachments)):
                        with column:
                            try:
                                st.download_button(attachment["label"], agent.artifact_bytes(attachment), file_name=attachment["filename"],
                                    mime=attachment["mime"], key=f"download_{key}_{artifact['id']}_{index}", use_container_width=True)
                            except (OSError, ValueError) as exc:
                                st.error(f"附件暂不可读取：{exc}")
    events, trace = item.get("events", []), item.get("trace", [])
    if events or trace:
        with st.expander("系统状态", expanded=False):
            runs = max((event.get("run", 0) for event in events), default=0)
            elapsed = max((event.get("elapsed_seconds", 0) for event in events), default=0)
            state = "本轮中断，已完成操作保留" if item.get("failed") else "执行完成"
            if item.get("stop_reason") == "round_limit":
                state = "已达工具轮次上限，可继续执行"
            st.caption(f"{state} · {runs} 个 Run · {len(trace)} 次工具调用 · {elapsed:.1f} 秒")
            if item.get("model"):
                st.caption(f"{item['model']} · 思考深度：{EFFORT_LABELS.get(item.get('reasoning_effort'), 'Default')}")
            execution, tools = st.tabs(["执行情况", "工具调用记录"])
            with execution:
                st.table([{"Run": str(event.get("run") or "—"), "阶段": EVENT_LABELS.get(event.get("event"), event.get("event", "")),
                           "说明": event.get("label", TOOL_LABELS.get(event.get("tool"), event.get("kind", ""))),
                           "耗时 (s)": str(event.get("elapsed_seconds", "—"))} for event in events])
            with tools:
                if trace:
                    selected = st.selectbox("选择工具调用", range(len(trace)),
                        format_func=lambda i: f"Run {trace[i].get('run', '—')} · {TOOL_LABELS.get(trace[i]['tool'], trace[i]['tool'])} · {'完成' if trace[i]['output'].get('ok') else '失败'}",
                        key=f"trace_{key}")
                    call = trace[selected]
                    st.caption(f"{call['tool']} · {call.get('duration_seconds', 0):.3f} 秒")
                    st.code(call["arguments"], language="json")
                    st.json(call["output"], expanded=False)
                else:
                    st.caption("本轮没有调用工具。")


def run_notes_markdown(notes: list[dict]) -> str:
    # Indent continuation lines so each Run remains one unordered-list item.
    return "\n\n".join("- " + note["content"].strip().replace("\n", "\n  ")
                        for note in notes if note.get("content", "").strip())


def show_answer(item: dict, key: str):
    notes = run_notes_markdown(item.get("run_messages", []))
    if notes:
        with st.container(key=f"run_notes_{key}"):
            st.markdown(notes)
    if item.get("content"):
        st.markdown(item["content"])


def show_message(item: dict, index: int, conversation_id: str = "chat", agent=None):
    with st.chat_message(item["role"]):
        show_answer(item, f"{conversation_id}_{index}")
        show_details(item, f"{conversation_id}_{index}", agent)


def begin_title_edit(agent: DesignAgent, identifier: str, title: str):
    st.session_state["editing_conversation"] = identifier
    st.session_state[f"title_input_{identifier}"] = title
    st.session_state.pop("title_edit_error", None)


def save_title_edit(agent: DesignAgent, identifier: str):
    try:
        agent.rename_conversation(identifier, st.session_state[f"title_input_{identifier}"])
        st.session_state.pop("editing_conversation", None)
        st.session_state.pop("title_edit_error", None)
    except (RuntimeError, OSError, ValueError) as exc:
        st.session_state["title_edit_error"] = str(exc)


def conversation_history(agent: DesignAgent):
    try:
        records = agent.all_conversations()
        if not any(item["id"] == agent.conversation_id for item in records):
            # Keep the current draft editable/highlighted without storing it.
            records.insert(0, {"id": agent.conversation_id, "title": agent.conversation_title})
        with st.container(height=240, border=False, key="conversation_history"):
            if not records:
                st.caption("暂无已保存的对话")
            for item in records:
                identifier = item["id"]
                active = identifier == agent.conversation_id
                with st.container(key=f"conversation_{identifier}{'_active' if active else ''}"):
                    load, title, delete = st.columns([1, 5, 1], gap="small")
                    with load:
                        if st.button("●" if active else "↗", key=f"load_{identifier}",
                                     help="当前对话" if active else "载入此对话", use_container_width=True, disabled=active):
                            agent.load_conversation(identifier)
                            st.session_state.pop("editing_conversation", None)
                            st.rerun()
                    with title:
                        if st.session_state.get("editing_conversation") == identifier:
                            st.text_input("对话标题", key=f"title_input_{identifier}",
                                max_chars=80, label_visibility="collapsed",
                                on_change=save_title_edit, args=(agent, identifier),
                                placeholder="输入标题，按回车保存")
                        else:
                            st.button(item["title"], key=f"rename_{identifier}",
                                type="primary" if active else "secondary", use_container_width=True,
                                on_click=begin_title_edit, args=(agent, identifier, item["title"]))
                    with delete:
                        if st.button("×", key=f"delete_{identifier}", help="删除此对话", use_container_width=True):
                            agent.delete_conversation(identifier)
                            if st.session_state.get("editing_conversation") == identifier:
                                st.session_state.pop("editing_conversation", None)
                            st.rerun()
        if st.session_state.get("title_edit_error"):
            st.error(st.session_state["title_edit_error"])
    except (RuntimeError, OSError, ValueError) as exc:
        st.error(str(exc))


def sidebar(agent: DesignAgent):
    with st.sidebar:
        with st.container(key="sidebar_content"):
            st.markdown('<div class="eyebrow">DESIGN WORKSPACE</div>', unsafe_allow_html=True)
            if st.button("新建对话", type="primary", use_container_width=True):
                try:
                    agent.reset()
                    st.rerun()
                except RuntimeError as exc:
                    st.error(str(exc))
            st.subheader("载入对话")
            conversation_history(agent)
            view = agent.application_view()
            for panel in view.get("panels", []):
                with st.expander(panel["title"], expanded=panel.get("expanded", False)):
                    render_controls(agent, agent.config.skills.default_skill, panel.get("controls", []))
                    if panel.get("caption"):
                        st.caption(panel["caption"])
            if view.get("status"):
                st.caption(view["status"])
            for skill_id, extra in agent.supplementary_views().items():
                for panel in extra.get("panels", []):
                    with st.expander(panel["title"], expanded=panel.get("expanded", False)):
                        render_controls(agent, skill_id, panel.get("controls", []))
                if extra.get("status"):
                    st.caption(extra["status"])
        with st.container(key="sidebar_model_controls"):
            model_controls(agent)



def render_controls(agent, skill_id, controls):
    for control in controls:
        if not control.get("enabled", True):
            continue
        kind = control["type"]
        key = f"{skill_id}_{control['id']}_{agent.conversation_id}"
        try:
            if kind == "multiselect":
                value = st.multiselect(control["label"], control["options"], default=control["value"], key=key)
                if value != control["value"]:
                    output = agent.execute_action(skill_id, control["action"], {control["argument"]: value})
                    if not output["ok"]:
                        st.error(output["error"]["message"])
                    else:
                        st.rerun()
                continue
            if kind == "select_action":
                options = control["options"]
                if not options:
                    continue
                values = {option["value"]: option for option in options}
                value = st.selectbox(control["label"], list(values), format_func=lambda v: values[v]["label"], key=key)
                if values[value].get("note"):
                    st.caption(values[value]["note"])
            elif kind == "text_action":
                value = st.text_input(control["label"], value=control.get("value", ""), key=key)
            elif kind == "button":
                value = control.get("value")
            else:
                st.error("暂不支持该控件类型。")
                continue
            if st.button(control["button"], key=key + "_execute", use_container_width=True):
                args = {control["argument"]: value} if control.get("argument") else {}
                output = agent.execute_action(skill_id, control["action"], args, new_conversation=control.get("new_conversation", False))
                if output["ok"]:
                    st.rerun()
                else:
                    st.error(output["error"]["message"])
        except (RuntimeError, OSError, ValueError) as exc:
            st.error(str(exc))


def model_controls(agent: DesignAgent):
    models = agent.available_models()
    choices = {model.id: model for model in models}
    current = agent.current_model()
    if current.id not in choices:
        choices[current.id] = current
    model, refresh = st.columns([5, 1])
    with model:
        identifier = st.selectbox("模型", list(choices), index=list(choices).index(current.id),
                                  format_func=lambda key: choices[key].name,
                                  key=f"model_{agent.conversation_id}")
    with refresh:
        st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if st.button("↻", help="刷新模型", use_container_width=True, disabled=not agent.model_configured):
            agent.available_models(refresh=True)
            st.rerun()
    info = choices[identifier]
    # Select-slider retains the actual provider-neutral effort values, including
    # a single default-only choice for models without declared reasoning controls.
    effort = st.select_slider("思考深度", options=info.efforts,
        value=agent.reasoning_effort if agent.reasoning_effort in info.efforts else info.efforts[0],
        format_func=lambda value: EFFORT_LABELS.get(value, value), help=info.effort_hint,
        key=f"effort_{agent.conversation_id}_{identifier}", disabled=len(info.efforts) == 1)
    agent.select_model(identifier, effort)
    if agent.model_discovery_error:
        st.caption(agent.model_discovery_error)
    if not agent.model_configured:
        st.info("在 config.toml 的 [llm] 中填写 base_url 和 api_key，重启后即可自动读取可用模型。")


def main():
    st.set_page_config(page_title="设计工作台", page_icon="🧱", layout="wide")
    st.markdown(STYLE, unsafe_allow_html=True)
    if "design_agent" not in st.session_state:
        try:
            st.session_state.design_agent = create_agent()
        except (ConfigError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
            st.stop()
    agent = st.session_state.design_agent
    sidebar(agent)
    view = agent.application_view()
    st.markdown(f'<div class="eyebrow">{escape(view.get("eyebrow", "AGENT WORKSPACE"))}</div>', unsafe_allow_html=True)
    st.title(view.get("title", "设计工作台"))
    st.caption(view.get("subtitle", ""))
    st.divider()
    messages = agent.conversation_messages()
    st.session_state.messages = messages
    if not messages:
        st.markdown(view.get("welcome", ""))
        if view.get("loaded_message"):
            st.success(view["loaded_message"])
            st.markdown(view.get("parameter_table", ""))
        st.caption(view.get("scope", ""))
    for index, message in enumerate(messages):
        show_message(message, index, agent.conversation_id, agent)
    # Explicit bottom placement keeps the composer pinned without enabling
    # Streamlit's unconditional page-wide follow on expander/tab height changes.
    with st.bottom:
        prompt = st.chat_input(view.get("input_placeholder", "输入任务要求"))
    with st.container(key="scroll_controller"):
        controller = Path(__file__).with_name("scrolling.js").read_text(encoding="utf-8")
        # Only our static script is executable. The session marker is HTML-escaped.
        st.html(f'<span data-chat-session="{escape(agent.conversation_id, quote=True)}"></span>'
                f'<script>{controller}</script>', unsafe_allow_javascript=True)
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            # Register the styled container once. Only update its placeholder;
            # recreating keyed containers per chunk causes duplicate keys.
            with st.container(key="run_notes_live"):
                notes_slot = st.empty()
            body_slot, status_slot = st.empty(), st.empty()
            live = LiveTurn(notes_slot, body_slot, status_slot, agent.max_runs)
            turn = agent.chat(prompt, event_sink=live)
            live.finish(turn)
            status_slot.empty()
        st.rerun()
    if agent.persistence_error:
        st.error(agent.persistence_error)
        if st.button("重试保存对话"):
            agent.save_conversation()
            st.rerun()
