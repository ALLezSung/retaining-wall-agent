"""Shared input rules; pure Python, independent of tools and UI."""
from math import isfinite
from .models import WallInput, FIELD_LABELS, REQUIRED_FIELDS
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    parameters: dict | None = None
    code: str | None = None
    message: str = ""
    missing: tuple[str, ...] = ()


def check_parameters(values: dict, *, require_surcharge: bool = True) -> ValidationResult:
    """Chat validation requires explicit q; standalone legacy validate defaults it to 0."""
    required = tuple(FIELD_LABELS) if require_surcharge else REQUIRED_FIELDS
    missing = tuple(key for key in required if values.get(key) is None)
    if missing:
        return ValidationResult(False, code="MISSING_INPUT", missing=missing,
                                message="还缺少：" + "、".join(FIELD_LABELS[k] for k in missing))
    try:
        parameters = asdict(validate(values))
    except (ValueError, TypeError) as exc:
        return ValidationResult(False, code="INVALID_INPUT", message=str(exc))
    return ValidationResult(True, parameters, message="通过当前输入规则检查，不等同于设计合格。")


def validate(values: dict) -> WallInput:
    missing = [FIELD_LABELS[k] for k in REQUIRED_FIELDS if values.get(k) is None]
    if missing:
        raise ValueError("还缺少：" + "、".join(missing))
    try:
        data = {k: float(values.get(k, 0.0)) for k in FIELD_LABELS}
    except (TypeError, ValueError) as exc:
        raise ValueError("参数必须是数字，且使用表中指定的单位。") from exc
    if not all(isfinite(v) for v in data.values()):
        raise ValueError("参数不能包含无穷大或非数值。")
    if not 0 < data["height_m"] < 5:
        raise ValueError("本原型仅支持 0 < 墙高 < 5 m。")
    if not 0 < data["top_width_m"] <= data["base_width_m"]:
        raise ValueError("必须满足 0 < 墙顶宽 ≤ 墙底宽。")
    if not 0 < data["base_width_m"] <= 10:
        raise ValueError("墙底宽需在 0–10 m 内。")
    if not 0 < data["soil_unit_weight_kn_m3"] <= 35:
        raise ValueError("填土重度需在 0–35 kN/m³ 内。")
    if not 0 < data["wall_unit_weight_kn_m3"] <= 35:
        raise ValueError("墙体重度需在 0–35 kN/m³ 内。")
    if not 0 < data["friction_angle_deg"] < 60:
        raise ValueError("内摩擦角需在 0–60° 内。")
    if not 0 < data["base_friction_coefficient"] <= 1:
        raise ValueError("基底摩擦系数需在 0–1 内。")
    if not 0 <= data["surcharge_kpa"] <= 100:
        raise ValueError("均布附加荷载需在 0–100 kPa 内。")
    return WallInput(**data)
