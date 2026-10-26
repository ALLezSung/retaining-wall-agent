"""Raw v1 SQL fixtures, including historical reports and comparison-only turns."""
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from src.config import ProjectConfig, SkillsConfig, StorageConfig
from src.bootstrap import create_agent
from src.llm.protocol import ModelResponse
from src.skills.retaining_wall.calculation import calculate_wall
from src.skills.retaining_wall.cases import TEACHING_PRESETS
from src.skills.retaining_wall.standards_policy import selected_standard_report
from src.skills.retaining_wall.variants import optimize_geometry

ROOT = Path(__file__).resolve().parents[1]


class Model:
    def complete(self, messages, tools):
        return ModelResponse("继续。")


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests/.runtime")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.database = root / "conversations.sqlite3"
        self.config = ProjectConfig(storage=StorageConfig(root / "cases.sqlite3", self.database),
                                    skills=replace(SkillsConfig(), workspace=root / "workspace"))
        values = dict(TEACHING_PRESETS[0][2])
        result = calculate_wall(values)
        result["selected_standards"] = selected_standard_report(["GB 50330-2013", "GB 55003-2021"], result)
        options = optimize_geometry(values, ["top_width_m"])
        for i, row in enumerate(options["alternatives"], 1):
            row["index"] = i
        self.report_text = "# 原有计算书\n这是原记录中的精确下载内容。"
        self.payload = {"version": 1, "session": {"parameters": values, "parameter_sources": {}, "wall_shape": "trapezoid", "current_result": result, "pending_options": options,
                        "selected_standards": ["GB 50330-2013", "GB 55003-2021"]},
                        "messages": [{"role": "user", "content": "计算"}, {"role": "assistant", "content": "完成", "artifacts": {"result": result, "report": {"json": json.dumps(result, ensure_ascii=False), "markdown": self.report_text}}},
                                     {"role": "user", "content": "比选"}, {"role": "assistant", "content": "已有候选", "artifacts": {"optimization": options}}],
                        "history": [{"role": "user", "content": "计算"}, {"role": "assistant", "content": "完成"}], "loaded_skills": ["retaining_wall"]}
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,payload_json TEXT NOT NULL)")
            db.execute("INSERT INTO conversations VALUES(?,?,?,?,?)", ("legacy", "旧记录", "2026-10-24T08:00:00+08:00", "2026-10-24T00:00:00Z", json.dumps(self.payload, ensure_ascii=False)))

    def test_legacy_result_candidates_and_exact_download_survive_migration(self):
        before = self.database.read_bytes()
        app = create_agent(Model(), config=self.config)
        app.load_conversation("legacy")
        self.assertEqual(self.database.read_bytes(), before)  # Loading alone does not rewrite the old row.
        self.assertEqual(app.state_snapshot("wall_design")["payload"], self.payload["session"])
        messages = app.conversation_messages()
        self.assertEqual([m["content"] for m in messages], [m["content"] for m in self.payload["messages"]])
        report = messages[1]["artifacts"]["items"][0]
        self.assertEqual(app.artifact_bytes(report["attachments"][1]).decode("utf-8"), self.report_text)
        self.assertEqual(messages[3]["artifacts"]["items"][0]["kind"], "table")
        self.assertTrue(app.save_conversation())
        restored = create_agent(Model(), config=self.config)
        restored.load_conversation("legacy")
        self.assertEqual(restored.state_snapshot("wall_design"), app.state_snapshot("wall_design"))
        self.assertEqual(restored.conversation_messages(), messages)
        chosen = restored.state_snapshot("wall_design")["payload"]["pending_options"]["alternatives"][0]
        self.assertTrue(restored.execute_action("retaining_wall", "apply_option", {"index": 1})["ok"])
        self.assertEqual(restored.state_snapshot("wall_design")["payload"]["parameters"]["base_width_m"], chosen["base_width_m"])

    def test_invalid_legacy_state_never_replaces_current_conversation(self):
        payload = deepcopy(self.payload)
        payload["session"]["parameters"]["height_m"] = 99
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE conversations SET payload_json=?", (json.dumps(payload),))
        app = create_agent(Model(), config=self.config)
        original = app.conversation_id, app.workspace.serialize()
        with self.assertRaises(ValueError): app.load_conversation("legacy")
        self.assertEqual((app.conversation_id, app.workspace.serialize()), original)

    def test_case_resource_transaction_failure_does_not_touch_state_or_legacy_rows(self):
        app = create_agent(Model(), config=self.config)
        app.load_conversation("legacy")
        before = deepcopy(app.workspace.serialize())
        from unittest.mock import patch
        with patch.object(app.capabilities.resources, "commit", side_effect=OSError("disk full")):
            output = app.execute_action("retaining_wall", "save_current", {"title": "不可落盘"})
        self.assertEqual(output["error"]["code"], "PERSISTENCE_FAILED")
        self.assertEqual([e for e in app._events if e.name == "skill_finished"][-1].data["status"], "failed")
        self.assertEqual(app.workspace.serialize(), before)
