"""Small SQLite JSON store for the first CrossBot integration milestone."""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TypeVar, Type
from core.models import AuditEvent, Case, CrossCheckResult, Document, ReviewTask, from_dict, to_dict
from core.errors import CrossBotError, ErrorCode
from core.schemas import WorkflowConversation
from core.validators import validate_case

T = TypeVar("T")

class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("SQLiteStore requires a file path")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def close(self):
        return None

    def _init_db(self):
        with closing(self._connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise CrossBotError(ErrorCode.CONFIGURATION, "数据库版本高于当前程序支持范围")
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
                CREATE TABLE IF NOT EXISTS workflow_conversations (
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    workflow TEXT NOT NULL CHECK(workflow IN ('sales', 'listing')),
                    payload TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY (case_id, workflow)
                );
            """)
            from .v3_store import initialize_v3_schema
            initialize_v3_schema(connection)
            connection.commit()

    def _save(self, table: str, entity_id: str, payload: object, case_id: str | None = None):
        columns = "id, payload" if case_id is None else "id, case_id, payload"
        placeholders = "?, ?" if case_id is None else "?, ?, ?"
        values = (entity_id, json.dumps(to_dict(payload), ensure_ascii=False)) if case_id is None else (entity_id, case_id, json.dumps(to_dict(payload), ensure_ascii=False))
        updates = "payload = excluded.payload" if case_id is None else "case_id = excluded.case_id, payload = excluded.payload"
        with closing(self._connect()) as connection:
            connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}",
                values,
            )
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

    def create_case(self, case: Case, actor_id: str = "local_user") -> None:
        """Create a Case and its audit record in one transaction."""
        validate_case(case)
        audit = AuditEvent(
            case_id=case.case_id, actor_id=actor_id, action="case_created",
            entity_type="case", entity_id=case.case_id,
            after={"case_number": case.case_number, "status": case.status.value},
        )
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM cases WHERE id = ? OR json_extract(payload, '$.case_number') = ?",
                (case.case_id, case.case_number),
            ).fetchone()
            if existing:
                raise CrossBotError(ErrorCode.CONFLICT, "Case 编号已存在")
            connection.execute("INSERT INTO cases VALUES (?, ?)", (case.case_id, json.dumps(to_dict(case), ensure_ascii=False)))
            connection.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?)",
                (audit.audit_id, case.case_id, json.dumps(to_dict(audit), ensure_ascii=False)),
            )

    def get_conversation(self, case_id: str, workflow: str) -> WorkflowConversation:
        self._validate_conversation_key(case_id, workflow)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload, revision FROM workflow_conversations WHERE case_id = ? AND workflow = ?",
                (case_id, workflow),
            ).fetchone()
        if row is None:
            return WorkflowConversation(case_id, workflow)
        return WorkflowConversation(case_id, workflow, json.loads(row["payload"]), row["revision"])

    def save_conversation(self, conversation: WorkflowConversation) -> None:
        self._validate_conversation_key(conversation.case_id, conversation.workflow)
        payload = json.dumps(to_dict(conversation.messages), ensure_ascii=False)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM workflow_conversations WHERE case_id = ? AND workflow = ?",
                (conversation.case_id, conversation.workflow),
            ).fetchone()
            revision = row["revision"] if row else 0
            if revision != conversation.revision:
                raise CrossBotError(ErrorCode.CONFLICT, "对话已在其他页面更新，请刷新后重试")
            connection.execute(
                "INSERT INTO workflow_conversations VALUES (?, ?, ?, ?) "
                "ON CONFLICT(case_id, workflow) DO UPDATE SET payload = excluded.payload, revision = excluded.revision",
                (conversation.case_id, conversation.workflow, payload, revision + 1),
            )
        conversation.revision = revision + 1

    def _validate_conversation_key(self, case_id: str, workflow: str) -> None:
        if workflow not in {"sales", "listing"}:
            raise CrossBotError(ErrorCode.INVALID_INPUT, "未知工作流")
        if self.get_case(case_id) is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "Case 不存在")
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
