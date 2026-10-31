"""Source candidates remain traceable and never overwrite human form edits."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from core.errors import CrossBotError
from core.models import (Case, CaseStatus, Document, DocumentType as T, DocumentVersion,
                         ExtractedField, SourceTable, Workspace)
from documents.forms import FormService, get_form
from documents.prefill import PrefillService
from documents.service import DocumentService
from documents.workbench import collect_document, reclassify
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository
from tests.test_w3_workflow import MemoryRepository, actor


def source(buyer="Synthetic Buyer", quantity="2"):
    version = DocumentVersion(original_filename="synthetic.xlsx",
        fields=[ExtractedField(name="customer_name", value=buyer, sheet_name="Data", cell_reference="B1"),
                ExtractedField(name="order_no", value="SYN-PO")],
        source_tables=[SourceTable("Data", [["Buyer", buyer],
            ["SKU", "Description", "Qty", "Unit Price"],
            ["SYN-1", "Synthetic product", quantity, "10"]])])
    document = Document(document_type=T.PURCHASE_ORDER, title="synthetic.xlsx")
    version.document_id = document.document_id
    document.current_version_id = version.version_id
    document.versions = [version]
    return document


class PrefillTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("business", "sales")
        self.first = source()
        self.ws = Workspace(Case(), documents=[self.first])
        self.repo.save(self.ws, 0)
        self.service = PrefillService(self.repo)

    def fill(self, ids=None):
        return self.service.fill(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                 ids if ids is not None else [self.first.document_id])

    def test_prefill_labels_and_rows_keep_exact_source_locations_without_confirmation(self):
        ws = self.fill()
        form = get_form(ws, T.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "Synthetic Buyer")
        self.assertNotIn("invoice_no", form.values)
        self.assertEqual(form.rows[0]["quantity"], "2")
        self.assertEqual(form.row_candidates[0].row_number, 3)
        self.assertEqual(form.row_candidates[0].cell_references["quantity"], "C3")
        self.assertEqual(form.field_candidates["buyer"][0].cell_reference, "B1")
        self.assertFalse(form.confirmed_by)
        self.assertEqual(ws.trade.buyer, "")
        repeated = self.fill()
        self.assertEqual(get_form(repeated, T.COMMERCIAL_INVOICE), form)
        self.assertEqual(repeated.revision, ws.revision)

    def test_conflicting_sources_keep_candidates_without_choosing_or_duplicating_products(self):
        second = source("Different Buyer", "3")
        ws = self.repo.get(self.ws.case.case_id)
        ws.documents.append(second)
        self.repo.save(ws, ws.revision)
        ws = self.fill([self.first.document_id, second.document_id])
        form = get_form(ws, T.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "")
        self.assertEqual(len(form.field_candidates["buyer"]), 2)
        self.assertEqual(form.rows, [])
        self.assertEqual(len(form.row_candidates), 2)
        ws = self.fill([second.document_id])
        self.assertEqual(get_form(ws, T.COMMERCIAL_INVOICE).rows[0]["quantity"], "3")

    def test_equal_product_sets_from_two_files_are_not_concatenated(self):
        second = source()
        ws = self.repo.get(self.ws.case.case_id)
        ws.documents.append(second)
        self.repo.save(ws, ws.revision)
        form = get_form(self.fill([self.first.document_id, second.document_id]), T.COMMERCIAL_INVOICE)
        self.assertEqual(len(form.rows), 1)
        self.assertEqual(len(form.row_candidates), 2)

    def test_human_clears_and_deleted_rows_survive_repeated_prefill(self):
        form = get_form(self.fill(), T.COMMERCIAL_INVOICE)
        values = dict(form.values, buyer="")
        FormService(self.repo).save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                   values, [], "人工删除")
        ws = self.fill()
        saved = get_form(ws, T.COMMERCIAL_INVOICE)
        self.assertEqual(saved.values["buyer"], "")
        self.assertIn("buyer", saved.manual_fields)
        self.assertEqual(saved.rows, [])
        self.assertTrue(saved.rows_manually_edited)

    def test_human_confirmed_form_does_not_lose_confirmation_on_identical_fill(self):
        form = get_form(self.fill(), T.COMMERCIAL_INVOICE)
        FormService(self.repo).save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                   form.values, form.rows, "人工确认", confirm=True)
        self.assertEqual(get_form(self.fill(), T.COMMERCIAL_INVOICE).confirmed_by, "business")

    def test_label_value_table_is_not_a_product_matrix(self):
        ws = self.repo.get(self.ws.case.case_id)
        ws.documents[0].versions[0].source_tables = [SourceTable("Data",
            [["Description", "Synthetic product"], ["Quantity", "2"], ["Unit Price", "10"]])]
        self.repo.save(ws, ws.revision)
        self.assertEqual(get_form(self.fill(), T.COMMERCIAL_INVOICE).rows, [])

    def test_legacy_human_values_and_rows_are_never_overwritten(self):
        from core.models import DocumentForm
        ws = self.repo.get(self.ws.case.case_id)
        ws.document_forms.append(DocumentForm(T.COMMERCIAL_INVOICE,
            values={"buyer": "Human legacy buyer"}, rows=[{"description": "Human row"}]))
        self.repo.save(ws, ws.revision)
        form = get_form(self.fill(), T.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "Human legacy buyer")
        self.assertEqual(form.rows, [{"description": "Human row"}])
        self.assertTrue(form.field_candidates["buyer"])

    def test_confirmed_values_survive_source_revision_but_confirmation_is_withdrawn(self):
        form = get_form(self.fill(), T.COMMERCIAL_INVOICE)
        FormService(self.repo).save(self.ws.case.case_id, self.actor, T.COMMERCIAL_INVOICE,
                                   form.values, form.rows, "人工确认", confirm=True)
        ws = self.repo.get(self.ws.case.case_id)
        document = ws.documents[0]
        changed = source("Revised Buyer").versions[0]
        changed.document_id = document.document_id
        document.versions.append(changed)
        document.current_version_id = changed.version_id
        self.repo.save(ws, ws.revision)
        form = get_form(self.fill(), T.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "Synthetic Buyer")
        self.assertEqual(form.field_candidates["buyer"][0].value, "Revised Buyer")
        self.assertFalse(form.confirmed_by)

    def test_cross_case_and_closed_case_sources_are_rejected(self):
        with self.assertRaises(CrossBotError):
            self.fill(["outside-document"])
        ws = self.repo.get(self.ws.case.case_id)
        ws.case.status = CaseStatus.CLOSED
        self.repo.save(ws, ws.revision)
        with self.assertRaises(CrossBotError):
            self.fill()

    def test_reclassification_withdraws_auto_values_and_candidates(self):
        self.fill()
        ws = reclassify(self.repo, self.ws.case.case_id, self.first.document_id, self.actor,
                        T.OTHER, "external", "分类更正")
        form = get_form(ws, T.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "")
        self.assertFalse(form.field_candidates)
        self.assertEqual(form.rows, [])

    def test_upload_revision_withdraws_old_machine_values_and_keeps_history(self):
        self.fill()
        with TemporaryDirectory() as root:
            documents = DocumentService(self.repo, LocalBlobStore(Path(root)))
            ws = documents.upload(self.ws.case.case_id, self.actor, "revised.xlsx", b"new original",
                                  T.PURCHASE_ORDER, document_id=self.first.document_id)
        self.assertEqual(len(ws.documents[0].versions), 2)
        self.assertEqual(get_form(ws, T.COMMERCIAL_INVOICE).values["buyer"], "")
        self.assertFalse(get_form(ws, T.COMMERCIAL_INVOICE).row_candidates)

    def test_failed_recollection_withdraws_obsolete_unconfirmed_machine_fields(self):
        self.fill()
        with TemporaryDirectory() as root:
            blobs = LocalBlobStore(Path(root))
            ws = self.repo.get(self.ws.case.case_id)
            ws.documents[0].versions[0].storage_path = blobs.put(b"synthetic original")
            self.repo.save(ws, ws.revision)
            with patch("documents.intake.collect_source", return_value=("", [], [], ["采集失败"])):
                ws = collect_document(self.repo, blobs, ws.case.case_id, self.first.document_id,
                                      self.actor, T.COMMERCIAL_INVOICE)
        self.assertFalse(ws.documents[0].versions[0].fields)
        form = get_form(ws, T.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "")
        self.assertEqual(form.rows, [])
        self.assertFalse(form.field_candidates)

    def test_same_recollection_does_not_duplicate_tasks_or_invalidate_again(self):
        with TemporaryDirectory() as root:
            blobs = LocalBlobStore(Path(root))
            ws = self.repo.get(self.ws.case.case_id)
            ws.documents[0].versions[0].storage_path = blobs.put(b"synthetic original")
            self.repo.save(ws, ws.revision)
            version = ws.documents[0].versions[0]
            result = ("Synthetic content", version.source_tables, version.fields, [])
            with patch("documents.intake.collect_source", return_value=result):
                first = collect_document(self.repo, blobs, ws.case.case_id, self.first.document_id,
                                         self.actor, T.COMMERCIAL_INVOICE)
                second = collect_document(self.repo, blobs, ws.case.case_id, self.first.document_id,
                                          self.actor, T.COMMERCIAL_INVOICE)
        self.assertEqual(len(first.review_tasks), 1)
        self.assertEqual(len(second.review_tasks), 1)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(len(first.audit), len(second.audit))

    def test_effective_rows_preserves_explicit_zero_values(self):
        from core.models import DocumentForm, TradeData, TradeLine
        from documents.forms import effective_rows
        rows = effective_rows(TradeData(line_items=[TradeLine("SYN", "Item", "1", "0", packages=0)]),
                              DocumentForm(T.PACKING_LIST))
        self.assertEqual(rows[0]["packages"], "0")

    def test_seller_shipper_and_consignee_remain_distinct_source_facts(self):
        from documents.intake import collect_source, source_label
        self.assertEqual(source_label("Seller"), "seller")
        self.assertEqual(source_label("卖方"), "seller")
        self.assertEqual(source_label("Supplier"), "seller")
        self.assertEqual(source_label("Shipper"), "shipper")
        self.assertEqual(source_label("发货人"), "shipper")
        _, _, fields, _ = collect_source(
            b"Seller: Synthetic Seller\nShipper: Synthetic Warehouse\nConsignee: Synthetic Receiver", ".txt")
        ws = self.repo.get(self.ws.case.case_id)
        ws.documents[0].versions[0].fields = fields
        self.repo.save(ws, ws.revision)
        form = get_form(self.fill(), T.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["seller"], "Synthetic Seller")
        self.assertEqual(form.values["shipper"], "Synthetic Warehouse")
        self.assertEqual(form.values["consignee"], "Synthetic Receiver")

    def test_shipper_is_not_used_to_invent_a_missing_seller(self):
        ws = self.repo.get(self.ws.case.case_id)
        ws.documents[0].versions[0].fields = [ExtractedField(name="shipper", value="Synthetic Warehouse")]
        self.repo.save(ws, ws.revision)
        form = get_form(self.fill(), T.COMMERCIAL_INVOICE)
        self.assertNotIn("seller", form.values)
        self.assertEqual(form.values["shipper"], "Synthetic Warehouse")
        ws = self.service.fill(ws.case.case_id, self.actor, T.QUOTATION, [self.first.document_id])
        form = get_form(ws, T.QUOTATION)
        self.assertNotIn("seller", form.values)

    def test_opt_in_ocr_preserves_seller_and_shipper_semantics_for_adjacent_labels(self):
        from documents.intake import collect_source
        from documents.ocr import RecognizedLine
        lines = [RecognizedLine("Seller", 0.99, (0, 0, 80, 20), 1),
                 RecognizedLine("Synthetic Seller", 0.95, (100, 0, 350, 20), 1),
                 RecognizedLine("Shipper", 0.99, (0, 40, 80, 60), 1),
                 RecognizedLine("Synthetic Warehouse", 0.95, (100, 40, 350, 60), 1),
                 RecognizedLine("Consignee: Synthetic Receiver", 0.95, (0, 80, 350, 100), 1)]
        with patch("documents.ocr.OCRBackend.recognize_image", return_value=lines):
            _, _, fields, _ = collect_source(b"synthetic image", ".png", ocr_images=True)
        self.assertEqual({field.name: field.value for field in fields},
                         {"seller": "Synthetic Seller", "shipper": "Synthetic Warehouse",
                          "consignee": "Synthetic Receiver"})

    def test_manual_removal_of_rows_remains_missing_even_when_trade_has_rows(self):
        from core.models import DocumentForm, TradeData, TradeLine
        from documents.forms import missing_form_fields
        trade = TradeData(line_items=[TradeLine("SYN", "Item", "1", "10")])
        form = DocumentForm(T.COMMERCIAL_INVOICE, rows_manually_edited=True)
        self.assertIn("rows", missing_form_fields(trade, form))

    def test_collect_and_sqlite_reload_use_same_prefill_mapping(self):
        with TemporaryDirectory() as root:
            repository = SQLiteWorkspaceRepository(Path(root) / "workspace.db")
            self.addCleanup(repository.close)
            blobs = LocalBlobStore(Path(root) / "files")
            workspace = Workspace(Case())
            repository.save(workspace, 0)
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["Buyer", "Synthetic Buyer"])
            sheet.append(["SKU", "Description", "Quantity", "Unit Price"])
            sheet.append(["SYN-1", "Synthetic product", 2, 10])
            output = BytesIO()
            workbook.save(output)
            documents = DocumentService(repository, blobs)
            workspace = documents.upload(workspace.case.case_id, self.actor,
                                         "PO.xlsx", output.getvalue(), T.PURCHASE_ORDER)
            document = workspace.documents[0]
            collect_document(repository, blobs, workspace.case.case_id, document.document_id,
                             self.actor, T.COMMERCIAL_INVOICE)
            loaded = repository.get(workspace.case.case_id)
            form = get_form(loaded, T.COMMERCIAL_INVOICE)
            self.assertEqual(form.values["buyer"], "Synthetic Buyer")
            self.assertEqual(form.rows[0]["quantity"], "2")
            self.assertEqual(form.row_candidates[0].version_id, document.current_version_id)
            self.assertFalse(form.confirmed_by)


if __name__ == "__main__":
    unittest.main()
