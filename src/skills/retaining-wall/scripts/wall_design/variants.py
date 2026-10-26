"""Transparent base-width comparison inside the existing teaching model."""

from __future__ import annotations

from math import ceil

from .calculation import calculate_wall
from .validation import validate


def optimize_geometry(values: dict, fixed_parameters: list[str] | None = None, step_m: float = 0.1) -> dict:
    """Search B and/or t while all soil, load, material and height data stay fixed.

    This is a transparent teaching grid search, not a construction design.
    """
    p = validate(values)
    fixed = set(fixed_parameters or [])
    if not fixed <= {"base_width_m", "top_width_m"}:
        raise ValueError("目前只能锁定墙底宽或墙顶宽。")
    if not 0 < step_m <= 0.5:
        raise ValueError("比选步长需在 0–0.5 m 内。")
    current = calculate_wall(values)
    floor_top = min(0.3, p.top_width_m)
    widths = [p.base_width_m] if "base_width_m" in fixed else [round(i * step_m, 6) for i in range(1, int(10 / step_m) + 1)]
    feasible = []
    attempted = []
    for width in widths:
        if "top_width_m" in fixed:
            tops = [p.top_width_m] if p.top_width_m <= width else []
        else:
            tops = [round(i * step_m, 6) for i in range(ceil(floor_top / step_m), int(round(width / step_m)) + 1)]
            if width >= floor_top and width not in tops:
                tops.append(width)
            if width == p.base_width_m and p.top_width_m not in tops:
                tops.append(p.top_width_m)
        for top in tops:
            if not 0 < top <= width:
                continue
            candidate = calculate_wall({**values, "base_width_m": width, "top_width_m": top})
            attempted.append(candidate)
            if candidate["sliding_pass"] and candidate["overturning_pass"] and candidate["base_contact"] == "全底面受压":
                feasible.append(candidate)
    best = min(feasible, key=lambda r: (r["wall_weight_kn_m"], r["input"]["base_width_m"], r["input"]["top_width_m"])) if feasible else None
    def deficit(r: dict) -> tuple:
        return (
            int(r["base_contact"] != "全底面受压"),
            max(0.0, 1.3 - r["sliding_ratio"]) + max(0.0, 1.6 - r["overturning_ratio"]),
            r["wall_weight_kn_m"],
        )
    closest = min(attempted, key=deficit) if attempted else current

    def row(label: str, r: dict) -> dict:
        return {
            "label": label,
            "base_width_m": r["input"]["base_width_m"],
            "top_width_m": r["input"]["top_width_m"],
            "wall_weight_kn_m": r["wall_weight_kn_m"],
            "weight_change_percent": 100 * (r["wall_weight_kn_m"] / current["wall_weight_kn_m"] - 1),
            "sliding_ratio": r["sliding_ratio"],
            "overturning_ratio": r["overturning_ratio"],
            "base_contact": r["base_contact"],
            "base_pressure_max_kpa": r["base_pressure_max_kpa"],
            "passes_teaching_screen": r["sliding_pass"] and r["overturning_pass"] and r["base_contact"] == "全底面受压",
        }

    alternatives = []
    if best:
        compact = min(feasible, key=lambda r: (r["input"]["base_width_m"], r["wall_weight_kn_m"]))
        more_margin = [r for r in feasible if
                       abs(r["input"]["top_width_m"] - best["input"]["top_width_m"]) < 1e-6
                       and r["input"]["base_width_m"] >= best["input"]["base_width_m"] + 2 * step_m - 1e-6]
        robust = min(more_margin, key=lambda r: r["input"]["base_width_m"]) if more_margin else None
        seen = {(p.base_width_m, p.top_width_m)}
        for label, candidate in (("方案1 · 较少墙体用量", best),
                                 ("方案2 · 较小墙底宽", compact),
                                 ("方案3 · 更多稳定余量", robust)):
            if candidate is None:
                continue
            identity = (candidate["input"]["base_width_m"], candidate["input"]["top_width_m"])
            if identity not in seen:
                alternatives.append(row(label, candidate))
                seen.add(identity)
    rows = [row("当前方案", current)] + alternatives
    if not best and (closest["input"]["base_width_m"], closest["input"]["top_width_m"]) != (p.base_width_m, p.top_width_m):
        rows.append(row("最接近的试算（仍未达到）", closest))

    if best:
        reason = f"找到 {len(alternatives)} 个不同的教学候选，分别侧重墙体用量、较小底宽或稳定余量；请指定想试的方案编号。"
    elif "base_width_m" in fixed:
        reason = "墙底宽已保持不变。只调整墙顶宽后，仍找不到同时满足抗滑、抗倾覆和基底全接触的候选。若底宽必须固定，请进一步说明是否允许改变墙型、墙体材料或基础抗滑措施；这些方案需要新增参数和相应计算，当前工具不能直接判定它们有效。"
    else:
        reason = "在本次几何搜索范围内，没有找到同时满足三项教学筛选条件的候选。"
    return {
        "fixed_parameters": sorted(fixed),
        "step_m": step_m,
        "searched_count": len(attempted),
        "recommended": alternatives[0] if alternatives else (row("当前方案", current) if best else None),
        "alternatives": alternatives,
        "candidates": rows,
        "reason": reason,
        "scope": "仅在现有干燥水平填土模型内试算几何尺寸；墙高、土性、荷载、墙体重度和基底摩擦系数不变。抗滑≥1.30、抗倾覆≥1.60及基底全接触只是教学筛选。墙顶宽搜索下限 0.3 m（已有更小值时保留原值），不是规范构造下限；未核算地基承载力、整体稳定、墙身强度、构造和实际造价。",
    }


