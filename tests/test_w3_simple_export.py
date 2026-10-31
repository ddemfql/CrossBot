from hashlib import sha256
from datetime import datetime, timezone
from io import BytesIO
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import load_workbook

from core.errors import CrossBotError, ErrorCode
from core.models import (Actor, Case, CaseStatus, DocumentForm, DocumentType,
                         TradeData, TradeLine, Workspace)
from documents.simple_export import SimpleExportService
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository


class SimpleExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteWorkspaceRepository(":memory:")
        self.addCleanup(self.repo.close)
        self.blobs = LocalBlobStore(self.temp.name)
        self.service = SimpleExportService(self.repo, self.blobs)
        self.actor = Actor("synthetic-sales", ["sales"], authenticated=True)
        self.workspace = Workspace(case=Case(), trade=TradeData(order_no="SYN-INCOMPLETE"))
        self.repo.save(self.workspace, expected_revision=0)
        self.case_id = self.workspace.case.case_id

    def generate(self, **kwargs):
        return self.service.generate(self.case_id, self.actor,
                                     DocumentType.COMMERCIAL_INVOICE,
                                     human_confirmed=True, **kwargs)

    def test_incomplete_information_exports_with_warnings_after_one_confirmation(self):
        result = self.generate()
        item = result.artifacts[-1]
        self.assertEqual(result.trade.confirmed_by, None)
        self.assertNotEqual(result.case.status, CaseStatus.PASSED)
        self.assertFalse(result.approvals)
        self.assertTrue(any(c.status.value != "pass" for c in item.checks))
        workbook = load_workbook(BytesIO(self.blobs.get(item.storage_path)))
        self.addCleanup(workbook.close)
        self.assertEqual(workbook["文件"]["B2"].value, "是")
        self.assertEqual(workbook["文件"]["D2"].value, self.actor.user_id)
        self.assertGreater(workbook["缺失与提示"].max_row, 1)
        self.assertTrue(any(e.action == "simple_export_confirmed" for e in result.audit))
        self.assertEqual(sha256(self.blobs.get(item.storage_path)).hexdigest(), item.sha256)

    def test_confirmation_must_be_explicit_boolean(self):
        for value in (False, None, "yes", 1):
            with self.subTest(value=value), self.assertRaises(CrossBotError) as raised:
                self.service.generate(self.case_id, self.actor,
                    DocumentType.COMMERCIAL_INVOICE, human_confirmed=value)
            self.assertEqual(raised.exception.code, ErrorCode.REVIEW_REQUIRED)
        self.assertFalse(self.repo.get(self.case_id).artifacts)

    def test_ai_and_unauthenticated_actors_cannot_confirm(self):
        for actor in (Actor("ai", ["admin"], authenticated=True, kind="ai"),
                      Actor("human", ["sales"], authenticated=False)):
            with self.assertRaises(CrossBotError) as raised:
                self.service.generate(self.case_id, actor,
                    DocumentType.COMMERCIAL_INVOICE, human_confirmed=True)
            self.assertEqual(raised.exception.code, ErrorCode.FORBIDDEN)

    def test_external_documents_cannot_be_fabricated(self):
        for kind in (DocumentType.BILL_OF_LADING, DocumentType.CERTIFICATE_OF_ORIGIN,
                     DocumentType.TEST_REPORT, DocumentType.BANK_RECEIPT):
            with self.subTest(kind=kind), self.assertRaises(CrossBotError) as raised:
                self.service.generate(self.case_id, self.actor, kind, human_confirmed=True)
            self.assertEqual(raised.exception.code, ErrorCode.FILE_TYPE_UNSUPPORTED)

    def test_repeat_exports_append_versions_and_preserve_original_bytes(self):
        first = self.generate().artifacts[-1]
        original = self.blobs.get(first.storage_path)
        result = self.generate()
        second = result.artifacts[-1]
        self.assertEqual([a.version_number for a in result.artifacts], [1, 2])
        self.assertNotEqual(first.artifact_id, second.artifact_id)
        self.assertEqual(self.blobs.get(first.storage_path), original)
        self.assertEqual(second.sha256, first.sha256)
        self.assertEqual(result.artifacts[0].status, "stale")
        self.assertEqual(second.input_snapshot, result.artifacts[0].input_snapshot)

    def test_supplied_form_content_is_preserved_as_text_and_conflicts_are_visible(self):
        workspace = self.repo.get(self.case_id)
        workspace.document_forms = [DocumentForm(
            document_type=DocumentType.COMMERCIAL_INVOICE,
            values={"buyer": "=HYPERLINK(\"https://example.invalid\",\"x\")",
                    "order_no": "SYN-CONFLICT"},
            rows=[{"description": "=1+1", "quantity": "2", "unit_price": "3"}])]
        self.repo.save(workspace, expected_revision=workspace.revision)
        result = self.generate()
        item = result.artifacts[-1]
        self.assertEqual(item.form_revision, 1)
        self.assertEqual(item.form_snapshot["values"]["order_no"], "SYN-CONFLICT")
        self.assertTrue(any(c.rule_code == "FORM_CONSISTENCY" and c.status.value != "pass"
                            for c in item.checks))
        workbook = load_workbook(BytesIO(self.blobs.get(item.storage_path)), data_only=False)
        self.addCleanup(workbook.close)
        cells = [cell for sheet in workbook for row in sheet for cell in row]
        self.assertFalse(any(cell.data_type == "f" for cell in cells))
        self.assertTrue(any(cell.value == "=1+1" for cell in cells))

    def test_closed_archive_stays_read_only(self):
        workspace = self.repo.get(self.case_id)
        workspace.case.status = CaseStatus.CLOSED
        self.repo.save(workspace, expected_revision=workspace.revision)
        with self.assertRaises(CrossBotError) as raised:
            self.generate()
        self.assertEqual(raised.exception.code, ErrorCode.CONFLICT)

    def test_ci_and_pl_remain_current_when_exported_from_unchanged_inputs(self):
        self.generate()
        result = self.service.generate(self.case_id, self.actor,
            DocumentType.PACKING_LIST, human_confirmed=True)
        self.assertEqual([a.status for a in result.artifacts], ["draft", "draft"])

    def test_export_bytes_do_not_change_when_openpyxl_save_clock_changes(self):
        hashes = []
        for year in (2020, 2030):
            with patch("openpyxl.writer.excel.datetime") as clock:
                clock.timezone.utc = timezone.utc
                clock.datetime.now.return_value = datetime(year, 5, 6, 12, 30,
                                                           tzinfo=timezone.utc)
                item = self.generate().artifacts[-1]
            hashes.append(item.sha256)
            with ZipFile(BytesIO(self.blobs.get(item.storage_path))) as archive:
                properties = ElementTree.fromstring(archive.read("docProps/core.xml"))
                for name in ("created", "modified"):
                    self.assertEqual(properties.find("{http://purl.org/dc/terms/}" + name).text,
                                     "2000-01-01T00:00:00Z")
        self.assertEqual(hashes[0], hashes[1])

    def test_quotation_exports_trade_rows_with_calculated_amount(self):
        workspace = self.repo.get(self.case_id)
        workspace.trade.line_items = [TradeLine("SYN-A", "Synthetic item", "2.5", "3")]
        self.repo.save(workspace, expected_revision=workspace.revision)
        result = self.service.generate(self.case_id, self.actor,
            DocumentType.QUOTATION, human_confirmed=True)
        workbook = load_workbook(BytesIO(self.blobs.get(result.artifacts[-1].storage_path)))
        self.addCleanup(workbook.close)
        sheet = workbook["文件"]
        self.assertTrue(any([cell.value for cell in row] ==
            ["SYN-A", "Synthetic item", "2.5", "pcs", "3", "7.5"] for row in sheet))


if __name__ == "__main__":
    unittest.main()
