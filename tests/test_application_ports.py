"""Exercise the application without filesystem, subprocess, SQLite or local adapters."""
from collections import deque
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from src.agent.serialization import dumps
from src.bootstrap import create_agent
from src.config import ProjectConfig, SkillsConfig
from src.design_agent import DesignAgent, ApplicationPorts
from src.design_agent.errors import CapabilityError, ConversationConflict
from src.design_agent.skills.catalog import SkillEntry
from src.llm.protocol import ModelResponse, ToolCall


class MemoryConversations:
    def __init__(self):
        self.records = {}
        self.fail = False

    def save(self, identifier, title, created_at, payload):
        if self.fail:
            raise RuntimeError("memory store temporarily unavailable")
        old = self.records.get(identifier, {}).get("payload", {})
        if old.get("storage_revision") != payload["expected_revision"]:
            raise ConversationConflict("stale revision")
        payload = deepcopy(payload)
        payload.pop("expected_revision")
        self.records[identifier] = {"id": identifier, "title": title, "created_at": created_at, "payload": payload}

    def recent(self, limit=5):
        rows = [{k: v for k, v in r.items() if k != "payload"} for r in reversed(self.records.values())]
        return rows if limit is None else rows[:limit]

    def rename(self, identifier, title):
        self.records[identifier]["title"] = title

    def load(self, identifier):
        return deepcopy(self.records[identifier])

    def delete(self, identifier):
        self.records.pop(identifier, None)


class MemoryCatalog:
    def __init__(self):
        entry = SkillEntry("counter", "内存计数", "端口替换验收", Path("virtual/counter/SKILL.md"), {
            "owner": "counter", "resource_namespace": "counter_records",
            "lifecycle": {key: key for key in ("initialize", "restore", "project", "reset")},
            "actions": {"increase": {"label": "增加计数"}, "save_item": {"label": "保存计数记录"}}})
        self.entries = {entry.id: entry}
        self.owners = {"counter": entry}
        self.loaded = set()

    def directory(self):
        return [{"id": "counter", "name": "内存计数", "description": "端口替换验收"}]

    def get(self, identifier):
        if identifier not in self.entries:
            raise CapabilityError("INVALID_INPUT", "unknown skill")
        return self.entries[identifier]

    def read_skill(self, name):
        self.get(name)
        self.loaded.add(name)
        return {"id": name, "content": "A skill served entirely from memory."}

    def read_resource(self, skill_id, path):
        self.get(skill_id)
        return {"content": "memory reference"}


class MemoryResources:
    def __init__(self):
        self.documents = {}

    def snapshot(self, namespace, legacy_tables=()):
        return {"documents": deepcopy(list(self.documents.get(namespace, {}).values())), "legacy_tables": {}}

    def commit(self, namespace, proposals):
        documents = self.documents.setdefault(namespace, {})
        for proposal in proposals:
            documents[proposal["id"]] = deepcopy(proposal["document"])


class MemoryArtifacts:
    def __init__(self):
        self.stages = {}
        self.files = {}
        self.fail = False

    def create_stage(self, session_id, request_id):
        stage = f"memory://{session_id}/{request_id}"
        self.stages[stage] = {}
        return stage

    def register(self, session_id, stage, artifacts):
        if self.fail:
            raise OSError("memory attachment store unavailable")
        result = deepcopy(artifacts)
        for artifact in result.values():
            for attachment in artifact.get("attachments", []):
                data = self.stages[stage][attachment["path"]]
                digest = hashlib.sha256(data).hexdigest()
                attachment.update(path="files/" + digest, sha256=digest)
                self.files[session_id, attachment["path"]] = data
        return result

    def read_attachment(self, session_id, attachment):
        return self.files[session_id, attachment["path"]]

    def discard_stage(self, session_id, stage):
        self.stages.pop(stage, None)


@dataclass
class CounterState:
    count: int = 0

    def __post_init__(self):
        if type(self.count) is not int or self.count < 0:
            raise ValueError("invalid counter state")


class MemoryExecutor:
    def __init__(self, files):
        self.files = files
        self.requests = []
        self.malformed = False

    def execute(self, entry, request):
        self.requests.append(deepcopy(request))
        snap, action = request["snapshot"], request["action_id"]
        state = CounterState(**snap["payload"])
        result = {"protocol_version": 1, "request_id": request["request_id"], "status": "ok", "model_result": {},
                  "state_proposal": None, "artifacts": [], "resource_proposals": [], "error": None}
        if action == "project":
            result["model_result"] = {"context": {"count": state.count, "records": len(request["resources"]["documents"])}, "exports": {},
                                      "view": {"title": "内存计数", "panels": []}}
        elif action == "save_item":
            result["resource_proposals"] = [{"id": request["runtime"]["operation_id"], "document": {"count": state.count}}]
        else:
            refs = snap["artifact_refs"]
            if action == "increase":
                if self.malformed:
                    return {"status": "ok"}, {"duration_seconds": 0, "stderr": ""}
                state = CounterState(state.count + request["input"]["amount"])
                self.files.stages[request["runtime"]["staging_dir"]]["counter.txt"] = str(state.count).encode()
                result["artifacts"] = [{"key": "counter", "kind": "report", "title": "计数记录", "content": str(state.count),
                                        "attachments": [{"path": "counter.txt", "filename": "counter.txt", "mime": "text/plain", "label": "下载计数"}]}]
                refs = ["counter"]
            elif action == "reset":
                state, refs = CounterState(), []
            if snap["payload"] != {"count": state.count} or result["artifacts"] or action == "reset":
                result["state_proposal"] = {"session_id": snap["session_id"], "owner": snap["owner"], "base_revision": snap["revision"],
                                            "data_version": 1, "payload": {"count": state.count}, "artifact_refs": refs}
            result["model_result"] = {"count": state.count}
        return result, {"duration_seconds": 0, "stderr": "memory log", "exit_code": 0}