def compare_base_widths(values: dict, step_m: float = 0.1) -> dict:
    """Try B values while holding all other inputs fixed.

    Screening uses the two teaching reference ratios plus full base contact.
    It does not perform bearing-capacity, settlement, or global checks.
    """
    p = validate(values)
    if not 0 < step_m <= 0.5:
        raise ValueError("比选步长需在 0–0.5 m 内。")
    current = calculate_wall(values)
    start = ceil(p.top_width_m / step_m)
    end = int(10 / step_m)
    feasible = None
    for i in range(start, end + 1):
        width = round(i * step_m, 6)
        candidate = calculate_wall({**values, "base_width_m": width})
        if candidate["sliding_pass"] and candidate["overturning_pass"] and candidate["base_contact"] == "全底面受压":
            feasible = candidate
            break

    chosen = [("当前方案", current)]
    if feasible is not None:
        chosen.append(("最小候选宽度", feasible))
        extra_width = round(feasible["input"]["base_width_m"] + 0.3, 6)
        if extra_width <= 10:
            chosen.append(("加宽 0.3 m", calculate_wall({**values, "base_width_m": extra_width})))
    seen = set()
    candidates = []
    for label, result in chosen:
        width = result["input"]["base_width_m"]
        if width in seen:
            continue
        seen.add(width)
        candidates.append({
            "label": label,
            "base_width_m": width,
            "wall_weight_kn_m": result["wall_weight_kn_m"],
            "weight_change_percent": 100 * (result["wall_weight_kn_m"] / current["wall_weight_kn_m"] - 1),
            "sliding_ratio": result["sliding_ratio"],
            "overturning_ratio": result["overturning_ratio"],
            "base_pressure_max_kpa": result["base_pressure_max_kpa"],
            "full_contact": result["base_contact"] == "全底面受压",
            "passes_teaching_screen": result["sliding_pass"] and result["overturning_pass"] and result["base_contact"] == "全底面受压",
        })
    return {
        "step_m": step_m,
        "fixed_top_width_m": p.top_width_m,
        "searched_to_m": 10.0,
        "recommended_width_m": feasible["input"]["base_width_m"] if feasible else None,
        "candidates": candidates,
        "scope": "仅改变墙底宽，保持其余教学参数不变；筛选条件为抗滑≥1.30、抗倾覆≥1.60且基底全接触。未验算地基承载力、整体稳定、墙身强度与经济性。",
    }
