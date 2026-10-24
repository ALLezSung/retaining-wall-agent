"""Small local skill catalog. Only front matter is loaded during discovery."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tools import Tool, ToolError, success


@dataclass(frozen=True)
class SkillEntry:
    id: str
    name: str
    description: str
    path: Path


class SkillCatalog:
    def __init__(self, root: Path):
        self.entries: dict[str, SkillEntry] = {}
        self.loaded: set[str] = set()
        for path in sorted(root.glob("*/SKILL.md")):
            metadata = {}
            with path.open(encoding="utf-8") as stream:
                if stream.readline().strip() != "---":
                    raise ValueError(f"Missing skill metadata: {path}")
                for line in stream:
                    if line.strip() == "---":
                        break
                    key, separator, value = line.partition(":")
                    if separator:
                        metadata[key.strip()] = value.strip()
            identifier = path.parent.name
            self.entries[identifier] = SkillEntry(identifier, metadata["name"], metadata["description"], path)

    def directory(self) -> list[dict]:
        return [{"id": item.id, "name": item.name, "description": item.description}
                for item in self.entries.values()]

    def read_skill(self, name: str) -> dict:
        if name not in self.entries:
            raise ToolError("INVALID_INPUT", "未知 skill 标识，请使用目录中的 id。")
        content = self.entries[name].path.read_text(encoding="utf-8")
        self.loaded.add(name)
        return success({"id": name, "content": content})

    def tool(self) -> Tool:
        return Tool("read_skill", "按目录 id 读取 skill 完整说明；首次开展对应业务前先读取。", {
            "type": "object", "properties": {"name": {"type": "string"}},
            "required": ["name"], "additionalProperties": False}, self.read_skill)
