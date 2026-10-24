"""Stable tool registration, runtime schema checks and uniform results."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable


ERROR_CODES = {"MISSING_INPUT", "INVALID_INPUT", "INVALID_STATE", "EXECUTION_FAILED"}


class ToolError(ValueError):
    def __init__(self, code: str, message: str):
        if code not in ERROR_CODES:
            raise ValueError("Unknown error code")
        self.code = code
        super().__init__(message)


def success(data: Any = None) -> dict:
    return {"ok": True, "data": data, "error": None}


def failure(code: str, message: str) -> dict:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def check_schema(value: Any, schema: dict, path: str = "arguments") -> None:
    """Validate the intentionally small JSON-schema subset used by our tools."""
    kind = schema.get("type")
    valid = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
    }
    if kind and (kind not in valid or not valid[kind]()):
        raise ToolError("INVALID_INPUT", f"{path} 类型不合法（需要 {kind}）。")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError("INVALID_INPUT", f"{path} 标识或取值不合法。")
    if kind == "object":
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            raise ToolError("MISSING_INPUT", f"{path} 缺少必需参数。")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ToolError("INVALID_INPUT", f"{path} 包含未知参数。")
        for key, item in value.items():
            if key in properties:
                check_schema(item, properties[key], f"{path}.{key}")
    elif kind == "array":
        for i, item in enumerate(value):
            check_schema(item, schema.get("items", {}), f"{path}[{i}]")
    elif kind in {"number", "integer"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolError("INVALID_INPUT", f"{path} 小于允许的最小值。")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolError("INVALID_INPUT", f"{path} 大于允许的最大值。")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    execute: Callable[..., dict]

    def definition(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": deepcopy(self.schema)}}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict]:
        return [item.definition() for item in self._tools.values()]

    def invoke(self, name: str, arguments: str | dict) -> dict:
        try:
            if name not in self._tools:
                raise ToolError("INVALID_INPUT", "没有这个工具。")
            try:
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
            except (json.JSONDecodeError, ValueError):
                raise ToolError("INVALID_INPUT", "工具参数必须是有效 JSON 对象。")
            tool = self._tools[name]
            check_schema(arguments, tool.schema)
            output = tool.execute(**arguments)
            if not isinstance(output, dict) or set(output) != {"ok", "data", "error"}:
                raise RuntimeError("Invalid tool result contract")
            return output
        except ToolError as exc:
            return failure(exc.code, str(exc))
        except (ValueError, TypeError) as exc:
            return failure("INVALID_INPUT", str(exc))
        except Exception:
            return failure("EXECUTION_FAILED", "工具执行失败，请重试。")
