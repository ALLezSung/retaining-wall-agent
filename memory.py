"""Optional local long-term storage for teaching cases."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


DEFAULT_DB = Path(__file__).parent / "data" / "cases.sqlite3"

TEACHING_PRESETS = (
    ("基础干燥填土", "无附加荷载；梯形墙。所有参数均为教学假定。", {"height_m": 4, "base_width_m": 2.4, "top_width_m": 0.5, "soil_unit_weight_kn_m3": 18, "friction_angle_deg": 30, "wall_unit_weight_kn_m3": 24, "base_friction_coefficient": 0.55, "surcharge_kpa": 0}),
    ("坡顶均布荷载", "含 10 kPa 均布附加荷载；梯形墙。所有参数均为教学假定。", {"height_m": 3.5, "base_width_m": 2.3, "top_width_m": 0.6, "soil_unit_weight_kn_m3": 19, "friction_angle_deg": 32, "wall_unit_weight_kn_m3": 24, "base_friction_coefficient": 0.6, "surcharge_kpa": 10}),
    ("矩形墙对照", "矩形截面、无附加荷载。所有参数均为教学假定。", {"height_m": 3, "base_width_m": 1.8, "top_width_m": 1.8, "soil_unit_weight_kn_m3": 17.5, "friction_angle_deg": 28, "wall_unit_weight_kn_m3": 24, "base_friction_coefficient": 0.58, "surcharge_kpa": 0}),
)


def _connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            title TEXT NOT NULL,
            values_json TEXT NOT NULL,
            result_json TEXT NOT NULL
        )"""
    )
    connection.execute("""CREATE TABLE IF NOT EXISTS teaching_presets (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        note TEXT NOT NULL,
        values_json TEXT NOT NULL
    )""")
    connection.executemany(
        "INSERT OR IGNORE INTO teaching_presets(id,title,note,values_json) VALUES(?,?,?,?)",
        [(i, title, note, json.dumps(values, ensure_ascii=False)) for i, (title, note, values) in enumerate(TEACHING_PRESETS, 1)],
    )
    return connection


def list_teaching_presets(db_path: Path = DEFAULT_DB) -> list[dict]:
    with _connect(db_path) as db:
        rows = db.execute("SELECT id,title,note,values_json FROM teaching_presets ORDER BY id").fetchall()
    return [{"id": row[0], "title": row[1], "note": row[2], "values": json.loads(row[3])} for row in rows]


def save_case(title: str, values: dict, result: dict, db_path: Path = DEFAULT_DB) -> int:
    name = title.strip()[:80]
    if not name:
        raise ValueError("案例名称不能为空。")
    with _connect(db_path) as db:
        cur = db.execute(
            "INSERT INTO cases(created_at,title,values_json,result_json) VALUES(?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), name,
             json.dumps(values, ensure_ascii=False), json.dumps(result, ensure_ascii=False)),
        )
        return int(cur.lastrowid)


def list_cases(db_path: Path = DEFAULT_DB) -> list[dict]:
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT id,created_at,title FROM cases ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return [{"id": row[0], "created_at": row[1], "title": row[2]} for row in rows]


def load_case(case_id: int, db_path: Path = DEFAULT_DB) -> dict:
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT title,values_json,result_json FROM cases WHERE id=?", (case_id,)
        ).fetchone()
    if row is None:
        raise ValueError("找不到这个案例。")
    return {"title": row[0], "values": json.loads(row[1]), "result": json.loads(row[2])}
