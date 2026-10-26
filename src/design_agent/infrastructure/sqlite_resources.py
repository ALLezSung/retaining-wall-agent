"""Opaque document collections and raw legacy-table transport in SQLite."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from ...agent.serialization import dumps, loads
from ..errors import CapabilityError
from ..ports import ResourceRepository
from ..skills.protocol import validate_resource_proposals


class SQLiteResourceStore(ResourceRepository):
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connect(self, readonly=False):
        try:
            if not readonly:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            address = self.path.resolve().as_uri() + "?mode=ro" if readonly else str(self.path)
            with closing(sqlite3.connect(address, uri=readonly)) as db, db:
                yield db
        except (sqlite3.Error, OSError) as exc:
            raise CapabilityError("PERSISTENCE_FAILED", "资源数据库读写失败，请检查路径与文件权限。") from exc

    def snapshot(self, namespace, legacy_tables=()):
        if not self.path.exists():
            return {"documents": [], "legacy_tables": {}}
        with self._connect(readonly=True) as db:
            db.row_factory = sqlite3.Row
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            docs = []
            if "skill_documents" in tables:
                docs = [loads(r[0]) for r in db.execute("SELECT document FROM skill_documents WHERE namespace=? ORDER BY identifier", (namespace,))]
            # Infrastructure transports raw rows. It does not decode business columns.
            legacy = {table: [dict(row) for row in db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]
                      for table in tables if table in legacy_tables}
        return {"documents": docs, "legacy_tables": legacy}

    def commit(self, namespace, proposals):
        validate_resource_proposals(proposals)
        if not proposals:
            return
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS skill_documents(namespace TEXT NOT NULL,identifier TEXT NOT NULL,document TEXT NOT NULL,PRIMARY KEY(namespace,identifier))")
            for item in proposals:
                serialized = dumps(item["document"])
                old = db.execute("SELECT document FROM skill_documents WHERE namespace=? AND identifier=?", (namespace, item["id"])).fetchone()
                if old and old[0] != serialized:
                    raise CapabilityError("RESOURCE_CONFLICT", "资源标识已存在，未覆盖原记录。")
                db.execute("INSERT OR IGNORE INTO skill_documents VALUES(?,?,?)", (namespace, item["id"], serialized))
