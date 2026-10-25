"""Streamlit interaction smoke test. Fake model; only local runtime sockets."""
from collections import deque
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.design_agent import DesignAgent
from src.llm.protocol import ModelDelta, ModelInfo, ModelResponse, ToolCall
from src.agent.events import Event
from src.webui.app import LiveTurn


ROOT = Path(__file__).resolve().parents[1]


class PageModel:
    def __init__(self):
        self.responses = deque([
            ModelResponse(tool_calls=(ToolCall("read", "read_skill", '{"name":"retaining_wall"}'),
                                      ToolCall("check", "validate_parameters", "{}"),
                                      ToolCall("calc", "calculate_design", "{}"))),
            ModelResponse("教学初算完成。"),
            ModelResponse(tool_calls=(ToolCall("compare", "compare_designs", '{"fixed_dimensions":["top_width_m"]}'),)),
            ModelResponse("候选已生成。"),
            ModelResponse(tool_calls=(ToolCall("pick", "apply_option", '{"index":1}'),)),
            ModelResponse("已选用并重算。"),
            ModelResponse(tool_calls=(ToolCall("edit", "set_parameters", '{"height_m":4.2}'),)),
            ModelResponse("已修改，请重新计算。"),
        ])

    def complete(self, messages, tools):
        return self.responses.popleft()


