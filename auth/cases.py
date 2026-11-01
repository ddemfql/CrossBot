"""Authorized Case/conversation access for UI and business modules."""
import json

from core.errors import CrossBotError, ErrorCode
from core.identity import Permission, Role
from core.models import AuditEvent, Case, CaseStatus, Document, ReviewTask, CrossCheckResult, from_dict
from core.validators import validate_case
from storage.v3_store import encode


class CaseAccessService:
    def __init__(self, auth):
        self.auth = auth
        self.store = auth.store

    def list_cases(self, token):
        with self.auth.repository.transaction() as conn:
            principal = self.auth._require(conn, token, Permission.CASE_READ)
            if principal.active_role == Role.ADMIN:
                rows = conn.execute("SELECT payload FROM cases ORDER BY rowid").fetchall()
            else:
                rows = conn.execute(
                    "SELECT c.payload FROM cases c JOIN case_assignments a ON c.id=a.case_id WHERE a.user_id=? ORDER BY c.rowid",
                    (principal.user_id,),
                ).fetchall()
            return [from_dict(Case, json.loads(row["payload"])) for row in rows]

    def get_case(self, token, case_id):
        self.auth.require_permission(token, Permission.CASE_READ, case_id)
        return self.store.get_case(case_id)

    def create_case(self, token, case):
        validate_case(case)
        with self.auth.repository.transaction() as conn:
            principal = self.auth._require(conn, token, Permission.CASE_CREATE)
            if conn.execute("SELECT id FROM cases WHERE id=? OR json_extract(payload, '$.case_number')=?", (case.case_id, case.case_number)).fetchone():
                raise CrossBotError(ErrorCode.CONFLICT, "Case 编号已存在")
            case.owner_id = principal.user_id
            conn.execute("INSERT INTO cases VALUES (?, ?)", (case.case_id, encode(case)))
            conn.execute("INSERT INTO case_assignments VALUES (?, ?)", (case.case_id, principal.user_id))
            self.auth.repository.audit(conn, "case_created", principal.user_id, case_id=case.case_id, entity_type="case", entity_id=case.case_id, after={"case_number": case.case_number})
        return case

    def get_conversation(self, token, case_id, workflow):
        self.auth.require_permission(token, Permission.W1_GENERATE if workflow == "sales" else Permission.W2_GENERATE, case_id)
        return self.store.get_conversation(case_id, workflow)

    def save_conversation(self, token, conversation):
        self.auth.require_permission(token, Permission.W1_GENERATE if conversation.workflow == "sales" else Permission.W2_GENERATE, conversation.case_id)
        self.store.save_conversation(conversation)

    def list_documents(self, token, case_id):
        self.auth.require_permission(token, Permission.ATTACHMENT_READ, case_id)
        return self.store.list_documents(case_id)

    def list_audit_events(self, token, case_id):
        self.auth.require_permission(token, Permission.CASE_READ, case_id)
        return self.store.list_audit_events(case_id)

    def transition(self, token, case_id, target, *, reason):
        from core.validators import transition_case

        target = CaseStatus(target)
        action = Permission.CASE_CLOSE if target == CaseStatus.CLOSED else Permission.CASE_REVIEW if target == CaseStatus.PASSED else Permission.CASE_UPDATE
        with self.auth.repository.transaction() as conn:
            principal = self.auth._require(conn, token, action, case_id)
            case = from_dict(Case, json.loads(conn.execute("SELECT payload FROM cases WHERE id=?", (case_id,)).fetchone()["payload"]))
            if case.metadata.get("w3_workspace") and target in {CaseStatus.PASSED, CaseStatus.CLOSED}:
                raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "W3 Case 必须通过单证工作流的当前版本核验与人工放行")
            def load(table, model):
                return [from_dict(model, json.loads(row["payload"])) for row in conn.execute(f"SELECT payload FROM {table} WHERE case_id=?", (case_id,))]
            case, audit = transition_case(
                case, target, documents=load("documents", Document),
                review_tasks=load("review_tasks", ReviewTask), checks=load("cross_checks", CrossCheckResult),
                actor_id=principal.user_id, reason=reason,
            )
            conn.execute("UPDATE cases SET payload=? WHERE id=?", (encode(case), case_id))
            conn.execute("INSERT INTO audit_events VALUES (?, ?, ?)", (audit.audit_id, case_id, encode(audit)))
            return case
