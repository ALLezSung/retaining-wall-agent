"""DeepSeek-led conversation with bounded, auditable local tool calls."""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import local_config
except ImportError:  # local_config.py 不入库；仅用环境变量也能运行
    local_config = None

from core import FIELD_LABELS, calculate_wall, query_standard, validate
from standards import selected_standard_report
from variants import optimize_geometry


def get_api_key() -> str:
    """Read the environment variable first, then the optional local file."""
    env_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    if local_config is None:
        return ""
    return getattr(local_config, "DEEPSEEK_API_KEY", "").strip()


def calculate_confirmed(state: dict, selected_standards: list[str] | None = None) -> dict:
    """Validate inputs and attach deterministic calculation evidence to state."""
    if selected_standards is None:
        selected_standards = ["GB 50330-2013"]
    values = dict(state.get("values", {}))
    validate(values)
    trace = list(state.get("trace", []))
    clauses = query_standard("11.2", selected_standards)
    trace.append({"step": "工具调用", "tool": "query_standard", "arguments": {"selected_standards": selected_standards}, "output": clauses})
    result = calculate_wall(values)
    result["selected_standards"] = selected_standard_report(selected_standards, result)
    trace.append({"step": "工具调用", "tool": "calculate_wall", "arguments": values.copy(), "output": result})
    completed = dict(state)
    completed.update({"values": values, "trace": trace, "result": result, "clauses": clauses, "awaiting_confirmation": False})
    return completed


SYSTEM_PROMPT = """你是重力式挡土墙教学初算的对话助手。自然地回应用户，不使用固定模板。
你可以直接解释概念、追问缺少的条件，也可以自主决定何时调用提供的工具。
需要保存或修改条件时调用 set_parameters；需要实际数值时调用 calculate_design；需要多个调整方案时调用 compare_designs；用户同意某个方案时调用 apply_option。
用户说“试一下”时结合已有候选理解，未指定编号时默认方案1。用户明确要求固定某个尺寸，必须在 compare_designs 中锁定，不能擅自更改。
所有工程数字、方案和规范覆盖结论只能来自工具结果；不要猜测参数，也不要把教学对照说成完整工程设计。
计算范围是小于5m的干燥、水平无黏性填土、非地震、直立墙背和水平基底；可有均布附加荷载。地下水、地震、黏性土、倾斜填土不在当前计算模型内。
如果条件不足，直接用自然语言问用户。工具报错时根据错误解释下一步。通常用2到5句中文回答，表格会由网页单独展示，无需长篇复述表格。"""

