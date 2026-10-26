"""Typed project configuration, read as TOML data without executing Python."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_DIR / "data" / "cases.sqlite3"
DEFAULT_CONVERSATIONS_DATABASE = PROJECT_DIR / "data" / "conversations.sqlite3"


class ConfigError(ValueError):
    """Configuration errors expose field names, never secret values."""


@dataclass(frozen=True)
class ModelConfig:
    api_key: str = field(default="", repr=False)
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: float = 45
    max_tokens: int = 1200
    reasoning_effort: str = "default"
    reasoning_overrides: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentConfig:
    max_rounds: int = 6


@dataclass(frozen=True)
class StorageConfig:
    cases_database: Path = DEFAULT_DATABASE
    conversations_database: Path = DEFAULT_CONVERSATIONS_DATABASE


@dataclass(frozen=True)
class SkillsConfig:
    directories: tuple[Path, ...] = (PROJECT_DIR / "src" / "skills",)
    workspace: Path = PROJECT_DIR / "data" / "workspace"
    default_skill: str = "retaining_wall"
    legacy_owner: str = "wall_design"


def _text(value, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value.strip() and not allow_empty):
        raise ConfigError(f"配置 {name} 必须是{'可为空的' if allow_empty else '非空'}字符串。")
    return value.strip()


def _positive_number(value, name: str, *, integer: bool = False):
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError
        if integer and not isinstance(value, (str, int)):
            raise ValueError
        parsed = int(value) if integer else float(value)
        if parsed <= 0 or not isfinite(parsed):
            raise ValueError
        return parsed
    except (ValueError, TypeError, OverflowError):
        raise ConfigError(f"配置 {name} 必须是正{'整数' if integer else '数'}。") from None


def _base_url(value) -> str:
    base = _text(value, "llm.base_url").rstrip("/")
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment)
        parsed.port  # Validate malformed port values, too.
    except ValueError:
        valid = False
    if not valid or base.endswith("/chat/completions"):
        raise ConfigError("配置 llm.base_url 应为 HTTP(S) 服务基础地址，可包含 /v1，不包含 /chat/completions、账号或查询参数。")
    return base


def base_url_from_endpoint(endpoint: str) -> str:
    """Compatibility with the previous DEEPSEEK_ENDPOINT environment variable."""
    endpoint = _text(endpoint, "DEEPSEEK_ENDPOINT").rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        raise ConfigError("DEEPSEEK_ENDPOINT 应以 /chat/completions 结尾；建议改用 DEEPSEEK_BASE_URL。")
    return _base_url(endpoint.removesuffix("/chat/completions"))


@dataclass(frozen=True)
class ProjectConfig:
    llm: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)

    @classmethod
    def load(cls, path: Path | None = None, *, environ: Mapping[str, str] | None = None) -> "ProjectConfig":
        path = Path(path) if path is not None else PROJECT_DIR / "config.toml"
        env = os.environ if environ is None else environ
        try:
            with path.open("rb") as stream:
                document = tomllib.load(stream)
        except FileNotFoundError:
            document = {}  # Environment-only startup remains supported.
        except tomllib.TOMLDecodeError:
            raise ConfigError("config.toml 格式错误，请检查 TOML 语法。") from None
        except OSError:
            raise ConfigError("无法读取 config.toml，请检查文件权限。") from None

        allowed = {"llm": {"api_key", "model", "base_url", "timeout_seconds", "max_tokens",
                           "reasoning_effort", "reasoning_overrides"},
                   "agent": {"max_rounds"}, "storage": {"cases_database", "conversations_database"},
                   "skills": {"directories", "workspace", "default_skill", "legacy_owner"}}
        for section, values in document.items():
            if section not in allowed or not isinstance(values, dict):
                raise ConfigError("config.toml 包含未知配置分组或分组格式不正确。")
            if set(values) - allowed[section]:
                raise ConfigError(f"config.toml 的 {section} 分组包含未知配置项。")

        def setting(section: str, name: str, env_name: str, default):
            return env.get(env_name, "").strip() or document.get(section, {}).get(name, default)

        def model_setting(name, legacy, default):
            return env.get("LLM_" + name.upper(), "").strip() or setting("llm", name, legacy, default)

        base = model_setting("base_url", "DEEPSEEK_BASE_URL", ModelConfig.base_url)
        if not (env.get("LLM_BASE_URL", "").strip() or env.get("DEEPSEEK_BASE_URL", "").strip()) and env.get("DEEPSEEK_ENDPOINT", "").strip():
            base = base_url_from_endpoint(env["DEEPSEEK_ENDPOINT"])
        effort = _text(model_setting("reasoning_effort", "DEEPSEEK_REASONING_EFFORT", "default"), "llm.reasoning_effort")
        if effort not in {"default", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ConfigError("配置 llm.reasoning_effort 不受支持。")
        overrides = document.get("llm", {}).get("reasoning_overrides", {})
        if not isinstance(overrides, dict) or any(
            not isinstance(mapping, dict) or not mapping or any(
                key not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
                or not isinstance(value, str) or not value.strip() for key, value in mapping.items())
            for mapping in overrides.values()
        ):
            raise ConfigError("配置 llm.reasoning_overrides 应为模型名到思考深度映射的表。")
        llm = ModelConfig(
            api_key=_text(model_setting("api_key", "DEEPSEEK_API_KEY", ""), "llm.api_key", allow_empty=True),
            model=_text(model_setting("model", "DEEPSEEK_MODEL", ModelConfig.model), "llm.model"),
            base_url=_base_url(base),
            timeout_seconds=_positive_number(model_setting("timeout_seconds", "DEEPSEEK_TIMEOUT_SECONDS", 45), "llm.timeout_seconds"),
            max_tokens=_positive_number(model_setting("max_tokens", "DEEPSEEK_MAX_TOKENS", 1200), "llm.max_tokens", integer=True),
            reasoning_effort=effort, reasoning_overrides=overrides,
        )
        agent = AgentConfig(_positive_number(setting("agent", "max_rounds", "AGENT_MAX_ROUNDS", 6), "agent.max_rounds", integer=True))
        database = Path(_text(setting("storage", "cases_database", "CASES_DATABASE", "data/cases.sqlite3"), "storage.cases_database"))
        if not database.is_absolute():
            database = PROJECT_DIR / database
        conversations = Path(_text(setting("storage", "conversations_database", "CONVERSATIONS_DATABASE", "data/conversations.sqlite3"), "storage.conversations_database"))
        if not conversations.is_absolute():
            conversations = PROJECT_DIR / conversations
        skills = document.get("skills", {})
        directories = skills.get("directories", ["src/skills"])
        if not isinstance(directories, list) or not directories or not all(isinstance(p, str) and p.strip() for p in directories):
            raise ConfigError("skills.directories 必须是非空路径列表。")
        def resolve(p):
            path = Path(p)
            return (PROJECT_DIR / path).resolve() if not path.is_absolute() else path.resolve()
        return cls(llm, agent, StorageConfig(database.resolve(), conversations.resolve()), SkillsConfig(
            tuple(resolve(p) for p in directories), resolve(_text(skills.get("workspace", "data/workspace"), "skills.workspace")),
            _text(skills.get("default_skill", "retaining_wall"), "skills.default_skill"),
            _text(skills.get("legacy_owner", "wall_design"), "skills.legacy_owner")))
