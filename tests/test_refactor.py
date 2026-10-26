"""Offline acceptance tests: regression, transcript protocol and state risks."""
from __future__ import annotations

import ast
import io
import json
import sqlite3
import tempfile
import unittest
from collections import deque
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.agent.loop import AgentLoop
from src.agent.tools import Tool, ToolRegistry, success
from src.bootstrap import create_agent
from support import business, business_artifacts, BusinessRegistry, skill_call, wire_action
from src.config import ModelConfig, ProjectConfig
from src.llm.deepseek import DeepSeekModel, provider_message
from src.llm.protocol import Message, ModelError, ModelResponse, ToolCall
from src.skills.retaining_wall.calculation import calculate_wall
from src.skills.retaining_wall.cases import TEACHING_PRESETS
from src.config import DEFAULT_DATABASE as DEFAULT_DB
from src.skills.retaining_wall.validation import check_parameters
from src.skills.retaining_wall.variants import compare_base_widths, optimize_geometry


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((Path(__file__).parent / "fixtures/legacy_cases.json").read_text(encoding="utf-8"))
VALUES = dict(TEACHING_PRESETS[0][2])


def call(name: str, args: dict | str | None = None, identifier: str = "call1") -> ToolCall:
    return skill_call(identifier, name, args if isinstance(args, str) else json.dumps(args or {}))


class FakeModel:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append(deepcopy((messages, tools)))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


