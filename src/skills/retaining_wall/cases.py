"""Business case conversion. No database access or host imports."""
import json
from .validation import check_parameters

TEACHING_PRESETS = (
    ("基础干燥填土", "无附加荷载；梯形墙。所有参数均为教学假定。", {"height_m": 4, "base_width_m": 2.4, "top_width_m": 0.5, "soil_unit_weight_kn_m3": 18, "friction_angle_deg": 30, "wall_unit_weight_kn_m3": 24, "base_friction_coefficient": 0.55, "surcharge_kpa": 0}),
    ("坡顶均布荷载", "含 10 kPa 均布附加荷载；梯形墙。所有参数均为教学假定。", {"height_m": 3.5, "base_width_m": 2.3, "top_width_m": 0.6, "soil_unit_weight_kn_m3": 19, "friction_angle_deg": 32, "wall_unit_weight_kn_m3": 24, "base_friction_coefficient": 0.6, "surcharge_kpa": 10}),
    ("矩形墙对照", "矩形截面、无附加荷载。所有参数均为教学假定。", {"height_m": 3, "base_width_m": 1.8, "top_width_m": 1.8, "soil_unit_weight_kn_m3": 17.5, "friction_angle_deg": 28, "wall_unit_weight_kn_m3": 24, "base_friction_coefficient": 0.58, "surcharge_kpa": 0}),
)


def validate_values(values):
    checked = check_parameters(values)
    if not checked.valid:
        raise ValueError(checked.message)


def documents(resources):
    output = {}
    for i, (title, note, values) in enumerate(TEACHING_PRESETS, 1):
        output[("preset", i)] = {"kind": "preset", "id": i, "title": title, "note": note, "values": values}
    legacy = resources.get("legacy_tables", {})
    for row in legacy.get("teaching_presets", []):
        output[("preset", row["id"])] = {"kind": "preset", "id": row["id"], "title": row["title"], "note": row["note"], "values": json.loads(row["values_json"])}
    for row in legacy.get("cases", []):
        output[("case", row["id"])] = {"kind": "case", "id": row["id"], "created_at": row["created_at"], "title": row["title"], "values": json.loads(row["values_json"]), "result": json.loads(row["result_json"])}
    for doc in resources.get("documents", []):
        if doc.get("kind") not in {"case", "preset"} or type(doc.get("id")) is not int or not isinstance(doc.get("title"), str):
            raise ValueError("案例数据格式无效。")
        output[(doc["kind"], doc["id"])] = doc
    for doc in output.values():
        validate_values(doc["values"])
    return list(output.values())
