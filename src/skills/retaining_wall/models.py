"""Retaining-wall input model and field metadata."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WallInput:
    height_m: float
    base_width_m: float
    top_width_m: float
    soil_unit_weight_kn_m3: float
    friction_angle_deg: float
    wall_unit_weight_kn_m3: float
    base_friction_coefficient: float
    surcharge_kpa: float = 0.0


FIELD_LABELS = {
    "height_m": "墙高 H（m）",
    "base_width_m": "墙底宽 B（m）",
    "top_width_m": "墙顶宽 t（m）",
    "soil_unit_weight_kn_m3": "填土重度 γ（kN/m³）",
    "friction_angle_deg": "填土内摩擦角 φ（°）",
    "wall_unit_weight_kn_m3": "墙体重度 γw（kN/m³）",
    "base_friction_coefficient": "基底摩擦系数 μ",
    "surcharge_kpa": "均布附加荷载 q（kPa）",
}

REQUIRED_FIELDS = tuple(k for k in FIELD_LABELS if k != "surcharge_kpa")
