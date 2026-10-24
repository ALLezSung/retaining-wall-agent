"""Streamlit interaction smoke test. Fake model; only local runtime sockets."""
from collections import deque
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.design_agent import DesignAgent
from src.llm.protocol import ModelResponse, ToolCall


ROOT = Path(__file__).resolve().parents[1]


class PageModel:
    def __init__(self):
        self.responses = deque([
            ModelResponse("案例已载入，可提出计算要求。"),
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
                button("载入教学案例并核对").click()
                run()
                self.assertEqual(agent.read_session().parameters["height_m"], 4)
                page.chat_input[0].set_value("请计算")
                run()
                self.assertEqual(agent.read_session().current_result["sliding_ratio"], 1.595)
                self.assertEqual(len(page.get("download_button")), 2)
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
                button("新建对话").click()
                run()
                self.assertEqual(page.session_state["messages"], [])
                self.assertEqual(agent.read_session().parameters, {})
                self.assertEqual(agent.catalog.loaded, set())


if __name__ == "__main__":
    unittest.main()
