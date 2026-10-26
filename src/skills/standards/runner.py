"""Stateless query via StandardContextV1; no sibling skill imports."""
import json
import sys


def handle(r):
    out = {"protocol_version": 1, "request_id": r["request_id"], "status": "ok", "model_result": {}, "state_proposal": None, "artifacts": [], "resource_proposals": [], "error": None}
    try:
        c = r["shared"]["standards"]
        if c["version"] != 1 or r["action_id"] != "lookup_standard":
            raise ValueError("规范共享协议或操作不支持。")
        args = r["input"]
        if set(args) - {"keyword"} or not isinstance(args.get("keyword", "11.2"), str):
            raise ValueError("关键词必须是字符串。")
        words = args.get("keyword", "11.2").strip().lower()
        entries = []
        if "GB 50330-2013" in c["selected"]:
            entries = [e for e in c["entries"] if not words or words in (e["clause"] + e["summary"]).lower()] or list(c["entries"])
        for code in c["selected"]:
            if code != "GB 50330-2013":
                item = c["catalog"][code]
                entries.append({"clause": f"{code} {item['name']}", "summary": item["role"] + "；" + item["coverage"], "url": item["url"]})
        out["model_result"] = {"selected_standards": c["selected"], "entries": entries, "requirements": c["requirements"]}
    except (KeyError, ValueError, TypeError) as exc:
        out.update(status="business_error", error={"code": "INVALID_INPUT", "message": str(exc)})
    return out


if __name__ == "__main__":
    r = json.load(sys.stdin)
    if sys.argv[1:] != ["--action", r["action_id"]]:
        raise SystemExit("action mismatch")
    print(json.dumps(handle(r), ensure_ascii=False, allow_nan=False))