class Model:
    def __init__(self):
        self.responses = deque([ModelResponse(tool_calls=(ToolCall("increase", "run_skill_action", dumps({
            "skill_id": "counter", "action_id": "increase", "input": {"amount": 2}})),)), ModelResponse("已增加到 2。")])

    def complete(self, messages, tools):
        return self.responses.popleft() if self.responses else ModelResponse("已记录。")


class ApplicationPortTests(unittest.TestCase):
    def setUp(self):
        self.files, self.store, self.resources = MemoryArtifacts(), MemoryConversations(), MemoryResources()
        self.executor, self.catalog = MemoryExecutor(self.files), MemoryCatalog()
        self.ports = ApplicationPorts(self.store, self.catalog, self.executor, self.resources, self.files)
        self.config = ProjectConfig(skills=replace(SkillsConfig(), default_skill="counter", legacy_owner="counter"))

    def block_local_io(self):
        stack = ExitStack()
        for name in ("subprocess.Popen", "sqlite3.connect", "pathlib.Path.open", "pathlib.Path.mkdir", "pathlib.Path.read_bytes", "pathlib.Path.write_bytes"):
            stack.enter_context(patch(name, side_effect=AssertionError("application attempted local I/O: " + name)))
        return stack

    def test_application_runs_persists_restores_and_downloads_through_memory_ports(self):
        with self.block_local_io():
            app = DesignAgent(Model(), ports=self.ports, config=self.config)
            self.assertEqual(app.application_view()["title"], "内存计数")
            self.assertTrue(app.loop.registry.invoke("read_skill", {"name": "counter"})["ok"])
            self.assertTrue(app.loop.registry.invoke("read_skill_resource", {"skill_id": "counter", "path": "ref"})["ok"])
            turn = app.chat("增加 2")
            self.assertFalse(turn.failed)
            self.assertEqual(app.state_snapshot("counter")["payload"], {"count": 2})
            artifact = turn.artifacts["items"][0]
            self.assertEqual(app.artifact_bytes(artifact["attachments"][0]), b"2")
            self.assertTrue(app.execute_action("counter", "save_item", {})["ok"])
            self.assertEqual(app.context_snapshot()["states"]["counter"]["records"], 1)
            other_ports = replace(self.ports, catalog=MemoryCatalog())
            restored = DesignAgent(Model(), ports=other_ports, config=self.config)
            restored.load_conversation(app.conversation_id)
            self.assertEqual(restored.state_snapshot("counter"), app.state_snapshot("counter"))
            self.assertEqual(restored.conversation_messages(), app.conversation_messages())
            self.assertEqual(restored.artifact_bytes(artifact["attachments"][0]), b"2")
            self.assertIs(restored.capabilities.executor, self.executor)
            self.assertIs(restored.workspace.files, self.files)
            self.assertFalse(self.files.stages)
            restored.reset()
            self.assertEqual(restored.state_snapshot("counter")["payload"], {"count": 0})

    def test_injected_factory_never_constructs_default_adapters(self):
        with self.block_local_io(), patch("src.bootstrap.application_ports", side_effect=AssertionError("default adapters")), patch("src.bootstrap.configured_model", side_effect=AssertionError("default model")):
            app = create_agent(Model(), ports=self.ports, config=self.config)
            self.assertIs(app._conversations, self.store)
            self.assertEqual(app.chat("增加 2").content, "已增加到 2。")

    def test_host_checks_protocol_even_when_executor_is_replaced(self):
        with self.block_local_io():
            app = DesignAgent(Model(), ports=self.ports, config=self.config)
            before = deepcopy(app.workspace.serialize())
            self.executor.malformed = True
            result = app.execute_action("counter", "increase", {"amount": 2})
            self.assertEqual(result["error"]["code"], "INVALID_RESPONSE")
            self.assertEqual(app.workspace.serialize(), before)
            self.assertFalse(self.files.stages)

    def test_attachment_port_failure_does_not_commit_state_or_artifacts(self):
        with self.block_local_io():
            app = DesignAgent(Model(), ports=self.ports, config=self.config)
            before = deepcopy(app.workspace.serialize())
            self.files.fail = True
            result = app.execute_action("counter", "increase", {"amount": 2})
            self.assertEqual(result["error"]["code"], "PERSISTENCE_FAILED")
            self.assertEqual(app.workspace.serialize(), before)
            self.assertFalse(self.files.stages)

    def test_conversation_port_failure_preserves_memory_and_can_retry(self):
        with self.block_local_io():
            app = DesignAgent(Model(), ports=self.ports, config=self.config)
            self.store.fail = True
            app.chat("增加 2")
            self.assertTrue(app.persistence_error)
            self.assertEqual(app.state_snapshot("counter")["payload"], {"count": 2})
            before = deepcopy(app.workspace.serialize())
            self.store.fail = False
            executions = len(self.executor.requests)
            self.assertTrue(app.save_conversation())
            self.assertEqual(len(self.executor.requests), executions)
            self.assertEqual(app.workspace.serialize(), before)
