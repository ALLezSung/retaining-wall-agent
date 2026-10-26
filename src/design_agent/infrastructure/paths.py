"""Filesystem containment checks used only by local adapters."""
from pathlib import Path
from ..errors import CapabilityError


def contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise CapabilityError("INVALID_PATH", "资源路径必须是目录内的相对路径。")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()) or ".." in Path(relative).parts:
        raise CapabilityError("INVALID_PATH", "资源路径越过允许目录。")
    return result
