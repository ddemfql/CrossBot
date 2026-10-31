"""Prefill honors exact current human row reviews without rewriting originals."""
from copy import deepcopy
import unittest

from core.models import Document, DocumentVersion, DocumentType as D, DocumentStatus as S, SourceTable, TradeData
from case_workflow.service import CaseService
from documents.forms import FormService, get_form
from documents.prefill import PrefillService, current_row_review
from documents.provenance import form_value_origins
from review.quality_inputs import pending_source_rows, confirm_source_rows
from validation.export_review import review_digest, review_export
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor


class ReviewedPrefillTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.sales, self.reviewer = actor("synthetic-copy-user", "sales"), actor("synthetic-source-reviewer", "sales")
        self.ws = CaseService(self.repo, PendingRules()).create(self.sales, TradeData(currency=""))
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.sales, D.COMMERCIAL_INVOICE,
            {"document_no": "CI-1", "document_date": "2026-10-31", "order_no": "SYN-1",
             "buyer": "Synthetic Buyer", "seller": "Synthetic Seller", "currency": "USD"}, [],
            "Known invoice headers", preserve_inherited_rows=True)
        self.document = Document(case_id=self.ws.case.case_id, document_type=D.PACKING_LIST, status=S.PARSED)
        version = DocumentVersion(document_id=self.document.document_id, status=S.PARSED, sha256="a"*64,
            original_filename="synthetic-packing.xlsx", source_tables=[SourceTable("Packing", [
                ["SKU", "Description", "Quantity", "Unit", "Unit price", "Gross weight kg"],
                ["A", "Synthetic A", "2", "pcs", "4", "5"],
                ["B", "Synthetic B", "5", "pcs", "6", "7"]])])
        self.document.current_version_id = version.version_id
        self.document.versions = [version]
        self.ws.documents = [self.document]
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.prefill = PrefillService(self.repo)
        self.refill()

    def refill(self):
        self.ws = self.prefill.fill(self.ws.case.case_id, self.sales, D.COMMERCIAL_INVOICE,
            [self.document.document_id])
        return get_form(self.ws, D.COMMERCIAL_INVOICE)

    def review_rows(self):
        selections = pending_source_rows(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(len(selections), 2)
        selections[0].values["quantity"] = "3"
        confirm_source_rows(self.repo, self.ws.case.case_id, self.reviewer, D.COMMERCIAL_INVOICE,
            selections, expected_review_digest=review_digest(self.ws, D.COMMERCIAL_INVOICE), human_confirmed=True)
        self.ws = self.repo.get(self.ws.case.case_id)

    def test_repeated_prefill_uses_human_quantity_and_preserves_machine_candidates_and_locations(self):
        originals = deepcopy(self.ws.documents[0].versions[0].source_tables)
        self.review_rows()
        form = self.refill()
        self.assertEqual(form.rows[0]["quantity"], "3")
        self.assertEqual(form.auto_rows[0]["quantity"], "3")
        self.assertEqual(form.row_candidates[0].values["quantity"], "2")
        self.assertEqual(self.ws.documents[0].versions[0].source_tables, originals)
        origin = next(item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
                      if item.row_number == 1 and item.field_name == "quantity")
        self.assertEqual((origin.origin_kind, origin.actor_id), ("manual", self.reviewer.user_id))
        self.assertEqual(origin.occurred_at, self.ws.reviewed_source_rows[0].confirmed_at)
        self.assertEqual(origin.location, "工作表 Packing · C2")
        self.assertIn("原采集值：2", origin.note)
        description = next(item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
                           if item.row_number == 1 and item.field_name == "description")
        self.assertEqual(description.origin_kind, "file")
        self.assertEqual(self.refill().rows[0]["quantity"], "3")
        self.assertFalse(any(check.rule_code == "RAW_CROSS_DOCUMENT" and check.status.value == "fail"
            and check.left_field == "quantity" for check in review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True).checks))

    def test_human_target_edits_and_explicit_clears_are_not_overwritten(self):
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        rows = deepcopy(form.rows)
        rows[0]["quantity"] = ""
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.sales, D.COMMERCIAL_INVOICE,
            form.values, rows, "Human intentionally clears one quantity")
        self.review_rows()
        self.assertEqual(self.refill().rows[0]["quantity"], "")
        self.assertTrue(get_form(self.ws, D.COMMERCIAL_INVOICE).rows_manually_edited)

    def test_mismatched_original_and_revoked_review_fall_back_without_forging_human_origin(self):
        self.review_rows()
        self.assertEqual(self.refill().rows[0]["quantity"], "3")
        self.ws.reviewed_source_rows[0].original_values["quantity"] = "999"
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        form = self.refill()
        self.assertEqual(form.rows[0]["quantity"], "2")
        self.assertIsNone(current_row_review(self.ws, form.row_candidates[0]))
        origin = next(item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
                      if item.row_number == 1 and item.field_name == "quantity")
        self.assertEqual((origin.origin_kind, origin.actor_id), ("file", ""))
        self.ws.reviewed_source_rows = []
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.refill()
        report = review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True)
        self.assertFalse(report.ready)
        self.assertTrue(any(check.rule_code == "RAW_ROW_SOURCE" and check.status.value == "pending_review" for check in report.checks))

    def test_old_version_review_cannot_apply_to_current_file_version(self):
        self.review_rows()
        self.refill()
        doc = self.ws.documents[0]
        old = doc.versions[0]
        current = DocumentVersion(document_id=doc.document_id, version_number=2, status=S.PARSED, sha256="b"*64,
            original_filename="synthetic-packing-v2.xlsx", source_tables=deepcopy(old.source_tables))
        current.source_tables[0].rows[1][2] = "4"
        doc.versions.append(current)
        doc.current_version_id = current.version_id
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        form = self.refill()
        self.assertEqual(form.rows[0]["quantity"], "4")
        self.assertIsNone(current_row_review(self.ws, form.row_candidates[0]))
        self.assertFalse(review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True).ready)

    def test_targeted_row_prefill_uses_same_review_and_keeps_machine_evidence(self):
        self.review_rows()
        self.ws = self.prefill.fill_requirement(self.ws.case.case_id, self.sales, D.PROFORMA_INVOICE,
            "rows", self.document.document_id, expected_revision=self.ws.revision)
        form = get_form(self.ws, D.PROFORMA_INVOICE)
        self.assertEqual(form.rows[0]["quantity"], "3")
        self.assertEqual(form.row_candidates[0].values["quantity"], "2")
        self.assertEqual(next(item for item in form_value_origins(self.ws, D.PROFORMA_INVOICE)
            if item.row_number == 1 and item.field_name == "quantity").actor_id, self.reviewer.user_id)

    def test_review_can_supply_missing_source_identity_without_inventing_machine_values(self):
        version = self.ws.documents[0].versions[0]
        version.source_tables[0].rows[1][0] = ""
        version.source_tables[0].rows[1][1] = ""
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.refill()
        selections = pending_source_rows(self.ws, D.COMMERCIAL_INVOICE)
        missing = next(row for row in selections if row.row_number == 2)
        missing.values.update(product_code="A", description="Human verified identity", quantity="3")
        confirm_source_rows(self.repo, self.ws.case.case_id, self.reviewer, D.COMMERCIAL_INVOICE,
            selections, expected_review_digest=review_digest(self.ws, D.COMMERCIAL_INVOICE), human_confirmed=True)
        form = self.refill()
        self.assertEqual(form.rows[0]["product_code"], "A")
        self.assertNotIn("product_code", form.row_candidates[0].values)
        origin = next(item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
                      if item.row_number == 1 and item.field_name == "product_code")
        self.assertEqual((origin.origin_kind, origin.actor_id), ("manual", self.reviewer.user_id))

    def test_duplicate_review_records_are_ambiguous_and_never_selected(self):
        self.review_rows()
        self.ws.reviewed_source_rows.append(deepcopy(self.ws.reviewed_source_rows[0]))
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        form = self.refill()
        self.assertEqual(form.rows[0]["quantity"], "2")
        self.assertIsNone(current_row_review(self.ws, form.row_candidates[0]))