class RegressionTests(unittest.TestCase):
    def test_all_case_results_unchanged(self):
        for fixture in FIXTURES:
            with self.subTest(title=fixture["title"]):
                self.assertEqual(calculate_wall(fixture["values"]), fixture["result"])

    def test_all_geometry_searches_unchanged(self):
        for fixture in FIXTURES:
            with self.subTest(title=fixture["title"]):
                values = fixture["values"]
                self.assertEqual(optimize_geometry(values), fixture["optimization"])
                self.assertEqual(optimize_geometry(values, ["base_width_m"]), fixture["fixed_base_optimization"])
                self.assertEqual(compare_base_widths(values), fixture["base_comparison"])

    def test_q_input_convention_is_explicit(self):
        values = {key: value for key, value in VALUES.items() if key != "surcharge_kpa"}
        self.assertEqual(calculate_wall(values), FIXTURES[0]["result"])
        checked = check_parameters(values)
        self.assertFalse(checked.valid)
        self.assertEqual(checked.code, "MISSING_INPUT")
        self.assertEqual(checked.missing, ("surcharge_kpa",))


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / "tests" / ".runtime"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.db = Path(self.temp.name) / "cases.sqlite3"
        self.app = create_agent(FakeModel(), db_path=self.db)
        self.registry = BusinessRegistry(self.app.loop.registry)

    def tearDown(self):
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to((ROOT / "tests" / ".runtime").resolve()))
        self.temp.cleanup()

    def invoke(self, tool_name, **args):
        result = self.registry.invoke(tool_name, args)
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["error"])
        return result["data"]

    def prepare(self):
        self.invoke("set_parameters", **VALUES)
        self.invoke("calculate_design")

    def test_tool_loop_read_validate_calculate_and_disclosure(self):
        fake = FakeModel(
            ModelResponse(tool_calls=(call("read_skill", {"name": "retaining_wall"}),)),
            ModelResponse(tool_calls=(call("set_parameters", VALUES, "set"),
                                      call("validate_parameters", identifier="check"),
                                      call("calculate_design", identifier="calc"))),
            ModelResponse("已完成教学初算。"),
        )
        self.app.loop.model = fake
        outcome = self.app.chat("请计算用户给定参数")
        self.assertFalse(outcome.failed)
        self.assertEqual(business_artifacts(self.app, outcome.artifacts)["result"]["sliding_ratio"], 1.595)
        body = self.app.catalog.entries["retaining_wall"].path.read_text(encoding="utf-8")
        first_messages, definitions = fake.requests[0]
        self.assertFalse(any(body in (m.content or "") for m in first_messages))
        self.assertEqual([m.role for m in first_messages], ["system", "context", "user"])
        skill_results = [json.loads(m.content) for m in fake.requests[1][0] if m.role == "tool"]
        self.assertEqual(skill_results[0]["data"]["content"], body)
        self.assertEqual([tools for _, tools in fake.requests], [definitions] * 3)
        self.assertEqual(self.app.catalog.loaded, {"retaining_wall"})
        self.assertEqual(len(definitions), 4)
        self.assertIn("read_skill", [tool["function"]["name"] for tool in definitions])
        self.assertIn("run_skill_action", [tool["function"]["name"] for tool in definitions])
        for name in ["tool_started", "tool_finished", "message_completed", "state_updated", "artifact_created", "run_finished"]:
            self.assertIn(name, [e.name for e in outcome.events])
        exported = json.loads(business_artifacts(self.app, outcome.artifacts)["report"]["json"])
        self.assertEqual(exported, business_artifacts(self.app, outcome.artifacts)["result"])
        self.assertIn("逐项计算", business_artifacts(self.app, outcome.artifacts)["report"]["markdown"])

    def test_context_stability_and_tool_pairs_across_turns(self):
        fake = FakeModel(
            ModelResponse(tool_calls=(call("read_skill", {"name": "retaining_wall"}),
                                      call("set_parameters", VALUES, "set"))),
            ModelResponse("已保存。"),
            ModelResponse(tool_calls=(call("set_parameters", {"height_m": 4.2}),)),
            ModelResponse("已修改。"),
        )
        self.app.loop.model = fake
        self.app.chat("保存参数")
        first = deepcopy(self.app.loop.history)
        self.app.chat("墙高改为4.2")
        self.assertEqual(fake.requests[0][0][0], fake.requests[2][0][0])
        self.assertEqual(fake.requests[0][1], fake.requests[2][1])
        self.assertEqual(self.app.loop.history[:len(first)], first)
        self.assertFalse(any(m.role in {"context", "system"} for m in self.app.loop.history))
        messages = fake.requests[2][0]
        self.assertEqual(messages[-2].role, "context")
        self.assertEqual(messages[-1], Message("user", "墙高改为4.2"))
        for i, item in enumerate(messages):
            if item.tool_calls:
                paired = messages[i + 1:i + 1 + len(item.tool_calls)]
                self.assertEqual([m.role for m in paired], ["tool"] * len(item.tool_calls))
                self.assertEqual([m.tool_call_id for m in paired], [c.id for c in item.tool_calls])
        self.assertEqual(business(self.app).read_session().parameters["height_m"], 4.2)

    def test_parameter_invalidation_noop_and_compare_is_readonly(self):
        self.prepare()
        before = business(self.app).read_session()
        self.invoke("compare_designs", fixed_dimensions=["base_width_m"])
        compared = business(self.app).read_session()
        self.assertEqual(compared.parameters, before.parameters)
        self.assertEqual(compared.current_result, before.current_result)
        self.assertIsNotNone(compared.pending_options)
        self.invoke("set_parameters", height_m=4)
        self.assertEqual(business(self.app).read_session().current_result, before.current_result)
        self.invoke("set_parameters", height_m=4.2)
        after = business(self.app).read_session()
        self.assertIsNone(after.current_result)
        self.assertIsNone(after.pending_options)
        self.assertEqual(business(self.app).current_artifacts(), {})
        self.assertEqual(self.registry.invoke("apply_option", {"index": 1})["error"]["code"], "INVALID_STATE")
        before.parameters["height_m"] = 100
        self.assertEqual(after.parameters["height_m"], 4.2)  # Snapshot cannot mutate authority.

    def test_calculate_then_modify_same_turn_never_returns_stale_card(self):
        self.app.loop.model = FakeModel(
            ModelResponse(tool_calls=(call("set_parameters", VALUES, "set"),
                                      call("calculate_design", identifier="calc"),
                                      call("compare_designs", {"fixed_dimensions": ["base_width_m"]}, "compare"),
                                      call("set_parameters", {"height_m": 4.2}, "edit"))),
            ModelResponse("已修改，需重新计算。"),
        )
        outcome = self.app.chat("计算后修改墙高")
        self.assertFalse(outcome.failed)
        self.assertEqual(outcome.artifacts, {})
        self.assertIsNone(business(self.app).read_session().current_result)
        self.assertEqual(len(outcome.trace), 4)

    def test_apply_updates_result_shape_and_contiguous_ids(self):
        self.invoke("load_case", title="矩形墙对照")
        self.invoke("calculate_design")
        data = self.invoke("compare_designs")
        for index, option in enumerate(data["alternatives"], 1):
            self.assertEqual(option["index"], index)
            self.assertTrue(option["label"].startswith(f"方案{index} · "))
        self.invoke("apply_option", index=1)
        session = business(self.app).read_session()
        self.assertEqual(session.current_result["input"], session.parameters)
        self.assertIsNone(session.pending_options)
        self.assertEqual(session.wall_shape, "trapezoid")

    def test_candidate_dedup_keeps_display_selection_in_sync(self):
        # A rectangular sample with fixed top has best==compact, leaving original labels 1 and 3.
        self.prepare()
        data = self.invoke("compare_designs", fixed_dimensions=["top_width_m"])
        options = data["alternatives"]
        self.assertEqual(len(options), 2)
        chosen = options[1]
        self.invoke("apply_option", index=2)
        self.assertEqual(business(self.app).read_session().parameters["base_width_m"], chosen["base_width_m"])
        self.assertTrue(chosen["label"].startswith("方案2 · "))

    def test_shared_validation_rejects_direct_compute(self):
        # Invalid changes are now rejected at the owning script boundary.
        for values in [{"height_m": 6}, {"base_width_m": 2, "top_width_m": 3}, {"height_m": True}]:
            before = self.app.state_snapshot("wall_design")
            self.assertFalse(self.registry.invoke("set_parameters", values)["ok"])
            self.assertEqual(self.app.state_snapshot("wall_design"), before)
        check = self.registry.invoke("validate_parameters", {})
        calculation = self.registry.invoke("calculate_design", {})
        self.assertFalse(check["ok"])
        self.assertEqual(check["error"], calculation["error"])
        self.assertIsNone(business(self.app).read_session().current_result)

    def test_case_database_compatibility_and_load_invalidation(self):
        self.assertEqual(DEFAULT_DB, ROOT / "data/cases.sqlite3")
        # Create the original schema and a legacy row without using the migrated writer.
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE cases(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,title TEXT NOT NULL,values_json TEXT NOT NULL,result_json TEXT NOT NULL)")
            db.execute("INSERT INTO cases(created_at,title,values_json,result_json) VALUES(?,?,?,?)",
                       ("2026-10-24T00:00:00", "原有案例", json.dumps(VALUES), json.dumps(FIXTURES[0]["result"])))
        business(self.app).load_saved_case(1)
        self.assertEqual(business(self.app).read_session().parameters, VALUES)
        self.assertIsNone(business(self.app).read_session().current_result)
        self.invoke("calculate_design")
        identifier = business(self.app).save_case("新案例")
        self.assertGreater(identifier, 1)
        self.assertEqual(next(d for d in business(self.app).saved_cases() if d["id"] == identifier)["result"], business(self.app).read_session().current_result)
        self.invoke("compare_designs", fixed_dimensions=["base_width_m"])
        business(self.app).load_saved_case(1)
        self.assertIsNone(business(self.app).read_session().current_result)
        self.assertIsNone(business(self.app).read_session().pending_options)

    def test_reset_clears_history_and_loaded_skills(self):
        self.invoke("read_skill", name="standards")
        self.prepare()
        self.app.loop.history.append(Message("user", "历史"))
        self.app.reset()
        self.assertEqual(self.app.loop.history, [])
        self.assertEqual(self.app.catalog.loaded, set())
        self.assertEqual(business(self.app).read_session().parameters, {})

    def test_model_failure_retains_completed_tools_and_emits_failure(self):
        self.app.loop.model = FakeModel(
            ModelResponse(tool_calls=(call("set_parameters", VALUES),)),
            ModelError("测试连接失败"),
            ModelResponse("可以继续。"),
        )
        outcome = self.app.chat("保存")
        self.assertTrue(outcome.failed)
        self.assertEqual(business(self.app).read_session().parameters, VALUES)
        self.assertEqual(outcome.events[-1].name, "run_failed")
        self.assertFalse(self.app.chat("继续").failed)

    def test_skill_identifier_cannot_be_a_path(self):
        output = self.registry.invoke("read_skill", {"name": "../config.toml"})
        self.assertEqual(output["error"]["code"], "INVALID_INPUT")
        self.assertEqual(self.app.catalog.loaded, set())


