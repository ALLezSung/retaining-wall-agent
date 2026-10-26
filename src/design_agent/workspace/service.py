"""Generic in-memory state and commit rules; attachment I/O is injected."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from uuid import uuid4
from ...agent.serialization import dumps
from ..errors import CapabilityError
from ..ports import ArtifactStore
from .models import StateEnvelope


class Workspace:
    def __init__(self, session_id: str, files: ArtifactStore):
        self.session_id = session_id
        self.files = files
        self.states = {}
        self.artifacts = {}
        self.operations = []
        self.turn_refs = []

    def snapshot(self, owner):
        return asdict(self.states.get(owner, StateEnvelope(self.session_id, owner)))

    def stage(self, request_id: str) -> str:
        return self.files.create_stage(self.session_id, request_id)

    def commit(self, request, result, stage, *, allow_state=True):
        """Validate everything before exposing state, references or operation records."""
        before = request["snapshot"]
        owner = before["owner"]
        current = self.snapshot(owner)
        if request["snapshot"]["session_id"] != self.session_id or before != current:
            raise CapabilityError("STALE_STATE", "状态已更新或会话已切换，请重新执行。")
        proposal = result["state_proposal"]
        if proposal:
            if not allow_state or not owner:
                raise CapabilityError("INVALID_RESPONSE", "该动作无权提交状态。")
            if any(proposal[k] != before[v] for k, v in (("session_id", "session_id"), ("owner", "owner"), ("base_revision", "revision"))):
                raise CapabilityError("STALE_STATE", "状态提案归属或修订不一致。")
        revision = uuid4().hex if proposal else current["revision"]
        prepared = {}
        for item in result["artifacts"]:
            artifact = deepcopy(item)
            identifier = hashlib.sha256(f"{self.session_id}/{request['request_id']}/{item['key']}".encode()).hexdigest()[:32]
            artifact.update(id=identifier, session_id=self.session_id, owner=owner, revision=revision)
            prepared[item["key"]] = artifact
        refs = proposal["artifact_refs"] if proposal else []
        mapped = []
        for ref in refs:
            if ref in prepared:
                mapped.append(prepared[ref]["id"])
            elif ref in self.artifacts and self.artifacts[ref]["owner"] == owner:
                mapped.append(ref)
            else:
                raise CapabilityError("INVALID_RESPONSE", "状态引用了不存在的成果。")
        # No visible state changes until the adapter registers every attachment.
        artifacts = self.files.register(self.session_id, stage, {a["id"]: a for a in prepared.values()})
        states = deepcopy(self.states)
        if proposal:
            states[owner] = StateEnvelope(self.session_id, owner, 1, proposal["data_version"], revision,
                                          deepcopy(proposal["payload"]), mapped)
        self.artifacts.update(artifacts)
        self.states = states
        # Removed current refs are still retained as immutable historical artifacts.
        if proposal:
            self.turn_refs = [ref for ref in self.turn_refs
                              if self.artifacts[ref]["owner"] != owner or ref in mapped]
        self.turn_refs.extend(a for a in artifacts if a not in self.turn_refs and (not proposal or a in mapped))
        self.operations.append({"request_id": request["request_id"], "skill_id": request["skill_id"],
                                "action_id": request["action_id"], "runtime": deepcopy(request["runtime"]),
                                "revision": revision, "artifacts": list(artifacts)})
        return [self.summary(a) for a in artifacts.values()]

    @staticmethod
    def summary(artifact):
        return {k: artifact[k] for k in ("id", "kind", "title", "summary") if k in artifact}

    def read_artifact(self, identifier):
        if identifier not in self.artifacts:
            raise CapabilityError("INVALID_INPUT", "成果不存在或不属于当前会话。")
        return deepcopy(self.artifacts[identifier])

    def attachment_bytes(self, attachment):
        return self.files.read_attachment(self.session_id, attachment)

    def serialize(self):
        return {"states": {k: asdict(v) for k, v in self.states.items()}, "artifacts": self.artifacts,
                "operations": self.operations}

    def restore(self, value):
        states = {}
        for owner, raw in value["states"].items():
            state = StateEnvelope(**raw)
            if state.owner != owner or state.session_id != self.session_id or state.protocol_version != 1:
                raise ValueError("Invalid state envelope")
            if not isinstance(owner, str) or not owner or type(state.data_version) is not int or state.data_version < 1 or not isinstance(state.revision, str) or not state.revision:
                raise ValueError("Invalid state version")
            if not isinstance(state.payload, dict) or not isinstance(state.artifact_refs, list):
                raise ValueError("Invalid state payload")
            if not all(isinstance(r, str) for r in state.artifact_refs):
                raise ValueError("Invalid artifact reference")
            states[owner] = state
        artifacts = deepcopy(value["artifacts"])
        for identifier, artifact in artifacts.items():
            if artifact["id"] != identifier or artifact["session_id"] != self.session_id:
                raise ValueError("Invalid artifact ownership")
        if any(ref not in artifacts for s in states.values() for ref in s.artifact_refs):
            raise ValueError("Missing artifact")
        dumps(value)
        self.states, self.artifacts, self.operations = states, artifacts, deepcopy(value.get("operations", []))
