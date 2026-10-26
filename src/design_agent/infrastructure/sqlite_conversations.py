"""Atomic, versioned conversation snapshots; independent from teaching cases."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from ...agent.serialization import loads
from ..errors import ConversationStorageError, ConversationConflict
from ..ports import ConversationStore


class SQLiteConversationStore(ConversationStore):
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, payload_json TEXT NOT NULL)""")
                yield connection
        except (OSError, sqlite3.Error) as exc:
            raise ConversationStorageError("对话数据库读写失败，请检查存储路径和文件权限。") from exc

    def save(self, identifier: str, title: str, created_at: str, payload: dict) -> None:
        payload = dict(payload)
        expected = payload.pop("expected_revision", None)
        serialized = json.dumps({"version": 1, **payload}, ensure_ascii=False, allow_nan=False)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT payload_json FROM conversations WHERE id=?", (identifier,)).fetchone()
            if payload.get("version") == 2:
                current = loads(old[0]).get("storage_revision", "legacy") if old else None
                if current != expected:
                    raise ConversationConflict("对话已由另一实例更新或删除，未覆盖数据库记录。")
            db.execute("""INSERT INTO conversations VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                updated_at=excluded.updated_at, payload_json=excluded.payload_json""",
                (identifier, title, created_at, datetime.now(timezone.utc).isoformat(), serialized))

    def recent(self, limit: int | None = 5) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT id,title,created_at,updated_at FROM conversations ORDER BY updated_at DESC,id DESC LIMIT ?",
                              (-1 if limit is None else max(1, min(limit, 100)),)).fetchall()
        return [dict(zip(("id", "title", "created_at", "updated_at"), row)) for row in rows]

    def rename(self, identifier: str, title: str) -> None:
        # Rename metadata only: preserve the snapshot and its history ordering.
        with self._connect() as db:
            cursor = db.execute("UPDATE conversations SET title=? WHERE id=?", (title, identifier))
            if not cursor.rowcount:
                raise ValueError("该对话已删除或不存在。")

    def load(self, identifier: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT title,created_at,payload_json FROM conversations WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("该对话已删除或不存在。")
        try:
            payload = loads(row[2])
            if type(payload.get("version")) is not int or payload["version"] not in {1, 2}:
                raise ValueError("unknown version")
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("对话记录格式不兼容，无法载入。") from exc
        return {"id": identifier, "title": row[0], "created_at": row[1], "payload": payload}

    def delete(self, identifier: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (identifier,))
