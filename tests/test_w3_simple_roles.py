"""Two-role authorization, legacy identity compatibility and simple drafts."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from case_workflow.importer import ArchiveImporter
from case_workflow.service import CaseService
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import Actor, DocumentType, RequirementPolicy, TradeData, TradeLine
from review.policy import PERMISSIONS, ROLES, require
from storage.blobs import LocalBlobStore
from storage.identity import SQLiteIdentityStore
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor, trade


class SimpleRoleTests(unittest.TestCase):
    def test_business_user_can_perform_every_business_operation(self):
        self.assertEqual(ROLES, {"admin", "sales"})
        for action in PERMISSIONS:
            if action == "template_manage":
                with self.assertRaises(CrossBotError):
                    require(None, actor("business-user", "sales"), action)
                require(None, actor("administrator", "admin"), action)
                continue
            with self.subTest(action=action):
                require(None, actor("business-user", "sales"), action)
        for denied in (Actor("anonymous", ["sales"]),
                       Actor("model", ["sales"], authenticated=True, kind="ai"),
                       actor("old-role", "reviewer"), actor("unknown", "outsider")):
            with self.subTest(denied=denied), self.assertRaises(CrossBotError) as raised:
                require(None, denied, "generate")
            self.assertEqual(raised.exception.code, ErrorCode.FORBIDDEN)

    def test_legacy_accounts_become_business_users_without_changing_credentials(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "identity.db"
            store = SQLiteIdentityStore(path)
            self.addCleanup(store.close)
            for role in ("documents", "reviewer", "logistics", "finance"):
                store.add_user(role, "synthetic-password", ["sales"])
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE w3_users SET roles=? WHERE user_id=?",
                               (json.dumps([role]), role))
                self.assertEqual(store.authenticate(role, "synthetic-password").roles, ["sales"])
                with self.assertRaises(ValidationError):
                    store.add_user("new-" + role, "synthetic-password", [role])
            self.assertTrue(all(user["roles"] == ["sales"] for user in store.list_users()))
            with sqlite3.connect(path) as db:
                self.assertEqual(json.loads(db.execute(
                    "SELECT roles FROM w3_users WHERE user_id='finance'").fetchone()[0]), ["finance"])

    def test_business_user_can_import_archive_without_approval(self):
        with TemporaryDirectory() as root:
            payload = BytesIO()
            with ZipFile(payload, "w") as archive:
                archive.writestr("invoice.txt", "Synthetic original")
            workspace = ArchiveImporter(MemoryRepository(), LocalBlobStore(Path(root))).import_zip(
                payload.getvalue(), "synthetic.zip", actor("business", "sales"))
            self.assertEqual(workspace.case.owner_id, "business")
            self.assertEqual(len(workspace.documents), 1)
            self.assertFalse(workspace.approvals)
            self.assertTrue(all(task.role == "sales" for task in workspace.tasks))

    def test_incomplete_drafts_remain_editable_and_strict_confirmation_stays_explicit(self):
        repository = MemoryRepository()
        service = CaseService(repository, PendingRules())
        business = actor("business", "sales")
        workspace = service.create(business, TradeData())
        self.assertEqual(workspace.trade.line_items, [])
        incomplete = deepcopy(workspace.trade)
        incomplete.line_items = [TradeLine("", "", "", "", gross_weight_kg="", net_weight_kg="")]
        workspace = service.update_trade(workspace.case.case_id, business, incomplete, "补充中")
        self.assertIsNone(workspace.trade.confirmed_by)
        with self.assertRaises(ValidationError):
            service.confirm_trade(workspace.case.case_id, business, "Incomplete")
        incomplete.line_items[0].quantity = "NaN"
        with self.assertRaises(ValidationError):
            service.update_trade(workspace.case.case_id, business, incomplete, "Bad number")

    def test_checklist_changes_preserve_policy_requirements_and_audit(self):
        service = CaseService(MemoryRepository(), PendingRules())
        business = actor("business", "sales")
        policy = RequirementPolicy("de", [DocumentType.SALES_CONTRACT], destination_country="DE")
        workspace = service.create(business, trade(), policies=[policy])
        workspace = service.set_required_documents(workspace.case.case_id, business,
                                                   [DocumentType.PACKING_LIST])
        self.assertEqual(workspace.base_required_document_types, [DocumentType.PACKING_LIST])
        self.assertEqual(workspace.required_document_types,
                         [DocumentType.PACKING_LIST, DocumentType.SALES_CONTRACT])
        self.assertEqual(workspace.audit[-1].action, "required_documents_updated")
        self.assertFalse(workspace.approvals)
        with self.assertRaises(ValidationError):
            service.set_required_documents(workspace.case.case_id, business, ["invalid"])


if __name__ == "__main__":
    unittest.main()