class WebUITests(unittest.TestCase):
    def test_streaming_preambles_rerender_without_duplicate_container_keys(self):
        class StreamingPageModel:
            count = 0
            def complete_stream(self, messages, tools, on_delta):
                self.count += 1
                if self.count == 1:
                    text = "先读取技能说明。"
                    calls = (ToolCall("r", "read_skill", '{"name":"retaining_wall"}'),)
                else:
                    text, calls = "最终回答：可以继续核算。", ()
                for start in range(0, len(text), 3):
                    on_delta(ModelDelta(text[start:start+3]))
                return ModelResponse(text, calls)
        temp_root = ROOT / "data/test-runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            agent = DesignAgent(StreamingPageModel(), db_path=Path(directory) / "stream-ui.sqlite3")
            page = AppTest.from_file(ROOT / "app.py", default_timeout=15)
            page.session_state["design_agent"] = agent
            page.run()
            page.chat_input[0].set_value("请核对")
            page.run()
            self.assertFalse(page.exception, [item.message for item in page.exception])
            self.assertEqual(agent.conversation_messages()[-1]["run_messages"], [{"run": 1, "content": "先读取技能说明。"}])
            self.assertEqual(agent.conversation_messages()[-1]["content"], "最终回答：可以继续核算。")

    def live_slots(self):
        operations = []

        class Slot:
            def __init__(self, name):
                self.name, self.value, self.calls = name, "", []
            def markdown(self, value, **kwargs):
                self.value = value
                self.calls.append(("markdown", value))
                operations.append((self.name, "markdown", value))
            def empty(self):
                self.value = ""
                self.calls.append(("empty", ""))
                operations.append((self.name, "empty", ""))

        notes, body, status = Slot("notes"), Slot("body"), Slot("status")
        return LiveTurn(notes, body, status, 7), notes, body, operations

    def stream_text(self, live, *chunks):
        for chunk in chunks:
            live.last_paint = 0
            live(Event("message_delta", {"content": chunk}))

    def test_pending_text_uses_body_from_first_delta_and_final_keeps_the_slot(self):
        live, notes, body, _ = self.live_slots()
        live(Event("model_started", {"run": 1}))
        self.stream_text(live, "**最", "终回答", "**：可以", "继续核算。")
        text = "**最终回答**：可以继续核算。"
        self.assertEqual(notes.value, "")
        self.assertEqual(notes.calls, [])
        self.assertEqual(body.value, text)
        self.assertEqual(body.calls[0], ("markdown", "**最"))
        before = list(body.calls)
        live(Event("model_finished", {"run": 1, "tool_count": 0, "content": text}))
        live(Event("message_completed", {"content": text}))
        self.assertIs(live.body_slot, body)
        self.assertEqual(body.calls, before)
        self.assertEqual(live.run_messages, [])

    def test_only_confirmed_tool_runs_move_to_list_once_and_body_clears_first(self):
        live, notes, body, operations = self.live_slots()
        for run, text in [(1, "先核对条件。"), (2, "再读取技能。")]:
            live(Event("model_started", {"run": run}))
            self.stream_text(live, text)
            self.assertEqual(body.value, text)
            self.assertNotIn(text, notes.value)
            finished = Event("model_finished", {"run": run, "tool_count": 1, "content": text})
            live(finished)
            self.assertEqual(body.value, "")
            self.assertEqual(notes.value.count(text), 1)
            notes_calls = list(notes.calls)
            live(finished)
            self.assertEqual(notes.calls, notes_calls)
        self.assertEqual(len(live.run_messages), 2)
        cleared = operations.index(("body", "empty", ""))
        listed = operations.index(("notes", "markdown", "- 先核对条件。"))
        self.assertLess(cleared, listed)
        live(Event("model_started", {"run": 3}))
        self.stream_text(live, "最终结论。")
        live(Event("model_finished", {"run": 3, "tool_count": 0, "content": "最终结论。"}))
        self.assertEqual(body.value, "最终结论。")
        self.assertNotIn("最终结论", notes.value)

    def test_interruption_keeps_notes_and_partial_answer_in_their_original_slots(self):
        live, notes, body, _ = self.live_slots()
        live(Event("model_started", {"run": 1}))
        self.stream_text(live, "先读取技能。")
        live(Event("model_finished", {"run": 1, "tool_count": 1, "content": "先读取技能。"}))
        live(Event("model_started", {"run": 2}))
        self.stream_text(live, "已输出的", "回答片段。")
        notes_calls = list(notes.calls)
        live(Event("run_failed", {"message": "连接中断"}))
        self.assertEqual(body.value, "已输出的回答片段。")
        self.assertIn("中断", live.stage)
        failed = SimpleNamespace(run_messages=list(live.run_messages), content=body.value + "\n\n本轮模型请求未完成：连接中断。")
        live.finish(failed)
        self.assertEqual(notes.calls, notes_calls)
        self.assertEqual(body.value, failed.content)
        self.assertEqual(body.value.count("已输出的回答片段。"), 1)
        self.assertNotIn("先读取技能", body.value)

    def test_message_completed_updates_limit_notice_without_appending_another_answer(self):
        live, notes, body, _ = self.live_slots()
        live(Event("model_started", {"run": 7, "final_round": True}))
        self.stream_text(live, "当前方案", "尚待核算。")
        live(Event("model_finished", {"run": 7, "tool_count": 0, "content": "当前方案尚待核算。"}))
        before = len(body.calls)
        content = "当前方案尚待核算。\n\n本轮已达到工具调用轮次上限，可继续执行。"
        completed = Event("message_completed", {"content": content})
        live(completed)
        live(completed)
        live.finish(SimpleNamespace(run_messages=[], content=content))
        self.assertEqual(body.value, content)
        self.assertEqual(len(body.calls), before + 1)
        self.assertEqual(body.value.count("当前方案尚待核算。"), 1)
        self.assertEqual(notes.calls, [])

    def test_intermediate_runs_are_a_list_and_final_answer_is_a_separate_markdown(self):
        temp_root = ROOT / "data/test-runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            model = PageModel()
            model.responses = deque([ModelResponse("先读取技能。\n\n核对参数。", (ToolCall("r", "read_skill", '{"name":"retaining_wall"}'),)),
                                     ModelResponse("**最终结论**：可以继续核算。")])
            agent = DesignAgent(model, db_path=Path(directory) / "run-notes.sqlite3")
            agent.chat("请核对")
            page = AppTest.from_file(ROOT / "app.py", default_timeout=15)
            page.session_state["design_agent"] = agent
            page.run()
            self.assertFalse(page.exception)
            notes = [item.value for item in page.markdown if item.value.startswith("- 先读取技能。")]
            self.assertEqual(notes, ["- 先读取技能。\n\n  核对参数。"])
            self.assertTrue(any(item.value == "**最终结论**：可以继续核算。" for item in page.markdown))

    def test_round_limit_shows_pause_status(self):
        temp_root = ROOT / "data/test-runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            agent = DesignAgent(PageModel(), max_rounds=1, db_path=Path(directory) / "limit.sqlite3")
            agent.load_teaching_case("基础干燥填土")
            agent.chat("请计算")
            page = AppTest.from_file(ROOT / "app.py", default_timeout=15)
            page.session_state["design_agent"] = agent
            page.run()
            self.assertFalse(page.exception)
            self.assertTrue(any("已达工具轮次上限，可继续执行" in item.value for item in page.caption))
            self.assertFalse(any(item.value.startswith("执行完成") for item in page.caption))

    def test_empty_conversations_remain_editable_drafts_without_saved_history(self):
        temp_root = ROOT / "data/test-runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            agent = DesignAgent(PageModel(), db_path=Path(directory) / "drafts.sqlite3")
            page = AppTest.from_file(ROOT / "app.py", default_timeout=15)
            page.session_state["design_agent"] = agent
            page.run()
            identifier = agent.conversation_id
            next(b for b in page.button if b.key == f"rename_{identifier}").click()
            page.run()
            next(item for item in page.text_input if item.key == f"title_input_{identifier}").set_value("未发送的草稿")
            page.run()
            self.assertEqual(agent.conversation_title, "未发送的草稿")
            for _ in range(3):
                next(b for b in page.button if b.label == "新建对话").click()
                page.run()
                self.assertFalse(page.exception)
                self.assertEqual(agent.all_conversations(), [])
                self.assertEqual(len([b for b in page.button if b.key and b.key.startswith("rename_")]), 1)
            next(b for b in page.button if b.label == "从案例新建对话").click()
            page.run()
            self.assertFalse(page.exception)
            self.assertTrue(agent.read_session().parameters)
            self.assertEqual(agent.all_conversations(), [])

    def test_model_and_reasoning_controls_update_the_application(self):
        class SelectablePageModel(PageModel):
            selected = "example-a"
            reasoning_effort = "default"

            def current_model(self):
                return next(item for item in self.discover_models() if item.id == self.selected)

            def discover_models(self, **kwargs):
                return [ModelInfo("example-a", "Model A", ("default", "low", "high")),
                        ModelInfo("example-b", "Model B", ("default", "high"))]

            def select_model(self, identifier, effort="default"):
                self.selected, self.reasoning_effort = identifier, effort

        temp_root = ROOT / "data/test-runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            agent = DesignAgent(SelectablePageModel(), db_path=Path(directory) / "models.sqlite3")
            page = AppTest.from_file(ROOT / "app.py", default_timeout=15)
            page.session_state["design_agent"] = agent
            page.run()
            next(item for item in page.selectbox if item.label == "模型").select("example-b")
            page.run()
            self.assertFalse(page.exception)
            self.assertEqual(agent.current_model().id, "example-b")
            next(item for item in page.select_slider if item.label == "思考深度").set_value("high")
            page.run()
            self.assertFalse(page.exception)
            self.assertEqual(agent.reasoning_effort, "high")
            self.assertTrue(any(item.label == "模型" for item in page.sidebar.selectbox))
            self.assertTrue(any(item.label == "思考深度" for item in page.sidebar.select_slider))

    def test_inline_title_rename_and_all_history_are_visible(self):
        temp_root = ROOT / "data/test-runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            model = type("HistoryModel", (), {"complete": lambda *args: ModelResponse("已记录。")})()
            agent = DesignAgent(model, db_path=Path(directory) / "titles.sqlite3")
            for index in range(8):
                if index:
                    agent.reset()
                agent.chat(f"对话 {index}")
            records = agent.all_conversations()
            oldest = records[-1]["id"]
            current = agent.conversation_id
            page = AppTest.from_file(ROOT / "app.py", default_timeout=15)
            page.session_state["design_agent"] = agent
            page.run()
            self.assertFalse(page.exception)
            self.assertEqual(len([b for b in page.button if b.key and b.key.startswith("load_")]), 8)
            active = next(b for b in page.button if b.key == f"rename_{current}")
            self.assertEqual(active.proto.type, "primary")
            next(b for b in page.button if b.key == f"rename_{oldest}").click()
            page.run()
            editor = next(item for item in page.text_input if item.key == f"title_input_{oldest}")
            editor.set_value("旧对话自定义标题")
            page.run()
            self.assertFalse(page.exception)
            self.assertEqual(agent.conversation_id, current)
            self.assertEqual(next(b for b in page.button if b.key == f"rename_{oldest}").label, "旧对话自定义标题")
            next(b for b in page.button if b.key == f"load_{oldest}").click()
            page.run()
            self.assertEqual(agent.conversation_title, "旧对话自定义标题")
            self.assertEqual(next(b for b in page.button if b.key == f"rename_{oldest}").proto.type, "primary")

    def test_page_case_chat_compare_apply_save_invalidate_and_reset(self):
        temp_root = ROOT / "data/test-runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            agent = DesignAgent(PageModel(), db_path=Path(directory) / "page.sqlite3")
            page = AppTest.from_file(ROOT / "app.py", default_timeout=15)
            page.session_state["design_agent"] = agent

            def run():
                page.run()
                self.assertFalse(page.exception, [item.message for item in page.exception])

            def button(label):
                return next(item for item in page.button if item.label == label)

            with patch("src.llm.deepseek.urlopen", side_effect=AssertionError("Online calls are forbidden")):
                run()
                self.assertEqual(len(page.title), 1)
                button("从案例新建对话").click()
                run()
                self.assertEqual(agent.read_session().parameters["height_m"], 4)
                page.chat_input[0].set_value("请计算")
                run()
                self.assertEqual(agent.read_session().current_result["sliding_ratio"], 1.595)
                self.assertEqual(len(page.get("download_button")), 2)
                self.assertEqual([item.label for item in page.expander if item.label in {"计算书", "系统状态"}], ["计算书", "系统状态"])
                page.chat_input[0].set_value("请比选")
                run()
                self.assertTrue(agent.read_session().pending_options)
                self.assertTrue(page.table)
                page.chat_input[0].set_value("试试方案1")
                run()
                self.assertIsNone(agent.read_session().pending_options)
                self.assertEqual(agent.read_session().current_result["input"], agent.read_session().parameters)
                button("保存当前参数与结果").click()
                run()
                self.assertEqual(len(agent.saved_cases()), 1)
                page.chat_input[0].set_value("墙高改为4.2")
                run()
                self.assertIsNone(agent.read_session().current_result)
                self.assertEqual(len(page.get("download_button")), 4)  # Two historical result cards.
                self.assertFalse(any(item.label == "保存当前参数与结果" for item in page.button))
                previous_id = agent.conversation_id
                button("新建对话").click()
                run()
                self.assertEqual(page.session_state["messages"], [])
                self.assertEqual(agent.read_session().parameters, {})
                self.assertEqual(agent.catalog.loaded, set())
                next(item for item in page.button if item.key == f"load_{previous_id}").click()
                run()
                self.assertEqual(agent.read_session().parameters["height_m"], 4.2)
                self.assertEqual(len(page.get("download_button")), 4)
                next(item for item in page.button if item.key == f"delete_{previous_id}").click()
                run()
                self.assertEqual(agent.conversation_messages(), [])
                self.assertNotIn(previous_id, [item["id"] for item in agent.recent_conversations()])


if __name__ == "__main__":
    unittest.main()
