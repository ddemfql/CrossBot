"""Synthetic, persistent W3 lifecycle from order entry through receivable close."""
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from case_workflow.service import CaseService, receivable
from core.models import DocumentType, TradeData, TradeLine, TradeMilestone, to_dict
from documents.service import DocumentService
from review.service import ReviewService
from review.policy import checks_digest, current_artifacts, has_current_approval
from storage.blobs import LocalBlobStore
from storage.identity import SQLiteIdentityStore
from storage.sqlite import SQLiteWorkspaceRepository
from validation.rules import DeterministicRules


class PersistentTradeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repository = SQLiteWorkspaceRepository(root / "workspace.db")
        self.identity = SQLiteIdentityStore(root / "workspace.db")
        self.identity.add_user("seller", "synthetic-password-1", ["sales"])
        self.identity.add_user("docs", "synthetic-password-2", ["sales"])
        self.identity.add_user("reviewer", "synthetic-password-3", ["sales"])
        self.identity.add_user("logistics", "synthetic-password-4", ["sales"])
        self.identity.add_user("finance", "synthetic-password-5", ["sales"])
        self.actors = {key: self.identity.authenticate(key, f"synthetic-password-{i}")
                       for i, key in enumerate(("seller", "docs", "reviewer", "logistics", "finance"), 1)}
        self.rules = DeterministicRules()
        self.cases = CaseService(self.repository, self.rules)
        self.documents = DocumentService(self.repository, LocalBlobStore(root / "files"))
        self.reviews = ReviewService(self.repository, self.rules)

    def tearDown(self):
        self.repository.close()
        self.identity.close()
        self.temp.cleanup()

    def test_order_draft_approval_shipment_and_receivable_close(self):
        trade = TradeData(order_no="SYN-E2E-1", seller="Synthetic Seller", buyer="Synthetic Buyer",
            destination_country="DE", currency="USD", incoterm="FCA Shanghai",
            payment_terms="Full payment before shipment", transport_mode="sea",
            product_category="synthetic goods", due_date=date.today().isoformat(),
            line_items=[TradeLine("SKU-1", "Synthetic lamp", "4", "12.50", "pcs", 2, "8", "6")])
        ws = self.cases.create(self.actors["seller"], trade)
        case_id = ws.case.case_id
        self.cases.confirm_trade(case_id, self.actors["seller"], "Synthetic PO and terms reviewed")
        for kind in (DocumentType.PROFORMA_INVOICE, DocumentType.COMMERCIAL_INVOICE,
                     DocumentType.PACKING_LIST):
            self.documents.generate(case_id, self.actors["docs"], kind)
        ws = self.cases.check(case_id, self.actors["seller"])
        self.assertTrue(ws.checks)
        self.assertEqual({check.status.value for check in ws.checks}, {"pass"},
                         [(check.rule_code, check.status.value, check.message) for check in ws.checks])
        ws = self.reviews.approve(case_id, self.actors["seller"], "Synthetic source and draft versions approved")
        self.assertEqual(ws.case.status.value, "passed")
        current_checks = self.rules.evaluate(ws)
        self.assertTrue(has_current_approval(ws, current_checks), {
            "approval": ws.approvals[-1].checks_sha256,
            "stored": checks_digest(ws.checks), "new": checks_digest(current_checks),
            "artifacts": [(a.status, a.artifact_id) for a in current_artifacts(ws)],
            "different": [(old.rule_code, {k: (to_dict(old)[k], to_dict(new)[k])
                           for k in to_dict(old) if k not in {"result_id", "checked_at"}
                           and to_dict(old)[k] != to_dict(new)[k]})
                           for old, new in zip(ws.checks, current_checks)
                           if any(to_dict(old)[k] != to_dict(new)[k] for k in to_dict(old)
                                  if k not in {"result_id", "checked_at"})],
        })
        approved_hashes = ws.approvals[-1].artifact_hashes.copy()
        ws = self.cases.set_milestone(case_id, self.actors["seller"], TradeMilestone.SHIPPED,
            "Synthetic pickup recorded", date.today().isoformat(), "SYN-BL-1")
        self.assertEqual(ws.approvals[-1].artifact_hashes, approved_hashes)
        self.assertEqual(ws.shipment_reference, "SYN-BL-1")
        self.cases.record_payment(case_id, self.actors["seller"], "25", "USD", "SYN-PAY-1",
                                  date.today().isoformat())
        ws = self.cases.record_payment(case_id, self.actors["seller"], "25", "USD", "SYN-PAY-2",
                                       date.today().isoformat())
        self.assertEqual(receivable(ws)["balance"], "0.00")
        self.assertEqual(receivable(ws)["status"], "paid")
        ws = self.cases.close(case_id, self.actors["seller"], "Synthetic order fully shipped and paid")
        self.assertEqual(ws.case.status.value, "closed")
        self.assertEqual(len(self.repository.history(case_id)), 11)
        self.assertEqual(len(ws.approvals), 1)


if __name__ == "__main__":
    unittest.main()

