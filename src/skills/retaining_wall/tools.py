"""Wall skill tools. State access is supplied by the design application."""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Protocol, Any

from ...agent.tools import Tool, ToolError, success
from ..standards.index import selected_standard_report
from . import cases
from .calculation import calculate_wall
from .models import FIELD_LABELS
from .validation import check_parameters
from .variants import optimize_geometry


class SessionAccess(Protocol):
    protected_dimensions: set[str]

    def read_session(self) -> Any: ...
    def commit_parameters(self, values: dict, sources: dict, shape: str | None,
                          *, replace: bool = False) -> None: ...
    def store_result(self, result: dict) -> None: ...
    def store_options(self, optimization: dict) -> None: ...


def protected_dimensions(text: str) -> set[str]:
    protected = set()
    for key, names in (("base_width_m", r"墙底宽|底宽"), ("top_width_m", r"墙顶宽|顶宽")):
        if re.search(rf"(?:{names}).{{0,10}}(?:不变|固定|不能动|保持|不改)|(?:保持|固定|不动|不改).{{0,10}}(?:{names})", text):
            protected.add(key)
    return protected


def shape_from_parameters(values: dict) -> str:
    return "rectangle" if values.get("top_width_m") == values.get("base_width_m") else "trapezoid"


def require_parameters(values: dict) -> dict:
    checked = check_parameters(values)
    if not checked.valid:
        raise ToolError(checked.code, checked.message)
    return checked.parameters


def result_evidence(result: dict) -> dict:
    return {key: deepcopy(result.get(key)) for key in (
        "input", "active_force_total_kn_m", "wall_weight_kn_m", "sliding_ratio", "overturning_ratio",
        "base_contact", "base_pressure_max_kpa", "base_pressure_min_kpa", "sliding_pass", "overturning_pass",
        "reference_sliding_min", "reference_overturning_min", "calculation_steps", "pending_checks", "scope", "selected_standards",
    )}


def calculate_current(values: dict, selected_standards: list[str]) -> dict:
    """Shared validation also runs when calculation is requested directly."""
    parameters = require_parameters(values)
    result = calculate_wall(parameters)
    result["selected_standards"] = selected_standard_report(selected_standards, result)
    return result


