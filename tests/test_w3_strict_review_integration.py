"""Independent formal-export gates using saved synthetic business data."""
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch
import unittest

from openpyxl import Workbook, load_workbook

from core.errors import CrossBotError
from core.models import Actor, CaseStatus, CrossCheckStatus as S, DocumentType as D, to_dict
from documents.catalog import FORM_FIELDS, ROW_FIELDS
from documents.forms import FormService, get_form
from documents.simple_export import SimpleExportService
from tests import test_w3_prefill_integration as harness


def complete_values(kind):
    values = {"document_no": "SYN-STRICT-01", "document_date": "2026-10-31",
        "invoice_no": "SYN-STRICT-01", "invoice_date": "2026-10-31",
        "order_no": "SYN-STRICT-ORDER", "seller": "Synthetic seller", "buyer": "Synthetic buyer",
        "currency": "USD", "destination_country": "DE", "incoterm": "FCA Shanghai",
        "payment_terms": "Full payment before dispatch", "transport_mode": "sea"}
    return {key: value for key, value in values.items() if key in FORM_FIELDS[kind]}


def complete_row(kind, quantity="4"):
    values = {"product_code": "SYN-A", "description": "Synthetic product", "quantity": quantity,
        "unit": "pcs", "unit_price": "2.50", "amount": str(int(quantity) * 2.5),
        "packages": "1", "gross_weight_kg": "5", "net_weight_kg": "4",
        "marks_and_numbers": "SYN-BOX", "package_contents": "Synthetic product"}
    return {key: value for key, value in values.items() if key in ROW_FIELDS[kind]}


