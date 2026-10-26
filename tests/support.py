"""Business assertions for the migrated public protocol, never runtime adapters.

Old behavioral tests keep their numeric expectations; these helpers construct
the new wire calls and inspect business-owned evidence through public APIs.
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from src.llm.protocol import ToolCall

# Tests may import the standalone business package; production hosts never do.
WALL_SCRIPTS = Path(__file__).resolve().parents[1] / "src/skills/retaining-wall/scripts"
sys.path.insert(0, str(WALL_SCRIPTS))

WALL_ACTIONS = {"set_parameters", "validate_parameters", "calculate_design", "compare_designs", "apply_option", "get_current_result", "list_cases", "load_case", "load_saved", "save_current", "select_standards"}


def wire_action(name, args):
    if name in WALL_ACTIONS or name == "lookup_standard":
        return "run_skill_action", {"skill_id": "standards" if name == "lookup_standard" else "retaining_wall", "action_id": name, "input": args}
    return name, args


def skill_call(identifier, name, arguments="{}"):
    if name not in WALL_ACTIONS and name != "lookup_standard":
        return ToolCall(identifier, name, arguments if isinstance(arguments, str) else json.dumps(arguments))
    name, args = wire_action(name, json.loads(arguments) if isinstance(arguments, str) else arguments)
    return ToolCall(identifier, name, json.dumps(args, ensure_ascii=False))


class BusinessRegistry:
    def __init__(self, registry):
        self.registry = registry

    def invoke(self, name, args):
        name, args = wire_action(name, args)
        return self.registry.invoke(name, args)


class BusinessAssertions:
    def __init__(self, app):
        self.app = app

    def read_session(self):
        return SimpleNamespace(**self.app.state_snapshot("wall_design")["payload"])

    def action(self, action, **args):
        out = self.app.execute_action("retaining_wall", action, args)
        if not out["ok"]:
            raise ValueError(out["error"]["message"])
        return out["data"]

    def load_teaching_case(self, title):
        return self.action("load_case", title=title)

    def load_saved_case(self, case_id):
        return self.action("load_saved", case_id=case_id)

    def save_case(self, title):
        return self.action("save_current", title=title)["case_id"]

    def select_standards(self, selected):
        return self.action("select_standards", selected=selected)

    def saved_cases(self):
        docs = self.app.capabilities.resources.snapshot("wall_cases")["documents"]
        return [d for d in docs if d["kind"] == "case"]

    def current_artifacts(self):
        return business_artifacts(self.app, self.app.current_artifacts())


def business(app):
    return BusinessAssertions(app)


def business_artifacts(app, artifacts):
    output = {}
    for artifact in artifacts.get("items", []):
        if artifact["kind"] == "report":
            files = {a["filename"]: app.artifact_bytes(a).decode("utf-8") for a in artifact["attachments"]}
            output["result"] = json.loads(files["wall_result.json"])
            output["report"] = {"json": files["wall_result.json"], "markdown": files["wall_report.md"],
                                "sections": {s["title"]: s["content"] for s in artifact.get("sections", [])}}
        elif artifact["title"] == "方案比选":
            output["optimization"] = artifact
    return output
