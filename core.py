"""Transparent teaching calculation for a small gravity retaining wall.

This is a deliberately restricted, dry-soil Rankine model. It is not a
complete GB 50330 design or a construction document.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, radians, sin


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


def calculate_wall(values: dict) -> dict:
    """Calculate per-metre forces and two stability ratios.

    Geometry: vertical back at x=B; front face connects (0,0) to (B-t,H).
    Active pressure is Rankine pressure for horizontal, dry, cohesionless
    backfill and a smooth vertical back. Base is horizontal.
    """
    p = validate(values)
    H, B, t = p.height_m, p.base_width_m, p.top_width_m
    ka = (1 - sin(radians(p.friction_angle_deg))) / (
        1 + sin(radians(p.friction_angle_deg))
    )
    ea_soil = 0.5 * ka * p.soil_unit_weight_kn_m3 * H**2
    ea_surcharge = ka * p.surcharge_kpa * H
    ea_total = ea_soil + ea_surcharge
    area = H * (B + t) / 2
    centroid_x = B - (B * B + B * t + t * t) / (3 * (B + t))
    wall_weight = area * p.wall_unit_weight_kn_m3
    resisting_moment = wall_weight * centroid_x
    overturning_moment = ea_soil * H / 3 + ea_surcharge * H / 2
    sliding_resistance = p.base_friction_coefficient * wall_weight
    sliding = sliding_resistance / ea_total
    overturning = resisting_moment / overturning_moment
    resultant_from_toe = (resisting_moment - overturning_moment) / wall_weight
    eccentricity = B / 2 - resultant_from_toe
    mean_base_pressure = wall_weight / B
    if 0 < resultant_from_toe < B:
        if abs(eccentricity) <= B / 6:
            base_contact = "全底面受压"
            base_pressure_max = mean_base_pressure * (1 + 6 * abs(eccentricity) / B)
            base_pressure_min = mean_base_pressure * (1 - 6 * abs(eccentricity) / B)
            contact_width = B
        else:
            base_contact = "部分底面受压（三角形压力简化）"
            contact_width = 3 * min(resultant_from_toe, B - resultant_from_toe)
            base_pressure_max = 2 * wall_weight / contact_width
            base_pressure_min = 0.0
    else:
        base_contact = "合力位于基底外，无法建立本模型的压应力分布"
        contact_width = None
        base_pressure_max = None
        base_pressure_min = None

    steps = [
        {"name": "主动土压力系数", "formula": "Ka = (1−sinφ)/(1+sinφ)", "substitution": f"φ={p.friction_angle_deg:g}°", "value": ka, "unit": "—"},
        {"name": "墙底填土侧压力", "formula": "pγ = Ka·γ·H", "substitution": f"{ka:.4f}×{p.soil_unit_weight_kn_m3:g}×{H:g}", "value": ka * p.soil_unit_weight_kn_m3 * H, "unit": "kPa"},
        {"name": "附加荷载侧压力", "formula": "pq = Ka·q", "substitution": f"{ka:.4f}×{p.surcharge_kpa:g}", "value": ka * p.surcharge_kpa, "unit": "kPa"},
        {"name": "填土主动土压力合力", "formula": "Eγ = 0.5·Ka·γ·H²", "substitution": f"0.5×{ka:.4f}×{p.soil_unit_weight_kn_m3:g}×{H:g}²", "value": ea_soil, "unit": "kN/m"},
        {"name": "附加荷载土压力合力", "formula": "Eq = Ka·q·H", "substitution": f"{ka:.4f}×{p.surcharge_kpa:g}×{H:g}", "value": ea_surcharge, "unit": "kN/m"},
        {"name": "主动土压力总合力", "formula": "Ea = Eγ + Eq", "substitution": f"{ea_soil:.3f}+{ea_surcharge:.3f}", "value": ea_total, "unit": "kN/m"},
        {"name": "墙体截面积", "formula": "A = H(B+t)/2", "substitution": f"{H:g}×({B:g}+{t:g})/2", "value": area, "unit": "m²/m"},
        {"name": "墙体自重", "formula": "G = A·γw", "substitution": f"{area:.3f}×{p.wall_unit_weight_kn_m3:g}", "value": wall_weight, "unit": "kN/m"},
        {"name": "墙体重心距墙趾", "formula": "xG = B − (B²+Bt+t²)/[3(B+t)]", "substitution": f"{B:g}−({B:g}²+{B:g}×{t:g}+{t:g}²)/[3×({B:g}+{t:g})]", "value": centroid_x, "unit": "m"},
        {"name": "抗滑阻力", "formula": "R = μ·G", "substitution": f"{p.base_friction_coefficient:g}×{wall_weight:.3f}", "value": sliding_resistance, "unit": "kN/m"},
        {"name": "抗滑系数", "formula": "Ks = R/Ea", "substitution": f"{sliding_resistance:.3f}/{ea_total:.3f}", "value": sliding, "unit": "—"},
        {"name": "抗倾覆力矩", "formula": "Mr = G·xG", "substitution": f"{wall_weight:.3f}×{centroid_x:.3f}", "value": resisting_moment, "unit": "kN·m/m"},
        {"name": "填土倾覆力矩", "formula": "Mγ = Eγ·H/3", "substitution": f"{ea_soil:.3f}×{H:g}/3", "value": ea_soil * H / 3, "unit": "kN·m/m"},
        {"name": "附加荷载倾覆力矩", "formula": "Mq = Eq·H/2", "substitution": f"{ea_surcharge:.3f}×{H:g}/2", "value": ea_surcharge * H / 2, "unit": "kN·m/m"},
        {"name": "倾覆力矩总计", "formula": "Mo = Mγ + Mq", "substitution": f"{ea_soil * H / 3:.3f}+{ea_surcharge * H / 2:.3f}", "value": overturning_moment, "unit": "kN·m/m"},
        {"name": "抗倾覆系数", "formula": "Ko = Mr/Mo", "substitution": f"{resisting_moment:.3f}/{overturning_moment:.3f}", "value": overturning, "unit": "—"},
        {"name": "基底合力距墙趾", "formula": "xR = (Mr−Mo)/G", "substitution": f"({resisting_moment:.3f}−{overturning_moment:.3f})/{wall_weight:.3f}", "value": resultant_from_toe, "unit": "m"},
        {"name": "基底偏心距", "formula": "e = B/2−xR", "substitution": f"{B:g}/2−{resultant_from_toe:.3f}", "value": eccentricity, "unit": "m"},
        {"name": "基底平均压力", "formula": "p̄ = G/B", "substitution": f"{wall_weight:.3f}/{B:g}", "value": mean_base_pressure, "unit": "kPa"},
    ]
    if base_pressure_max is not None:
        if base_contact == "全底面受压":
            max_substitution = f"{mean_base_pressure:.3f}×(1+6×{abs(eccentricity):.3f}/{B:g})"
            min_substitution = f"{mean_base_pressure:.3f}×(1−6×{abs(eccentricity):.3f}/{B:g})"
        else:
            max_substitution = f"2×{wall_weight:.3f}/{contact_width:.3f}"
            min_substitution = "受压边缘以外为零"
        steps.extend([
            {"name": "基底接触宽度", "formula": "全底面：a=B；部分受压：a=3·min(xR,B−xR)", "substitution": base_contact, "value": contact_width, "unit": "m"},
            {"name": "基底最大压力", "formula": "全底面：G/B·(1+6|e|/B)；部分受压：2G/a", "substitution": max_substitution, "value": base_pressure_max, "unit": "kPa"},
            {"name": "基底最小压力", "formula": "全底面：G/B·(1−6|e|/B)；部分受压：0", "substitution": min_substitution, "value": base_pressure_min, "unit": "kPa"},
        ])
    return {
        "input": asdict(p),
        "earth_pressure_coefficient_ka": ka,
        "active_force_soil_kn_m": ea_soil,
        "active_force_surcharge_kn_m": ea_surcharge,
        "active_force_total_kn_m": ea_total,
        "wall_area_m2_per_m": area,
        "wall_centroid_from_toe_m": centroid_x,
        "wall_weight_kn_m": wall_weight,
        "sliding_resistance_kn_m": sliding_resistance,
        "sliding_margin_kn_m": sliding_resistance - ea_total,
        "resisting_moment_knm_m": resisting_moment,
        "overturning_moment_knm_m": overturning_moment,
        "overturning_moment_soil_knm_m": ea_soil * H / 3,
        "overturning_moment_surcharge_knm_m": ea_surcharge * H / 2,
        "resultant_from_toe_m": resultant_from_toe,
        "eccentricity_m": eccentricity,
        "middle_third_limit_m": B / 6,
        "base_contact": base_contact,
        "base_contact_width_m": contact_width,
        "base_pressure_mean_kpa": mean_base_pressure,
        "base_pressure_max_kpa": base_pressure_max,
        "base_pressure_min_kpa": base_pressure_min,
        "calculation_steps": steps,
        "sliding_ratio": sliding,
        "overturning_ratio": overturning,
        "reference_sliding_min": 1.3,
        "reference_overturning_min": 1.6,
        "sliding_pass": sliding >= 1.3,
        "overturning_pass": overturning >= 1.6,
        "pending_checks": [
            {"item": "地基承载力", "needed": "地基承载力特征值及地基土、基础埋深等资料；已估算基底压力，但不能判定承载力是否满足"},
            {"item": "地基变形/沉降", "needed": "地层分布、压缩模量或沉降试验资料及基础尺寸"},
            {"item": "整体稳定", "needed": "坡体剖面、地层、地下水、潜在滑面和各层抗剪参数"},
            {"item": "墙身强度与构造", "needed": "墙体材料强度、连接与施工构造信息"},
            {"item": "地下水、地震等特殊工况", "needed": "水位、排水、地震参数；本模型不适用时应换用相应方法"},
        ],
        "scope": "教学初算：直立墙背、水平无黏性填土、干燥、水平基底、非地震。基底压力只是静力估算，未构成地基承载力验算；不能据此作为施工设计。",
    }


STANDARD_ENTRIES = {
    "11.2.2": {
        "clause": "GB 50330-2013 第 11.2.2 条",
        "summary": "重力式挡墙设计需检查抗滑移与抗倾覆；特殊地基或坡顶位置还需检查地基稳定。",
    },
    "11.2.3": {
        "clause": "GB 50330-2013 第 11.2.3 条",
        "summary": "给出抗滑移稳定性验算；本教学原型以 1.30 作为一般工况的对照值。",
    },
    "11.2.4": {
        "clause": "GB 50330-2013 第 11.2.4 条",
        "summary": "给出抗倾覆稳定性验算；本教学原型以 1.60 作为一般工况的对照值。",
    },
    "11.2.6": {
        "clause": "GB 50330-2013 第 11.2.6 条",
        "summary": "地基承载力和墙体结构强度还需按相关标准计算，本原型暂未覆盖。",
    },
}


def query_standard(keyword: str = "稳定", selected: list[str] | None = None) -> list[dict]:
    """Return selected standard records and the reviewed GB 50330 clause index."""
    from standards import STANDARDS

    if selected is None:
        selected = ["GB 50330-2013"]
    if any(code not in STANDARDS for code in selected):
        raise ValueError("规范选择无效。")
    words = keyword.strip().lower()
    output = []
    if "GB 50330-2013" in selected:
        matches = [item for item in STANDARD_ENTRIES.values() if not words or words in (item["clause"] + item["summary"]).lower()]
        output.extend(matches or list(STANDARD_ENTRIES.values()))
    for code in selected:
        if code != "GB 50330-2013":
            item = STANDARDS[code]
            output.append({"clause": f"{code} {item['name']}", "summary": item["role"] + "；" + item["coverage"], "url": item["url"]})
    return output
