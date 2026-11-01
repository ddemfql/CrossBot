"""Synthetic ZIP custody, candidate extraction and historical import checks."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from core.errors import CrossBotError
from core.models import Actor, CaseStatus, DocumentType as Type, FieldValueStatus, TradeMilestone
from case_workflow.importer import ArchiveImporter
from documents.intake import classify_source, collect_image_source, collect_source
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository


def archive_bytes(files):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as package:
        for name, content in files:
            package.writestr(name, content)
    return output.getvalue()


def workbook_bytes():
    from openpyxl import Workbook
    book = Workbook()
    book.active.title = "Invoice"
    for row in (("Order No", "SYN-12"), ("Buyer", "Synthetic Buyer"),
                ("Product Code", "Description", "Quantity", "Unit Price"),
                ("SKU-1", "Synthetic lamp", 2, "12.50")):
        book.active.append(row)
    sheet = book.create_sheet("Packing")
    sheet.append(["Gross Weight", 8])
    sheet.append(["Net Weight", 6])
    sheet.append(["Unmapped source", "keep me", "=A1"])
    result = BytesIO()
    book.save(result)
    book.close()
    return result.getvalue()


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repository = SQLiteWorkspaceRepository(root / "workspace.db")
        self.blobs = LocalBlobStore(root / "blobs")
        self.importer = ArchiveImporter(self.repository, self.blobs)
        self.admin = Actor("synthetic-admin", ["admin"], authenticated=True)

    def tearDown(self):
        self.repository.close()
        self.temp.cleanup()

    def test_workbook_preserves_all_sheets_and_unmapped_cells_without_confirming(self):
        text, tables, fields, warnings = collect_source(workbook_bytes(), ".xlsx")
        self.assertEqual([table.name for table in tables], ["Invoice", "Packing"])
        self.assertIn("keep me", text)
        self.assertIn("=A1", text)
        self.assertEqual(tables[1].rows[0], ["Gross Weight", "8", ""])
        self.assertEqual(next(item.value for item in fields if item.name == "order_no"), "SYN-12")
        self.assertTrue(all(item.status is FieldValueStatus.CANDIDATE and not item.confirmed_by for item in fields))
        self.assertTrue(warnings)

    def test_pdf_preserves_each_page_and_ocr_only_fills_scanned_pages(self):
        from pypdf import PdfReader, PdfWriter
        from documents.ocr import PDFOCRResult, RecognizedLine
        from test_w3_documents import pdf_bytes
        writer = PdfWriter()
        writer.add_page(PdfReader(BytesIO(pdf_bytes(["Order No: SYN-PDF"]))).pages[0])
        writer.add_blank_page(width=300, height=400)
        output = BytesIO()
        writer.write(output)
        result = PDFOCRResult((RecognizedLine("Consignee: Synthetic Receiver", 0.9,
            (0, 0, 100, 20), 2),), (1,))
        with patch("documents.ocr.OCRBackend.recognize_pdf", return_value=result):
            text, tables, fields, warnings = collect_source(output.getvalue(), ".pdf")
        self.assertIn("[Page 1]", text)
        self.assertIn("[Page 2]", text)
        self.assertEqual({field.name: field.page_number for field in fields},
                         {"order_no": 1, "consignee": 2})
        self.assertTrue(any("OCR" in warning for warning in warnings))

    def test_legacy_xls_keeps_all_sheets(self):
        import xlwt
        book = xlwt.Workbook()
        for name, value in (("First", "SYN-A"), ("Second", "SYN-B")):
            sheet = book.add_sheet(name)
            sheet.write(0, 0, "Unmapped source")
            sheet.write(0, 1, value)
        output = BytesIO()
        book.save(output)
        text, tables, fields, warnings = collect_source(output.getvalue(), ".xls")
        self.assertEqual([table.name for table in tables], ["First", "Second"])
        self.assertIn("SYN-B", text)
        self.assertFalse(fields)

    def test_docx_csv_and_gbk_text_preserve_original_structure(self):
        from docx import Document
        word = Document()
        word.add_paragraph("Bill of Lading No: SYN-BL-1")
        table = word.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Consignee", "Synthetic Buyer"
        table.cell(1, 0).text, table.cell(1, 1).text = "Unknown", "Unmapped value"
        child = table.cell(1, 1).add_table(rows=1, cols=2)
        child.cell(0, 0).text, child.cell(0, 1).text = "Nested source", "Keep nested data"
        output = BytesIO()
        word.save(output)
        text, tables, fields, warnings = collect_source(output.getvalue(), ".docx")
        self.assertEqual(tables[0].rows[1][0], "Unknown")
        self.assertEqual(tables[0].rows[1][1].rstrip(), "Unmapped value")
        self.assertEqual(tables[1].rows[0], ["Nested source", "Keep nested data"])
        self.assertEqual({field.name for field in fields}, {"consignee", "bill_of_lading_no"})
        text, tables, fields, warnings = collect_source("订单号,SYN-CSV\n客户,虚构客户".encode("gb18030"), ".csv")
        self.assertEqual(tables[0].rows[1], ["客户", "虚构客户"])
        self.assertEqual(next(field.value for field in fields if field.name == "customer_name"), "虚构客户")

    def test_conflicting_candidates_are_not_silently_chosen(self):
        text, tables, fields, warnings = collect_source(b"Order No: SYN-1\nOrder No: SYN-2", ".txt")
        self.assertFalse(fields)
        self.assertTrue(any("多个" in warning for warning in warnings))

    def test_corrupt_unsupported_and_photos_keep_a_manual_path(self):
        for extension, content in ((".xlsx", b"broken"), (".bin", b"original"), (".jpg", b"photo bytes")):
            with self.subTest(extension=extension):
                text, tables, fields, warnings = collect_source(content, extension)
                self.assertFalse(fields)
                self.assertTrue(warnings)
        with patch("documents.ocr.OCRBackend.recognize_image", side_effect=AssertionError("photo must not be OCRed")):
            collect_source(b"photo", ".jpg")

    def test_opt_in_image_ocr_transcribes_real_lines_and_adjacent_fields_only(self):
        from documents.ocr import RecognizedLine
        lines = [RecognizedLine("Buyer", 0.98, (0, 0, 50, 20), 1),
                 RecognizedLine("Synthetic Receiver", 0.91, (70, 0, 200, 20), 1),
                 RecognizedLine("Keep this unlabelled packaging text", 0.87, (0, 50, 300, 70), 1)]
        with patch("documents.ocr.OCRBackend.recognize_image", return_value=lines) as recognize:
            text, tables, fields, warnings = collect_source(b"synthetic pixels", ".jpg", ocr_images=True)
        recognize.assert_called_once_with(b"synthetic pixels", page_number=1)
        self.assertIn("Keep this unlabelled packaging text", text)
        self.assertEqual([(field.name, field.value) for field in fields], [("customer_name", "Synthetic Receiver")])
        self.assertEqual(fields[0].confidence, 0.91)
        self.assertEqual(fields[0].page_number, 1)
        self.assertFalse(fields[0].confirmed_by)
        self.assertTrue(any("未虚构" in warning for warning in warnings))

    def test_optional_image_ocr_failure_or_unknown_text_does_not_fill_business_values(self):
        from documents.ocr import RecognizedLine
        with patch("documents.ocr.OCRBackend.recognize_image", side_effect=RuntimeError("unavailable")):
            text, tables, fields, warnings = collect_image_source(b"synthetic image")
        self.assertFalse(fields)
        self.assertTrue(any("未完成" in warning for warning in warnings))
        lines = [RecognizedLine("Unlabelled product claim", 0.9, (0, 0, 200, 20), 1)]
        with patch("documents.ocr.OCRBackend.recognize_image", return_value=lines):
            text, tables, fields, warnings = collect_image_source(b"synthetic image")
        self.assertIn("Unlabelled product claim", text)
        self.assertFalse(fields)

    def test_classification_distinguishes_factory_po_isf_forms_and_combined_files(self):
        cases = [
            ("工厂/PO-123.xlsx", Type.PRODUCTION_ORDER, "seller"),
            ("客户/PO-123.pdf", Type.PURCHASE_ORDER, "external"),
            ("出货照片/PI-photo.jpg", Type.PHOTO, "seller"),
            ("包装设计/PO-label.pdf", Type.PACKAGING_ARTWORK, "seller"),
            ("ISF Form.xlsx", Type.ISF_WORKSHEET, "seller"),
            ("ISF receipt.pdf", Type.ISF_RECEIPT, "external"),
            ("Appendix J.docx", Type.COMPLIANCE_DRAFT, "seller"),
            ("客户模板/Appendix N.docx", Type.COMPLIANCE_DRAFT, "external"),
            ("CI+PL-SYN.xlsx", Type.COMBINED_INVOICE_PACKING_LIST, "seller"),
            ("出货相片/IMG-001.jpg", Type.PHOTO, "seller"),
            ("出货图片/IMG-002.jpg", Type.PHOTO, "seller"),
            ("验货照片/SKU/IMG-003.jpg", Type.PHOTO, "seller"),
            ("验货相片/SKU/IMG-004.jpg", Type.PHOTO, "seller"),
            ("Inspection Photos/SKU/IMG-005.jpg", Type.PHOTO, "seller"),
            ("合同.pdf", Type.SALES_CONTRACT, "seller"),
            ("supplier contract.pdf", Type.SALES_CONTRACT, "seller"),
            ("CFT_Synthetic product.xlsx", Type.PRODUCT_SPEC, "seller"),
            ("AW- Synthetic label.png", Type.PACKAGING_ARTWORK, "seller"),
            ("贴纸1配方.docx", Type.PACKAGING_ARTWORK, "seller"),
            ("配方内容表.docx", Type.PRODUCT_SPEC, "seller"),
            ("AWB-123.pdf", Type.AIR_WAYBILL, "external"),
            ("SKU9-SINGLE.pdf", Type.OTHER, "external"),
            ("SKU9-2PACK.pdf", Type.OTHER, "external"),
        ]
        for path, expected, responsibility in cases:
            with self.subTest(path=path):
                self.assertEqual(classify_source(path)[:2], (expected, responsibility))

    def test_historical_folder_aliases_remain_unverified_declarations(self):
        data = archive_bytes([("已经发货等待收钱/验货照片/SYN/IMG-1.jpg", b"synthetic photo")])
        ws = self.importer.import_zip(data, "synthetic-history.zip", self.admin)
        self.assertIn("已发货", ws.import_context.reported_trade_state)
        self.assertIn("未核验", ws.import_context.reported_trade_state)
        self.assertIn("待收款", ws.import_context.reported_receivable_state)
        self.assertIn("未核验", ws.import_context.reported_receivable_state)
        self.assertEqual(ws.documents[0].document_type, Type.PHOTO)
        self.assertEqual(ws.documents[0].responsibility, "seller")
        self.assertEqual(ws.case.status, CaseStatus.DRAFT)
        self.assertEqual(ws.milestone, TradeMilestone.ORDER)
        self.assertFalse(ws.approvals)
        self.assertFalse(ws.payments)

    def test_zip_roundtrip_keeps_duplicates_forms_and_reported_state_unconfirmed(self):
        originals = [("已发货待收钱/CI+PL-SYN.xlsx", workbook_bytes()),
                     ("参考/a.txt", b"Order No: SYN-12"),
                     ("备份/b.txt", b"Order No: SYN-12"),
                     ("照片/unknown.bin", b"unknown bytes")]
        data = archive_bytes(originals)
        ws = self.importer.import_zip(data, "synthetic.zip", self.admin)
        saved = self.repository.get(ws.case.case_id)
        self.assertEqual(len(saved.documents), len(originals))
        self.assertEqual([doc.versions[0].original_path for doc in saved.documents], [name for name, _ in originals])
        for document, (_, content) in zip(saved.documents, originals):
            self.assertEqual(self.blobs.get(document.versions[0].storage_path), content)
        self.assertNotEqual(saved.documents[1].document_id, saved.documents[2].document_id)
        self.assertEqual(saved.documents[1].versions[0].sha256, saved.documents[2].versions[0].sha256)
        self.assertEqual(saved.case.status, CaseStatus.DRAFT)
        self.assertEqual(saved.milestone, TradeMilestone.ORDER)
        self.assertFalse(saved.trade.line_items)
        self.assertFalse(saved.trade.confirmed_by)
        self.assertFalse(saved.approvals)
        self.assertFalse(saved.payments)
        self.assertTrue(saved.import_context.reported_trade_state)
        self.assertTrue(saved.import_context.reported_receivable_state)
        self.assertEqual(saved.import_context.archive_sha256, sha256(data).hexdigest())
        forms = {form.document_type: form for form in saved.document_forms}
        for kind in (Type.COMMERCIAL_INVOICE, Type.PACKING_LIST):
            self.assertIn(saved.documents[0].document_id, forms[kind].source_document_ids)
            self.assertIsNone(forms[kind].confirmed_by)
            self.assertEqual(forms[kind].rows[0]["description"], "Synthetic lamp")
            self.assertEqual(forms[kind].rows[0]["quantity"], "2")
        self.assertGreater(len(forms), 3)
        self.assertEqual(len(self.repository.history(saved.case.case_id)), 1)
        second = self.importer.import_zip(data, "renamed.zip", self.admin)
        self.assertEqual(second.case.case_id, saved.case.case_id)
        self.assertEqual(len(self.repository.list()), 1)
        # Simulate a racing importer whose initial list predates the winner.
        with patch.object(self.repository, "list", return_value=[]):
            raced = self.importer.import_zip(data, "renamed.zip", self.admin)
        self.assertEqual(raced.case.case_id, saved.case.case_id)
        self.assertEqual(len(self.repository.history(saved.case.case_id)), 1)

    def test_import_requires_supported_authenticated_human(self):
        data = archive_bytes([("x.txt", b"original")])
        for actor in (Actor("outsider", ["unknown"], authenticated=True), Actor("fake", ["admin"])):
            with self.subTest(actor=actor.user_id), self.assertRaises(CrossBotError):
                self.importer.import_zip(data, "synthetic.zip", actor)
        self.assertFalse(self.repository.list())

    def test_traversal_symlink_and_size_fail_before_persistence(self):
        for path in ("../escape.txt", "/absolute.txt", "C:/escape.txt", "folder/../../escape.txt", "folder\\..\\escape.txt"):
            with self.subTest(path=path), self.assertRaises(CrossBotError):
                self.importer.import_zip(archive_bytes([(path, b"original")]), "synthetic.zip", self.admin)
        link = ZipInfo("symlink")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(CrossBotError):
            self.importer.import_zip(archive_bytes([(link, b"../target")]), "synthetic.zip", self.admin)
        with patch("case_workflow.importer.MAX_FILE_BYTES", 3), self.assertRaises(CrossBotError):
            self.importer.import_zip(archive_bytes([("large.txt", b"1234")]), "synthetic.zip", self.admin)
        self.assertFalse(self.repository.list())

    def test_legacy_gbk_zip_names_are_recovered_without_disk_extraction(self):
        # Build valid UTF-8 ZIP, then replace both filename headers with equal-
        # length CP936 bytes and clear UTF-8 flags to model legacy Windows ZIPs.
        original = "中文.txt"
        encoded = original.encode("gbk")
        placeholder = "x" * (len(encoded) - 4) + ".txt"
        data = bytearray(archive_bytes([(placeholder, b"Order No: SYN-GBK")]))
        local = data.index(b"PK\x03\x04")
        central = data.index(b"PK\x01\x02")
        data[local + 30:local + 30 + len(encoded)] = encoded
        data[central + 46:central + 46 + len(encoded)] = encoded
        struct.pack_into("<H", data, local + 6, 0)
        struct.pack_into("<H", data, central + 8, 0)
        ws = self.importer.import_zip(bytes(data), "synthetic.zip", self.admin)
        self.assertEqual(ws.documents[0].versions[0].original_path, original)


if __name__ == "__main__":
    unittest.main()

