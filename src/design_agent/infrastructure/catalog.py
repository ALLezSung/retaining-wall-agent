"""Local metadata discovery and bounded resource reads; no model tool registration."""
from pathlib import Path
import re
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from ..errors import CapabilityError
from ..ports import SkillCatalog
from ..skills.catalog import SkillEntry
from .paths import contained
from .skill_metadata import read_metadata


class LocalSkillCatalog(SkillCatalog):
    def __init__(self, roots):
        self.loaded = set()
        self.owners = {}
        paths = set()
        for root in ([roots] if isinstance(roots, Path) else roots):
            root = Path(root).resolve()
            if not root.is_dir():
                raise ValueError(f"技能目录不存在：{root}")
            for path in root.glob("*/SKILL.md"):
                if not path.resolve().is_relative_to(root):
                    raise ValueError(f"技能目录越界：{path}")
                paths.add(path.resolve())
        found = {}
        for path in sorted(paths):
            legacy = path.with_name("skill.toml")
            metadata = read_metadata(path, legacy_name=legacy.is_file())
            extensions = metadata.get("metadata", {})
            integration = extensions.get("design-agent-integration")
            if integration is not None and legacy.exists():
                raise ValueError(f"技能同时声明新旧集成文件，请移除旧 skill.toml：{path}")
            try:
                config = contained(path.parent, integration) if integration is not None else contained(path.parent, "skill.toml")
            except CapabilityError as exc:
                raise ValueError(f"技能集成文件路径无效：{path}") from exc
            if integration is not None and not config.is_file():
                raise ValueError(f"技能声明的集成文件不存在：{config}")
            manifest = tomllib.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
            identifier = manifest.get("id", path.parent.name if legacy.is_file() else metadata["name"])
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", identifier):
                raise ValueError(f"无效技能标识：{path}")
            if identifier in found:
                raise ValueError(f"重复技能标识 {identifier}：{found[identifier].path} / {path}")
            if (integration is not None or manifest) and (manifest.get("protocol_version") != 1 or not isinstance(manifest.get("actions", {}), dict)):
                raise ValueError(f"不支持的技能清单：{config}")
            unavailable = ""
            if manifest:
                try:
                    if not contained(path.parent, manifest["entry"]).is_file():
                        unavailable = "技能入口不存在。"
                except (KeyError, CapabilityError):
                    unavailable = "技能入口无效。"
                for name, action in manifest.get("actions", {}).items():
                    if not isinstance(action, dict) or not isinstance(action.get("label"), str):
                        raise ValueError(f"无效动作 {name}：{config}")
                if manifest.get("owner") and not all(manifest.get("lifecycle", {}).get(k) for k in ("initialize", "restore", "project", "reset")):
                    raise ValueError(f"状态所有者缺少生命周期：{config}")
            display_name = extensions.get("design-agent-display-name", metadata["name"])
            if not display_name.strip():
                raise ValueError(f"技能展示名不能为空：{path}")
            found[identifier] = SkillEntry(identifier, display_name, metadata["description"], path, manifest, unavailable)
        self.entries = dict(sorted(found.items()))
        for entry in self.entries.values():
            owner = entry.manifest.get("owner")
            if owner:
                if owner in self.owners:
                    raise ValueError(f"重复状态所有者：{owner}")
                self.owners[owner] = entry
        for entry in self.entries.values():
            for binding in entry.manifest.get("imports", {}).values():
                if not isinstance(binding, dict) or binding.get("owner") not in self.owners or not binding.get("export"):
                    raise ValueError(f"无效共享投影声明：{entry.path}")

    def directory(self):
        return [{"id": e.id, "name": e.name, "description": e.description,
                 "actions": [{"id": k, "label": v["label"], "available": not bool(e.unavailable)} for k, v in e.manifest.get("actions", {}).items()]}
                for e in self.entries.values()]

    def get(self, identifier):
        if identifier not in self.entries:
            raise CapabilityError("INVALID_INPUT", "未知 skill 标识，请使用目录中的 id。")
        return self.entries[identifier]

    def read_skill(self, name):
        entry = self.get(name)
        try:
            content = entry.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise CapabilityError("INVALID_PATH", "技能说明暂不可读取。") from None
        self.loaded.add(name)
        return {"id": name, "content": content}

    def read_resource(self, skill_id, path):
        target = contained(self.get(skill_id).root, path)
        try:
            if target.stat().st_size > 262144:
                raise CapabilityError("OUTPUT_LIMIT", "参考资源过大。")
            return {"content": target.read_text(encoding="utf-8")}
        except (OSError, UnicodeError):
            raise CapabilityError("INVALID_PATH", "技能参考资源暂不可读取。") from None