class GenericLoopTests(unittest.TestCase):
    def test_errors_are_uniform_and_loop_recovers(self):
        registry = ToolRegistry()
        schema = {"type": "object", "properties": {}, "additionalProperties": False}

        def broken():
            raise RuntimeError("not exposed")

        registry.register(Tool("broken", "Test failure", schema, broken))
        registry.register(Tool("healthy", "Test success", schema, lambda: success(42)))
        fake = FakeModel(
            ModelResponse(tool_calls=(call("missing", identifier="unknown"), call("healthy", "[]", "array"),
                                      call("healthy", "{", "json"), call("broken", identifier="broken"),
                                      call("healthy", {"extra": 1}, "extra"))),
            ModelResponse(tool_calls=(call("healthy"),)),
            ModelResponse("Recovered"),
        )
        events = []
        loop = AgentLoop(fake, registry, "Generic", lambda: {}, events.append)
        result = loop.run("Run")
        self.assertEqual(result.content, "Recovered")
        codes = [item["output"]["error"]["code"] for item in result.trace[:-1]]
        self.assertEqual(codes, ["INVALID_INPUT"] * 3 + ["EXECUTION_FAILED", "INVALID_INPUT"])
        self.assertEqual(result.trace[-1]["output"], success(42))
        self.assertNotIn("not exposed", str(result.trace))

    def test_empty_text_and_round_limit_are_reported(self):
        for fake, rounds in [(FakeModel(ModelResponse()), 1),
                             (FakeModel(ModelResponse(tool_calls=(call("missing"),)), ModelResponse("Done")), 1)]:
            events = []
            loop = AgentLoop(fake, ToolRegistry(), "Generic", lambda: {}, events.append, rounds)
            if not fake.responses[0].tool_calls:
                with self.assertRaises(ModelError):
                    loop.run("test")
                self.assertEqual(events[-1].name, "run_failed")
            else:
                outcome = loop.run("test")
                self.assertTrue(outcome.content.startswith("Done"))
                self.assertEqual(outcome.stop_reason, "round_limit")
                self.assertIn("run_stopped", [event.name for event in events])
                self.assertNotIn("run_finished", [event.name for event in events])
                self.assertEqual(fake.requests[-1][1], [])


