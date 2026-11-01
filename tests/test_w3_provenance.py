"""Displayed and exported values explain their actual source or uncertainty."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook
from core.models import (Case, DocumentForm, DocumentType as T, FieldValueStatus,
                         TradeData, Workspace, utc_now)
from case_workflow.service import CaseService
from documents.forms import FormService, get_form
from documents.prefill import PrefillService
from documents.provenance import form_value_origins
from documents.simple_export import SimpleExportService
from storage.blobs import LocalBlobStore
from tests.test_w3_prefill import source
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor, trade


class ValueOriginTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-business", "sales")
        self.ws = Workspace(Case(), documents=[source()])
        self.repo.save(self.ws, 0)
        self.fill = PrefillService(self.repo)
        self.ws = self.fill.fill(self.ws.case.case_id, self.actor,
                                 T.COMMERCIAL_INVOICE, [self.ws.documents[0].document_id])

    def origins(self, ws=None):
        return form_value_origins(ws or self.ws, T.COMMERCIAL_INVOICE)

    def origin(self, name, row=None, ws=None):
        return next(item for item in self.origins(ws) if item.field_name == name and item.row_number == row)

    def test_auto_scalar_and_row_cells_name_file_version_and_position(self):
        buyer = self.origin("buyer")
        self.assertEqual(buyer.origin_kind, "file")
        self.assertEqual(buyer.filename, "synthetic.xlsx")
        self.assertEqual(buyer.version_number, 1)
        self.assertIn("Data", buyer.location)
        self.assertIn("B1", buyer.location)
        quantity = self.origin("quantity", 1)
        self.assertEqual(quantity.origin_kind, "file")
        self.assertIn("C3", quantity.location)

    def test_human_corrected_source_scalar_is_manual_with_original_file_context(self):
        workspace = self.repo.get(self.ws.case.case_id)
        field = workspace.documents[0].versions[0].fields[0]
        field.confirmed_value = "Synthetic human-corrected source buyer"
        field.confirmed_by, field.confirmed_at = self.actor.user_id, utc_now()
        field.status = FieldValueStatus.CONFIRMED
        self.repo.save(workspace, workspace.revision)
        self.ws = self.fill.fill(workspace.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                 [workspace.documents[0].document_id])
        buyer = self.origin("buyer")
        self.assertEqual(buyer.value, field.confirmed_value)
        self.assertEqual(buyer.origin_kind, "manual")
        self.assertEqual(buyer.actor_id, self.actor.user_id)
        self.assertEqual(buyer.occurred_at, field.confirmed_at)
        self.assertEqual(buyer.filename, "synthetic.xlsx")
        self.assertEqual(buyer.version_number, 1)
        self.assertIn("B1", buyer.location)
        self.assertIn("Synthetic Buyer", buyer.note)
        self.assertIn(field.confirmed_value, buyer.note)
        self.assertIn("不表示原单元格", buyer.note)
        preserved = self.ws.documents[0].versions[0].fields[0]
        self.assertEqual(preserved.value, "Synthetic Buyer")
        self.assertEqual(get_form(self.ws, T.COMMERCIAL_INVOICE).field_candidates["buyer"][0].value,
                         field.confirmed_value)

    def test_confirming_unchanged_source_scalar_keeps_literal_file_origin(self):
        workspace = self.repo.get(self.ws.case.case_id)
        field = workspace.documents[0].versions[0].fields[0]
        field.confirmed_value = field.value
        field.confirmed_by, field.confirmed_at = self.actor.user_id, utc_now()
        field.status = FieldValueStatus.CONFIRMED
        self.repo.save(workspace, workspace.revision)
        self.ws = self.fill.fill(workspace.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                 [workspace.documents[0].document_id])
        buyer = self.origin("buyer")
        self.assertEqual(buyer.origin_kind, "file")
        self.assertEqual(buyer.actor_id, "")
        self.assertIn("B1", buyer.location)

    def test_manual_override_has_actor_and_time_and_unchanged_cells_keep_file_origin(self):
        form = get_form(self.ws, T.COMMERCIAL_INVOICE)
        rows = deepcopy(form.rows)
        rows[0]["quantity"] = "7"
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
            dict(form.values, buyer="Human corrected buyer"), rows, "Synthetic correction")
        buyer = self.origin("buyer")
        self.assertEqual(buyer.origin_kind, "manual")
        self.assertEqual(buyer.actor_id, self.actor.user_id)
        self.assertIsNotNone(buyer.occurred_at)
        self.assertEqual(buyer.filename, "")
        quantity = self.origin("quantity", 1)
        self.assertEqual(quantity.origin_kind, "manual")
        self.assertEqual(quantity.actor_id, self.actor.user_id)
        self.assertEqual(self.origin("unit_price", 1).origin_kind, "file")
        self.assertEqual(self.origin("description", 1).origin_kind, "file")

    def test_manual_clear_records_human_intent_without_restoring_candidate(self):
        form = get_form(self.ws, T.COMMERCIAL_INVOICE)
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
            dict(form.values, buyer=""), form.rows, "Synthetic clear")
        origin = self.origin("buyer")
        self.assertEqual(origin.value, "")
        self.assertEqual(origin.origin_kind, "manual")
        self.assertIn("清空", origin.note)

    def test_master_fallback_has_matching_trade_audit_actor(self):
        service = CaseService(self.repo, PendingRules())
        ws = service.create(self.actor, trade())
        buyer = self.origin("buyer", ws=ws)
        self.assertEqual(buyer.origin_kind, "trade")
        self.assertEqual(buyer.actor_id, self.actor.user_id)
        self.assertIn("buyer", buyer.note)
        self.assertEqual(self.origin("quantity", 1, ws=ws).origin_kind, "trade")

    def test_untouched_default_is_not_claimed_as_human_or_file(self):
        ws = Workspace(Case())
        currency = self.origin("currency", ws=ws)
        self.assertEqual(currency.origin_kind, "default")
        self.assertEqual(currency.actor_id, "")
        self.assertIn("USD", currency.note)

    def test_legacy_nonempty_data_has_unknown_origin_without_human_claim(self):
        ws = Workspace(Case(), document_forms=[DocumentForm(T.COMMERCIAL_INVOICE,
            values={"buyer": "Legacy buyer"}, rows=[{"description": "Legacy item"}])])
        self.assertEqual(self.origin("buyer", ws=ws).origin_kind, "unknown")
        self.assertEqual(self.origin("description", 1, ws=ws).origin_kind, "unknown")
        self.assertTrue(self.origin("buyer", ws=ws).note)

    def test_stale_source_does_not_claim_file_as_current_origin(self):
        ws = deepcopy(self.ws)
        ws.documents[0].current_version_id = "new-current-version"
        origin = self.origin("buyer", ws=ws)
        self.assertEqual(origin.origin_kind, "unknown")
        self.assertIn("失效", origin.note)
        self.assertEqual(self.origin("quantity", 1, ws=ws).origin_kind, "unknown")

    def test_legacy_protection_flags_without_audit_do_not_fabricate_manual_origin(self):
        ws = Workspace(Case(), document_forms=[DocumentForm(T.COMMERCIAL_INVOICE,
            values={"buyer": "Legacy protected buyer"}, manual_fields=["buyer"],
            rows=[{"description": "Legacy protected row"}], rows_manually_edited=True)])
        self.assertEqual(self.origin("buyer", ws=ws).origin_kind, "unknown")
        self.assertEqual(self.origin("description", 1, ws=ws).origin_kind, "unknown")

    def test_derived_amount_explains_formula_and_both_input_sources(self):
        amount = self.origin("amount", 1)
        self.assertEqual(amount.origin_kind, "derived")
        self.assertEqual(amount.value, "20")
        self.assertIn("quantity × unit_price", amount.note)
        self.assertIn("C3", amount.note)
        self.assertIn("D3", amount.note)

    def test_saved_unchanged_order_defaults_still_explain_order_inheritance(self):
        service = CaseService(self.repo, PendingRules())
        ws = service.create(self.actor, trade())
        ws = FormService(self.repo).save(ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                        {"currency": "USD"}, [], "Save default")
        self.assertEqual(self.origin("currency", ws=ws).origin_kind, "trade")

    def test_ambiguous_duplicate_sku_after_partial_edit_does_not_invent_cell_location(self):
        ws = self.repo.get(self.ws.case.case_id)
        version = ws.documents[0].versions[0]
        version.source_tables[0].rows.append(["SYN-1", "Synthetic product", "3", "10"])
        self.repo.save(ws, ws.revision)
        ws = self.fill.fill(ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                            [ws.documents[0].document_id])
        form = get_form(ws, T.COMMERCIAL_INVOICE)
        rows = [dict(form.rows[1], quantity="7")]
        ws = FormService(self.repo).save(ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                        form.values, rows, "Delete first and edit remaining")
        self.assertEqual(self.origin("quantity", 1, ws=ws).origin_kind, "manual")
        self.assertEqual(self.origin("unit_price", 1, ws=ws).origin_kind, "unknown")

    def test_identical_unedited_source_rows_preserve_distinct_source_cell_identity(self):
        ws = self.repo.get(self.ws.case.case_id)
        version = ws.documents[0].versions[0]
        version.source_tables[0].rows.append(["SYN-1", "Synthetic product", "2", "10"])
        self.repo.save(ws, ws.revision)
        ws = self.fill.fill(ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                            [ws.documents[0].document_id])
        self.assertIn("C3", self.origin("quantity", 1, ws=ws).location)
        self.assertIn("C4", self.origin("quantity", 2, ws=ws).location)

    def test_export_has_actual_origins_separate_from_unselected_candidates(self):
        form = get_form(self.ws, T.COMMERCIAL_INVOICE)
        ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
            dict(form.values, buyer="Human corrected buyer"), form.rows, "Synthetic correction")
        with TemporaryDirectory() as root:
            blobs = LocalBlobStore(Path(root))
            preview = SimpleExportService(self.repo, blobs).preview(ws.case.case_id, self.actor,
                T.COMMERCIAL_INVOICE)
            book = load_workbook(BytesIO(preview.content))
            try:
                rows = list(book["字段来源"].values)
            finally:
                book.close()
        actual = next(row for row in rows if row[0] == "buyer")
        self.assertEqual(actual[2], "人工填写")
        self.assertEqual(actual[10], self.actor.user_id)
        self.assertIsNone(actual[4])
        candidate = next(row for row in rows if row[0] == "候选字段 buyer")
        self.assertEqual(candidate[3], "Synthetic Buyer")
        self.assertEqual(candidate[4], "synthetic.xlsx")
        self.assertTrue(any(row[0] == "明细行 1.amount" and row[2] == "计算所得" for row in rows))


if __name__ == "__main__":
    unittest.main()

