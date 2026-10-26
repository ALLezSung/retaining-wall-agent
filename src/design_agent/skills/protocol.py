"""Application-owned skill wire protocol. Payload semantics belong to the owner."""
from ...agent.serialization import dumps
from ..errors import CapabilityError


def response(value, request):
    required = {"protocol_version", "request_id", "status", "model_result", "state_proposal", "artifacts", "resource_proposals", "error"}
    try:
        dumps(value)
        if not (isinstance(value, dict) and set(value) == required):
            raise ValueError("invalid protocol field")
        if not (type(value["protocol_version"]) is int and value["protocol_version"] == 1):
            raise ValueError("invalid protocol field")
        if not (value["request_id"] == request["request_id"]):
            raise ValueError("invalid protocol field")
        if not (value["status"] in {"ok", "missing_input", "business_error"}):
            raise ValueError("invalid protocol field")
        if not (isinstance(value["model_result"], dict)):
            raise ValueError("invalid protocol field")
        if not (isinstance(value["artifacts"], list) and isinstance(value["resource_proposals"], list)):
            raise ValueError("invalid protocol field")
        if value["status"] != "ok":
            if not (value["state_proposal"] is None and not value["artifacts"] and not value["resource_proposals"]):
                raise ValueError("invalid protocol field")
            if not (isinstance(value["error"], dict) and set(value["error"]) == {"code", "message"}):
                raise ValueError("invalid protocol field")
            if not (all(isinstance(v, str) and v for v in value["error"].values())):
                raise ValueError("invalid protocol field")
        else:
            if not (value["error"] is None):
                raise ValueError("invalid protocol field")
        proposal = value["state_proposal"]
        if proposal is not None:
            if not (isinstance(proposal, dict)):
                raise ValueError("invalid protocol field")
            if not (set(proposal) == {"session_id", "owner", "base_revision", "data_version", "payload", "artifact_refs"}):
                raise ValueError("invalid protocol field")
            if not (isinstance(proposal["payload"], dict)):
                raise ValueError("invalid protocol field")
            if not (type(proposal["data_version"]) is int and proposal["data_version"] > 0):
                raise ValueError("invalid protocol field")
            if not (isinstance(proposal["artifact_refs"], list) and all(isinstance(r, str) for r in proposal["artifact_refs"])):
                raise ValueError("invalid protocol field")
        for artifact in value["artifacts"]:
            if not (isinstance(artifact, dict)):
                raise ValueError("invalid protocol field")
            if not (set(artifact) <= {"key", "kind", "title", "group", "summary", "content", "sections", "columns", "rows", "attachments"}):
                raise ValueError("invalid protocol field")
            if not (all(isinstance(artifact.get(k), str) and artifact[k] for k in ("key", "kind", "title"))):
                raise ValueError("invalid protocol field")
            if not (artifact["kind"] in {"text", "table", "report", "attachment"}):
                raise ValueError("invalid protocol field")
            if not (isinstance(artifact.get("summary", ""), str) and isinstance(artifact.get("content", ""), str)):
                raise ValueError("invalid protocol field")
            if not isinstance(artifact.get("group", ""), str):
                raise ValueError("invalid artifact group")
            if "rows" in artifact:
                if not (isinstance(artifact["rows"], list) and all(isinstance(r, dict) for r in artifact["rows"])):
                    raise ValueError("invalid protocol field")
            if "columns" in artifact:
                if not (isinstance(artifact["columns"], list) and all(isinstance(c, str) for c in artifact["columns"])):
                    raise ValueError("invalid protocol field")
            if "sections" in artifact:
                if not (isinstance(artifact["sections"], list)):
                    raise ValueError("invalid protocol field")
                if not (all(isinstance(s, dict) and set(s) == {"title", "content"} and all(isinstance(v, str) for v in s.values()) for s in artifact["sections"])):
                    raise ValueError("invalid protocol field")
            if not (isinstance(artifact.get("attachments", []), list)):
                raise ValueError("invalid protocol field")
            for attachment in artifact.get("attachments", []):
                if not (set(attachment) == {"path", "filename", "mime", "label"}):
                    raise ValueError("invalid protocol field")
                if not (all(isinstance(v, str) and v for v in attachment.values())):
                    raise ValueError("invalid protocol field")
        keys = [a["key"] for a in value["artifacts"]]
        if not (len(keys) == len(set(keys))):
            raise ValueError("invalid protocol field")
    except (ValueError, KeyError, TypeError):
        raise CapabilityError("INVALID_RESPONSE", "技能响应不符合协议，未提交业务变化。") from None
    return value



def validate_resource_proposals(proposals):
    for item in proposals:
        if not isinstance(item, dict) or set(item) != {"id", "document"} or not isinstance(item["id"], str) or not isinstance(item["document"], dict):
            raise CapabilityError("INVALID_RESPONSE", "资源提案格式无效。")
    dumps(proposals)
