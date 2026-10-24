"""Demo standard lookup registration, separate from the generic loop."""
from typing import Callable

from ...agent.tools import Tool, success
from .index import query_standard


def tools(selected_standards: Callable[[], list[str]]) -> list[Tool]:
    def lookup_standard(keyword: str = "11.2") -> dict:
        selected = selected_standards()
        return success({"selected_standards": selected, "entries": query_standard(keyword, selected)})

    return [Tool("lookup_standard", "查询所选标准的本地摘要及覆盖说明，不是全文检索或完整规范校核。", {
        "type": "object", "properties": {"keyword": {"type": "string"}},
        "additionalProperties": False}, lookup_standard)]
