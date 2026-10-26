"""Generic authoritative state envelope. The payload's owner validates it."""
from dataclasses import dataclass, field


@dataclass
class StateEnvelope:
    session_id: str
    owner: str
    protocol_version: int = 1
    data_version: int = 1
    revision: str = "0"
    payload: dict = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