class WallTools:
    def __init__(self, access: SessionAccess, db_path: Path = cases.DEFAULT_DB):
        self.access = access
        self.db_path = db_path

    def set_parameters(self, **arguments) -> dict:
        session = self.access.read_session()
        values = dict(session.parameters)
        supplied = {key: float(value) for key, value in arguments.items() if key in FIELD_LABELS}
        values.update(supplied)
        shape = arguments.get("wall_shape", session.wall_shape)
        if shape == "rectangle" and "base_width_m" in values:
            if "top_width_m" in supplied and values["top_width_m"] != values["base_width_m"]:
                raise ToolError("INVALID_INPUT", "矩形墙要求墙顶宽等于墙底宽。")
            values["top_width_m"] = values["base_width_m"]
        for key in self.access.protected_dimensions:
            if key in session.parameters and values.get(key) != session.parameters[key]:
                raise ToolError("INVALID_INPUT", f"你已明确要求固定{FIELD_LABELS[key]}，本轮不能修改。")
        if all(key in values for key in FIELD_LABELS):
            require_parameters(values)
        changed = {key: value for key, value in values.items() if session.parameters.get(key) != value}
        sources = {key: "用户输入 · 工具调用" for key in supplied}
        if "top_width_m" in changed and "top_width_m" not in supplied:
            sources["top_width_m"] = "矩形墙：等于墙底宽"
        self.access.commit_parameters(values, sources, shape)
        return success({"updated": changed, "current_values": values,
                        "missing": [FIELD_LABELS[key] for key in FIELD_LABELS if key not in values]})

    def validate_parameters(self) -> dict:
        values = self.access.read_session().parameters
        require_parameters(values)
        return success({"valid": True, "current_values": values,
                        "message": "通过当前输入规则检查，不等同于设计合格。"})

    def calculate_design(self) -> dict:
        session = self.access.read_session()
        result = calculate_current(session.parameters, session.selected_standards)
        self.access.store_result(result)
        return success({"result": result_evidence(result)})

    def compare_designs(self, fixed_dimensions: list[str] | None = None) -> dict:
        session = self.access.read_session()
        if session.current_result is None:
            raise ToolError("INVALID_STATE", "请先提供全部参数并完成当前方案的计算。")
        fixed = sorted(set(fixed_dimensions or []) | self.access.protected_dimensions)
        optimization = optimize_geometry(session.parameters, fixed)
        # Keep the original candidate geometries, but expose contiguous selection ids.
        for index, row in enumerate(optimization["alternatives"], 1):
            row["index"] = index
            row["label"] = f"方案{index} · " + row["label"].split(" · ", 1)[-1]
        self.access.store_options(optimization)
        return success({key: optimization[key] for key in (
            "alternatives", "reason", "fixed_parameters", "scope")})

    def apply_option(self, index: int) -> dict:
        session = self.access.read_session()
        options = (session.pending_options or {}).get("alternatives", [])
        if index < 1 or index > len(options):
            raise ToolError("INVALID_STATE", f"没有该编号的候选；当前有 {len(options)} 个候选。")
        picked = options[index - 1]
        if any(picked[key] != session.parameters.get(key) for key in self.access.protected_dimensions):
            raise ToolError("INVALID_INPUT", "该候选会改变你本轮明确要求固定的尺寸；请重新比选。")
        values = {**session.parameters, "base_width_m": picked["base_width_m"], "top_width_m": picked["top_width_m"]}
        # Prepare everything before committing, so invalid candidates cannot half-apply.
        result = calculate_current(values, session.selected_standards)
        self.access.commit_parameters(values,
                                      {key: f"用户选择方案 {index}" for key in ("base_width_m", "top_width_m")},
                                      shape_from_parameters(values), replace=True)
        self.access.store_result(result)
        return success({"applied_option": index, "label": picked["label"], "result": result_evidence(result)})

    def get_current_result(self) -> dict:
        result = self.access.read_session().current_result
        if result is None:
            raise ToolError("INVALID_STATE", "当前没有有效计算结果，请先完成计算。")
        return success({"result": result_evidence(result)})

    def teaching_cases(self) -> list[dict]:
        return cases.list_teaching_presets(self.db_path)

    def saved_cases(self) -> list[dict]:
        return cases.list_cases(self.db_path)

    def available_cases(self) -> dict[str, dict]:
        available = {item["title"]: item["values"] for item in self.teaching_cases()}
        available.update({f"{item['id']} · {item['title']}": cases.load_case(item["id"], self.db_path)["values"]
                          for item in self.saved_cases()})
        return available

    def list_cases(self) -> dict:
        return success({"case_names": list(self.available_cases())})

    def _load(self, title: str, values: dict) -> dict:
        if all(key in values for key in FIELD_LABELS):
            require_parameters(values)
        self.access.commit_parameters(values, {key: f"数据库案例：{title}" for key in values},
                                      shape_from_parameters(values), replace=True)
        return success({"loaded_case": title, "values": values})

    def load_case(self, title: str) -> dict:
        available = self.available_cases()
        matches = [name for name in available if name == title]
        if not matches:
            matches = [name for name in available if title and title in name]
        if len(matches) != 1:
            raise ToolError("INVALID_INPUT", "案例名称不唯一或不存在。请先列出案例名称。")
        chosen = matches[0]
        return self._load(chosen, available[chosen])

    def load_saved(self, case_id: int) -> dict:
        old = cases.load_case(case_id, self.db_path)
        return self._load(old["title"], old["values"])

    def save_current(self, title: str) -> int:
        session = self.access.read_session()
        if session.current_result is None:
            raise ToolError("INVALID_STATE", "请先计算当前方案再保存。")
        return cases.save_case(title, session.parameters, session.current_result, self.db_path)

    def definitions(self) -> list[Tool]:
        empty = {"type": "object", "properties": {}, "additionalProperties": False}
        numeric = {key: {"type": "number", "description": label} for key, label in FIELD_LABELS.items()}
        return [
            Tool("set_parameters", "保存用户明确提供的挡墙参数，不自动计算；不得猜测参数。", {
                "type": "object", "properties": {**numeric, "wall_shape": {"type": "string", "enum": ["rectangle", "trapezoid"]}},
                "additionalProperties": False}, self.set_parameters),
            Tool("validate_parameters", "检查当前参数是否齐全、数值范围和几何关系，不代表设计合格。", empty, self.validate_parameters),
            Tool("calculate_design", "校验当前参数并调用确定性计算，返回数值证据和适用范围。", empty, self.calculate_design),
            Tool("compare_designs", "搜索挡墙几何方案，可锁定底宽或顶宽，不改变当前参数。", {
                "type": "object", "properties": {"fixed_dimensions": {"type": "array", "items": {
                    "type": "string", "enum": ["base_width_m", "top_width_m"]}}}, "additionalProperties": False}, self.compare_designs),
            Tool("apply_option", "用户同意候选后按编号选用并重新计算；未指定编号时用1。", {
                "type": "object", "properties": {"index": {"type": "integer", "minimum": 1, "maximum": 3}},
                "required": ["index"], "additionalProperties": False}, self.apply_option),
            Tool("get_current_result", "读取当前有效计算结果及计算步骤。", empty, self.get_current_result),
            Tool("list_cases", "列出本地教学及已保存案例的名称。", empty, self.list_cases),
            Tool("load_case", "按名称载入案例参数；需要结果时再计算。", {
                "type": "object", "properties": {"title": {"type": "string"}},
                "required": ["title"], "additionalProperties": False}, self.load_case),
        ]
