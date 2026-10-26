"""Standalone JSON CLI. Only this business package is imported."""
import json
from pathlib import Path
import sys

if not __package__:
    __package__ = Path(__file__).parent.name
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .state import WallState, BusinessError, success
from .tools import WallOperations, result_evidence
from .models import FIELD_LABELS
from .report import summary_markdown, calculation_markdown, standards_markdown, book_sections
from .standards_policy import STANDARDS, STANDARD_ENTRIES, selected_standard_report


def projection(state, operations):
    context = {"current_parameters": state.parameters, "wall_shape": state.wall_shape,
               "has_calculated_result": state.current_result is not None,
               "result_summary": {k: state.current_result[k] for k in ("sliding_ratio", "overturning_ratio", "base_contact")} if state.current_result else None,
               "last_options": (state.pending_options or {}).get("alternatives", []),
               "selected_standards": state.selected_standards, "standard_requirements": selected_standard_report(state.selected_standards)}
    presets, saved = operations.teaching_cases(), operations.saved_cases()
    view = {"title": "挡土墙设计工作台", "page_title": "挡土墙 · 设计工作台", "icon": "🧱", "eyebrow": "RETAINING WALL / AGENT",
            "subtitle": "讨论设计条件，逐项计算，保留每一步依据。", "welcome": "从设计条件开始。描述墙高、截面与填土条件，或从侧栏选用教学案例。",
            "input_placeholder": "输入设计条件、修改要求，或询问计算结果",
            "scope": "适用：墙高小于 5 m、干燥水平无黏性填土、直立墙背、水平基底及非地震教学工况。",
            "status": "当前方案：已有有效计算结果。" if state.current_result else "当前方案：尚无有效计算结果。",
            "loaded_message": "案例参数已载入，可直接提出计算或修改要求。" if state.parameters else "",
            "parameter_table": "| 参数 | 当前值 |\n|---|---:|\n" + "\n".join(f"| {label} | {state.parameters.get(key, '待补充')} |" for key, label in FIELD_LABELS.items()),
            "panels": [
                {"title": "规范加载", "expanded": True, "controls": [{"id": "standards", "type": "multiselect", "label": "选择本次核算规范", "options": list(STANDARDS), "value": state.selected_standards, "action": "select_standards", "argument": "selected"}],
                 "caption": "所选规范纳入核算要求。变更后需重新计算；缺少数据的项目列为待验算。"},
                {"title": "教学案例库", "expanded": False, "controls": [
                    {"id": "teaching", "type": "select_action", "label": "从预设参数开始", "options": [{"value": d["title"], "label": d["title"], "note": d.get("note", "")} for d in presets], "action": "load_case", "argument": "title", "button": "从案例新建对话", "new_conversation": True},
                    {"id": "saved", "type": "select_action", "label": "已保存的参数案例", "options": [{"value": d["id"], "label": d["title"]} for d in saved], "action": "load_saved", "argument": "case_id", "button": "从保存案例新建", "new_conversation": True},
                    {"id": "save", "type": "text_action", "label": "保存案例名称", "value": "对话算例", "action": "save_current", "argument": "title", "button": "保存当前参数与结果", "enabled": state.current_result is not None}]}]}
    return {"context": context, "view": view, "exports": {"standard_context": {"version": 1, "selected": state.selected_standards, "catalog": STANDARDS, "entries": list(STANDARD_ENTRIES.values()), "requirements": selected_standard_report(state.selected_standards, state.current_result)}}}


def artifacts(state, stage):
    output = []
    for key in dict.fromkeys(state.generated):
        if key == "calculation":
            result = state.current_result
            sections = book_sections(result)
            markdown = summary_markdown(result) + "\n\n" + calculation_markdown(result) + "\n\n### 规范覆盖\n" + standards_markdown(result)
            (stage / "wall_result.json").write_bytes(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))
            (stage / "wall_report.md").write_bytes(markdown.encode("utf-8"))
            output.append({"key": key, "kind": "report", "title": "计算书", "group": "计算书", "summary": f"抗滑 {result['sliding_ratio']:.3f}；抗倾覆 {result['overturning_ratio']:.3f}", "content": summary_markdown(result),
                           "sections": [{"title": title, "content": content} for title, content in sections.items()],
                           "attachments": [{"path": name, "filename": name, "mime": mime, "label": label} for name, mime, label in (("wall_result.json", "application/json", "下载计算 JSON"), ("wall_report.md", "text/markdown", "下载计算书 Markdown"))]})
        else:
            opt = state.pending_options
            rows = [{"方案": r["label"], "底宽 B (m)": f"{r['base_width_m']:.2f}", "顶宽 t (m)": f"{r['top_width_m']:.2f}", "自重 (kN/m)": f"{r['wall_weight_kn_m']:.2f}", "用量变化": f"{r['weight_change_percent']:+.1f}%", "抗滑": f"{r['sliding_ratio']:.3f}", "抗倾覆": f"{r['overturning_ratio']:.3f}", "基底接触": r["base_contact"], "教学筛选": "达到" if r["passes_teaching_screen"] else "未达到"} for r in opt["candidates"]]
            output.append({"key": key, "kind": "table", "title": "方案比选", "group": "计算书", "summary": opt["reason"], "content": opt["scope"], "rows": rows})
    return output


