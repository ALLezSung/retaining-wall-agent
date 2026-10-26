"""Skill metadata, independent of the local directory discovery implementation."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillEntry:
    id: str
    name: str
    description: str
    path: Path
    manifest: dict = field(default_factory=dict)
    unavailable: str = ""

    @property
    def root(self) -> Path:
        return self.path.parent
