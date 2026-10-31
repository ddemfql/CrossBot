"""W3 snapshots and shared Case projections in one SQLite transaction."""
from copy import deepcopy
import json
import sqlite3

from auth.cases import CaseAccessService
from core.errors import CrossBotError, ErrorCode
from core.identity import Permission
from core.models import Case, TradeData, Workspace, from_dict
from review.policy import require
from .sqlite import SQLiteWorkspaceRepository
from .v3_store import encode


class IntegratedWorkspaceRepository(SQLiteWorkspaceRepository):
    def __init__(self, auth, token, case_id):
        self.principal = auth.require_permission(token, Permission.CASE_READ, case_id)
        self.auth, self.token, self.case_id = auth, token, case_id
        super().__init__(auth.store.path)
        self._db.row_factory = sqlite3.Row

    def _authorize(self, case_id):
        if case_id != self.case_id:
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "只能访问当前选中的 Case")
        return self.auth.require_permission(self.token, Permission.CASE_READ, case_id)

    def get(self, case_id):
        self._authorize(case_id)
        return super().get(case_id)

    def list(self):
        self._authorize(self.case_id)
        try:
            return [self.get(self.case_id)]
        except CrossBotError as error:
            if error.code != ErrorCode.NOT_FOUND:
                raise
            return []

    def history(self, case_id):
        self._authorize(case_id)
        return super().history(case_id)

    def save(self, workspace, expected_revision):
        self._authorize(workspace.case.case_id)
        super().save(workspace, expected_revision)

    def initialize(self, actor):
        case = CaseAccessService(self.auth).get_case(self.token, self.case_id)
        require(None, actor, "update_trade")
        workspace = Workspace(case=deepcopy(case), trade=TradeData(
            order_no=case.order_no or "", buyer=case.customer_name,
            destination_country=case.destination_country, currency=case.currency or "",
            incoterm=case.incoterm or ""),
            required_document_types=list(case.required_document_types),
            base_required_document_types=list(case.required_document_types),
            documents=self.auth.store.list_documents(case.case_id))
        workspace.case.metadata["w3_workspace"] = True
        self.save(workspace, expected_revision=0)
        return workspace

    def _project(self, connection, workspace):
        principal = self.auth._require(connection, self.token, Permission.CASE_READ, self.case_id)
        if (principal.user_id != self.principal.user_id
                or principal.active_role != self.principal.active_role):
            raise CrossBotError(ErrorCode.CONFLICT, "岗位已变化，请刷新后重新操作")
        row = connection.execute("SELECT payload FROM cases WHERE id=?", (self.case_id,)).fetchone()
        current = from_dict(Case, json.loads(row["payload"]))
        previous = connection.execute(
            "SELECT snapshot FROM w3_snapshots WHERE case_id=? AND revision=?",
            (self.case_id, workspace.revision)).fetchone()
        old = json.loads(previous["snapshot"]) if previous else {}
        workspace.case.metadata = {**current.metadata, **workspace.case.metadata, "w3_workspace": True}
        workspace.case.owner_id = current.owner_id
        workspace.case.required_document_types = list(workspace.required_document_types)
        # Retain references owned by other workflows while replacing W3 references.
        for key, collection, id_key in (("document_ids", "documents", "document_id"),
                ("review_task_ids", "review_tasks", "task_id"),
                ("cross_check_result_ids", "checks", "result_id")):
            old_ids = {item[id_key] for item in old.get(collection, [])}
            ids = [item for item in getattr(current, key) if item not in old_ids]
            ids.extend(getattr(item, id_key) for item in getattr(workspace, collection))
            setattr(workspace.case, key, list(dict.fromkeys(ids)))
        connection.execute("UPDATE cases SET payload=? WHERE id=?", (encode(workspace.case), self.case_id))
        for table, collection, id_key in (("documents", "documents", "document_id"),
                ("review_tasks", "review_tasks", "task_id"), ("cross_checks", "checks", "result_id")):
            values = getattr(workspace, collection)
            for item in values:
                if table == "documents":
                    hashes = {version.sha256 for version in item.versions}
                    others = connection.execute(
                        "SELECT payload FROM documents WHERE case_id=? AND id<>?",
                        (self.case_id, item.document_id)).fetchall()
                    if any(hashes.intersection(version["sha256"] for version in json.loads(other["payload"])["versions"])
                            for other in others):
                        raise CrossBotError(ErrorCode.CONFLICT, "该 Case 已有相同原文件，请使用已有文档或其修订版本")
                connection.execute(
                    f"INSERT INTO {table}(id,case_id,payload) VALUES (?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload WHERE case_id=excluded.case_id",
                    (getattr(item, id_key), self.case_id, encode(item)))
            current_ids = {getattr(item, id_key) for item in values}
            for item in old.get(collection, []):
                if table != "documents" and item[id_key] not in current_ids:
                    connection.execute(f"DELETE FROM {table} WHERE id=? AND case_id=?", (item[id_key], self.case_id))
        for event in workspace.audit:
            connection.execute("INSERT OR IGNORE INTO audit_events VALUES (?,?,?)",
                (event.audit_id, self.case_id, encode(event)))
