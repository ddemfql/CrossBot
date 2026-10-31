"""Small SQLite JSON store for the first CrossBot integration milestone."""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TypeVar, Type
from core.models import AuditEvent, Case, CrossCheckResult, Document, ReviewTask, from_dict, to_dict
from core.errors import CrossBotError, ErrorCode

T = TypeVar("T")

class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def close(self):
        return None

    def _init_db(self):
        with closing(self._connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS review_tasks (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cross_checks (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id);
                CREATE INDEX IF NOT EXISTS idx_reviews_case ON review_tasks(case_id);
                CREATE INDEX IF NOT EXISTS idx_checks_case ON cross_checks(case_id);
                CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id);
            """)
            connection.commit()

    def _save(self, table: str, entity_id: str, payload: object, case_id: str | None = None):
        columns = "id, payload" if case_id is None else "id, case_id, payload"
        placeholders = "?, ?" if case_id is None else "?, ?, ?"
        values = (entity_id, json.dumps(to_dict(payload), ensure_ascii=False)) if case_id is None else (entity_id, case_id, json.dumps(to_dict(payload), ensure_ascii=False))
        with closing(self._connect()) as connection:
            connection.execute(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})", values)
            connection.commit()

    def _get(self, table: str, entity_id: str, factory: Type[T]) -> T | None:
        with closing(self._connect()) as connection:
            row = connection.execute(f"SELECT payload FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        return None if row is None else from_dict(factory, json.loads(row["payload"]))

    def _list(self, table: str, case_id: str, factory: Type[T]) -> list[T]:
        with closing(self._connect()) as connection:
            rows = connection.execute(f"SELECT payload FROM {table} WHERE case_id = ? ORDER BY rowid", (case_id,)).fetchall()
        return [from_dict(factory, json.loads(row["payload"])) for row in rows]

    def save_case(self, case: Case) -> None: self._save("cases", case.case_id, case)
    def get_case(self, case_id: str) -> Case | None: return self._get("cases", case_id, Case)
    def list_cases(self) -> list[Case]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT payload FROM cases ORDER BY rowid").fetchall()
        return [from_dict(Case, json.loads(row["payload"])) for row in rows]

    def save_document(self, document: Document) -> None:
        for version in document.versions:
            duplicate = self.find_document_by_sha256(document.case_id, version.sha256)
            if duplicate is not None and duplicate.document_id != document.document_id:
                raise CrossBotError(ErrorCode.CONFLICT, "同一 Case 已存在相同文件哈希", details={"sha256": version.sha256, "document_id": duplicate.document_id})
        self._save("documents", document.document_id, document, document.case_id)
    def get_document(self, document_id: str) -> Document | None: return self._get("documents", document_id, Document)
    def list_documents(self, case_id: str) -> list[Document]: return self._list("documents", case_id, Document)

    def find_document_by_sha256(self, case_id: str, sha256: str) -> Document | None:
        for document in self.list_documents(case_id):
            if any(version.sha256 == sha256 for version in document.versions):
                return document
        return None

    def save_review_task(self, task: ReviewTask) -> None: self._save("review_tasks", task.task_id, task, task.case_id)
    def list_review_tasks(self, case_id: str) -> list[ReviewTask]: return self._list("review_tasks", case_id, ReviewTask)

    def save_cross_check(self, result: CrossCheckResult) -> None: self._save("cross_checks", result.result_id, result, result.case_id)
    def list_cross_checks(self, case_id: str) -> list[CrossCheckResult]: return self._list("cross_checks", case_id, CrossCheckResult)

    def append_audit(self, event: AuditEvent) -> None: self._save("audit_events", event.audit_id, event, event.case_id)
    def list_audit_events(self, case_id: str) -> list[AuditEvent]: return self._list("audit_events", case_id, AuditEvent)
