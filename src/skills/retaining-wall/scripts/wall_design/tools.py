"""Wall skill tools. State access is supplied by the design application."""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from dataclasses import asdict
from .state import WallState, BusinessError as ToolError, success

from .standards_policy import selected_standard_report
from . import cases
from .calculation import calculate_wall
from .models import FIELD_LABELS
from .validation import check_parameters
from .variants import optimize_geometry


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


class WallOperations:
    def __init__(self, state: WallState, resources: dict, user_text: str = "", operation_id: str = ""):
        self.access = state
        self.access.protected_dimensions = protected_dimensions(user_text)
        self.resources = resources
        self.resource_proposals = []
        self.operation_id = operation_id

    def set_parameters(self, **arguments) -> dict:
        session = self.access.read_session()
        values = dict(session.parameters)
        if set(arguments) - set(FIELD_LABELS) - {"wall_shape"}:
            raise ToolError("INVALID_INPUT", "包含未知参数。")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for k, v in arguments.items() if k in FIELD_LABELS):
            raise ToolError("INVALID_INPUT", "参数必须是数字。")
        supplied = {key: float(value) for key, value in arguments.items() if key in FIELD_LABELS}
        values.update(supplied)
        shape = arguments.get("wall_shape", session.wall_shape)
        if shape not in {None, "rectangle", "trapezoid"}:
            raise ToolError("INVALID_INPUT", "墙型无效。")
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
        if fixed_dimensions is not None and (not isinstance(fixed_dimensions, list) or not all(isinstance(k, str) and k in {"base_width_m", "top_width_m"} for k in fixed_dimensions)):
            raise ToolError("INVALID_INPUT", "固定尺寸必须是底宽/顶宽标识列表。")
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
        if type(index) is not int:
            raise ToolError("INVALID_INPUT", "候选编号必须为整数。")
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

    def documents(self):
        return cases.documents(self.resources)

    def teaching_cases(self):
        return [d for d in self.documents() if d["kind"] == "preset"]

    def saved_cases(self):
        return [d for d in self.documents() if d["kind"] == "case"][-20:][::-1]

    def available_cases(self):
        return {d["title"] if d["kind"] == "preset" else f"{d['id']} · {d['title']}": d["values"] for d in self.documents()}

    def list_cases(self):
        return success({"case_names": list(self.available_cases())})

    def _load(self, title, values):
        cases.validate_values(values)
        self.access.commit_parameters(values, {key: f"数据库案例：{title}" for key in values}, shape_from_parameters(values), replace=True)
        return success({"loaded_case": title, "values": values})

    def load_case(self, title):
        available = self.available_cases()
        matches = [name for name in available if name == title]
        if not matches:
            matches = [name for name in available if title and title in name]
        if len(matches) != 1:
            raise ToolError("INVALID_INPUT", "案例名称不唯一或不存在。请先列出案例名称。")
        return self._load(matches[0], available[matches[0]])

    def load_saved(self, case_id):
        found = [d for d in self.documents() if d["kind"] == "case" and str(d["id"]) == str(case_id)]
        if len(found) != 1:
            raise ToolError("INVALID_INPUT", "找不到这个案例。")
        return self._load(found[0]["title"], found[0]["values"])

    def save_current(self, title):
        if self.access.current_result is None:
            raise ToolError("INVALID_STATE", "请先计算当前方案再保存。")
        if not isinstance(title, str) or not title.strip():
            raise ToolError("INVALID_INPUT", "案例名称不能为空。")
        docs = self.documents()
        saved = [d for d in docs if self.operation_id and d.get("operation_id") == self.operation_id]
        if saved:
            return success({"case_id": saved[0]["id"]})
        identifier = max([int(d["id"]) for d in docs if d["kind"] == "case"] or [0]) + 1
        document = {"kind": "case", "id": identifier, "title": title.strip()[:80], "operation_id": self.operation_id,
                    "values": self.access.parameters, "result": self.access.current_result}
        # Include legacy rows in an idempotent resource-only import; never rewrite originals.
        self.resource_proposals = [{"id": f"{d['kind']}:{d['id']}", "document": d} for d in docs]
        self.resource_proposals.append({"id": f"case:{identifier}", "document": document})
        return success({"case_id": identifier})
