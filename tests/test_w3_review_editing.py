"""Human corrections are narrow, current, auditable and never approvals."""
import unittest
from copy import deepcopy

from core.errors import CrossBotError, ValidationError
from core.models import (ReviewEditArea as A, ReviewEditTarget, DocumentType as D,
    DocumentForm, TradeData, TradeLine, to_dict)
from case_workflow.service import CaseService
from documents.forms import FormService, get_form, effective_rows
from documents.manual_inputs import ManualInputService
from documents.provenance import form_value_origins
from documents.requirements import data_requirements
from review.editing import ReviewEditingService
from validation.export_review import review_export
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor, trade


class ReviewEditingTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-review-human", "sales")
        self.cases = CaseService(self.repo, PendingRules())
        self.ws = self.cases.create(self.actor, TradeData(currency=""))
        self.edits = ReviewEditingService(self.repo, self.cases)

    def target(self, area, field, row=None, record_id=None):
        report = review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True)
        target = next(target for check in report.checks if check.status.value != "pass"
            for target in check.edit_targets if target.area == area and target.field_name == field
            and target.row_number == row and (record_id is None or target.record_id == record_id))
        return target, report

    def save(self, area, field, value=None, row=None, rows=None, record_id=None):
        target, report = self.target(area, field, row, record_id)
        self.ws = self.edits.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE, target,
            value=value, rows=rows, reason="Human checks this precise finding",
            expected_revision=self.ws.revision, expected_review_digest=report.input_digest)
        return self.ws

    def test_header_only_patch_preserves_unrelated_hidden_content_and_inherited_rows(self):
        self.ws = self.cases.create(self.actor, trade())
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"buyer": "", "notes": "Hidden original note", "seller": self.ws.trade.seller}, [],
            "Intentional clear", manual_fields=["buyer"], preserve_inherited_rows=True)
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.actor,
            D.COMMERCIAL_INVOICE, [], [], "Hide optional note", context_fields=[])
        original_trade = to_dict(self.ws.trade)
        self.save(A.FORM, "buyer", "Correct Buyer")
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["notes"], "Hidden original note")
        self.assertEqual(form.values["seller"], "Example Seller")
        self.assertEqual(form.values["buyer"], "Correct Buyer")
        self.assertFalse(form.rows)
        self.assertFalse(form.rows_manually_edited)
        self.assertEqual(len(effective_rows(self.ws.trade, form)), 1)
        self.assertEqual(to_dict(self.ws.trade), original_trade)
        self.assertFalse(self.ws.approvals)

    def test_unchanged_legacy_value_verifies_only_targeted_field(self):
        self.ws.document_forms = [DocumentForm(D.COMMERCIAL_INVOICE,
            values={"buyer": "Legacy Buyer", "seller": "Legacy Seller"},
            manual_fields=["buyer", "seller"])]
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.save(A.FORM, "buyer", "Legacy Buyer")
        origins = {item.field_name: item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)}
        self.assertEqual((origins["buyer"].origin_kind, origins["buyer"].actor_id), ("manual", self.actor.user_id))
        self.assertEqual(origins["seller"].origin_kind, "unknown")
        event = self.ws.audit[-1]
        self.assertEqual(event.action, "review_value_verified")
        self.assertEqual(event.after["field_name"], "buyer")
        self.assertFalse(get_form(self.ws, D.COMMERCIAL_INVOICE).manually_provided)

    def test_row_cell_patch_preserves_other_rows_and_human_provenance(self):
        rows = [{"product_code": "A", "description": "Item A", "quantity": "0", "unit": "pcs", "unit_price": "3"},
                {"product_code": "B", "description": "Item B", "quantity": "2", "unit": "pcs", "unit_price": "4"}]
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {}, rows, "Known source rows with one bad quantity")
        self.save(A.FORM, "quantity", "5", row=1)
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.rows[0], dict(rows[0], quantity="5"))
        self.assertEqual(form.rows[1], rows[1])
        origin = next(item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
                      if item.row_number == 1 and item.field_name == "quantity")
        self.assertEqual((origin.origin_kind, origin.actor_id), ("manual", self.actor.user_id))

    def test_trade_context_patch_does_not_adopt_unseen_default_values(self):
        self.ws = self.cases.create(self.actor, TradeData(destination_country="US", transport_mode="sea",
            line_items=[TradeLine("A", "Synthetic product", "2", "3")]))
        before = deepcopy(self.ws.trade)
        self.save(A.TRADE, "port_of_entry", "Synthetic Port")
        self.assertEqual(self.ws.trade.port_of_entry, "Synthetic Port")
        self.assertEqual(self.ws.trade.buyer, before.buyer)
        self.assertEqual(self.ws.trade.line_items, before.line_items)
        self.assertIn("port_of_entry", self.ws.trade.supplied_fields)
        origins = form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(next(item for item in origins if item.field_name == "currency").origin_kind, "default")
        self.assertEqual(next(item for item in origins if item.field_name == "unit").origin_kind, "default")

    def test_patch_exact_manual_source_record_preserves_record_identity(self):
        inputs = ManualInputService(self.repo)
        self.ws = inputs.save_record(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            {"buyer": "Old source buyer", "seller": "Source Seller"}, [], "Synthetic source", title="Selected PO")
        record = self.ws.manual_data_records[-1]
        self.ws = inputs.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            D.COMMERCIAL_INVOICE, "Use selected source", source_record_id=record.record_id)
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            dict(form.values, buyer="Correct buyer"), form.rows, "Human changes invoice buyer")
        self.save(A.MANUAL_SOURCE, "buyer", "Correct buyer", record_id=record.record_id)
        corrected = next(item for item in self.ws.manual_data_records if item.record_id == record.record_id)
        self.assertEqual((corrected.record_id, corrected.title, corrected.revision), (record.record_id, "Selected PO", 2))
        self.assertEqual(corrected.values["seller"], "Source Seller")
        self.assertEqual(get_form(self.ws, D.COMMERCIAL_INVOICE).values["buyer"], "Correct buyer")

    def test_context_collection_patch_is_typed_and_preserves_product_rows(self):
        self.ws = self.cases.create(self.actor, TradeData(destination_country="US", transport_mode="sea",
            line_items=[TradeLine("A", "Synthetic product", "2", "3")]))
        original = deepcopy(self.ws.trade.line_items)
        self.save(A.TRADE, "packages", rows=[{"package_number": "SYN-1", "product_code": "A",
            "contents_description": "Synthetic product x2", "quantity": "2", "unit": "pcs",
            "gross_weight_kg": "3", "net_weight_kg": "2", "marks_and_numbers": "SYN-1"}])
        self.assertEqual(self.ws.trade.packages[0].package_number, "SYN-1")
        self.assertEqual(self.ws.trade.line_items, original)
        self.assertFalse(self.ws.approvals)

    def test_unchanged_legacy_row_cell_adoption_is_narrow(self):
        self.ws.document_forms = [DocumentForm(D.COMMERCIAL_INVOICE,
            rows=[{"product_code": "A", "description": "Legacy description", "quantity": "2",
                   "unit": "pcs", "unit_price": "3"}], rows_manually_edited=True)]
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.save(A.FORM, "description", "Legacy description", row=1)
        origins = {item.field_name: item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
                   if item.row_number == 1}
        self.assertEqual(origins["description"].origin_kind, "manual")
        self.assertEqual(origins["quantity"].origin_kind, "unknown")

    def test_invalid_identity_cannot_edit_finding(self):
        target, report = self.target(A.FORM, "buyer")
        before = to_dict(self.ws)
        with self.assertRaises(CrossBotError):
            self.edits.save(self.ws.case.case_id, actor("unauthorized", "unknown"), D.COMMERCIAL_INVOICE,
                target, value="Rejected", reason="Unauthorized", expected_revision=self.ws.revision,
                expected_review_digest=report.input_digest)
        self.assertEqual(to_dict(self.repo.get(self.ws.case.case_id)), before)

    def test_new_product_without_box_count_saves_as_unknown_not_an_explicit_zero(self):
        for box_count in ("", None, "0"):
            with self.subTest(box_count=box_count):
                self.ws = self.cases.create(self.actor, TradeData(destination_country="US", transport_mode="sea", currency=""))
                self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
                    {}, [{"product_code": "A", "description": "Synthetic product", "quantity": "2",
                          "unit": "pcs", "unit_price": "3"}], "Manual invoice has no shared product yet")
                self.save(A.TRADE, "line_items", rows=[{"product_code": "A", "description": "Synthetic product",
                    "quantity": "2", "unit_price": "3", "unit": "pcs", "packages": box_count,
                    "gross_weight_kg": "", "net_weight_kg": "", "hs_code": "", "country_of_origin": "",
                    "grade_quality": "", "marks_and_numbers": "", "package_contents": ""}])
                line = self.ws.trade.line_items[0]
                self.assertEqual(line.packages, 0)
                packing = data_requirements(self.ws, D.PACKING_LIST)
                packages = next(item for item in packing if item.field_name == "packages" and item.row_number == 1)
                if box_count == "0":
                    self.assertIn("packages", line.supplied_fields)
                    self.assertEqual(packages.status, "provided")
                else:
                    self.assertNotIn("packages", line.supplied_fields)
                    self.assertEqual((packages.value, packages.status), ("0", "review_required"))
                    self.assertEqual(packages.origin.origin_kind, "default")
                self.assertFalse(self.ws.approvals)

    def test_stale_revision_digest_unknown_target_and_source_links_rejected_without_writes(self):
        target, report = self.target(A.FORM, "buyer")
        before = to_dict(self.ws)
        for revision, digest, edit_target in ((self.ws.revision - 1, report.input_digest, target),
                (self.ws.revision, "outdated", target),
                (self.ws.revision, report.input_digest, ReviewEditTarget(A.FORM, D.COMMERCIAL_INVOICE, "swift"))):
            with self.assertRaises((CrossBotError, ValidationError)):
                self.edits.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE, edit_target,
                    value="No write", reason="Must reject stale or unknown target", expected_revision=revision,
                    expected_review_digest=digest)
            self.assertEqual(to_dict(self.repo.get(self.ws.case.case_id)), before)
        self.ws.document_forms[0].source_document_ids = ["missing-source"]
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        target, report = self.target(A.SOURCE_LINKS, "source_document_ids")
        before = to_dict(self.ws)
        with self.assertRaises(ValidationError):
            self.edits.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE, target,
                value="No source edit", reason="Use dedicated source controls", expected_revision=self.ws.revision,
                expected_review_digest=report.input_digest)
        self.assertEqual(to_dict(self.repo.get(self.ws.case.case_id)), before)


if __name__ == "__main__":
    unittest.main()
