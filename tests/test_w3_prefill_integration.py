"""Synthetic source-to-form-to-export checks, independent of approval workflows."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook, load_workbook

from case_workflow.service import CaseService
from core.models import Actor, DocumentType as D, TradeData, TradeLine, to_dict
from documents.forms import FormService, get_form
from documents.prefill import PrefillService
from documents.service import DocumentService
from documents.simple_export import SimpleExportService
from documents.workbench import collect_document
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository
from validation.rules import DeterministicRules


def synthetic_workbook(buyer="Synthetic source buyer", quantity="4"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Source"
    sheet.append(["Buyer", buyer])
    sheet.append(["Order No", "SYN-PREFILL-01"])
    sheet.append([])
    sheet.append(["Product Code", "Description", "Quantity", "Unit Price", "Unit"])
    sheet.append(["SYN-A", "Synthetic first item", quantity, "2.50", "pcs"])
    sheet.append(["SYN-A", "Synthetic second item", "3", "5", "pcs"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class PrefillIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = SQLiteWorkspaceRepository(root / "workspace.sqlite3")
        self.blobs = LocalBlobStore(root / "files")
        self.actor = Actor("synthetic-sales", ["sales"], authenticated=True)
        self.cases = CaseService(self.repo, DeterministicRules())
        self.documents = DocumentService(self.repo, self.blobs)
        self.workspace = self.cases.create(self.actor, TradeData(), required_types=[D.COMMERCIAL_INVOICE])
        self.case_id = self.workspace.case.case_id

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def source(self, kind=D.COMMERCIAL_INVOICE):
        content = synthetic_workbook()
        workspace = self.documents.upload(self.case_id, self.actor, "synthetic-source.xlsx", content, kind)
        doc = workspace.documents[-1]
        workspace = collect_document(self.repo, self.blobs, self.case_id, doc.document_id, self.actor)
        return workspace, doc.document_id, content

    def test_collected_workbook_prefills_without_confirming_or_altering_originals(self):
        workspace, doc_id, original = self.source()
        form = get_form(workspace, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "Synthetic source buyer")
        self.assertEqual([row["description"] for row in form.rows],
                         ["Synthetic first item", "Synthetic second item"])
        self.assertEqual(len(form.row_candidates), 2)
        self.assertEqual(len({(row.version_id, row.table_index, row.row_number)
                              for row in form.row_candidates}), 2)
        self.assertTrue(all(row.cell_references for row in form.row_candidates))
        candidate = form.field_candidates["buyer"][0]
        self.assertEqual(candidate.document_id, doc_id)
        self.assertTrue(candidate.cell_reference)
        self.assertIsNone(form.confirmed_by)
        self.assertFalse(workspace.approvals)
        self.assertIsNone(workspace.trade.confirmed_by)
        self.assertEqual(self.documents.download(self.case_id, self.actor, document_id=doc_id), original)

    def test_repeat_fill_protects_manual_clear_and_does_not_duplicate_source_rows(self):
        workspace, doc_id, _ = self.source(D.PURCHASE_ORDER)
        service = PrefillService(self.repo)
        workspace = service.fill(self.case_id, self.actor, D.PROFORMA_INVOICE, [doc_id])
        first = get_form(workspace, D.PROFORMA_INVOICE)
        workspace = service.fill(self.case_id, self.actor, D.PROFORMA_INVOICE, [doc_id])
        self.assertEqual(to_dict(get_form(workspace, D.PROFORMA_INVOICE)), to_dict(first))
        values = dict(first.values, buyer="")
        workspace = FormService(self.repo).save(self.case_id, self.actor, D.PROFORMA_INVOICE,
            values, [], "Synthetic user clears buyer and product rows")
        workspace = service.fill(self.case_id, self.actor, D.PROFORMA_INVOICE, [doc_id])
        saved = get_form(workspace, D.PROFORMA_INVOICE)
        self.assertEqual(saved.values["buyer"], "")
        self.assertEqual(saved.rows, [])
        self.assertIn("buyer", saved.manual_fields)
        self.assertTrue(saved.rows_manually_edited)
        self.assertEqual(len(saved.row_candidates), 2)

    def test_source_candidates_manual_edits_and_output_snapshot_remain_separate(self):
        workspace, doc_id, _ = self.source(D.PURCHASE_ORDER)
        workspace = PrefillService(self.repo).fill(self.case_id, self.actor, D.PROFORMA_INVOICE, [doc_id])
        form = get_form(workspace, D.PROFORMA_INVOICE)
        rows = deepcopy(form.rows)
        rows[0]["quantity"] = "9"
        workspace = FormService(self.repo).save(self.case_id, self.actor, D.PROFORMA_INVOICE,
            dict(form.values, buyer="Synthetic edited buyer"), rows, "Synthetic correction")
        workspace = SimpleExportService(self.repo, self.blobs).generate(
            self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        artifact = workspace.artifacts[-1]
        self.assertEqual(artifact.form_snapshot["values"]["buyer"], "Synthetic edited buyer")
        self.assertEqual(artifact.form_snapshot["rows"][0]["quantity"], "9")
        self.assertEqual(artifact.form_snapshot["row_candidates"][0]["values"]["quantity"], "4")
        content = self.documents.download(self.case_id, self.actor, artifact_id=artifact.artifact_id)
        self.assertEqual(sha256(content).hexdigest(), artifact.sha256)
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False)
        try:
            all_rows = list(workbook["文件"].values)
            self.assertTrue(any("Synthetic edited buyer" in row for row in all_rows))
            self.assertTrue(any("Synthetic first item" in row and "9" in map(str, row) for row in all_rows))
        finally:
            workbook.close()
        self.assertFalse(workspace.approvals)

    def test_manual_trade_without_original_can_generate_pi(self):
        trade = TradeData(buyer="Synthetic manual buyer", seller="Synthetic seller",
            line_items=[TradeLine("SYN-M", "Synthetic manual product", "2", "7", "pcs")])
        self.cases.update_trade(self.case_id, self.actor, trade, "Synthetic manual input")
        workspace = SimpleExportService(self.repo, self.blobs).generate(
            self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertFalse(workspace.documents)
        content = self.blobs.get(workspace.artifacts[-1].storage_path)
        workbook = load_workbook(BytesIO(content), read_only=True)
        try:
            rows = list(workbook["文件"].values)
            self.assertTrue(any("Synthetic manual buyer" in row for row in rows))
            self.assertTrue(any("Synthetic manual product" in row for row in rows))
        finally:
            workbook.close()

    def test_extreme_numeric_text_exports_without_decimal_context_overflow(self):
        FormService(self.repo).save(self.case_id, self.actor, D.PROFORMA_INVOICE, {},
            [{"description": "Synthetic extreme value", "quantity": "1e1000000", "unit": "pcs"}],
            "Synthetic value retained for human review")
        workspace = SimpleExportService(self.repo, self.blobs).generate(
            self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        workbook = load_workbook(BytesIO(self.blobs.get(workspace.artifacts[-1].storage_path)),
                                 read_only=True)
        try:
            self.assertTrue(any("1e1000000" in row for row in workbook["文件"].values))
        finally:
            workbook.close()


if __name__ == "__main__":
    unittest.main()
