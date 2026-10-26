"""Portable standard-library-only example. No host imports."""
from dataclasses import dataclass, field, asdict
import json
import sys


@dataclass
class Book:
    title: str
    read: bool = False

    def __post_init__(self):
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 200 or type(self.read) is not bool:
            raise ValueError("书名须为 1–200 字，已读标记须为布尔值。")
        self.title = self.title.strip()


@dataclass
class ReadingList:
    books: list[Book] = field(default_factory=list)

    @classmethod
    def restore(cls, payload):
        if set(payload) - {"books"} or not isinstance(payload.get("books", []), list):
            raise ValueError("阅读清单状态格式无效。")
        state = cls([Book(**b) for b in payload.get("books", [])])
        if len({b.title for b in state.books}) != len(state.books):
            raise ValueError("书名重复。")
        return state


def handle(r):
    out = {"protocol_version": 1, "request_id": r["request_id"], "status": "ok", "model_result": {}, "state_proposal": None, "artifacts": [], "resource_proposals": [], "error": None}
    try:
        snap, action, args = r["snapshot"], r["action_id"], r["input"]
        if r["protocol_version"] != 1 or not isinstance(args, dict):
            raise ValueError("请求协议无效。")
        if snap["data_version"] != 1:
            raise ValueError("不支持的阅读清单数据版本。")
        state = ReadingList.restore(snap["payload"])
        if action == "project":
            out["model_result"] = {"context": {"books": [asdict(b) for b in state.books]}, "exports": {}, "view": {"title": "阅读清单", "status": f"共 {len(state.books)} 本书", "panels": []}}
            return out
        if action == "add":
            book = Book(**args)
            if not any(b.title == book.title for b in state.books):
                state.books.append(book)
        elif action == "mark_read":
            index = args.get("index")
            if set(args) != {"index"} or type(index) is not int or not 1 <= index <= len(state.books):
                raise ValueError("没有该编号的书目。")
            state.books[index - 1].read = True
        elif action == "reset":
            state = ReadingList()
        elif action not in {"initialize", "restore", "show"}:
            raise ValueError("未知操作。")
        out["model_result"] = {"count": len(state.books), "read_count": sum(b.read for b in state.books)}
        refs = list(snap["artifact_refs"])
        if action in {"add", "mark_read", "show"}:
            out["artifacts"] = [{"key": "books", "kind": "table", "title": "阅读清单", "summary": f"共 {len(state.books)} 本书", "rows": [{"序号": i, "书名": b.title, "状态": "已读" if b.read else "未读"} for i, b in enumerate(state.books, 1)]}]
            refs = ["books"]
        if action == "reset":
            refs = []
        if asdict(state) != snap["payload"] or out["artifacts"] or action in {"initialize", "reset"}:
            out["state_proposal"] = {"session_id": snap["session_id"], "owner": snap["owner"], "base_revision": snap["revision"], "data_version": 1, "payload": asdict(state), "artifact_refs": refs}
    except (KeyError, ValueError, TypeError) as exc:
        out.update(status="business_error", model_result={}, state_proposal=None, artifacts=[], error={"code": "INVALID_INPUT", "message": str(exc)})
    return out


if __name__ == "__main__":
    r = json.load(sys.stdin)
    if sys.argv[1:] != ["--action", r["action_id"]]:
        raise SystemExit("action mismatch")
    print(json.dumps(handle(r), ensure_ascii=False, allow_nan=False))
