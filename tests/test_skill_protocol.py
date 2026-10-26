"""Black-box acceptance tests for portable skills and hostile/failed responses."""
import ast
from collections import deque
from copy import deepcopy
from dataclasses import replace
import hashlib
from importlib.util import resolve_name
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.design_agent.infrastructure.catalog import LocalSkillCatalog
from src.config import ProjectConfig, SkillsConfig, StorageConfig
from src.bootstrap import create_agent
from src.design_agent.infrastructure.sqlite_conversations import SQLiteConversationStore
from src.llm.protocol import ModelResponse, ToolCall
from src.agent.serialization import loads
from src.design_agent.errors import CapabilityError
from src.design_agent.skills.protocol import response
from src.design_agent.infrastructure.executor import ScriptExecutor
from src.design_agent.infrastructure.sqlite_resources import SQLiteResourceStore
from src.design_agent.workspace.service import Workspace
from src.design_agent.infrastructure.files import LocalArtifactStore

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "src/skills/reading_list"


class OfflineModel:
    def __init__(self, *responses):
        self.responses = deque(responses)

    def complete(self, messages, tools):
        return self.responses.popleft() if self.responses else ModelResponse("已记录。")


class SkillProtocolTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tests/.runtime").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests/.runtime")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.config = ProjectConfig(storage=StorageConfig(self.root / "resources.sqlite3", self.root / "chats.sqlite3"),
                                    skills=SkillsConfig((self.skills,), self.root / "workspace", "reading_list", "reading_list"))

    def install_example(self):
        shutil.copytree(EXAMPLE, self.skills / "reading_list", ignore=shutil.ignore_patterns("__pycache__"))

    def app(self, *responses):
        return create_agent(OfflineModel(*responses), config=self.config)

    def invoke(self, app, action, args=None):
        return app.execute_action("reading_list", action, args or {})

    def test_extension_only_adds_directory_and_keeps_kernel_and_tools_unchanged(self):
        tracked = [*ROOT.glob("src/agent/*.py"), ROOT / "src/design_agent/application.py", ROOT / "src/design_agent/skills/capabilities.py"]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
        initial = self.app()
        definitions = initial.loop.registry.definitions()
        self.install_example()
        call = ToolCall("add", "run_skill_action", json.dumps({"skill_id": "reading_list", "action_id": "add", "input": {"title": "算法导论"}}))
        app = self.app(ModelResponse(tool_calls=(call,)), ModelResponse("已添加。"))
        self.assertEqual(app.loop.registry.definitions(), definitions)
        self.assertEqual([e["id"] for e in app.catalog.directory()], ["reading_list"])
        self.assertIn("Book / ReadingList", app.capabilities.read_skill("reading_list")["data"]["content"])
        turn = app.chat("把算法导论加入阅读清单")
        self.assertFalse(turn.failed)
        self.assertEqual(app.state_snapshot("reading_list")["payload"], {"books": [{"title": "算法导论", "read": False}]})
        self.assertEqual(turn.artifacts["items"][0]["rows"][0]["书名"], "算法导论")
        restored = self.app()
        restored.load_conversation(app.conversation_id)
        self.assertEqual(restored.state_snapshot("reading_list"), app.state_snapshot("reading_list"))
        self.assertEqual(restored.conversation_messages(), app.conversation_messages())
        self.assertTrue(self.invoke(restored, "mark_read", {"index": 1})["ok"])
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked})

    def test_standalone_script_with_no_host_import_and_strict_state(self):
        self.install_example()
        app = self.app()
        request = {"protocol_version": 1, "request_id": "standalone", "skill_id": "reading_list", "action_id": "add", "input": {"title": "独立运行"},
                   "runtime": {}, "snapshot": app.state_snapshot("reading_list"), "shared": {}, "resources": {}}
        result = subprocess.run([sys.executable, "-X", "utf8", str(EXAMPLE / "runner.py"), "--action", "add"], input=json.dumps(request),
                                text=True, encoding="utf-8", capture_output=True, cwd=self.root, timeout=10, env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(response(loads(result.stdout), request)["state_proposal"]["payload"]["books"][0]["title"], "独立运行")
        before = app.state_snapshot("reading_list")
        self.assertFalse(self.invoke(app, "add", {"title": 42})["ok"])
        self.assertFalse(self.invoke(app, "add", {"title": "x", "unknown": True})["ok"])
        self.assertEqual(app.state_snapshot("reading_list"), before)

    def test_documentation_missing_entry_duplicate_ids_and_paths(self):
        notes = self.skills / "notes"
        notes.mkdir()
        (notes / "SKILL.md").write_text("---\nname: Notes\ndescription: Instructions only\n---\nBody", encoding="utf-8")
        (notes / "ref.txt").write_text("参考资料", encoding="utf-8")
        app = self.app()
        self.assertTrue(app.capabilities.read_skill("notes")["ok"])
        self.assertTrue(app.capabilities.read_skill_resource("notes", "ref.txt")["ok"])
        self.assertFalse(app.capabilities.read_skill_resource("notes", "../ref.txt")["ok"])
        self.assertFalse(app.capabilities.run_skill_action("notes", "run", {})["ok"])
        broken = self.skills / "broken"
        broken.mkdir()
        (broken / "SKILL.md").write_text((notes / "SKILL.md").read_text(), encoding="utf-8")
        (broken / "skill.toml").write_text('id="broken"\nprotocol_version=1\nentry="missing.py"\n[actions.run]\nlabel="Run"\n', encoding="utf-8")
        app = self.app()
        self.assertEqual(app.capabilities.run_skill_action("broken", "run", {})["error"]["code"], "START_FAILED")
        self.assertTrue(app.capabilities.read_skill("broken")["ok"])
        self.assertEqual(list(app.catalog.entries), ["broken", "notes"])
        (broken / "skill.toml").write_text('id="notes"\nprotocol_version=1\nentry="missing.py"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重复技能标识"):
            LocalSkillCatalog(self.skills)

    def fault_app(self):
        self.install_example()
        path = self.skills / "reading_list/runner.py"
        source = path.read_text(encoding="utf-8")
        source = source.replace('    print(json.dumps(handle(r), ensure_ascii=False, allow_nan=False))', '''    if r["action_id"] == "fault":
        import time
        mode = r["input"]["mode"]
        if mode == "timeout": time.sleep(2)
        if mode == "json": print("{invalid"); raise SystemExit(0)
        if mode == "duplicate": print('{"a":1,"a":2}'); raise SystemExit(0)
        if mode == "overflow": print("x" * 4000000); raise SystemExit(0)
        if mode == "stderr": print("x" * 400000, file=sys.stderr); raise SystemExit(0)
        if mode == "secret":
            import os
            assert not any(k in os.environ for k in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "LLM_BASE_URL", "PYTHONPATH"))
        r["action_id"] = "add"
        r["input"] = {"title": "不得误提交"}
        out = handle(r)
        if mode == "stale": out["state_proposal"]["base_revision"] = "outdated"
        if mode == "session": out["state_proposal"]["session_id"] = "other"
        if mode == "owner": out["state_proposal"]["owner"] = "other"
        if mode == "protocol": out["protocol_version"] = 999
        if mode == "error_changes": out.update(status="business_error", error={"code":"X","message":"error"})
        if mode == "attachment": out["artifacts"][0]["attachments"] = [{"path":"../outside.txt","filename":"x","mime":"text/plain","label":"x"}]
        if mode == "nonzero": print(json.dumps(out)); raise SystemExit(3)
        print(json.dumps(out))
    else:
        print(json.dumps(handle(r), ensure_ascii=False, allow_nan=False))''')
        path.write_text(source, encoding="utf-8")
        manifest = self.skills / "reading_list/skill.toml"
        manifest.write_text(manifest.read_text() + '\n[actions.fault]\nlabel="Fault"\n[limits]\ntimeout_seconds=0.5\n', encoding="utf-8")
        app = self.app()
        self.assertTrue(self.invoke(app, "add", {"title": "历史成果"})["ok"])
        return app

    def test_failed_scripts_and_invalid_responses_never_change_state_or_artifacts(self):
        app = self.fault_app()
        before = deepcopy(app.workspace.serialize())
        refs = list(app.workspace.turn_refs)
        modes = {"timeout": "TIMEOUT", "json": "INVALID_RESPONSE", "duplicate": "INVALID_RESPONSE", "overflow": "OUTPUT_LIMIT", "stderr": "OUTPUT_LIMIT",
                 "nonzero": "NONZERO_EXIT", "protocol": "INVALID_RESPONSE", "error_changes": "INVALID_RESPONSE", "stale": "STALE_STATE", "session": "STALE_STATE", "owner": "STALE_STATE", "attachment": "INVALID_PATH"}
        for mode, code in modes.items():
            with self.subTest(mode=mode):
                output = self.invoke(app, "fault", {"mode": mode})
                self.assertFalse(output["ok"], output)
                self.assertEqual(output["error"]["code"], code)
                self.assertEqual(app.workspace.serialize(), before)
                self.assertEqual(app.workspace.turn_refs, refs)
        with patch.dict(os.environ, {"LLM_API_KEY": "secret", "DEEPSEEK_API_KEY": "secret", "LLM_BASE_URL": "https://secret", "PYTHONPATH": "secret"}):
            self.assertTrue(self.invoke(app, "fault", {"mode": "secret"})["ok"])

    def test_late_response_after_update_or_session_switch_is_rejected(self):
        self.install_example()
        app = self.app()
        execute = app.capabilities.executor.execute
        for switch in (False, True):
            def delayed(entry, request):
                result = execute(entry, request)
                if request["action_id"] == "add":
                    if switch:
                        app.capabilities.workspace = app.capabilities.new_workspace("another-session")
                    else:
                        app.workspace.states["reading_list"].revision = "newer"
                return result
            with patch.object(app.capabilities.executor, "execute", side_effect=delayed):
                output = self.invoke(app, "add", {"title": "过期执行"})
            self.assertEqual(output["error"]["code"], "STALE_STATE")
            self.assertFalse(app.workspace.artifacts)

    def test_database_failure_retry_and_cross_instance_compare_and_swap(self):
        self.install_example()
        app = self.app()
        self.invoke(app, "add", {"title": "保留在内存"})
        with patch.object(app._conversations, "save", side_effect=RuntimeError("disk full")):
            app.chat("保存清单")
            self.assertTrue(app.persistence_error)
            for action in (app.reset, lambda: app.load_conversation(app.conversation_id)):
                with self.assertRaises(RuntimeError): action()
        state, artifacts = app.state_snapshot("reading_list"), deepcopy(app.workspace.artifacts)
        self.assertTrue(app.save_conversation())
        other = self.app()
        other.load_conversation(app.conversation_id)
        self.assertEqual(other.state_snapshot("reading_list"), state)
        self.assertEqual(other.workspace.artifacts, artifacts)
        self.invoke(app, "mark_read", {"index": 1})
        self.assertTrue(app.save_conversation())
        self.invoke(other, "add", {"title": "另一实例"})
        self.assertFalse(other.save_conversation())
        durable = app._conversations.load(app.conversation_id)["payload"]
        self.assertEqual(durable["workspace"]["states"]["reading_list"]["payload"], app.state_snapshot("reading_list")["payload"])
        self.assertEqual(len(other.state_snapshot("reading_list")["payload"]["books"]), 2)

    def test_strict_json_and_optimized_python_still_validate_protocol(self):
        for value in ('{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}'):
            with self.assertRaises(ValueError): loads(value)
        code = "from src.design_agent.skills.protocol import response,CapabilityError\ntry: response({}, {'request_id':'r'})\nexcept CapabilityError: pass\nelse: raise RuntimeError('validation disabled')"
        subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT, check=True, capture_output=True, timeout=10)

    def test_documented_examples_and_missing_interpreter(self):
        fixture = loads((ROOT / "tests/fixtures/protocol_examples.json").read_text(encoding="utf-8"))
        for example in fixture["examples"]:
            if "host_result" in example:
                with self.assertRaises(CapabilityError): response(example["response"], fixture["request"])
            else:
                response(example["response"], fixture["request"])
        self.install_example()
        app = self.app()
        before = deepcopy(app.workspace.serialize())
        for arguments in ('{"skill_id":"reading_list","action_id":"add","input":{"title":"x","title":"y"}}',
                          '{"skill_id":"reading_list","action_id":"add","input":{"title":NaN}}'):
            self.assertEqual(app.loop.registry.invoke("run_skill_action", arguments)["error"]["code"], "INVALID_INPUT")
        self.assertEqual(self.invoke(app, "add", {"title": float("nan")})["error"]["code"], "INVALID_INPUT")
        app.capabilities.executor.interpreter = str(self.root / "missing-python.exe")
        self.assertEqual(self.invoke(app, "add", {"title": "不应保存"})["error"]["code"], "START_FAILED")
        self.assertEqual(app.workspace.serialize(), before)

    def test_stateless_artifact_survives_subsequent_readonly_action(self):
        workspace = Workspace("s", LocalArtifactStore(self.root / "workspace"))
        stage = workspace.stage("stateless")
        request = {"request_id": "one", "skill_id": "notes", "action_id": "show", "runtime": {}, "snapshot": workspace.snapshot("")}
        result = {"state_proposal": None, "artifacts": [{"key": "note", "kind": "text", "title": "说明", "content": "正文"}]}
        workspace.commit(request, result, stage)
        refs = list(workspace.turn_refs)
        workspace.commit({**request, "request_id": "two"}, {"state_proposal": None, "artifacts": []}, stage)
        self.assertEqual(workspace.turn_refs, refs)


class DependencyBoundaryTests(unittest.TestCase):
    def test_recursive_import_boundaries(self):
        rules = {"agent": {"skills", "design_agent", "streamlit", "sqlite3"},
                 "design_agent": {"skills", "streamlit"},
                 "webui": {"skills", "llm", "sqlite3"},
                 "skills": {"agent", "design_agent", "webui", "runtime", "config", "streamlit", "sqlite3"}}
        for package, forbidden in rules.items():
            for path in (ROOT / "src" / package).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                    for name in names:
                        if isinstance(node, ast.ImportFrom) and node.level:
                            parent = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
                            name = resolve_name("." * node.level + name, parent)
                        parts = name.split(".")
                        dependency = parts[1] if parts[0] == "src" and len(parts) > 1 else parts[0]
                        self.assertNotIn(dependency, forbidden, f"{path}: {name}")
                        self.assertNotEqual(dependency, "runtime", f"Obsolete runtime dependency: {path}")
                        relative = path.relative_to(ROOT / "src")
                        app_policy = relative.parts[0] == "design_agent" and "infrastructure" not in relative.parts
                        if relative.parts[0] == "agent":
                            self.assertNotIn(dependency, {"subprocess", "sqlite3", "pathlib", "shutil", "bootstrap"}, str(path))
                        if app_policy:
                            self.assertFalse(name.startswith("src.design_agent.infrastructure"), f"Policy imports adapter: {path}: {name}")
                            self.assertNotIn(dependency, {"bootstrap", "subprocess", "sqlite3", "shutil", "os"}, str(path))
                        if package == "skills" and isinstance(node, ast.ImportFrom):
                            self.assertLessEqual(node.level, 1, f"Sibling/host import: {path}")

    def test_obsolete_packages_are_removed(self):
        for relative in ("src/runtime", "src/agent/skills.py", "src/design_agent/persistence", "src/design_agent/session.py", "src/design_agent/conversations.py"):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_application_policy_has_no_direct_filesystem_calls(self):
        forbidden = {"open", "read_text", "write_text", "read_bytes", "write_bytes", "mkdir", "unlink", "rmdir", "glob", "rglob", "stat", "is_file", "is_dir"}
        paths = [ROOT / "src/design_agent/application.py", *ROOT.glob("src/design_agent/skills/*.py"), *ROOT.glob("src/design_agent/workspace/*.py")]
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                    self.assertNotIn(name, forbidden, f"{path}:{node.lineno}")

    def test_production_module_dependencies_are_acyclic(self):
        modules = {}
        for path in (ROOT / "src").rglob("*.py"):
            parts = path.relative_to(ROOT).with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            modules[module] = path
        graph = {name: set() for name in modules}
        for name, path in modules.items():
            parent = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                targets = []
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = resolve_name("." * node.level + (node.module or ""), parent) if node.level else (node.module or "")
                    targets = [base, *[base + "." + alias.name for alias in node.names]]
                graph[name].update(target for target in targets if target in modules and target != name)
        done, active = set(), []

        def visit(name):
            self.assertNotIn(name, active, " -> ".join([*active, name]))
            if name in done:
                return
            active.append(name)
            for target in sorted(graph[name]):
                visit(target)
            active.pop()
            done.add(name)

        for name in sorted(graph):
            visit(name)
