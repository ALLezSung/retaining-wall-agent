"""Local session files with stable paths and immutable, checked attachments."""
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
from uuid import uuid4

from ..errors import CapabilityError
from ..ports import ArtifactStore
from .paths import contained


class LocalArtifactStore(ArtifactStore):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def session_root(self, session_id: str) -> Path:
        # Preserve the pre-refactor session directories and attachment references.
        return self.root / hashlib.sha256(session_id.encode()).hexdigest()[:32]

    def create_stage(self, session_id: str, request_id: str) -> str:
        path = contained(self.session_root(session_id), f"staging/{request_id}")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _stage(self, session_id: str, stage: str) -> Path:
        path = Path(stage).resolve()
        root = (self.session_root(session_id) / "staging").resolve()
        if path == root or not path.is_relative_to(root):
            raise CapabilityError("INVALID_PATH", "暂存目录不属于当前会话。")
        return path

    def register(self, session_id: str, stage: str, artifacts: dict[str, dict]) -> dict[str, dict]:
        stage_path = self._stage(session_id, stage)
        prepared = deepcopy(artifacts)
        copies = []
        # Check every attachment before writing any registered file.
        for artifact in prepared.values():
            for attachment in artifact.get("attachments", []):
                source = contained(stage_path, attachment["path"])
                if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
                    raise CapabilityError("INVALID_RESPONSE", "附件不存在或超过 16 MiB。")
                data = source.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                relative = f"files/{digest}"
                copies.append((self.session_root(session_id) / relative, data))
                attachment.update(path=relative, sha256=digest)
        for path, data in copies:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
        return prepared

    def read_attachment(self, session_id: str, attachment: dict) -> bytes:
        data = contained(self.session_root(session_id), attachment["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != attachment["sha256"]:
            raise CapabilityError("INVALID_RESOURCE", "附件内容校验失败。")
        return data

    def discard_stage(self, session_id: str, stage: str) -> None:
        # Resolve and verify the exact target before recursively deleting it.
        path = self._stage(session_id, stage)
        if path.exists():
            shutil.rmtree(path)