def handle(request):
    snap, action, args = request["snapshot"], request["action_id"], request["input"]
    base = {"protocol_version": 1, "request_id": request["request_id"], "status": "ok", "model_result": {}, "state_proposal": None, "artifacts": [], "resource_proposals": [], "error": None}
    try:
        if request["protocol_version"] != 1 or not isinstance(args, dict):
            raise BusinessError("INVALID_INPUT", "不支持的请求协议。")
        if snap["data_version"] != 1:
            raise BusinessError("INVALID_STATE", "不支持的挡墙数据版本。")
        if action == "restore" and "legacy_artifacts" in args:
            # Historical cards need no current authority: comparison-only cards
            # may have no parameter snapshot. Never infer one from current state.
            legacy = args["legacy_artifacts"]
            historical = WallState()
            historical.current_result = legacy.get("result")
            if historical.current_result is None and legacy.get("report", {}).get("json"):
                historical.current_result = json.loads(legacy["report"]["json"])
            historical.pending_options = legacy.get("optimization")
            historical.generated = (["calculation"] if historical.current_result else []) + (["comparison"] if historical.pending_options else [])
            stage = Path(request["runtime"]["staging_dir"])
            base["artifacts"] = artifacts(historical, stage)
            for key, filename in (("json", "wall_result.json"), ("markdown", "wall_report.md")):
                content = legacy.get("report", {}).get(key)
                if isinstance(content, str):
                    (stage / filename).write_bytes(content.encode("utf-8"))
            base["model_result"] = {"restored": True}
            return base
        state = WallState(**snap["payload"]) if snap["payload"] else WallState()
        state.refs = list(snap["artifact_refs"])
        old = state.to_dict()
        operations = WallOperations(state, request["resources"], request["runtime"].get("user_text", ""), request["runtime"].get("operation_id", ""))
        if action == "project":
            base["model_result"] = projection(state, operations)
            return base
        if action in {"initialize", "restore", "reset"}:
            if action == "reset":
                state = WallState(selected_standards=state.selected_standards)
            elif action == "restore" and "legacy_state" in args:
                state = WallState(**args["legacy_state"])
            # Business migration interprets legacy result cards without changing their evidence.
            legacy = args.get("legacy_artifacts", {})
            if legacy.get("result"):
                result = legacy["result"]
                state = WallState(parameters=result["input"], current_result=result,
                                  selected_standards=[s["code"] for s in result.get("selected_standards", [])])
                state.generated.append("calculation")
            elif action == "restore" and "legacy_state" in args and state.current_result:
                state.generated.append("calculation")
            if legacy.get("optimization"):
                state.pending_options = legacy["optimization"]
                state.generated.append("comparison")
            output = success({"restored": True})
        elif action == "select_standards":
            if set(args) != {"selected"} or not isinstance(args["selected"], list) or not all(isinstance(x, str) for x in args["selected"]):
                raise BusinessError("INVALID_INPUT", "规范选择无效。")
            state.select_standards(args["selected"])
            output = success({"selected_standards": state.selected_standards})
        else:
            allowed = {"set_parameters", "validate_parameters", "calculate_design", "compare_designs", "apply_option", "get_current_result", "list_cases", "load_case", "load_saved", "save_current"}
            if action not in allowed:
                raise BusinessError("INVALID_INPUT", "未知业务操作。")
            output = getattr(operations, action)(**args)
        state.validate()
        base["model_result"] = output["data"]
        # Normal calculation returns evidence without injecting the entire report/steps.
        if action in {"calculate_design", "apply_option"}:
            base["model_result"]["result"].pop("calculation_steps", None)
        base["artifacts"] = artifacts(state, Path(request["runtime"]["staging_dir"]))
        base["resource_proposals"] = operations.resource_proposals
        if state.to_dict() != old or state.generated or action in {"initialize", "reset"} or (action == "restore" and args):
            base["state_proposal"] = {"session_id": snap["session_id"], "owner": snap["owner"], "base_revision": snap["revision"], "data_version": 1, "payload": state.to_dict(), "artifact_refs": list(state.refs) + [a["key"] for a in base["artifacts"]]}
    except (BusinessError, ValueError, TypeError, KeyError) as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        base.update(status="missing_input" if code == "MISSING_INPUT" else "business_error", model_result={}, state_proposal=None, artifacts=[], resource_proposals=[], error={"code": code, "message": str(exc)})
    return base


if __name__ == "__main__":
    request = json.load(sys.stdin)
    if sys.argv[1:] != ["--action", request["action_id"]]:
        raise SystemExit("action mismatch")
    print(json.dumps(handle(request), ensure_ascii=False, allow_nan=False))
