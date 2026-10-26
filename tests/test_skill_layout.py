"""Directory portability, frontmatter, integration confinement and legacy IDs."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from src.bootstrap import create_agent
from src.config import ProjectConfig, SkillsConfig, StorageConfig
from src.design_agent.infrastructure.catalog import LocalSkillCatalog
from src.llm.protocol import ModelResponse

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "src/skills/reading-list"


class Model:
    def complete(self, messages, tools):
        return ModelResponse("已记录。")


class SkillLayoutTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "tests/.runtime").mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=ROOT / "tests/.runtime")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.skills = self.root / "skills"
        self.skills.mkdir()

    def document(self, name="notes", header=None):
        folder = self.skills / name
        folder.mkdir(exist_ok=True)
        (folder / "SKILL.md").write_text(header or f"---\nname: {name}\ndescription: Instructions only\n---\nBody", encoding="utf-8")
        return folder

    def test_bundled_layout_preserves_runtime_ids_and_reads_references_on_demand(self):
        catalog = LocalSkillCatalog(ROOT / "src/skills")
        self.assertEqual(list(catalog.entries), ["reading_list", "retaining_wall", "standards"])
        self.assertEqual(catalog.get("retaining_wall").root.name, "retaining-wall")
        self.assertEqual(catalog.get("reading_list").root.name, "reading-list")
        self.assertEqual(catalog.get("retaining_wall").name, "重力式挡土墙教学初算")
        self.assertEqual(set(catalog.owners), {"wall_design", "reading_list"})
        for entry in catalog.entries.values():
            self.assertEqual(list(entry.root.glob("*.py")), [])
            self.assertFalse((entry.root / "skill.toml").exists())
            self.assertEqual(entry.manifest["entry"], "scripts/runner.py")
        body = catalog.read_skill("retaining_wall")["content"]
        self.assertNotIn("| height_m |", body)
        self.assertIn("references/parameters.md", body)
        self.assertIn("| height_m |", catalog.read_resource("retaining_wall", "references/parameters.md")["content"])

    def test_standard_only_skill_with_unbound_scripts_needs_no_host_manifest(self):
        folder = self.document(header='''---
name: notes
description: >-
  Read notes: use when
  the user asks for notes.
metadata:
  design-agent-display-name: "阅读说明"
---
# Notes
See scripts/example.py and references/notes.md.
''')
        (folder / "scripts").mkdir()
        (folder / "scripts/example.py").write_text("raise RuntimeError('must not execute at discovery')", encoding="utf-8")
        (folder / "references").mkdir()
        (folder / "references/notes.md").write_text("按需读取", encoding="utf-8")
        catalog = LocalSkillCatalog(self.skills)
        self.assertEqual(catalog.get("notes").description, "Read notes: use when the user asks for notes.")
        self.assertEqual(catalog.directory()[0]["actions"], [])
        self.assertEqual(catalog.get("notes").name, "阅读说明")
        self.assertEqual(catalog.read_resource("notes", "references/notes.md")["content"], "按需读取")
        self.document("3d-notes")
        self.assertEqual(LocalSkillCatalog(self.skills).get("3d-notes").id, "3d-notes")

    def test_invalid_frontmatter_is_rejected(self):
        folder = self.document()
        cases = [
            "name: notes\nname: notes\ndescription: x",
            "name: notes\ndescription: x\nmetadata:\n  key: a\n  key: b",
            "name: notes\ndescription: x\nmetadata: !!python/object:builtins.object {}",
            "name: notes\ndescription: &text x\nmetadata:\n  key: *text",
            "name: notes\ndescription: x\nmetadata:\n  key: 1",
            "name: notes\ndescription: [x]",
            "name: other-name\ndescription: x",
            "name: notes_bad\ndescription: x",
            "name: notes\ndescription: x\nmetadata: [x]",
            "name: notes\ndescription: 'unterminated",
            "- notes\n- description",
        ]
        for header in cases:
            with self.subTest(header=header):
                (folder / "SKILL.md").write_text("---\n" + header + "\n---\nBody", encoding="utf-8")
                with self.assertRaises(ValueError):
                    LocalSkillCatalog(self.skills)
        for content in ("No header", "---\nname: notes\ndescription: x", "---\n" + "a" * 65537):
            (folder / "SKILL.md").write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                LocalSkillCatalog(self.skills)

    def test_integration_path_is_explicit_confined_and_unambiguous(self):
        folder = self.document()
        header = "---\nname: notes\ndescription: x\nmetadata:\n  design-agent-integration: {path}\n---\nBody"
        for path in ('"../outside.toml"', json.dumps(str(self.root / "outside.toml")), '""', '"missing.toml"'):
            with self.subTest(path=path):
                (folder / "SKILL.md").write_text(header.format(path=path), encoding="utf-8")
                with self.assertRaises(ValueError):
                    LocalSkillCatalog(self.skills)
        (folder / "integrations").mkdir()
        config = folder / "integrations/design-agent.toml"
        (folder / "SKILL.md").write_text(header.format(path='"integrations/design-agent.toml"'), encoding="utf-8")
        config.write_text('protocol_version=1\nentry="scripts/missing.py"\n[actions.run]\nlabel="Run"', encoding="utf-8")
        catalog = LocalSkillCatalog(self.skills)
        self.assertFalse(catalog.directory()[0]["actions"][0]["available"])
        self.assertTrue(catalog.read_skill("notes")["content"])
        config.write_text('protocol_version=1\nentry="../outside.py"', encoding="utf-8")
        self.assertTrue(LocalSkillCatalog(self.skills).get("notes").unavailable)
        config.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "清单"):
            LocalSkillCatalog(self.skills)
        (folder / "skill.toml").write_text('protocol_version=1\nentry="runner.py"', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "新旧集成文件"):
            LocalSkillCatalog(self.skills)

    def test_integration_symlink_cannot_escape_skill(self):
        folder = self.document(header="---\nname: notes\ndescription: x\nmetadata:\n  design-agent-integration: linked.toml\n---\n")
        outside = self.root / "outside.toml"
        outside.write_text('protocol_version=1\nentry="runner.py"', encoding="utf-8")
        try:
            (folder / "linked.toml").symlink_to(outside)
        except OSError:
            if os.name != "nt":
                raise
            # Windows directory junctions exercise the same resolve boundary
            # without requiring the symbolic-link creation privilege.
            import _winapi
            linked = folder / "integrations"
            _winapi.CreateJunction(str(self.root), str(linked))
            (folder / "SKILL.md").write_text(
                "---\nname: notes\ndescription: x\nmetadata:\n  design-agent-integration: integrations/outside.toml\n---\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "路径无效"):
            LocalSkillCatalog(self.skills)

    def test_legacy_manifest_session_state_and_artifacts_survive_directory_migration(self):
        legacy = self.document("reading_list", "---\nname: 阅读清单\ndescription: Legacy skill\n---\n")
        shutil.copyfile(EXAMPLE / "scripts/runner.py", legacy / "runner.py")
        # Old manifests could omit id: their directory, not Chinese name,
        # determines the stable runtime identifier.
        manifest = (EXAMPLE / "integrations/design-agent.toml").read_text(encoding="utf-8")
        (legacy / "skill.toml").write_text(manifest.replace('scripts/runner.py', 'runner.py').replace('id = "reading_list"\n', ''), encoding="utf-8")
        config = ProjectConfig(storage=StorageConfig(self.root / "cases.sqlite3", self.root / "chats.sqlite3"),
                               skills=SkillsConfig((self.skills,), self.root / "workspace", "reading_list", "reading_list"))
        before = create_agent(Model(), config=config)
        before.catalog.read_skill("reading_list")
        self.assertTrue(before.execute_action("reading_list", "add", {"title": "迁移前的书"})["ok"])
        before.chat("保存这条清单")
        state, artifacts = before.state_snapshot("reading_list"), deepcopy(before.current_artifacts())
        # Archive the old directory outside the configured discovery root.
        archive = self.root / "legacy-archive"
        self.assertTrue(archive.resolve().is_relative_to(self.root))
        legacy.rename(archive)
        shutil.copytree(EXAMPLE, self.skills / "reading-list", ignore=shutil.ignore_patterns("__pycache__"))
        after = create_agent(Model(), config=config)
        after.load_conversation(before.conversation_id)
        self.assertEqual(after.state_snapshot("reading_list"), state)
        self.assertEqual(after.current_artifacts(), artifacts)
        self.assertEqual(after.conversation_messages(), before.conversation_messages())
        self.assertEqual(after.catalog.loaded, {"reading_list"})
        self.assertTrue(after.execute_action("reading_list", "mark_read", {"index": 1})["ok"])
        self.assertTrue(after.save_conversation())
        again = create_agent(Model(), config=config)
        again.load_conversation(before.conversation_id)
        self.assertTrue(again.state_snapshot("reading_list")["payload"]["books"][0]["read"])

    def test_wall_scripts_run_from_an_unrelated_directory_without_host_or_pythonpath(self):
        scripts = self.root / "portable-wall"
        shutil.copytree(ROOT / "src/skills/retaining-wall/scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
        stage = self.root / "output"
        stage.mkdir()
        snapshot = {"session_id": "portable", "owner": "wall_design", "protocol_version": 1,
                    "data_version": 1, "revision": "r0", "payload": {}, "artifact_refs": []}
        values = {"height_m": 4, "base_width_m": 2.4, "top_width_m": 0.5, "soil_unit_weight_kn_m3": 18,
                  "friction_angle_deg": 30, "wall_unit_weight_kn_m3": 24, "base_friction_coefficient": 0.55, "surcharge_kpa": 0}
        for action, args in (("set_parameters", values), ("calculate_design", {})):
            request = {"protocol_version": 1, "request_id": action, "skill_id": "retaining_wall", "action_id": action,
                       "input": args, "snapshot": snapshot, "runtime": {"staging_dir": str(stage)}, "shared": {}, "resources": {}}
            result = subprocess.run([sys.executable, "-X", "utf8", str(scripts / "runner.py"), "--action", action],
                                    input=json.dumps(request), cwd=self.root, text=True, encoding="utf-8", capture_output=True,
                                    env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PYTHONUTF8": "1"}, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "ok", output)
            snapshot.update(payload=output["state_proposal"]["payload"], revision=action)
        self.assertEqual(output["artifacts"][0]["kind"], "report")
        self.assertGreater(snapshot["payload"]["current_result"]["sliding_ratio"], 1)
        self.assertTrue((stage / "wall_report.md").is_file())
