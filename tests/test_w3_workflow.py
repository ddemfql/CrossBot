import unittest
from copy import deepcopy

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (Actor, CaseStatus, CrossCheckResult, CrossCheckStatus,
                         DocumentType, RequirementPolicy, TradeData, TradeLine,
                         TradeMilestone, Workspace)
from case_workflow.service import CaseService, receivable
from review.service import ReviewService


def actor(user, *roles):
    return Actor(user, list(roles), authenticated=True)


def trade():
    return TradeData(order_no="SYN-100", seller="Example Seller", buyer="Example Buyer",
                     destination_country="DE", currency="USD", incoterm="FCA Shanghai",
                     payment_terms="50% deposit, balance before shipment",
                     transport_mode="sea", product_category="synthetic goods",
                     line_items=[TradeLine("SYN-1", "Synthetic item", "2", "10.25", packages=1,
                                           gross_weight_kg="1.2", net_weight_kg="1.0")])


class MemoryRepository:
    def __init__(self):
        self.items = {}
        self.denied_saves = 0

    def get(self, case_id):
        return deepcopy(self.items[case_id])

    def list(self):
        return [deepcopy(item) for item in self.items.values()]

    def save(self, workspace, expected_revision):
        old = self.items.get(workspace.case.case_id)
        actual = old.revision if old else 0
        if expected_revision != actual:
            raise CrossBotError(ErrorCode.CONFLICT, "stale")
        workspace.revision = actual + 1
        self.items[workspace.case.case_id] = deepcopy(workspace)


class PendingRules:
    def evaluate(self, workspace):
        return [CrossCheckResult(case_id=workspace.case.case_id, rule_code="NEEDS_DOCUMENTS",
                                 status=CrossCheckStatus.PENDING_REVIEW,
                                 message="Synthetic test requires documents")]


class PassingRules:
    def evaluate(self, workspace):
        return [CrossCheckResult(case_id=workspace.case.case_id, rule_code="SYNTHETIC_PASS",
                                 status=CrossCheckStatus.PASS, message="Synthetic data passes")]


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.service = CaseService(self.repo, PendingRules())
        self.sales = actor("sales-1", "sales")
        self.reviewer = actor("reviewer-1", "sales")
        self.finance = actor("finance-1", "sales")

    def create(self):
        return self.service.create(self.sales, trade(), required_types=[])

    def test_trade_confirmation_and_update_keep_auditable_revisions(self):
        ws = self.create()
        self.assertEqual(ws.trade.revision, 1)
        ws = self.service.confirm_trade(ws.case.case_id, self.sales, "PO checked against terms")
        self.assertEqual(ws.trade.confirmed_by, "sales-1")
        self.assertEqual(ws.case.status, CaseStatus.PENDING_REVIEW)
        amended = deepcopy(ws.trade)
        amended.line_items[0].quantity = "3"
        ws = self.service.update_trade(ws.case.case_id, self.sales, amended, "Buyer revised quantity")
        self.assertEqual(ws.trade.revision, 2)
        self.assertIsNone(ws.trade.confirmed_by)
        changes = [event for event in ws.audit if event.action == "trade_updated"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].before["line_items"][0]["quantity"], "2")
        self.assertEqual(changes[0].after["line_items"][0]["quantity"], "3")

    def test_base_requirements_survive_trade_policy_recalculation(self):
        policy = RequirementPolicy("de-only", [DocumentType.SALES_CONTRACT],
                                   destination_country="DE")
        ws = self.service.create(self.sales, trade(),
                                 required_types=[DocumentType.SALES_CONTRACT], policies=[policy])
        self.assertEqual(ws.required_document_types, [DocumentType.SALES_CONTRACT])
        amended = deepcopy(ws.trade)
        amended.destination_country = "US"
        ws = self.service.update_trade(ws.case.case_id, self.sales, amended, "Destination updated")
        self.assertEqual(ws.required_document_types, [DocumentType.SALES_CONTRACT])

    def test_unauthorized_action_is_saved_to_audit_before_rejection(self):
        ws = self.create()
        outsider = actor("warehouse", "unknown")
        with self.assertRaises(CrossBotError) as raised:
            self.service.update_trade(ws.case.case_id, outsider, trade(), "try")
        self.assertEqual(raised.exception.code, ErrorCode.FORBIDDEN)
        saved = self.repo.get(ws.case.case_id)
        self.assertEqual(saved.audit[-1].action, "access_denied")
        self.assertEqual(saved.audit[-1].after["action"], "update_trade")

    def test_missing_reason_and_malformed_trade_are_rejected(self):
        ws = self.create()
        with self.assertRaises(ValidationError):
            self.service.confirm_trade(ws.case.case_id, self.sales, " ")
        malformed = deepcopy(ws.trade)
        malformed.line_items[0].quantity = "NaN"
        with self.assertRaises(ValidationError):
            self.service.update_trade(ws.case.case_id, self.sales, malformed, "Bad quantity")

    def test_rule_check_cannot_auto_approve(self):
        ws = self.create()
        ws = self.service.confirm_trade(ws.case.case_id, self.sales, "Order checked")
        ws = self.service.check(ws.case.case_id, self.reviewer)
        self.assertEqual(ws.case.status, CaseStatus.PENDING_REVIEW)
        self.assertEqual(ws.checks[0].status, CrossCheckStatus.PENDING_REVIEW)
        review = ReviewService(self.repo, PendingRules())
        with self.assertRaises(CrossBotError) as raised:
            review.approve(ws.case.case_id, self.reviewer, "Try blocked approval")
        self.assertEqual(raised.exception.code, ErrorCode.CROSS_CHECK_FAILED)
        self.assertNotEqual(self.repo.get(ws.case.case_id).case.status, CaseStatus.PASSED)

    def test_partial_receivables_and_duplicate_references(self):
        ws = self.create()
        self.service.confirm_trade(ws.case.case_id, self.sales, "Order checked")
        ws = self.service.record_payment(ws.case.case_id, self.finance, "5.00", "USD",
                                         "BANK-REF-1", "2026-10-30")
        summary = receivable(ws)
        self.assertEqual(summary["total"], "20.50")
        self.assertEqual(summary["paid"], "5.00")
        self.assertEqual(summary["balance"], "15.50")
        self.assertEqual(summary["status"], "partial")
        with self.assertRaises(CrossBotError):
            self.service.record_payment(ws.case.case_id, self.finance, "1", "USD",
                                        "BANK-REF-1", "2026-10-30")
        with self.assertRaises(ValidationError):
            self.service.record_payment(ws.case.case_id, self.finance, "99", "USD",
                                        "BANK-REF-2", "2026-10-30")

    def test_ship_requires_approval_and_current_artifact_snapshot(self):
        ws = self.create()
        with self.assertRaises(CrossBotError) as raised:
            self.service.set_milestone(ws.case.case_id, actor("log-1", "sales"),
                                       TradeMilestone.SHIPPED, "Carrier pickup", reference="BL-1")
        self.assertEqual(raised.exception.code, ErrorCode.REVIEW_REQUIRED)

    def test_milestone_cannot_be_set_by_unknown_role(self):
        ws = self.create()
        with self.assertRaises(CrossBotError) as raised:
            self.service.set_milestone(ws.case.case_id, actor("outsider", "unknown"), TradeMilestone.PREPARING,
                                       "not authorized")
        self.assertEqual(raised.exception.code, ErrorCode.FORBIDDEN)


if __name__ == "__main__":
    unittest.main()