NUMERIC_FIELDS = {key: {"type": "number", "description": label} for key, label in FIELD_LABELS.items()}
TOOLS = [
    {"type": "function", "function": {"name": "set_parameters", "description": "保存本轮用户明确提供或修改的挡墙参数；不会自动计算。只能提交用户给出的值。", "parameters": {
        "type": "object", "properties": {**NUMERIC_FIELDS, "wall_shape": {"type": "string", "enum": ["rectangle", "trapezoid"]}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "calculate_design", "description": "用已保存的全部参数调用确定性挡墙算法与所选规范索引，返回可核对的数值和适用范围。缺项时返回缺项信息。", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "compare_designs", "description": "确定性搜索不同挡墙几何方案。可锁定墙底宽或墙顶宽。返回最多三个侧重点不同且通过教学筛选的候选；若没有，明确返回无候选。", "parameters": {
        "type": "object", "properties": {"fixed_dimensions": {"type": "array", "items": {"type": "string", "enum": ["base_width_m", "top_width_m"]}}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "apply_option", "description": "用户同意上一轮方案后，选用编号并重新计算完整结果。编号从1开始；用户未指定时用1。", "parameters": {
        "type": "object", "properties": {"index": {"type": "integer", "minimum": 1, "maximum": 3}}, "required": ["index"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_current_result", "description": "读取当前已计算结果、完整计算步骤、待验算项目，以便解释用户的问题。", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_cases", "description": "列出本地数据库可载入的教学案例和已保存案例名称。", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "load_case", "description": "按名称载入数据库案例的参数；载入后如需数值结果再调用 calculate_design。", "parameters": {
        "type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "lookup_standard", "description": "检索侧栏已选规范的本地审核索引和覆盖状态；不能把未验算项目说成已通过。", "parameters": {
        "type": "object", "properties": {"keyword": {"type": "string"}}, "additionalProperties": False}}},
]


def _completion(messages: list[dict], *, allow_tools: bool = True) -> dict:
    if not get_api_key():
        raise ValueError("请先配置 DeepSeek API Key。")
    payload = {"model": "deepseek-flash", "messages": messages, "thinking": {"type": "disabled"}, "max_tokens": 1200}
    if allow_tools:
        payload.update({"tools": TOOLS, "tool_choice": "auto"})
    request = Request("https://api.deepseek.com/chat/completions",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Authorization": f"Bearer {get_api_key()}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=45) as response:
            envelope = json.load(response)
        choice = envelope["choices"][0]
    except HTTPError as exc:
        raise ValueError(f"DeepSeek 请求失败（HTTP {exc.code}）。") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError("无法连接 DeepSeek，请检查网络。") from exc
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("DeepSeek 返回格式异常，请重试。") from exc
    if choice.get("finish_reason") == "length":
        raise ValueError("DeepSeek 回复被截断，请缩短问题重试。")
    return choice.get("message") or {}


def _result_evidence(result: dict) -> dict:
    return {key: result.get(key) for key in (
        "input", "active_force_total_kn_m", "wall_weight_kn_m", "sliding_ratio", "overturning_ratio",
        "base_contact", "base_pressure_max_kpa", "base_pressure_min_kpa", "sliding_pass", "overturning_pass",
        "reference_sliding_min", "reference_overturning_min", "calculation_steps", "pending_checks", "scope", "selected_standards",
    )}


def _protected_dimensions(text: str) -> set[str]:
    """Enforce dimensions the user explicitly locked in this message."""
    protected = set()
    for key, names in (("base_width_m", r"墙底宽|底宽"), ("top_width_m", r"墙顶宽|顶宽")):
        if re.search(rf"(?:{names}).{{0,10}}(?:不变|固定|不能动|保持|不改)|(?:保持|固定|不动|不改).{{0,10}}(?:{names})", text):
            protected.add(key)
    return protected


def _execute(name: str, arguments: dict, state: dict, selected_standards: list[str], cases: dict[str, dict], protected: set[str] | None = None) -> tuple[dict, dict, dict]:
    """Return (new state, tool response, UI artifacts)."""
    if name == "set_parameters":
        unexpected = set(arguments) - set(FIELD_LABELS) - {"wall_shape"}
        if unexpected:
            raise ValueError("包含未知参数。")
        values = dict(state.get("values", {}))
        changed = {}
        for key in FIELD_LABELS:
            if key in arguments:
                value = arguments[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{FIELD_LABELS[key]} 必须是数字。")
                if key in (protected or set()) and key in values and float(value) != float(values[key]):
                    raise ValueError(f"你已明确要求固定{FIELD_LABELS[key]}，本轮不能修改。")
                changed[key] = float(value)
        values.update(changed)
        shape = arguments.get("wall_shape", state.get("wall_shape"))
        if shape not in {None, "rectangle", "trapezoid"}:
            raise ValueError("墙型只能是矩形或梯形。")
        if shape == "rectangle" and "base_width_m" in values:
            if "top_width_m" in changed and values["top_width_m"] != values["base_width_m"]:
                raise ValueError("矩形墙要求墙顶宽等于墙底宽。")
            values["top_width_m"] = values["base_width_m"]
        if all(key in values for key in FIELD_LABELS):
            validate(values)
        next_state = {**state, "values": values, "result": None, "wall_shape": shape,
                      "sources": {**state.get("sources", {}), **{key: "用户输入 · DeepSeek 工具调用" for key in changed}}}
        next_state.pop("last_optimization", None)
        missing = [FIELD_LABELS[key] for key in FIELD_LABELS if key not in values]
        return next_state, {"ok": True, "updated": changed, "current_values": values, "missing": missing}, {}

    if name == "calculate_design":
        values = state.get("values", {})
        missing = [FIELD_LABELS[key] for key in FIELD_LABELS if key not in values]
        if missing:
            return state, {"ok": False, "missing": missing}, {}
        completed = calculate_confirmed(state, selected_standards)
        return completed, {"ok": True, "result": _result_evidence(completed["result"])}, {"result": completed["result"]}

    if name == "compare_designs":
        fixed = arguments.get("fixed_dimensions", [])
        if not isinstance(fixed, list) or any(key not in {"base_width_m", "top_width_m"} for key in fixed):
            raise ValueError("固定尺寸只支持墙底宽和墙顶宽。")
        fixed = sorted(set(fixed) | (protected or set()))
        if not state.get("result"):
            return state, {"ok": False, "error": "请先提供全部参数并完成当前方案的计算。"}, {}
        optimization = optimize_geometry(state["values"], fixed)
        next_state = {**state, "last_optimization": optimization}
        return next_state, {"ok": True, "alternatives": optimization["alternatives"], "reason": optimization["reason"], "fixed_parameters": optimization["fixed_parameters"], "scope": optimization["scope"]}, {"optimization": optimization}

    if name == "apply_option":
        index = arguments.get("index")
        options = (state.get("last_optimization") or {}).get("alternatives", [])
        if not isinstance(index, int) or isinstance(index, bool) or index < 1 or index > len(options):
            return state, {"ok": False, "error": f"没有该编号的候选；当前有 {len(options)} 个候选。"}, {}
        picked = options[index - 1]
        if any(picked.get(key) != state.get("values", {}).get(key) for key in (protected or set())):
            return state, {"ok": False, "error": "该候选会改变你本轮明确要求固定的尺寸；请重新比选。"}, {}
        values = {**state["values"], "base_width_m": picked["base_width_m"], "top_width_m": picked["top_width_m"]}
        next_state = {**state, "values": values, "sources": {**state.get("sources", {}), "base_width_m": f"用户选择方案 {index}", "top_width_m": f"用户选择方案 {index}"}}
        next_state.pop("last_optimization", None)
        completed = calculate_confirmed(next_state, selected_standards)
        return completed, {"ok": True, "applied_option": index, "label": picked["label"], "result": _result_evidence(completed["result"])}, {"result": completed["result"]}

    if name == "get_current_result":
        result = state.get("result")
        return state, {"ok": bool(result), "result": _result_evidence(result) if result else None}, {}

    if name == "list_cases":
        return state, {"ok": True, "case_names": list(cases)}, {}

    if name == "load_case":
        title = arguments.get("title")
        if not isinstance(title, str):
            raise ValueError("请提供案例名称。")
        matches = [name for name in cases if name == title or (title and title in name)]
        if len(matches) != 1:
            return state, {"ok": False, "error": "案例名称不唯一或不存在。", "available_cases": list(cases)}, {}
        chosen = matches[0]
        values = dict(cases[chosen])
        next_state = {"values": values, "sources": {key: f"数据库案例：{chosen}" for key in values}, "trace": [],
                      "result": None, "wall_shape": "rectangle" if values.get("top_width_m") == values.get("base_width_m") else "trapezoid"}
        return next_state, {"ok": True, "loaded_case": chosen, "values": values}, {}

    if name == "lookup_standard":
        keyword = arguments.get("keyword", "11.2")
        if not isinstance(keyword, str):
            raise ValueError("检索词必须是文字。")
        return state, {"ok": True, "selected_standards": selected_standards, "entries": query_standard(keyword, selected_standards)}, {}

    raise ValueError("没有这个工具。")


def deepseek_tool_turn(text: str, state: dict, *, history: list[dict] | None = None,
                       selected_standards: list[str] | None = None, cases: dict[str, dict] | None = None) -> dict:
    """Let DeepSeek answer or call local tools, then return its own final wording."""
    selected_standards = selected_standards or []
    cases = cases or {}
    state = dict(state)
    context = {"current_parameters": state.get("values", {}), "has_calculated_result": bool(state.get("result")),
               "result_summary": {key: state["result"].get(key) for key in ("sliding_ratio", "overturning_ratio", "base_contact")}
               if state.get("result") else None,
               "last_options": (state.get("last_optimization") or {}).get("alternatives", []),
               "selected_standards": selected_standards}
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": "当前会话的已验证状态：" + json.dumps(context, ensure_ascii=False)}]
    messages.extend({"role": item["role"], "content": str(item.get("content", ""))[:700]}
                    for item in (history or [])[-8:] if item.get("role") in {"user", "assistant"})
    messages.append({"role": "user", "content": text})
    trace = []
    artifacts = {}
    protected = _protected_dimensions(text)
    for _ in range(6):
        message = _completion(messages)
        calls = message.get("tool_calls") or []
        if not calls:
            answer = message.get("content")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("DeepSeek 没有返回可显示的回答。")
            return {"state": state, "content": answer.strip(), "trace": trace, **artifacts}
        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须是对象。")
                state, tool_output, display = _execute(name, arguments, state, selected_standards, cases, protected)
                artifacts.update(display)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                arguments = function.get("arguments", "")
                tool_output = {"ok": False, "error": str(exc)}
            trace.append({"step": "DeepSeek 工具调用", "tool": name, "arguments": arguments, "output": tool_output})
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(tool_output, ensure_ascii=False)})
    message = _completion(messages, allow_tools=False)
    answer = message.get("content")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("DeepSeek 没有完成本轮回答，请重试。")
    return {"state": state, "content": answer.strip(), "trace": trace, **artifacts}
