"""Small SQLite JSON store for the first CrossBot integration milestone."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TypeVar, Type
from contextlib import closing

from core.models import AuditEvent, Case, CrossCheckResult, Document, ReviewTask, from_dict, to_dict

T = TypeVar("T")

class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._init_db()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def close(self):
        """Compatibility hook; connections are opened per operation."""
        return None

    def _init_db(self):
        with closing(self._connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS review_tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cross_checks (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    def _save(self, table: str, entity_id: str, payload: object, *, case_id: str | None = None):
        columns = "id, payload" if case_id is None else "id, case_id, payload"
        placeholders = "?, ?" if case_id is None else "?, ?, ?"
        values = (entity_id, json.dumps(to_dict(payload), ensure_ascii=False)) if case_id is None else (entity_id, case_id, json.dumps(to_dict(payload), ensure_ascii=False))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            connection.execute(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})", values)
            connection.commit()

    def _get(self, table: str, entity_id: str, factory: Type[T]) -> T | None:
        with closing(self._connect()) as connection:
            row = connection.execute(f"SELECT payload FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            return None
        return from_dict(factory, json.loads(row["payload"]))

    def save_case(self, case: Case) -> None:
        self._save("cases", case.case_id, case)

    def get_case(self, case_id: str) -> Case | None:
        return self._get("cases", case_id, Case)

    def save_document(self, document: Document) -> None:
        self._save("documents", document.document_id, document)

    def get_document(self, document_id: str) -> Document | None:
        return self._get("documents", document_id, Document)

    def save_review_task(self, task: ReviewTask) -> None:
        self._save("review_tasks", task.task_id, task)

    def save_cross_check(self, result: CrossCheckResult) -> None:
        self._save("cross_checks", result.result_id, result)

    def append_audit(self, event: AuditEvent) -> None:
        self._save("audit_events", event.audit_id, event, case_id=event.case_id)
