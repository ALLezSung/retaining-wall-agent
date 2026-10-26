"""Storage relocation risks: stable paths, legacy snapshots and isolated writes."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src import bootstrap
from src.config import (
    DEFAULT_CONVERSATIONS_DATABASE, DEFAULT_DATABASE, PROJECT_DIR,
    ModelConfig, ProjectConfig, StorageConfig,
)
from src.bootstrap import create_agent
from support import business, business_artifacts, BusinessRegistry, skill_call, wire_action
from src.design_agent.infrastructure.sqlite_conversations import SQLiteConversationStore
from src.llm.protocol import ModelResponse


class OfflineModel:
    def complete(self, messages, tools):
        return ModelResponse("已记录。")


class StoragePathTests(unittest.TestCase):
    def setUp(self):
        runtime = PROJECT_DIR / "tests/.runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.cases = self.directory / "cases.sqlite3"
        self.chats = self.directory / "conversations.sqlite3"
        self.config = ProjectConfig(
            llm=ModelConfig(api_key=""),
            storage=StorageConfig(self.cases, self.chats),
        )

    def test_config_paths_are_anchored_to_project_from_another_directory(self):
        original = Path.cwd()
        try:
            os.chdir(self.directory)
            defaults = ProjectConfig.load(self.directory / "missing.toml", environ={})
            configured = ProjectConfig.load(PROJECT_DIR / "config.example.toml", environ={})
            overridden = ProjectConfig.load(self.directory / "missing.toml", environ={
                "CASES_DATABASE": "data/original-cases.sqlite3",
                "CONVERSATIONS_DATABASE": str(self.chats),
            })
        finally:
            os.chdir(original)
        expected = StorageConfig(PROJECT_DIR / "data/cases.sqlite3",
                                 PROJECT_DIR / "data/conversations.sqlite3")
        self.assertEqual(defaults.storage, expected)
        self.assertEqual(configured.storage, expected)
        self.assertEqual(DEFAULT_DATABASE, expected.cases_database)
        self.assertEqual(DEFAULT_CONVERSATIONS_DATABASE, expected.conversations_database)
        self.assertEqual(overridden.storage.cases_database, PROJECT_DIR / "data/original-cases.sqlite3")
        self.assertEqual(overridden.storage.conversations_database, self.chats)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_bootstrap_uses_existing_configured_database_without_creating_one(self):
        with patch.object(bootstrap.ProjectConfig, "load", return_value=self.config):
            app = bootstrap.create_agent()
        self.assertIsInstance(app._conversations, SQLiteConversationStore)
        self.assertEqual(app._conversations.path, self.chats)
        self.assertEqual(app.capabilities.resources.path, self.cases)
        self.assertFalse(self.chats.exists())  # An empty draft stays in memory.
        self.assertFalse(self.cases.exists())

    def test_legacy_sql_snapshot_loads_and_can_be_saved_after_relocation(self):
        payload = {
            "version": 1,
            "session": {"parameters": {"height_m": 4, "base_width_m": 2.4},
                        "parameter_sources": {"height_m": "用户输入"},
                        "wall_shape": "trapezoid", "current_result": None,
                        "pending_options": None,
                        "selected_standards": ["GB 50330-2013", "GB 55003-2021"]},
            "messages": [{"role": "user", "content": "保存原有条件"},
                         {"role": "assistant", "content": "已保存。"}],
            "history": [{"role": "user", "content": "保存原有条件"},
                        {"role": "assistant", "content": "已保存。"}],
            "loaded_skills": ["retaining_wall"],
        }
        # Build the old schema directly: the new adapter must not generate its
        # own fixture and thereby mask an incompatible format change.
        with closing(sqlite3.connect(self.chats)) as db, db:
            db.execute("""CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, payload_json TEXT NOT NULL)""")
            db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
                       ("legacy", "原对话", "2026-10-24T08:00:00+08:00",
                        "2026-10-24T00:00:00+00:00", json.dumps(payload, ensure_ascii=False)))
        before = self.chats.read_bytes()
        store = bootstrap.conversation_store(self.chats)
        self.assertEqual(store.load("legacy")["payload"], payload)
        self.assertEqual(self.chats.read_bytes(), before)
        app = create_agent(OfflineModel(), config=self.config, conversation_store=store)
        app.load_conversation("legacy")
        self.assertEqual(business(app).read_session().parameters, payload["session"]["parameters"])
        self.assertEqual(app.conversation_messages(), payload["messages"])
        self.assertEqual(app.catalog.loaded, {"retaining_wall"})
        app.chat("继续")
        reloaded = create_agent(OfflineModel(), config=self.config)
        reloaded.load_conversation("legacy")
        self.assertEqual(business(reloaded).read_session(), business(app).read_session())
        self.assertEqual(reloaded.conversation_messages(), app.conversation_messages())
        with closing(sqlite3.connect(self.chats)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT count(*) FROM conversations").fetchone()[0], 1)
        self.assertFalse(self.cases.exists())

    def test_separate_case_and_conversation_databases_round_trip(self):
        app = create_agent(OfflineModel(), config=self.config)
        business(app).load_teaching_case("基础干燥填土")
        business(app).action("calculate_design")
        case_id = business(app).save_case("隔离路径案例")
        app.chat("保留计算结果")
        restored = create_agent(OfflineModel(), config=self.config)
        restored.load_conversation(app.conversation_id)
        self.assertEqual(business(restored).read_session(), business(app).read_session())
        business(restored).load_saved_case(case_id)
        self.assertEqual(business(restored).read_session().parameters, business(app).read_session().parameters)
        for path, expected in ((self.cases, {"skill_documents"}),
                               (self.chats, {"conversations"})):
            with closing(sqlite3.connect(path)) as db:
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                self.assertEqual(tables, expected)

    def test_fresh_imports_work_in_both_orders_from_another_directory(self):
        for modules in (("src.bootstrap", "src.design_agent.infrastructure.sqlite_conversations", "src.design_agent"),
                        ("src.design_agent", "src.design_agent.infrastructure.sqlite_conversations", "src.bootstrap")):
            script = (f"import sys, importlib; sys.path.insert(0, {str(PROJECT_DIR)!r}); "
                      f"[importlib.import_module(name) for name in {modules!r}]")
            result = subprocess.run([sys.executable, "-c", script], cwd=self.directory,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
