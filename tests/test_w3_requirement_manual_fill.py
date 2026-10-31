"""Direct data entry shares correction mechanics without requiring a failed rule."""
import unittest
from copy import deepcopy

from core.errors import CrossBotError, ValidationError
from core.models import DocumentType as D, ReviewEditArea as A, ReviewEditTarget, TradeData, TradeLine, to_dict
from case_workflow.service import CaseService
from documents.forms import FormService, get_form, effective_values, effective_rows
from documents.provenance import form_value_origins
from documents.requirement_inputs import RequirementInputService
from review.editing import ReviewEditingService
from validation.export_review import review_digest, review_export
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor


class NoBlobAccess:
    def put(self, *args):
        raise AssertionError("Manual entry must not manufacture a file")
    def get(self, *args):
        raise AssertionError("Manual entry does not need source bytes")


class RequirementManualFillTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-manual-data", "sales")
        self.cases = CaseService(self.repo, PendingRules())
        self.ws = self.cases.create(self.actor, TradeData(currency=""))
        self.service = RequirementInputService(self.repo, NoBlobAccess())

    def fill(self, field, value=None, *, rows=None, row=None, kind=D.COMMERCIAL_INVOICE):
        self.ws = self.service.fill(self.ws.case.case_id, self.actor, kind, field, row,
            value=value, rows=rows, reason="Human supplies this exact missing data item",
            expected_revision=self.ws.revision, expected_review_digest=review_digest(self.ws, kind))
        return self.ws

    def test_header_entry_without_upload_has_manual_origin_and_no_other_effects(self):
        before_trade = deepcopy(self.ws.trade)
        before = to_dict(self.ws)
        target = self.service.manual_target(self.ws, D.COMMERCIAL_INVOICE, "buyer")
        self.assertEqual(target, ReviewEditTarget(A.FORM, D.COMMERCIAL_INVOICE, "buyer"))
        self.assertEqual(to_dict(self.ws), before)
        self.fill("buyer", "Synthetic Buyer")
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values, {"buyer": "Synthetic Buyer"})
        self.assertEqual(self.ws.trade, before_trade)
        origin = next(item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE) if item.field_name == "buyer")
        self.assertEqual((origin.origin_kind, origin.actor_id, origin.document_id), ("manual", self.actor.user_id, ""))
        self.assertFalse(self.ws.documents)
        self.assertFalse(self.ws.source_requirement_links)
        self.assertFalse(self.ws.approvals)
        self.assertFalse(self.ws.artifacts)

    def test_whole_rows_then_one_cell_preserves_other_rows_and_fields(self):
        rows = [{"product_code": "A", "description": "Item A", "quantity": "2", "unit": "pcs", "unit_price": "3"},
                {"product_code": "B", "description": "Item B", "quantity": "5", "unit": "pcs", "unit_price": "7"}]
        self.fill("rows", rows=rows)
        self.fill("quantity", "4", row=1)
        actual = get_form(self.ws, D.COMMERCIAL_INVOICE).rows
        self.assertEqual(actual[0], dict(rows[0], quantity="4"))
        self.assertEqual(actual[1], rows[1])
        self.assertFalse(self.ws.documents)

    def test_active_blank_optional_is_writable_without_loosening_strict_review_editor(self):
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.actor,
            D.COMMERCIAL_INVOICE, ["notes"], [], "Enable optional note", context_fields=[])
        report = review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True)
        target = self.service.manual_target(self.ws, D.COMMERCIAL_INVOICE, "notes")
        self.assertFalse(any(target in check.edit_targets for check in report.checks if check.status.value != "pass"))
        with self.assertRaises(ValidationError):
            ReviewEditingService(self.repo, self.cases).save(self.ws.case.case_id, self.actor,
                D.COMMERCIAL_INVOICE, target, value="Not an audit failure", reason="Strict edit target boundary",
                expected_revision=self.ws.revision, expected_review_digest=report.input_digest)
        self.fill("notes", "User supplied optional delivery note")
        self.assertEqual(get_form(self.ws, D.COMMERCIAL_INVOICE).values["notes"], "User supplied optional delivery note")

    def test_hidden_required_header_and_row_input_remain_hidden_in_output(self):
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.actor,
            D.COMMERCIAL_INVOICE, [], [], "Require private account input", context_fields=[],
            required_input_fields=["bank_account"])
        self.fill("bank_account", "SYNTHETIC-ACCOUNT")
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["bank_account"], "SYNTHETIC-ACCOUNT")
        self.assertNotIn("bank_account", effective_values(self.ws.trade, form))
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.actor,
            D.PRODUCT_SPEC, [], ["parameter", "value"], "Private technical basis", context_fields=[],
            required_input_row_fields=["basis"])
        self.fill("rows", rows=[{"parameter": "Width", "value": "12", "basis": ""}], kind=D.PRODUCT_SPEC)
        self.fill("basis", "Synthetic customer specification page 2", row=1, kind=D.PRODUCT_SPEC)
        form = get_form(self.ws, D.PRODUCT_SPEC)
        self.assertEqual(form.rows[0]["basis"], "Synthetic customer specification page 2")
        self.assertNotIn("basis", effective_rows(self.ws.trade, form)[0])

    def test_trade_context_boolean_collection_and_nested_field_use_same_safe_mutations(self):
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.actor,
            D.COMMERCIAL_INVOICE, [], [], "Known private fees", context_fields=[],
            required_input_context_fields=["invoice_adjustments_confirmed", "adjustments"])
        self.fill("invoice_adjustments_confirmed", True)
        self.assertIs(self.ws.trade.invoice_adjustments_confirmed, True)
        self.fill("adjustments", rows=[{"kind": "freight", "name": "Synthetic freight", "amount": "12",
            "currency": "USD", "included_in_invoice_price": False}])
        self.fill("adjustments[1].amount", "15")
        self.assertEqual(self.ws.trade.adjustments[0].amount, "15")
        self.assertEqual(self.ws.trade.adjustments[0].name, "Synthetic freight")
        self.assertFalse(self.ws.documents)
        self.assertFalse(self.ws.approvals)

    def test_product_supplement_routes_by_unique_sku_not_display_row_position(self):
        self.ws = self.cases.create(self.actor, TradeData(destination_country="US", currency="",
            line_items=[TradeLine("B", "Item B", "1", "2"), TradeLine("A", "Item A", "3", "4")]))
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {}, [{"product_code": "A", "description": "Item A", "quantity": "3", "unit": "pcs", "unit_price": "4"},
                 {"product_code": "B", "description": "Item B", "quantity": "1", "unit": "pcs", "unit_price": "2"}],
            "Form products in another order")
        target = self.service.manual_target(self.ws, D.COMMERCIAL_INVOICE, "grade_quality", 1)
        self.assertEqual((target.area, target.row_number), (A.TRADE, 2))
        self.fill("grade_quality", "Grade A", row=1)
        self.assertEqual(self.ws.trade.line_items[1].grade_quality, "Grade A")
        self.assertEqual(self.ws.trade.line_items[0].grade_quality, "")

    def test_inactive_arbitrary_invalid_rows_external_issuance_and_stale_rejected(self):
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.actor,
            D.COMMERCIAL_INVOICE, [], [], "Only core inputs", context_fields=[])
        for kind, field, row in ((D.COMMERCIAL_INVOICE, "bank_account", None),
                (D.COMMERCIAL_INVOICE, "confirmed_by", None), (D.COMMERCIAL_INVOICE, "quantity", 999),
                (D.TEST_REPORT, "recorded_conclusion", None)):
            with self.assertRaises(ValidationError):
                self.service.manual_target(self.ws, kind, field, row)
        before = to_dict(self.ws)
        for revision, digest in ((self.ws.revision - 1, review_digest(self.ws, D.COMMERCIAL_INVOICE)),
                (self.ws.revision, "stale")):
            with self.assertRaises(CrossBotError):
                self.service.fill(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE, "buyer",
                    value="No stale write", reason="Must reject", expected_revision=revision,
                    expected_review_digest=digest)
        with self.assertRaises(CrossBotError):
            self.service.fill(self.ws.case.case_id, actor("robot", "invalid"), D.COMMERCIAL_INVOICE,
                "buyer", value="No unauthorized write", reason="Must reject", expected_revision=self.ws.revision,
                expected_review_digest=review_digest(self.ws, D.COMMERCIAL_INVOICE))
        self.assertEqual(to_dict(self.repo.get(self.ws.case.case_id)), before)

