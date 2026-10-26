"""The sole owner of the complete wall design state and its transitions."""
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from math import isfinite
from .models import FIELD_LABELS
from .validation import check_parameters
from .calculation import calculate_wall
from .standards_policy import selected_standard_report


class BusinessError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def success(data=None):
    return {"ok": True, "data": data, "error": None}


def partial_parameters(values):
    if not isinstance(values, dict) or set(values) - set(FIELD_LABELS):
        raise BusinessError("INVALID_INPUT", "参数表包含未知字段。")
    bounds = {"height_m": (0, 5, False), "base_width_m": (0, 10, True), "top_width_m": (0, 10, True),
              "soil_unit_weight_kn_m3": (0, 35, True), "wall_unit_weight_kn_m3": (0, 35, True),
              "friction_angle_deg": (0, 60, False), "base_friction_coefficient": (0, 1, True), "surcharge_kpa": (0, 100, True)}
    for key, value in values.items():
        low, high, inclusive = bounds[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise BusinessError("INVALID_INPUT", "参数必须是有限数字。")
        if (value < low if key == "surcharge_kpa" else value <= low) or (value > high if inclusive else value >= high):
            raise BusinessError("INVALID_INPUT", f"{FIELD_LABELS[key]} 超出适用范围。")
    if "top_width_m" in values and "base_width_m" in values and values["top_width_m"] > values["base_width_m"]:
        raise BusinessError("INVALID_INPUT", "必须满足 0 < 墙顶宽 ≤ 墙底宽。")


@dataclass
class WallState:
    parameters: dict[str, float] = field(default_factory=dict)
    parameter_sources: dict[str, str] = field(default_factory=dict)
    wall_shape: str | None = None
    current_result: dict | None = None
    pending_options: dict | None = None
    selected_standards: list[str] = field(default_factory=lambda: ["GB 50330-2013", "GB 55003-2021"])

    def __post_init__(self):
        self.protected_dimensions = set()
        self.refs = []
        self.generated = []
        self.validate()

    def validate(self):
        partial_parameters(self.parameters)
        if not isinstance(self.parameter_sources, dict) or set(self.parameter_sources) - set(self.parameters) or not all(isinstance(v, str) for v in self.parameter_sources.values()):
            raise BusinessError("INVALID_INPUT", "参数来源无效。")
        if self.wall_shape not in {None, "rectangle", "trapezoid"}:
            raise BusinessError("INVALID_INPUT", "墙型无效。")
        if self.wall_shape == "rectangle" and all(k in self.parameters for k in ("base_width_m", "top_width_m")) and self.parameters["base_width_m"] != self.parameters["top_width_m"]:
            raise BusinessError("INVALID_INPUT", "矩形墙要求顶宽与底宽相等。")
        if not isinstance(self.selected_standards, list) or not all(isinstance(s, str) for s in self.selected_standards):
            raise BusinessError("INVALID_INPUT", "规范选择无效。")
        selected_standard_report(self.selected_standards)
        if self.current_result is not None:
            checked = check_parameters(self.parameters)
            if not checked.valid or not isinstance(self.current_result, dict):
                raise BusinessError("INVALID_STATE", "当前结果没有完整有效的输入。")
            expected = calculate_wall(self.parameters)
            if any(self.current_result.get(k) != v for k, v in expected.items()):
                raise BusinessError("INVALID_STATE", "当前结果与参数或计算模型不一致。")
            if "selected_standards" in self.current_result and self.current_result["selected_standards"] != selected_standard_report(self.selected_standards, self.current_result):
                raise BusinessError("INVALID_STATE", "当前结果与所选规范不一致。")
        if self.pending_options is not None:
            if self.current_result is None or not isinstance(self.pending_options, dict) or not isinstance(self.pending_options.get("alternatives"), list):
                raise BusinessError("INVALID_STATE", "候选没有对应的有效结果。")
            for row in self.pending_options["alternatives"]:
                checked = check_parameters({**self.parameters, "base_width_m": row["base_width_m"], "top_width_m": row["top_width_m"]})
                if not checked.valid:
                    raise BusinessError("INVALID_STATE", "候选参数无效。")

    def to_dict(self):
        return asdict(self)

    def read_session(self):
        return deepcopy(self)

    def invalidate(self):
        self.current_result = self.pending_options = None
        self.refs.clear()
        self.generated.clear()

    def commit_parameters(self, values, sources, shape, *, replace=False):
        partial_parameters(values)
        changed = values != self.parameters or shape != self.wall_shape
        self.parameters = deepcopy(values)
        self.parameter_sources = deepcopy(sources) if replace else {**self.parameter_sources, **deepcopy(sources)}
        self.wall_shape = shape
        if changed or replace:
            self.invalidate()

    def store_result(self, result):
        if result["input"] != self.parameters:
            raise BusinessError("INVALID_STATE", "计算结果与当前参数不一致。")
        self.current_result = deepcopy(result)
        self.generated.append("calculation")

    def store_options(self, options):
        self.pending_options = deepcopy(options)
        self.generated.append("comparison")

    def select_standards(self, selected):
        selected_standard_report(selected)
        if selected != self.selected_standards:
            self.selected_standards = list(selected)
            self.invalidate()