class AdapterTests(unittest.TestCase):
    def test_context_mapping_and_tool_message_protocol(self):
        self.assertEqual(provider_message(Message("context", "snapshot")), {"role": "system", "content": "snapshot"})
        self.assertEqual(provider_message(Message("tool", "{}", tool_call_id="abc"))["tool_call_id"], "abc")

    def test_transport_and_malformed_response_are_model_errors(self):
        model = DeepSeekModel(ModelConfig(api_key="test-key"))
        for error in [URLError("test"), HTTPError("https://example.invalid", 401, "test", {}, None)]:
            with patch("src.llm.deepseek.urlopen", side_effect=error):
                with self.assertRaises(ModelError):
                    model.complete([Message("user", "test")], [])
        self.assertNotIn("test-key", repr(model.config))

    def test_response_normalization_and_request_contains_no_credentials_in_messages(self):
        envelope = {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "abc", "function": {"name": "example", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
        model = DeepSeekModel(ModelConfig(api_key="offline-secret", base_url="https://example.invalid/v1/", timeout_seconds=9))
        with patch("src.llm.deepseek.urlopen", return_value=io.BytesIO(json.dumps(envelope).encode())) as transport:
            response = model.complete([Message("context", "snapshot"), Message("user", "test")], [{"type": "function"}])
        self.assertEqual(response.tool_calls, (ToolCall("abc", "example", "{}"),))
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
        self.assertEqual(transport.call_args.kwargs["timeout"], 9)
        self.assertNotIn("offline-secret", request.data.decode())
        self.assertEqual(request.get_header("Authorization"), "Bearer offline-secret")
        self.assertEqual(json.loads(request.data)["messages"][0]["role"], "system")

    def test_malformed_and_truncated_responses_fail_before_tools_execute(self):
        model = DeepSeekModel(ModelConfig(api_key="offline"))
        envelopes = [{}, {"choices": []}, {"choices": [{"message": {"content": 123}}]},
                     {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}]
        for envelope in envelopes:
            with self.subTest(envelope=envelope):
                with patch("src.llm.deepseek.urlopen", return_value=io.BytesIO(json.dumps(envelope).encode())):
                    with self.assertRaises(ModelError):
                        model.complete([], [])

    def test_environment_config_precedes_local_file(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake-env-key", "DEEPSEEK_MODEL": "fake-model"}):
            config = ProjectConfig.load().llm
            self.assertEqual(config.api_key, "fake-env-key")
            self.assertEqual(config.model, "fake-model")


class ArchitectureTests(unittest.TestCase):
    def test_dependency_boundaries(self):
        for package, forbidden in [
            ("agent", {"streamlit", "sqlite3", "skills", "design_agent"}),
            ("llm", {"streamlit", "sqlite3", "skills", "design_agent", "agent"}),
            ("webui", {"llm", "skills", "urllib", "requests", "sqlite3"}),
        ]:
            for path in (ROOT / "src" / package).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                               else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                    for module in modules:
                        self.assertFalse(set(module.split(".")) & forbidden, f"{path}: {module}")


if __name__ == "__main__":
    unittest.main()