class StrictReviewIntegrationTests(unittest.TestCase):
    setUp = harness.PrefillIntegrationTests.setUp
    tearDown = harness.PrefillIntegrationTests.tearDown

    def service(self):
        return SimpleExportService(self.repo, self.blobs)

    def save(self, kind=D.PROFORMA_INVOICE, rows=None, values=None):
        return FormService(self.repo).save(self.case_id, self.actor, kind,
            complete_values(kind) if values is None else values,
            [complete_row(kind)] if rows is None else rows, "Synthetic strict review input")

    def test_complete_manual_pi_needs_no_original_and_does_not_approve_case(self):
        self.save()
        report = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertTrue(report.ready, [(c.rule_code, c.status.value, c.message) for c in report.checks if c.required])
        self.assertTrue(report.rule_version)
        self.assertTrue(report.input_digest)
        self.assertTrue(any(c.comparison_policy for c in report.checks if c.required))
        self.assertTrue(any(not c.required and c.status is S.PENDING_REVIEW for c in report.checks),
                        "Missing whole-case CI must remain visible without blocking a standalone PI")
        result = self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE,
            human_confirmed=True, expected_review_digest=report.input_digest)
        self.assertFalse(result.documents)
        self.assertEqual(len(result.artifacts), 1)
        self.assertFalse(result.approvals)
        self.assertNotEqual(result.case.status, CaseStatus.PASSED)
        self.assertTrue(all(c.status is S.PASS for c in result.artifacts[-1].checks if c.required))

    def test_incomplete_and_unconfirmed_api_calls_cannot_write_output_blobs(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), patch.object(self.blobs, "put", wraps=self.blobs.put) as put:
                with self.assertRaises(CrossBotError):
                    self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE,
                                            human_confirmed=confirmed)
                put.assert_not_called()
        self.assertFalse(self.repo.get(self.case_id).artifacts)
        preview = self.service().preview(self.case_id, self.actor, D.PROFORMA_INVOICE)
        workbook = load_workbook(BytesIO(preview.content), read_only=True)
        try:
            self.assertTrue(any("否（待人工确认）" in row for row in workbook["文件"].values))
        finally:
            workbook.close()

    def test_missing_unit_and_duplicate_sku_remain_pending_even_after_human_checkbox(self):
        for rows in ([dict(complete_row(D.PROFORMA_INVOICE), unit="")],
                     [complete_row(D.PROFORMA_INVOICE), complete_row(D.PROFORMA_INVOICE)]):
            with self.subTest(rows=rows):
                self.save(rows=rows)
                report = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
                self.assertFalse(report.ready)
                self.assertTrue(any(c.required and c.status is S.PENDING_REVIEW for c in report.checks))
                with self.assertRaises(CrossBotError):
                    self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)

    def test_saved_ci_pl_quantity_conflict_blocks_formal_output(self):
        self.save(D.COMMERCIAL_INVOICE)
        self.save(D.PACKING_LIST, rows=[complete_row(D.PACKING_LIST, "9")])
        report = self.service().assess(self.case_id, self.actor, D.COMMERCIAL_INVOICE, human_confirmed=True)
        self.assertFalse(report.ready)
        failures = [c for c in report.checks if c.required and c.status is S.FAIL]
        self.assertTrue(failures, [(c.rule_code, c.status.value, c.message) for c in report.checks])
        self.assertTrue(any(c.left_value is not None and c.right_value is not None for c in failures))
        self.assertTrue(any(c.comparison_policy and c.scope and c.rule_version for c in failures))
        with self.assertRaises(CrossBotError), patch.object(self.blobs, "put", wraps=self.blobs.put) as put:
            self.service().generate(self.case_id, self.actor, D.COMMERCIAL_INVOICE, human_confirmed=True)
        put.assert_not_called()

    def test_illegal_numeric_values_never_become_pass_through_equal_self_comparison(self):
        for quantity in ("0", "-1", "NaN", "1e3", "1,000"):
            with self.subTest(quantity=quantity):
                self.save(rows=[dict(complete_row(D.PROFORMA_INVOICE), quantity=quantity, amount="")])
                report = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
                self.assertFalse(report.ready)
                self.assertTrue(any(c.required and c.status is not S.PASS for c in report.checks))
                with self.assertRaises(CrossBotError):
                    self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)

    def test_changed_form_or_source_version_cannot_reuse_pass_digest(self):
        self.save()
        first = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertTrue(first.ready)
        self.save(rows=[complete_row(D.PROFORMA_INVOICE, "5")])
        second = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertNotEqual(first.input_digest, second.input_digest)
        with self.assertRaises(CrossBotError):
            self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE,
                human_confirmed=True, expected_review_digest=first.input_digest)
        self.documents.upload(self.case_id, self.actor, "synthetic-source.txt", b"Synthetic source", D.OTHER)
        third = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertNotEqual(second.input_digest, third.input_digest)
        with self.assertRaises(CrossBotError):
            self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE,
                human_confirmed=True, expected_review_digest=second.input_digest)
        self.assertFalse(self.repo.get(self.case_id).artifacts)

    def test_assessment_is_read_only_and_ai_cannot_formally_generate(self):
        self.save()
        before = to_dict(self.repo.get(self.case_id))
        report = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=False)
        self.assertFalse(report.ready)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        with self.assertRaises(CrossBotError):
            self.service().generate(self.case_id, Actor("synthetic-ai", ["admin"], authenticated=True, kind="ai"),
                D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertFalse(self.repo.get(self.case_id).artifacts)

    def test_known_order_identity_conflict_cannot_be_ignored_for_standalone_pi(self):
        from core.models import TradeData
        self.cases.update_trade(self.case_id, self.actor,
            TradeData(order_no="SYN-DIFFERENT-ORDER", buyer="Synthetic other master buyer"),
            "Synthetic known order identity")
        self.save()
        report = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertFalse(report.ready)
        self.assertTrue(any(c.required and c.status is S.FAIL for c in report.checks))
        with self.assertRaises(CrossBotError):
            self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)

    def test_raw_counterpart_conflict_keeps_current_version_units_and_normalized_evidence(self):
        from core.models import DocumentStatus, ExtractedField, FieldValueStatus, utc_now
        self.save(D.COMMERCIAL_INVOICE)
        source_book = load_workbook(BytesIO(harness.synthetic_workbook(quantity="9")))
        source_book.active.delete_rows(6)
        source_content = BytesIO()
        source_book.save(source_content)
        source_book.close()
        workspace = self.documents.upload(self.case_id, self.actor, "synthetic-packing.xlsx",
            source_content.getvalue(), D.PACKING_LIST)
        document = workspace.documents[-1]
        version = document.versions[-1]
        document.status = version.status = DocumentStatus.PARSED
        from documents.intake import collect_source
        version.source_tables = collect_source(source_content.getvalue(), ".xlsx")[1]
        # Synthetic confirmed parser output; no production fields are promoted.
        version.fields = [ExtractedField(name, value, confirmed_value=value,
            confirmed_by=self.actor.user_id, confirmed_at=utc_now(),
            status=FieldValueStatus.CONFIRMED, sheet_name="Source", cell_reference=cell)
            for name, value, cell in (("product_code", "SYN-A", "A5"), ("unit", "pcs", "E5"),
                                      ("quantity", "9", "C5"))]
        self.repo.save(workspace, expected_revision=workspace.revision)
        report = self.service().assess(self.case_id, self.actor, D.COMMERCIAL_INVOICE, human_confirmed=True)
        conflicts = [check for check in report.checks if check.status is S.FAIL
                     and check.right_document_id == document.document_id and check.right_field == "quantity"]
        self.assertTrue(conflicts)
        check = conflicts[0]
        self.assertEqual(check.right_version_id, version.version_id)
        self.assertEqual(check.left_unit, "pcs")
        self.assertEqual(check.right_unit, "pcs")
        self.assertEqual(check.normalized_left, "4")
        self.assertEqual(check.normalized_right, "9")
        self.assertTrue(check.scope)
        self.assertTrue(check.comparison_policy)
        self.assertIn("synthetic-packing.xlsx", check.right_source)

    def test_raw_extra_product_rows_cannot_be_ignored_by_matching_first_scalar(self):
        from core.models import DocumentStatus, ExtractedField, FieldValueStatus, SourceTable, utc_now
        self.save(D.COMMERCIAL_INVOICE)
        source_table = SourceTable("Source", [
            ["Product Code", "Description", "Quantity", "Unit Price", "Unit"],
            ["SYN-A", "Synthetic product", "4", "2.50", "pcs"],
            ["SYN-B", "Synthetic extra product", "3", "5", "pcs"]])
        source_book = Workbook()
        source_book.active.title = "Source"
        for row in source_table.rows:
            source_book.active.append(row)
        source_content = BytesIO()
        source_book.save(source_content)
        source_book.close()
        workspace = self.documents.upload(self.case_id, self.actor, "synthetic-multirow-packing.xlsx",
            source_content.getvalue(), D.PACKING_LIST)
        document = workspace.documents[-1]
        version = document.versions[-1]
        document.status = version.status = DocumentStatus.PARSED
        version.source_tables = [source_table]
        version.fields = [ExtractedField(name, value, confirmed_value=value,
            confirmed_by=self.actor.user_id, confirmed_at=utc_now(),
            status=FieldValueStatus.CONFIRMED, sheet_name="Source", cell_reference=cell)
            for name, value, cell in (("product_code", "SYN-A", "A2"), ("unit", "pcs", "E2"),
                                      ("quantity", "4", "C2"))]
        self.repo.save(workspace, expected_revision=workspace.revision)
        report = self.service().assess(self.case_id, self.actor, D.COMMERCIAL_INVOICE, human_confirmed=True)
        self.assertFalse(report.ready, "A matching first raw row must not hide another unmatched product")
        self.assertTrue(any(c.required and c.status is S.PENDING_REVIEW for c in report.checks))


if __name__ == "__main__":
    unittest.main()

