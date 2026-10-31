"""Strict current-form export readiness without approval or source promotion."""
from copy import deepcopy
from hashlib import sha256
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from case_workflow.service import CaseService
from core.models import (AuditEvent, Document, DocumentForm, DocumentStatus as DS,
    DocumentType as T, DocumentVersion, ExtractedField, FieldValueStatus as F,
    FormFieldCandidate, SourceTable, TradeData, to_dict)
from documents.catalog import FORM_FIELDS, ROW_FIELDS
from documents.forms import FormService, get_form
from documents.prefill import PrefillService
from validation.export_review import review_digest, review_export
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor


class ExportReviewTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-sales", "sales")
        self.cases = CaseService(self.repo, PendingRules())
        self.ws = self.cases.create(self.actor, TradeData(currency=""))
        self.forms = FormService(self.repo)

    def complete(self, kind=T.COMMERCIAL_INVOICE, *, quantity="2", unit="pcs", buyer="Synthetic Buyer", rows=None):
        values = {"document_no": "SYN-" + kind.value, "document_date": "2026-10-31",
            "order_no": "SYN-100", "seller": "Synthetic Seller", "buyer": buyer,
            "currency": "USD", "incoterm": "FCA Shanghai", "destination_country": "DE"}
        values = {key: value for key, value in values.items() if key in FORM_FIELDS[kind]}
        rows = rows or [{"product_code": "SYN-A", "description": "Synthetic item", "quantity": quantity,
            "unit": unit, "unit_price": "10", "packages": "1", "gross_weight_kg": "2", "net_weight_kg": "1"}]
        rows = [{key: value for key, value in row.items() if key in ROW_FIELDS[kind]} for row in rows]
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, kind, values, rows, "Synthetic manual record")
        return self.ws

    def report(self, kind=T.COMMERCIAL_INVOICE, *, human=True):
        return review_export(self.ws, kind, human_confirmed=human)

    def blockers(self, report=None):
        return [item for item in (report or self.report()).checks if item.required and item.status.value != "pass"]

    def test_complete_audited_manual_document_can_export_without_any_sources_or_complete_master(self):
        self.complete()
        before = to_dict(self.ws)
        report = self.report()
        self.assertTrue(report.ready, [(item.rule_code, item.message) for item in self.blockers(report)])
        self.assertFalse(self.ws.documents)
        self.assertFalse(self.ws.approvals)
        self.assertEqual(to_dict(self.ws), before)
        advisory = [item for item in report.checks if item.rule_code == "CASE_REQUIRED_DOCUMENT"]
        self.assertTrue(any(item.left_value == T.PACKING_LIST.value and not item.required
                            and item.status.value == "pending_review" for item in advisory))

    def test_human_flag_is_required_without_mutating_field_confirmations(self):
        self.complete()
        self.assertFalse(self.report(human=False).ready)
        self.assertFalse(self.report(human=1).ready)
        self.assertIsNone(self.ws.trade.confirmed_by)
        self.assertIsNone(get_form(self.ws, T.COMMERCIAL_INVOICE).confirmed_by)

    def test_missing_date_seller_and_unit_remain_pending(self):
        self.complete()
        form = get_form(self.ws, T.COMMERCIAL_INVOICE)
        values = dict(form.values, document_date="", seller="")
        rows = [dict(form.rows[0], unit="")]
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                 values, rows, "Incomplete synthetic fields")
        self.assertFalse(self.report().ready)
        self.assertTrue(any(item.rule_code == "DOCUMENT_COMPLETENESS" for item in self.blockers()))

    def test_plain_decimal_policy_rejects_exponents_floats_nan_and_negative_counts(self):
        for value in ("1e3", "NaN", "1,000", "-1", "0", 1.5):
            with self.subTest(value=value):
                self.complete()
                # Inject malformed input to exercise API boundary independently
                # of the UI/form text validator.
                next(form for form in self.ws.document_forms if form.document_type is T.COMMERCIAL_INVOICE).rows[0]["quantity"] = value
                report = self.report()
                self.assertFalse(report.ready)
                self.assertTrue(any(item.rule_code == "NUMBER_VALID" and item.status.value == "fail"
                                    for item in report.checks))

    def test_duplicate_sku_is_pending_and_matching_total_cannot_hide_changed_lines(self):
        rows = [{"product_code": "A", "description": "Item", "quantity": "1", "unit": "pcs", "unit_price": "10"},
                {"product_code": "A", "description": "Item", "quantity": "1", "unit": "pcs", "unit_price": "10"}]
        self.complete(rows=rows)
        self.assertTrue(any(item.rule_code == "ROW_IDENTITY" for item in self.blockers()))
        rows[1]["product_code"] = "B"
        self.complete(rows=rows)
        self.complete(T.PACKING_LIST, rows=[dict(rows[0], quantity="2"), dict(rows[1], quantity="0.5")])
        report = self.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(item.rule_code == "CROSS_DOCUMENT" and item.left_field == "quantity"
                            and item.status.value == "fail" for item in report.checks))

    def test_related_currency_mismatch_is_fail_and_money_comparison_is_pending(self):
        self.complete()
        self.complete(T.SALES_CONTRACT)
        form = get_form(self.ws, T.SALES_CONTRACT)
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, T.SALES_CONTRACT,
                                 dict(form.values, currency="EUR"), form.rows, "Synthetic EUR contract")
        report = self.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(item.left_field == "currency" and item.status.value == "fail" for item in report.checks))
        self.assertTrue(any(item.left_field == "total_amount" and item.status.value == "pending_review" for item in report.checks))

    def test_units_and_text_identity_are_exact_not_casefolded(self):
        self.complete(unit="pcs")
        self.complete(T.PACKING_LIST, unit="PCS", buyer="synthetic buyer")
        report = self.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(item.left_field == "quantity" and item.status.value == "pending_review" for item in report.checks))
        self.assertTrue(any(item.left_field == "buyer" and item.status.value == "fail" for item in report.checks))

    def test_wrong_line_amount_and_fractional_boxes_fail(self):
        self.complete(rows=[{"product_code": "A", "description": "Item", "quantity": "2", "unit": "pcs",
                             "unit_price": "10", "amount": "19"}])
        self.assertTrue(any(item.rule_code == "LINE_AMOUNT" and item.status.value == "fail" for item in self.blockers()))
        self.complete(T.PACKING_LIST)
        next(form for form in self.ws.document_forms if form.document_type is T.PACKING_LIST).rows[0]["packages"] = "1.5"
        self.assertFalse(self.report(T.PACKING_LIST).ready)

    def test_linked_copied_source_is_reviewed_in_form_without_promoting_raw_candidates(self):
        document = Document(case_id=self.ws.case.case_id, document_type=T.COMMERCIAL_INVOICE,
                            status=DS.PARSED, title="synthetic-ci.xlsx")
        version = DocumentVersion(document_id=document.document_id, sha256="a" * 64,
            original_filename="synthetic-ci.xlsx", status=DS.PARSED,
            fields=[ExtractedField(name="customer_name", value="Synthetic Buyer", sheet_name="Data", cell_reference="B1")],
            source_tables=[SourceTable("Data", [["SKU", "Description", "Qty", "Unit Price", "Unit"],
                ["SYN-A", "Synthetic item", "2", "10", "pcs"]])])
        document.versions = [version]
        document.current_version_id = version.version_id
        ws = self.repo.get(self.ws.case.case_id)
        ws.documents.append(document)
        self.repo.save(ws, ws.revision)
        self.ws = PrefillService(self.repo).fill(ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                               [document.document_id])
        form = get_form(self.ws, T.COMMERCIAL_INVOICE)
        values = dict(form.values, document_no="SYN-CI", document_date="2026-10-31",
                      order_no="SYN-100", seller="Synthetic Seller", currency="USD")
        self.ws = self.forms.save(ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                 values, form.rows, "Fill missing headers")
        report = self.report()
        self.assertTrue(report.ready, [(item.rule_code, item.message) for item in self.blockers(report)])
        self.assertEqual(self.ws.documents[0].versions[0].fields[0].status, F.CANDIDATE)
        self.assertIsNone(self.ws.documents[0].versions[0].fields[0].confirmed_by)
        self.ws.documents[0].current_version_id = "replacement"
        self.assertFalse(self.report().ready)
        self.assertTrue(any(item.rule_code == "SOURCE_CURRENT" for item in self.blockers()))

    def test_unknown_legacy_manual_marker_is_not_proof_of_human_entry(self):
        self.complete()
        self.ws.audit = [event for event in self.ws.audit if event.action != "document_form_saved"]
        report = self.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(item.rule_code == "VALUE_PROVENANCE" for item in self.blockers(report)))

    def test_unresolved_scalar_candidates_need_actual_manual_resolution_not_only_checkbox(self):
        self.complete()
        form = next(form for form in self.ws.document_forms if form.document_type is T.COMMERCIAL_INVOICE)
        form.manual_fields.remove("buyer")
        form.auto_values["buyer"] = "Synthetic Buyer"
        form.field_candidates["buyer"] = [FormFieldCandidate("Synthetic Buyer", "doc-a", "v1"),
                                           FormFieldCandidate("Different Buyer", "doc-b", "v1")]
        self.assertFalse(self.report().ready)
        self.assertTrue(any(item.rule_code == "SOURCE_FIELD_CONFLICT" for item in self.blockers()))

    def test_digest_ignores_download_audits_but_binds_forms_current_versions_and_policies(self):
        self.complete()
        digest = review_digest(self.ws, T.COMMERCIAL_INVOICE)
        self.ws.audit.append(AuditEvent(action="file_downloaded", actor_id="reader"))
        self.assertEqual(review_digest(self.ws, T.COMMERCIAL_INVOICE), digest)
        next(form for form in self.ws.document_forms if form.document_type is T.COMMERCIAL_INVOICE).rows[0]["quantity"] = "3"
        self.assertNotEqual(review_digest(self.ws, T.COMMERCIAL_INVOICE), digest)
        changed = review_digest(self.ws, T.COMMERCIAL_INVOICE)
        self.ws.case.customer_name = "Different case customer"
        self.assertNotEqual(review_digest(self.ws, T.COMMERCIAL_INVOICE), changed)

    def test_count_units_are_integral_but_explicit_mass_quantities_can_be_fractional(self):
        self.complete(quantity="2.5", unit="pcs")
        report = self.report()
        self.assertFalse(report.ready)
        quantity = next(item for item in report.checks if item.rule_code == "NUMBER_VALID" and item.left_field == "quantity")
        self.assertFalse(quantity.comparison_policy["allow_fractional"])
        self.complete(quantity="2.5", unit="kg")
        self.assertTrue(self.report().ready)

    def test_explicit_invoice_number_and_date_aliases_satisfy_document_identity_only(self):
        self.complete()
        form = get_form(self.ws, T.COMMERCIAL_INVOICE)
        values = dict(form.values, document_no="", document_date="", invoice_no="INV-SYN", invoice_date="2026-10-31")
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                 values, form.rows, "Use explicit invoice identity")
        self.assertTrue(self.report().ready)

    def test_populated_master_conflicts_are_evidence_even_for_standalone_document(self):
        self.complete()
        self.ws.trade.order_no = "OTHER-ORDER"
        self.ws.trade.buyer = "Other Buyer"
        report = self.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(item.rule_code == "ORDER_IDENTITY" and item.status.value == "fail"
                            and item.right_source.startswith("trade:") for item in report.checks))


if __name__ == "__main__":
    unittest.main()
