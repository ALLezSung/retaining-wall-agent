"""JSON orchestration; executable business code is never imported."""
from copy import deepcopy
from threading import RLock
from time import monotonic
from uuid import uuid4
from ...agent.events import Event
from ...agent.tools import RUN_CONTEXT, Tool, success, failure
from ..errors import CapabilityError
from ..ports import SkillCatalog, SkillExecutor, ResourceRepository, ArtifactStore
from ..workspace.service import Workspace
from .protocol import response, validate_resource_proposals


class Capabilities:
    def __init__(self, catalog: SkillCatalog, executor: SkillExecutor, resources: ResourceRepository,
                 artifacts: ArtifactStore, session_id: str, emit):
        self.catalog, self.executor, self.resources = catalog, executor, resources
        self.artifacts, self.emit = artifacts, emit
        self.lock = RLock()
        self.workspace = self.new_workspace(session_id)
        self.logs = []

    def new_workspace(self, identifier):
        return Workspace(identifier, self.artifacts)

    def for_session(self, identifier, emit):
        return Capabilities(self.catalog, self.executor, self.resources, self.artifacts, identifier, emit)

    def initialize(self):
        for owner, entry in self.catalog.owners.items():
            if owner not in self.workspace.states and not entry.unavailable:
                self.lifecycle(owner, "initialize")
        self.workspace.turn_refs.clear()

    def lifecycle(self, owner, operation, args=None):
        entry = self.catalog.owners[owner]
        result = self._execute(entry, entry.manifest["lifecycle"][operation], args or {}, lifecycle=True)
        if not result["ok"]:
            raise CapabilityError(result["error"]["code"], result["error"]["message"])
        return result

    def project(self, owner):
        return self.lifecycle(owner, "project")["data"]

    def context(self):
        return {"states": {owner: self.project(owner).get("context", {}) for owner in self.workspace.states},
                "artifacts": [self.workspace.summary(self.workspace.artifacts[ref]) for s in self.workspace.states.values() for ref in s.artifact_refs],
                "loaded_skills": sorted(self.catalog.loaded)}

    def view(self, skill_id):
        owner = self.catalog.get(skill_id).manifest.get("owner")
        return self.project(owner).get("view", {}) if owner in self.workspace.states else {}

    def run_skill_action(self, skill_id, action_id, input):
        try:
            entry = self.catalog.get(skill_id)
            if action_id not in entry.manifest.get("actions", {}):
                raise CapabilityError("INVALID_INPUT", "技能未声明该操作。")
            if not isinstance(input, dict):
                raise CapabilityError("INVALID_INPUT", "本次业务输入须为 JSON 对象。")
            return self._execute(entry, action_id, input)
        except CapabilityError as exc:
            return failure(exc.code, str(exc))
        except (OSError, RuntimeError):
            return failure("PERSISTENCE_FAILED", "资源或成果保存失败，未提交业务状态，请重试。")

    def _execute(self, entry, action, args, lifecycle=False):
        workspace = self.workspace
        owner = entry.manifest.get("owner", "")
        namespace = entry.manifest.get("resource_namespace")
        request_id = uuid4().hex
        runtime = deepcopy(RUN_CONTEXT.get()) or {"source": "host", "user_text": ""}
        runtime.setdefault("operation_id", f"{runtime['run_id']}:{runtime['call_id']}" if runtime.get("run_id") and runtime.get("call_id") else request_id)
        runtime["staging_dir"] = str(workspace.stage(request_id))
        shared = {name: self.project(binding["owner"])["exports"][binding["export"]]
                  for name, binding in entry.manifest.get("imports", {}).items()}
        request = {"protocol_version": 1, "request_id": request_id, "skill_id": entry.id, "action_id": action,
                   "input": deepcopy(args), "snapshot": workspace.snapshot(owner), "runtime": runtime,
                   "shared": shared, "resources": self.resources.snapshot(namespace, entry.manifest.get("legacy_tables", [])) if namespace else {}}
        label = entry.manifest.get("actions", {}).get(action, {}).get("label", action)
        meta = {k: v for k, v in runtime.items() if k != "staging_dir"}
        meta.update(skill_id=entry.id, action_id=action, label=label, request_id=request_id)
        started = monotonic()
        status = "failed"
        log = {}
        if not lifecycle:
            self.emit(Event("skill_started", meta))
        try:
            result, log = self.executor.execute(entry, request)
            result = response(result, request)
            self.logs.append({**meta, **log, "status": result["status"]})
            status = result["status"]
            if result["status"] != "ok":
                return {"ok": False, "data": result["model_result"], "error": result["error"]}
            readonly = lifecycle and action == entry.manifest["lifecycle"].get("project")
            if readonly and (result["state_proposal"] or result["artifacts"] or result["resource_proposals"]):
                raise CapabilityError("INVALID_RESPONSE", "上下文投影必须只读。")
            if result["resource_proposals"] and (not namespace or result["state_proposal"] or result["artifacts"]):
                raise CapabilityError("INVALID_RESPONSE", "资源保存必须是独立操作。")
            validate_resource_proposals(result["resource_proposals"])
            with self.lock:
                if self.workspace is not workspace or workspace.snapshot(owner) != request["snapshot"]:
                    raise CapabilityError("STALE_STATE", "状态或会话已更新，未提交执行结果。")
                if not readonly:
                    if result["resource_proposals"]:
                        self.resources.commit(namespace, result["resource_proposals"])
                    summaries = workspace.commit(request, result, runtime["staging_dir"], allow_state=bool(owner))
                else:
                    summaries = []
            if not lifecycle:
                if result["state_proposal"]:
                    self.emit(Event("state_updated", {**meta, "owner": owner, "revision": workspace.snapshot(owner)["revision"]}))
                for summary in summaries:
                    self.emit(Event("artifact_created", {**meta, **summary}))
            data = deepcopy(result["model_result"])
            if summaries:
                data["artifacts"] = summaries
            return success(data)
        except CapabilityError as exc:
            status = "failed"
            self.logs.append({**meta, **log, **getattr(exc, "details", {}), "status": status, "error_code": exc.code, "duration_seconds": round(monotonic() - started, 3)})
            raise
        except (OSError, RuntimeError) as exc:
            status = "failed"
            self.logs.append({**meta, "status": status, "error_code": "PERSISTENCE_FAILED", "duration_seconds": round(monotonic() - started, 3)})
            raise CapabilityError("PERSISTENCE_FAILED", "资源或成果保存失败，未提交业务状态，请重试。") from exc
        finally:
            if not lifecycle:
                self.emit(Event("skill_finished", {**meta, "status": status, "duration_seconds": round(monotonic() - started, 3)}))
            try:
                self.artifacts.discard_stage(workspace.session_id, runtime["staging_dir"])
            except (OSError, CapabilityError):
                self.logs.append({"request_id": request_id, "status": "cleanup_pending"})

    def read_skill(self, name):
        try:
            return success(self.catalog.read_skill(name))
        except CapabilityError as exc:
            return failure(exc.code, str(exc))

    def read_skill_resource(self, skill_id, path):
        try:
            return success(self.catalog.read_resource(skill_id, path))
        except CapabilityError as exc:
            return failure(exc.code, str(exc))

    def read_artifact(self, artifact_id):
        try:
            return success(self.workspace.read_artifact(artifact_id))
        except CapabilityError as exc:
            return failure(exc.code, str(exc))

    def tools(self):
        def schema(properties):
            return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
        return [Tool("read_skill", "按目录 id 读取技能说明。", schema({"name": {"type": "string"}}), self.read_skill),
                Tool("read_skill_resource", "读取技能目录内参考文本。", schema({"skill_id": {"type": "string"}, "path": {"type": "string"}}), self.read_skill_resource),
                Tool("run_skill_action", "执行技能声明的操作；只提供本次输入，宿主注入权威快照。", schema({"skill_id": {"type": "string"}, "action_id": {"type": "string"}, "input": {"type": "object"}}), self.run_skill_action),
                Tool("read_artifact", "按标识读取当前会话成果。", schema({"artifact_id": {"type": "string"}}), self.read_artifact)]
