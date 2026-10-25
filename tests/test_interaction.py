"""Offline contract tests for real streaming, model discovery and durable sessions."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections import deque
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry
from src.config import ModelConfig
from src.design_agent import DesignAgent
from src.llm.compatible import OpenAICompatibleModel
from src.llm.protocol import Message, ModelDelta, ModelError, ModelResponse, ToolCall
from src.design_agent.persistence.sqlite import SQLiteConversationStore
from src.skills.retaining_wall.cases import TEACHING_PRESETS
from src.skills.retaining_wall.calculation import calculate_wall

ROOT = Path(__file__).resolve().parents[1]


def sse(*packets, done=True):
    data = b": heartbeat\r\n\r\n"
    for packet in packets:
        data += ("data: " + json.dumps(packet, ensure_ascii=False) + "\r\n\r\n").encode()
    if done:
        data += b"data: [DONE]\n\n"
    return io.BytesIO(data)


def chunk(delta=None, finish=None):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}


class StreamingTests(unittest.TestCase):
    def test_raw_dsml_is_rejected_before_display_including_split_markers(self):
        markers = ['<｜DSML｜ calls>', '<｜｜DSML｜｜ calls>', '<|DSML|tool_calls>', '</||DSML||invoke>']
        model = OpenAICompatibleModel(ModelConfig(api_key="offline"))
        for marker in markers:
            for cut in range(1, len(marker)):
                deltas = []
                stream = sse(chunk({"content": "正在处理。"}), chunk({"content": marker[:cut]}),
                             chunk({"content": marker[cut:]}), chunk(finish="stop"))
                with self.subTest(marker=marker, cut=cut), patch.object(model, "_open", return_value=stream):
                    with self.assertRaises(ModelError):
                        model.complete_stream([], [], deltas.append)
                displayed = "".join(item.content for item in deltas)
                self.assertEqual(displayed, "正在处理。")
        envelope = {"choices": [{"message": {"content": markers[0]}, "finish_reason": "stop"}]}
        with patch.object(model, "_open", return_value=io.BytesIO(json.dumps(envelope).encode())):
            with self.assertRaises(ModelError):
                model.complete([], [])

    def test_comparison_text_is_preserved_and_old_raw_history_is_not_replayed(self):
        model = OpenAICompatibleModel(ModelConfig(api_key="offline"))
        text = "要求 p < 60 kPa；该条件尚未验证。"
        deltas = []
        with patch.object(model, "_open", return_value=sse(*(chunk({"content": char}) for char in text), chunk(finish="stop"))):
            self.assertEqual(model.complete_stream([], [], deltas.append).content, text)
        self.assertEqual("".join(delta.content for delta in deltas), text)
        raw = '<｜｜DSML｜｜ invoke name="calculate_design">'
        payload = model._payload([Message("assistant", raw), Message("user", "继续")], [])
        self.assertNotIn("DSML", payload["messages"][0]["content"])
        self.assertIn("未执行", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["content"], "继续")

    def test_final_request_retains_tool_schemas_and_disables_tool_choice(self):
        model = OpenAICompatibleModel(ModelConfig(api_key="offline"))
        tools = [{"type": "function", "function": {"name": "example"}}]
        with patch.object(model, "_open", return_value=sse(chunk({"content": "还需继续核算。"}), chunk(finish="stop"))) as transport:
            model.complete_final([Message("user", "run")], tools, lambda _: None)
        payload = json.loads(transport.call_args.args[0].data)
        self.assertEqual(payload["tools"], tools)
        self.assertEqual(payload["tool_choice"], "none")
        self.assertTrue(payload["stream"])

    def test_text_arrives_before_response_and_fragmented_parallel_tools_reassemble(self):
        model = OpenAICompatibleModel(ModelConfig(api_key="offline"))
        stream = sse(chunk({"reasoning_content": "private reasoning"}), chunk({"content": "正在"}),
            chunk({"content": "核算", "tool_calls": [
                {"index": 0, "id": "a", "function": {"name": "set_", "arguments": '{"height'}},
                {"index": 1, "id": "b", "function": {"name": "validate_parameters", "arguments": "{"}}]}),
            chunk({"tool_calls": [
                {"index": 0, "function": {"name": "parameters", "arguments": '_m":4}'}},
                {"index": 1, "function": {"arguments": "}"}}]}), chunk(finish="tool_calls"))
        deltas = []
        total_length = len(stream.getvalue())
        with patch.object(model, "_open", return_value=stream) as transport:
            response = model.complete_stream([Message("user", "calculate")], [],
                lambda delta: deltas.append((delta, stream.tell())))
        self.assertEqual(response.content, "正在核算")
        self.assertEqual(response.tool_calls, (ToolCall("a", "set_parameters", '{"height_m":4}'), ToolCall("b", "validate_parameters", "{}")))
        self.assertTrue(all(position < total_length for _, position in deltas))
        self.assertEqual([delta.content for delta, _ in deltas if delta.content], ["正在", "核算"])
        self.assertNotIn("private reasoning", str(deltas))
        self.assertEqual(response.reasoning_content, "private reasoning")
        self.assertTrue(json.loads(transport.call_args.args[0].data)["stream"])

    def test_incomplete_truncated_or_error_streams_do_not_return_tools(self):
        model = OpenAICompatibleModel(ModelConfig(api_key="offline"))
        for stream in [sse(chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "tool", "arguments": "{"}}]})),
                       sse(chunk({"content": "partial"}), chunk(finish="length")),
                       sse({"error": {"message": "secret provider response"}}),
                       sse(chunk({"content": 42})), sse(chunk(finish="content_filter"))]:
            with self.subTest(stream=stream), patch.object(model, "_open", return_value=stream):
                with self.assertRaises(ModelError) as caught:
                    model.complete_stream([], [], lambda _: None)
                self.assertNotIn("secret provider response", str(caught.exception))

    def test_loop_run_numbers_and_reasoning_replay_do_not_leak_to_events(self):
        class StreamingModel:
            requests = []
            def complete_stream(self, messages, tools, on_delta):
                self.requests.append(deepcopy(messages))
                on_delta(ModelDelta(reasoning=True))
                if len(self.requests) == 1:
                    on_delta(ModelDelta("先检查。"))
                    return ModelResponse("先检查。", (ToolCall("x", "missing", "{}"),), "tool_calls", "private")
                on_delta(ModelDelta("完成。"))
                return ModelResponse("完成。", finish_reason="stop")
        model, events = StreamingModel(), []
        result = AgentLoop(model, ToolRegistry(), "system", lambda: {}, events.append).run("run")
        self.assertEqual(result.content, "完成。")
        self.assertEqual(result.run_messages, [{"run": 1, "content": "先检查。"}])
        self.assertEqual([e.data["run"] for e in events if e.name == "model_started"], [1, 2])
        self.assertEqual(result.trace[0]["run"], 1)
        self.assertEqual(result.trace[0]["call_id"], "x")
        self.assertEqual(model.requests[1][-2].reasoning_content, "private")
        self.assertNotIn("private", str(events))


class DiscoveryTests(unittest.TestCase):
    def test_discovery_uses_authenticated_get_and_metadata_with_cache(self):
        model = OpenAICompatibleModel(ModelConfig(api_key="secret", model="absent"))
        envelope = {"data": [{"id": "deepseek-flash", "name": "Flash", "effort": {"supported_levels": ["low", "high", "max"]}},
                             {"id": "embeddings-v1"}, {"id": "image-v1", "output_modalities": ["image"]}]}
        with patch.object(model, "_open", return_value=io.BytesIO(json.dumps(envelope).encode())) as request:
            models = model.discover_models()
            self.assertEqual(model.discover_models(), models)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0].get_method(), "GET")
        self.assertEqual(request.call_args.args[0].full_url, "https://api.deepseek.com/models")
        self.assertEqual(model.config.model, "deepseek-flash")
        self.assertEqual(models[0].efforts, ("default", "none", "low", "high", "max"))
        self.assertNotIn("secret", str(models))

    def test_deepseek_and_openai_reasoning_payloads_and_unknown_defaults(self):
        model = OpenAICompatibleModel(ModelConfig(api_key="offline"))
        for effort, expected in [("none", None), ("low", "low"), ("medium", "high"), ("high", "high"), ("max", "max")]:
            model.select_model("deepseek-flash", effort)
            payload = model._payload([], [])
            self.assertEqual(payload.get("reasoning_effort"), expected)
            self.assertEqual(payload["thinking"]["type"], "disabled" if expected is None else "enabled")
        other = OpenAICompatibleModel(ModelConfig(api_key="offline", base_url="https://example.invalid/v1", model="gpt-5.4"))
        other.select_model("gpt-5.4", "high")
        payload = other._payload([Message("assistant", "text", reasoning_content="private")], [])
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertIn("max_completion_tokens", payload)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning_content", payload["messages"][0])
        other.select_model("unknown", "default")
        self.assertEqual(other.current_model().efforts, ("default",))
        self.assertNotIn("reasoning_effort", other._payload([], []))
        with self.assertRaises(ValueError):
            other.select_model("unknown", "high")

    def test_unknown_model_override_and_secret_safe_discovery_error(self):
        model = OpenAICompatibleModel(ModelConfig(api_key="offline", base_url="https://example.invalid/v1", model="custom",
            reasoning_overrides={"custom": {"low": "minimal", "high": "max"}}))
        model.select_model("custom", "high")
        self.assertEqual(model._payload([], [])["reasoning_effort"], "max")
        with patch.object(model, "_open", side_effect=HTTPError("https://example.invalid", 403, "secret", {}, None)):
            with self.assertRaises(ModelError) as caught:
                model.discover_models()
        self.assertNotIn("secret", str(caught.exception))


class ConversationTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "tests/.runtime"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.db = Path(self.temp.name) / "conversations.sqlite3"
        self.store = SQLiteConversationStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def agent(self, *responses):
        class Model:
            def __init__(self):
                self.responses = deque(responses)
            def complete(self, messages, tools):
                return self.responses.popleft() if self.responses else ModelResponse("继续。")
        return DesignAgent(Model(), db_path=self.db, conversation_store=self.store)

    def test_run_preambles_and_final_answer_remain_separate_after_reload(self):
        intro = "先读取技能。\n\n再核对当前条件。"
        final = "**最终回答**：已完成核对。"
        app = self.agent(ModelResponse(intro, (ToolCall("r", "read_skill", '{"name":"retaining_wall"}'),)), ModelResponse(final))
        turn = app.chat("请核对")
        self.assertEqual(turn.content, final)
        self.assertEqual(turn.run_messages, [{"run": 1, "content": intro}])
        identifier = app.conversation_id
        reloaded = self.agent()
        reloaded.load_conversation(identifier)
        message = reloaded.conversation_messages()[-1]
        self.assertEqual(message["content"], final)
        self.assertEqual(message["run_messages"], turn.run_messages)
        self.assertTrue(any(m.content == intro and m.tool_calls for m in reloaded.loop.history))

    def test_legacy_joined_reply_is_split_for_display_without_rewriting_history(self):
        intro, final = "先读取技能。", "最终回答。"
        app = self.agent(ModelResponse(intro, (ToolCall("r", "read_skill", '{"name":"retaining_wall"}'),)), ModelResponse(final))
        app.chat("请核对")
        identifier = app.conversation_id
        record = self.store.load(identifier)
        payload = record["payload"]
        payload["messages"][-1].pop("run_messages")
        payload["messages"][-1]["content"] = intro + "\n\n" + final
        self.store.save(identifier, record["title"], record["created_at"], payload)
        reloaded = self.agent()
        reloaded.load_conversation(identifier)
        message = reloaded.conversation_messages()[-1]
        self.assertEqual(message["content"], final)
        self.assertEqual(message["run_messages"], [{"run": 1, "content": intro}])
        self.assertEqual(self.store.load(identifier)["payload"]["messages"][-1]["content"], intro + "\n\n" + final)
        self.assertEqual(reloaded.loop.history, app.loop.history)

    def test_interrupted_final_keeps_completed_run_notes_separate_from_partial_answer(self):
        class PartialModel:
            count = 0
            def complete_stream(self, messages, tools, on_delta):
                self.count += 1
                if self.count == 1:
                    on_delta(ModelDelta("先读取技能。"))
                    return ModelResponse("先读取技能。", (ToolCall("r", "read_skill", '{"name":"retaining_wall"}'),))
                on_delta(ModelDelta("部分最终回答。"))
                raise ModelError("连接中断")
        app = DesignAgent(PartialModel(), db_path=self.db, conversation_store=self.store)
        turn = app.chat("请核对")
        self.assertTrue(turn.failed)
        self.assertEqual(turn.run_messages, [{"run": 1, "content": "先读取技能。"}])
        self.assertIn("部分最终回答。", turn.content)
        self.assertNotIn("先读取技能。", turn.content)

    def replay_pressure_scenario(self, final_content):
        """Replay the six recorded runs without network access or user DB edits."""
        calls = [("read_skill", {"name": "retaining_wall"}), ("calculate_design", {}),
                 ("compare_designs", {}), ("set_parameters", {"base_width_m": 2.6}),
                 ("calculate_design", {}), ("set_parameters", {"base_width_m": 2.7})]
        streams = [sse(chunk({"tool_calls": [{"index": 0, "id": f"call_{index}", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]}), chunk(finish="tool_calls"))
            for index, (name, arguments) in enumerate(calls)]
        streams.append(sse(chunk({"content": final_content[:3]}), chunk({"content": final_content[3:]}), chunk(finish="stop")))
        model = OpenAICompatibleModel(ModelConfig(api_key="offline"))
        app = DesignAgent(model, db_path=self.db, conversation_store=self.store, max_rounds=6)
        app.load_teaching_case(TEACHING_PRESETS[0][0])
        displayed = []
        with patch.object(model, "_open", side_effect=streams) as transport:
            turn = app.chat("核算该方案，需要满足基底最大压力小于60kpa，如果不行就一直优化，直到得到可行方案。", event_sink=displayed.append)
        return app, turn, displayed, json.loads(transport.call_args_list[-1].args[0].data)

    def test_recorded_run7_leak_is_an_error_and_can_resume_from_uncomputed_parameters(self):
        raw = '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="calculate_design">\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'
        app, turn, events, payload = self.replay_pressure_scenario(raw)
        self.assertTrue(turn.failed)
        self.assertNotIn("DSML", turn.content)
        self.assertNotIn("DSML", "".join(e.data.get("content", "") for e in events if e.name == "message_delta"))
        self.assertEqual(len(turn.trace), 6)
        self.assertEqual(app.read_session().parameters["base_width_m"], 2.7)
        self.assertIsNone(app.read_session().current_result)
        self.assertFalse(any(event.name == "run_finished" for event in events))
        self.assertEqual(events[-1].name, "run_failed")
        self.assertEqual(payload["tool_choice"], "none")
        self.assertTrue(payload["tools"])
        self.assertFalse(any("DSML" in (m.content or "") for m in app.loop.history))
        calculate = sse(chunk({"tool_calls": [{"index": 0, "id": "resume_calc", "function": {
            "name": "calculate_design", "arguments": "{}"}}]}), chunk(finish="tool_calls"))
        with patch.object(app.loop.model, "_open", side_effect=[calculate, sse(chunk({"content": "已按当前参数重新核算。"}), chunk(finish="stop"))]):
            resumed = app.chat("继续")
        self.assertFalse(resumed.failed)
        self.assertLess(resumed.artifacts["result"]["base_pressure_max_kpa"], 60)
        self.assertAlmostEqual(resumed.artifacts["result"]["base_pressure_max_kpa"], 57.81069958847743)

    def test_round_limit_is_persisted_as_stopped_instead_of_completed(self):
        app, turn, events, payload = self.replay_pressure_scenario("底宽已改为 2.7 m，尚未重新计算，请继续核算。")
        self.assertFalse(turn.failed)
        self.assertEqual(turn.stop_reason, "round_limit")
        self.assertIn("轮次上限", turn.content)
        self.assertEqual(events[-1].name, "run_stopped")
        self.assertIsNone(app.read_session().current_result)
        self.assertEqual(app.conversation_messages()[-1]["stop_reason"], "round_limit")
        self.assertEqual(self.store.load(app.conversation_id)["payload"]["messages"][-1]["stop_reason"], "round_limit")
        self.assertEqual(payload["messages"][-1]["role"], "system")
        self.assertIn("不要输出工具标记", payload["messages"][-1]["content"])

    def test_restart_restores_history_parameters_skills_candidates_and_report(self):
        app = self.agent(ModelResponse(tool_calls=(ToolCall("r", "read_skill", '{"name":"retaining_wall"}'),
            ToolCall("c", "calculate_design", "{}"), ToolCall("o", "compare_designs", "{}"))), ModelResponse("完成。"))
        app.load_teaching_case(TEACHING_PRESETS[0][0])
        app.chat("计算并比选")
        identifier = app.conversation_id
        expected_session, expected_history = app.read_session(), deepcopy(app.loop.history)
        expected_messages = app.conversation_messages()
        restarted = self.agent(ModelResponse(tool_calls=(ToolCall("p", "apply_option", '{"index":1}'),)), ModelResponse("已选用。"))
        restarted.load_conversation(identifier)
        self.assertEqual(restarted.read_session(), expected_session)
        self.assertEqual(restarted.loop.history, expected_history)
        self.assertEqual(restarted.conversation_messages(), expected_messages)
        self.assertEqual(restarted.catalog.loaded, {"retaining_wall"})
        self.assertIn("report", restarted.conversation_messages()[-1]["artifacts"])
        restarted.chat("选方案一")
        self.assertIsNone(restarted.read_session().pending_options)
        self.assertIsNotNone(restarted.read_session().current_result)

    def test_new_is_empty_recent_is_five_and_delete_active_does_not_resurrect(self):
        app = self.agent()
        ids = []
        for index in range(7):
            ids.append(app.conversation_id)
            app.chat(str(index))
            app.reset()
        self.assertEqual(app.read_session().parameters, {})
        self.assertEqual(app.conversation_messages(), [])
        self.assertEqual(len(app.recent_conversations()), 5)
        self.assertRegex(app.conversation_title, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        deleted = app.conversation_id
        app.delete_conversation(deleted)
        with self.assertRaises(ValueError):
            self.store.load(deleted)
        app.load_conversation(ids[-1])
        self.assertEqual(app.conversation_messages()[0]["content"], "6")

    def test_drafts_do_not_write_when_created_configured_renamed_or_loaded_from_case(self):
        with patch.object(self.store, "save", wraps=self.store.save) as save:
            app = DesignAgent(OpenAICompatibleModel(ModelConfig()), db_path=self.db, conversation_store=self.store)
            app.select_model("deepseek-flash", "low")
            app.select_standards([])
            app.rename_conversation(app.conversation_id, "草稿标题")
            app.load_teaching_case(TEACHING_PRESETS[0][0])
            self.assertTrue(app.save_conversation())
            for _ in range(10):
                app.reset()
            save.assert_not_called()
        self.assertEqual(app.all_conversations(), [])
        self.assertFalse(app.persistence_error)

    def test_first_message_saves_draft_state_and_new_drafts_do_not_add_rows(self):
        app = self.agent()
        identifier = app.conversation_id
        app.load_teaching_case(TEACHING_PRESETS[0][0])
        app.rename_conversation(identifier, "方案草稿")
        self.assertEqual(app.all_conversations(), [])
        with patch.object(self.store, "save", wraps=self.store.save) as save:
            app.chat("请查看已载入的参数")
            self.assertEqual(save.call_count, 1)
        stored = self.store.load(identifier)
        self.assertEqual(stored["title"], "方案草稿")
        self.assertEqual(stored["payload"]["session"]["parameters"], app.read_session().parameters)
        self.assertEqual(len(stored["payload"]["messages"]), 2)
        app.reset()
        self.assertEqual(len(app.all_conversations()), 1)
        app.load_conversation(identifier)
        self.assertEqual(len(app.all_conversations()), 1)
        app.delete_conversation(identifier)
        self.assertEqual(app.all_conversations(), [])
        self.assertEqual(app.conversation_messages(), [])

    def test_failed_stream_retains_partial_reply_and_completed_tool_state(self):
        class PartialModel:
            def complete_stream(self, messages, tools, on_delta):
                on_delta(ModelDelta("部分回复"))
                raise ModelError("流中断")
        app = DesignAgent(PartialModel(), db_path=self.db, conversation_store=self.store)
        seen = []
        turn = app.chat("hello", event_sink=seen.append)
        self.assertTrue(turn.failed)
        self.assertIn("部分回复", turn.content)
        self.assertIn("message_delta", [event.name for event in seen])
        persisted = self.store.load(app.conversation_id)["payload"]
        self.assertEqual(len(persisted["messages"]), 2)
        self.assertFalse(any(event["event"] == "message_delta" for event in persisted["messages"][-1]["events"]))
        self.assertIsNone(app._turn_sink)

    def test_standard_change_invalidates_current_but_preserves_historical_book(self):
        app = self.agent(ModelResponse(tool_calls=(ToolCall("c", "calculate_design", "{}"),)), ModelResponse("完成"))
        app.load_teaching_case(TEACHING_PRESETS[0][0])
        app.chat("计算")
        old = app.conversation_messages()[-1]["artifacts"]["report"]
        app.select_standards(["GB 50007-2011"])
        self.assertIsNone(app.read_session().current_result)
        self.assertEqual(app.conversation_messages()[-1]["artifacts"]["report"], old)
        self.assertIn("规范合规情况", old["sections"])
        self.assertIn("GB 50330-2013", old["sections"]["规范合规情况"])

    def test_storage_failure_is_visible_and_does_not_discard_reply(self):
        app = self.agent()
        with patch.object(self.store, "save", side_effect=RuntimeError("no access")):
            turn = app.chat("hello")
        self.assertFalse(turn.failed)
        self.assertTrue(app.persistence_error)
        self.assertEqual(len(app.conversation_messages()), 2)
        self.assertNotIn("no access", app.persistence_error)

    def test_unsaved_conversation_cannot_be_lost_by_new_or_load(self):
        app = self.agent()
        app.chat("keep this")
        identifier, messages = app.conversation_id, app.conversation_messages()
        with patch.object(self.store, "save", side_effect=RuntimeError("disk full")):
            for action in [app.reset, lambda: app.load_conversation(identifier)]:
                with self.assertRaises(RuntimeError):
                    action()
                self.assertEqual(app.conversation_id, identifier)
                self.assertEqual(app.conversation_messages(), messages)
        self.assertTrue(app.save_conversation())
        self.assertFalse(app.persistence_error)

    def test_rename_is_durable_and_changes_only_title_metadata(self):
        app = self.agent()
        app.chat("keep history")
        identifier = app.conversation_id
        before = self.store.load(identifier)
        ordering = self.store.recent(None)
        app.rename_conversation(identifier, "  挡墙方案 A  ")
        self.assertEqual(app.conversation_title, "挡墙方案 A")
        self.assertEqual(self.store.load(identifier)["payload"], before["payload"])
        self.assertEqual(self.store.recent(None)[0]["updated_at"], ordering[0]["updated_at"])
        app.chat("still named")
        self.assertEqual(self.store.load(identifier)["title"], "挡墙方案 A")
        app.reset()
        app.rename_conversation(identifier, "历史方案")
        self.assertNotEqual(app.conversation_title, "历史方案")
        app.load_conversation(identifier)
        self.assertEqual(app.conversation_title, "历史方案")
        for title in ["", "   ", "长" * 81]:
            with self.assertRaises(ValueError):
                app.rename_conversation(identifier, title)
        with patch.object(self.store, "rename", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                app.rename_conversation(identifier, "failed")
        self.assertEqual(app.conversation_title, "历史方案")


if __name__ == "__main__":
    unittest.main()
