"""Append-only workspace snapshots with SQLite optimistic concurrency."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from threading import RLock
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import Workspace, to_dict
from core.serialization import from_dict


class SQLiteWorkspaceRepository:
    def __init__(self, db_path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
        self._lock = RLock()
        self._db.execute("PRAGMA busy_timeout=15000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS w3_snapshots (
                case_id TEXT NOT NULL, revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL, PRIMARY KEY(case_id, revision));
            CREATE TRIGGER IF NOT EXISTS w3_snapshots_no_update
                BEFORE UPDATE ON w3_snapshots BEGIN
                SELECT RAISE(ABORT, 'workspace history is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS w3_snapshots_no_delete
                BEFORE DELETE ON w3_snapshots BEGIN
                SELECT RAISE(ABORT, 'workspace history is immutable'); END;
        """)

    def get(self, case_id: str) -> Workspace:
        with self._lock:
            row = self._db.execute("SELECT snapshot FROM w3_snapshots WHERE case_id=? "
                                   "ORDER BY revision DESC LIMIT 1", (case_id,)).fetchone()
        if row is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "交易不存在")
        return from_dict(Workspace, json.loads(row[0]))

    def list(self) -> list[Workspace]:
        with self._lock:
            rows = self._db.execute("""SELECT s.snapshot FROM w3_snapshots s
                JOIN (SELECT case_id, MAX(revision) revision FROM w3_snapshots GROUP BY case_id) latest
                ON s.case_id=latest.case_id AND s.revision=latest.revision ORDER BY s.case_id""").fetchall()
        return [from_dict(Workspace, json.loads(row[0])) for row in rows]

    def save(self, workspace: Workspace, expected_revision: int) -> None:
        if not workspace.case.case_id or expected_revision < 0 or workspace.revision != expected_revision:
            raise ValidationError("交易版本参数无效")
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                row = self._db.execute("SELECT MAX(revision) FROM w3_snapshots WHERE case_id=?",
                                       (workspace.case.case_id,)).fetchone()
                current = row[0] or 0
                if current != expected_revision:
                    raise CrossBotError(ErrorCode.CONFLICT, "资料已被其他人员更新，请刷新后重试")
                snapshot = to_dict(workspace)
                snapshot["revision"] = current + 1
                self._db.execute("INSERT INTO w3_snapshots VALUES (?, ?, ?)",
                    (workspace.case.case_id, current + 1,
                     json.dumps(snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True)))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        workspace.revision = current + 1

    def history(self, case_id: str) -> list[Workspace]:
        with self._lock:
            rows = self._db.execute("SELECT snapshot FROM w3_snapshots WHERE case_id=? ORDER BY revision",
                                    (case_id,)).fetchall()
        return [from_dict(Workspace, json.loads(row[0])) for row in rows]

    def close(self):
        with self._lock:
            self._db.close()
