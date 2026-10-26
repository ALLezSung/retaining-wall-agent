"""Human-readable, reproducible report for the supported teaching model."""

from __future__ import annotations

from .models import FIELD_LABELS


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def summary_markdown(result: dict) -> str:
    selected = result.get("selected_standards", [])
    has_slope_code = any(item["code"] == "GB 50330-2013" for item in selected)
    lines = [
        "**这组条件已完成教学初算。**",
        f"- 主动土压力：**{result['active_force_total_kn_m']:.2f} kN/m**（填土 {result['active_force_soil_kn_m']:.2f} + 附加荷载 {result['active_force_surcharge_kn_m']:.2f}）",
        f"- 抗滑系数：**{result['sliding_ratio']:.3f}**；抗倾覆系数：**{result['overturning_ratio']:.3f}**",
        f"- 基底最大/最小压力估算：**{result['base_pressure_max_kpa']:.2f} / {result['base_pressure_min_kpa']:.2f} kPa**" if result["base_pressure_max_kpa"] is not None else f"- 基底合力位置：**{result['base_contact']}**",
    ]
    if has_slope_code:
        lines.append(f"- GB 50330-2013 教学对照：抗滑 {'达到' if result['sliding_pass'] else '未达到'} 1.30；抗倾覆 {'达到' if result['overturning_pass'] else '未达到'} 1.60。")
    else:
        lines.append("- 本次未勾选 GB 50330-2013，因此只展示力学值，不给出其阈值对照。")
    lines.extend(["", "**结论范围**：仅为当前简化工况初算；地基承载力、整体稳定等仍待验算。下方可查看全部公式与输入数据。"])
    return "\n".join(lines)


def calculation_markdown(result: dict) -> str:
    lines = ["### 输入与模型假定", "| 参数 | 数值 |", "|---|---:|"]
    for key, label in FIELD_LABELS.items():
        lines.append(f"| {label} | {result['input'][key]:g} |")
    lines.extend(["", result["scope"], "", "填土自重侧压力按三角形分布，均布荷载引起的侧压力按矩形分布；两者作用点分别取距基底 H/3 和 H/2。", "", "### 逐项计算", "| 计算量 | 公式 | 代入 | 结果 |", "|---|---|---|---:|"])
    for step in result["calculation_steps"]:
        lines.append(f"| {step['name']} | {_cell(step['formula'])} | {_cell(step['substitution'])} | {step['value']:.4f} {step['unit']} |")
    lines.extend(["", "### 基底压力说明", f"合力位置：{result['base_contact']}。基底压力是在墙体自重和水平土压力作用下的静力估算，尚未与地基承载力比较。", "", "### 仍需补充的验算资料"])
    for item in result["pending_checks"]:
        lines.append(f"- **{item['item']}**：{item['needed']}")
    return "\n".join(lines)


def standards_markdown(result: dict) -> str:
    items = result.get("selected_standards", [])
    if not items:
        return "本次未选择规范。"
    lines = []
    for item in items:
        lines.append(f"**[{item['code']}《{item['name']}》]({item['url']})**")
        lines.append(f"\n{item.get('status', item['coverage'])}。{item['coverage']}。\n")
        if item.get("checks"):
            lines.extend(["| 已实现项目 | 计算值 | 教学对照值 | 状态 |", "|---|---:|---:|---|"])
            for check in item["checks"]:
                lines.append(f"| {check['item']} | {check['value']:.3f} | {check['teaching_reference']:.2f} | {'达到' if check['passed'] else '未达到'} |")
        if item.get("requirements"):
            lines.append("\n核算关注项：" + "、".join(item["requirements"]) + "。\n")
    lines.append("所选规范纳入本次核算要求；未实现项目保持待验算状态，不能据此判定全面合规。")
    return "\n".join(lines)


def book_sections(result: dict) -> dict[str, str]:
    """Presentation-ready sections; UI never reconstructs domain calculations."""
    parameters = ["| 参数 | 数值 |", "|---|---:|"]
    parameters.extend(f"| {label} | {result['input'][key]:g} |" for key, label in FIELD_LABELS.items())
    steps = ["| 计算量 | 公式 | 代入 | 结果 |", "|---|---|---|---:|"]
    steps.extend(f"| {step['name']} | {_cell(step['formula'])} | {_cell(step['substitution'])} | {step['value']:.4f} {step['unit']} |"
                 for step in result["calculation_steps"])
    pressure = [f"**接触状态：**{result['base_contact']}。"]
    if result["base_pressure_max_kpa"] is not None:
        pressure.append(f"\n最大压力 **{result['base_pressure_max_kpa']:.2f} kPa** · 最小压力 **{result['base_pressure_min_kpa']:.2f} kPa**")
    pressure.append("\n基底压力为自重和水平土压力作用下的静力估算，尚需地基承载力资料进行比较。")
    return {
        "参数表": "\n".join(parameters),
        "计算背景": result["scope"] + "\n\n填土自重和均布荷载侧压力分别按三角形与矩形分布，作用点取 H/3 与 H/2。",
        "逐项计算结果": "\n".join(steps),
        "基底压力": "\n".join(pressure),
        "规范合规情况": standards_markdown(result),
        "需补充的验算资料": "\n".join(f"- **{item['item']}**：{item['needed']}" for item in result["pending_checks"]),
    }
