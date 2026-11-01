from hashlib import sha256
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import load_workbook

from core.errors import CrossBotError, ErrorCode
from core.models import (Actor, Case, CaseStatus, Document, DocumentForm, DocumentType,
                         DocumentVersion, FormFieldCandidate, FormRowCandidate,
                         TradeData, TradeLine, Workspace, to_dict)
from documents.simple_export import SimpleExportService
from case_workflow.service import CaseService
from validation.rules import DeterministicRules
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
        self.workspace = CaseService(self.repo, DeterministicRules()).create(
            self.actor, TradeData(order_no="SYN-INCOMPLETE"))
        self.case_id = self.workspace.case.case_id

    def generate(self, **kwargs):
        return self.service.generate(self.case_id, self.actor,
                                     DocumentType.COMMERCIAL_INVOICE,
                                     human_confirmed=True, **kwargs)

    def preview(self, kind=DocumentType.COMMERCIAL_INVOICE):
        return self.service.preview(self.case_id, self.actor, kind)

    def complete(self, kind=DocumentType.COMMERCIAL_INVOICE, *, buyer="Synthetic Buyer", rows=None):
        from documents.catalog import FORM_FIELDS, ROW_FIELDS
        from documents.forms import FormService
        values = {"document_no": "SYN-" + kind.value, "document_date": "2026-10-31",
                  "order_no": "SYN-INCOMPLETE", "seller": "Synthetic Seller",
                  "buyer": buyer}
        if "currency" in FORM_FIELDS[kind]:
            values["currency"] = "USD"
        rows = rows if rows is not None else [{"product_code": "SYN-A", "description": "Synthetic item",
                "quantity": "2", "unit": "pcs", "unit_price": "3", "packages": "1",
                "gross_weight_kg": "2", "net_weight_kg": "1"}]
        rows = [{name: value for name, value in row.items() if name in ROW_FIELDS[kind]} for row in rows]
        return FormService(self.repo).save(self.case_id, self.actor, kind, values, rows,
                                         "Synthetic complete manual form")

    def test_incomplete_information_is_preview_only_and_formal_generation_is_blocked(self):
        output = self.preview()
        result = self.repo.get(self.case_id)
        self.assertEqual(result.trade.confirmed_by, None)
        self.assertNotEqual(result.case.status, CaseStatus.PASSED)
        self.assertFalse(result.approvals)
        self.assertFalse(result.artifacts)
        workbook = load_workbook(BytesIO(output.content))
        self.addCleanup(workbook.close)
        self.assertEqual(workbook["文件"]["B2"].value, "否（待人工确认）")
        self.assertIsNone(workbook["文件"]["D2"].value)
        self.assertGreater(workbook["缺失与提示"].max_row, 1)
        before = to_dict(result)
        with self.assertRaises(CrossBotError) as raised:
            self.generate()
        self.assertEqual(raised.exception.code, ErrorCode.REVIEW_REQUIRED)
        self.assertTrue(raised.exception.details["checks"])
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)

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
        self.complete()
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
        report = self.service.assess(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE)
        self.assertFalse(report.ready)
        workbook = load_workbook(BytesIO(self.preview().content), data_only=False)
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
        self.complete()
        self.complete(DocumentType.PACKING_LIST)
        self.generate()
        result = self.service.generate(self.case_id, self.actor,
            DocumentType.PACKING_LIST, human_confirmed=True)
        self.assertEqual([a.status for a in result.artifacts], ["draft", "draft"])

    def test_export_bytes_do_not_change_when_openpyxl_save_clock_changes(self):
        self.complete()
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
        result = self.preview(DocumentType.QUOTATION)
        workbook = load_workbook(BytesIO(result.content))
        self.addCleanup(workbook.close)
        sheet = workbook["文件"]
        self.assertTrue(any([cell.value for cell in row] ==
            ["SYN-A", "Synthetic item", 2.5, "pcs", 3, 7.5] for row in sheet))

    def workbook(self, result):
        content = result.content if hasattr(result, "content") else self.blobs.get(result.artifacts[-1].storage_path)
        workbook = load_workbook(BytesIO(content))
        self.addCleanup(workbook.close)
        return workbook

    def test_manual_first_form_exports_numbers_and_subtotal_without_source_files(self):
        self.complete(buyer="Manual Buyer", rows=[
            {"product_code": "SYN-A", "description": "Synthetic A", "quantity": "2.5", "unit": "kg", "unit_price": "3"},
            {"product_code": "SYN-B", "description": "Synthetic B", "quantity": "1", "unit": "kg", "unit_price": "4"}])
        result = self.generate()
        self.assertFalse(result.documents)
        workbook = self.workbook(result)
        totals = list(workbook["明细合计"].values)
        self.assertIn(("产品行数量之和", 3.5, "kg", None), totals)
        self.assertIn(("产品行金额小计", 11.5, "USD", "不含另列费用或调整，不代表最终应付总额"), totals)
        self.assertTrue(any(cell.data_type == "n" and cell.value == 2.5
                            for row in workbook["文件"] for cell in row))
        self.assertTrue(any(row[2] == "人工填写" for row in workbook["字段来源"].values))

    def test_mixed_units_and_missing_amount_do_not_produce_false_totals(self):
        workspace = self.repo.get(self.case_id)
        workspace.document_forms = [DocumentForm(document_type=DocumentType.COMMERCIAL_INVOICE,
            values={"currency": "USD"}, rows=[{"description": "A", "quantity": "2", "unit": "pcs", "unit_price": "3"},
                  {"description": "B", "quantity": "1", "unit": "kg", "unit_price": ""}])]
        self.repo.save(workspace, expected_revision=workspace.revision)
        totals = list(self.workbook(self.preview())["明细合计"].values)
        self.assertEqual(totals[1][1], None)
        self.assertEqual(totals[2][1], None)

    def test_pl_line_sums_are_explicit_and_missing_values_remain_blank(self):
        workspace = self.repo.get(self.case_id)
        workspace.document_forms = [DocumentForm(document_type=DocumentType.PACKING_LIST,
            rows=[{"description": "A", "quantity": "2", "unit": "pcs", "packages": "0", "gross_weight_kg": "2.5", "net_weight_kg": ""},
                  {"description": "B", "quantity": "1", "unit": "pcs", "packages": "2", "gross_weight_kg": "3.5", "net_weight_kg": "1"}])]
        self.repo.save(workspace, expected_revision=workspace.revision)
        result = self.preview(DocumentType.PACKING_LIST)
        totals = {row[0]: row for row in self.workbook(result)["明细合计"].values}
        self.assertEqual(totals["明细行箱数之和（未去重）"][1], 2)
        self.assertEqual(totals["明细行毛重之和（未去重）"][1], 6)
        self.assertEqual(totals["明细行净重之和（未去重）"][1], None)

    def test_source_sheet_retains_candidates_positions_and_manual_override(self):
        workspace = self.repo.get(self.case_id)
        doc = Document(case_id=self.case_id, document_type=DocumentType.COMMERCIAL_INVOICE, title="synthetic.xlsx")
        version = DocumentVersion(document_id=doc.document_id, original_filename="synthetic.xlsx")
        doc.versions.append(version)
        doc.current_version_id = version.version_id
        workspace.documents.append(doc)
        workspace.document_forms = [DocumentForm(document_type=DocumentType.COMMERCIAL_INVOICE,
            values={"buyer": "Source buyer"},
            field_candidates={"buyer": [FormFieldCandidate("Source buyer", doc.document_id,
                version.version_id, field_name="customer_name", sheet_name="Sheet A", cell_reference="B3")]},
            row_candidates=[FormRowCandidate({"quantity": "3"}, doc.document_id,
                version.version_id, 0, 4, "Sheet A", cell_references={"quantity": "C4"})])]
        self.repo.save(workspace, expected_revision=workspace.revision)
        from documents.forms import FormService
        FormService(self.repo).save(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE,
            {"buyer": "Corrected buyer"}, [], "Synthetic manual correction")
        rows = list(self.workbook(self.preview())["字段来源"].values)
        buyer = next(row for row in rows if row[0] == "buyer")
        self.assertEqual(buyer[1:4], ("Corrected buyer", "人工填写", None))
        self.assertEqual(buyer[10], self.actor.user_id)
        candidate = next(row for row in rows if row[0] == "候选字段 buyer")
        self.assertEqual(candidate[3:6], ("Source buyer", "synthetic.xlsx", "v1"))
        self.assertEqual(candidate[8:10], ("Sheet A", "B3"))
        self.assertTrue(any(row[0] == "来源明细行 4" and 'C4' in row[9] for row in rows))

    def test_edit_form_exports_newest_values_and_preserves_previous_output(self):
        self.complete(buyer="First buyer")
        first = self.generate().artifacts[-1]
        self.complete(buyer="Newest buyer")
        result = self.generate()
        latest = result.artifacts[-1]
        self.assertNotEqual(first.sha256, latest.sha256)
        self.assertEqual(latest.form_snapshot["values"]["buyer"], "Newest buyer")
        self.assertTrue(any(cell.value == "Newest buyer" for row in self.workbook(result)["文件"] for cell in row))

    def test_print_layout_and_precision_safe_numeric_cells(self):
        workspace = self.repo.get(self.case_id)
        workspace.document_forms = [DocumentForm(document_type=DocumentType.COMMERCIAL_INVOICE,
            rows=[{"description": "Synthetic", "quantity": "1234567890123456", "unit": "pcs", "unit_price": "3"}])]
        self.repo.save(workspace, expected_revision=workspace.revision)
        sheet = self.workbook(self.preview())["文件"]
        self.assertEqual(sheet.page_setup.fitToWidth, 1)
        self.assertEqual(sheet.page_setup.orientation, "landscape")
        self.assertIsNotNone(sheet.print_title_rows)
        self.assertIsNotNone(sheet.freeze_panes)
        value = next(cell for row in sheet for cell in row if cell.value == "1234567890123456")
        self.assertEqual(value.data_type, "s")

    def test_explicit_manual_row_deletion_does_not_restore_trade_lines(self):
        workspace = self.repo.get(self.case_id)
        workspace.trade.line_items = [TradeLine("SYN-A", "Should stay deleted", "1", "2")]
        workspace.document_forms = [DocumentForm(document_type=DocumentType.COMMERCIAL_INVOICE,
                                                rows=[], rows_manually_edited=True)]
        self.repo.save(workspace, expected_revision=workspace.revision)
        self.assertFalse(any(cell.value == "Should stay deleted"
                             for row in self.workbook(self.preview())["文件"] for cell in row))

    def test_extreme_decimal_inputs_stay_text_and_uncomputable_total_stays_blank(self):
        workspace = self.repo.get(self.case_id)
        workspace.document_forms = [DocumentForm(document_type=DocumentType.COMMERCIAL_INVOICE,
            rows=[{"description": "A", "quantity": "1e1000000", "unit_price": "2", "unit": "pcs"},
                  {"description": "B", "quantity": "-1e1000000", "unit_price": "2", "unit": "pcs"}])]
        self.repo.save(workspace, expected_revision=workspace.revision)
        workbook = self.workbook(self.preview())
        for text in ("1e1000000", "-1e1000000"):
            matching = [cell for row in workbook["文件"] for cell in row if cell.value == text]
            self.assertTrue(matching)
            self.assertEqual(matching[0].data_type, "s")
        totals = {row[0]: row for row in workbook["明细合计"].values}
        self.assertIsNone(totals["产品行金额小计"][1])

    def test_preview_sourced_form_is_unconfirmed_and_does_not_change_repository_or_blobs(self):
        workspace = self.repo.get(self.case_id)
        doc = Document(case_id=self.case_id, document_type=DocumentType.COMMERCIAL_INVOICE,
                       title="synthetic-source.xlsx")
        original = b"synthetic-original-preserved"
        version = DocumentVersion(document_id=doc.document_id, original_filename=doc.title,
                                  storage_path=self.blobs.put(original), sha256=sha256(original).hexdigest())
        doc.versions.append(version)
        doc.current_version_id = version.version_id
        workspace.documents.append(doc)
        workspace.document_forms = [DocumentForm(document_type=DocumentType.COMMERCIAL_INVOICE,
            values={"buyer": "Synthetic source buyer"},
            auto_values={"buyer": "Synthetic source buyer"}, source_document_ids=[doc.document_id],
            field_candidates={"buyer": [FormFieldCandidate("Synthetic source buyer", doc.document_id,
                version.version_id, field_name="customer_name", sheet_name="Source", cell_reference="B2")]})]
        self.repo.save(workspace, expected_revision=workspace.revision)
        before = to_dict(self.repo.get(self.case_id))
        blobs_before = sorted(p.name for p in Path(self.temp.name).iterdir())
        output = self.service.preview(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), blobs_before)
        self.assertEqual(self.blobs.get(version.storage_path), original)
        workbook = load_workbook(BytesIO(output.content))
        self.addCleanup(workbook.close)
        self.assertEqual(workbook["文件"]["B2"].value, "否（待人工确认）")
        self.assertIsNone(workbook["文件"]["D2"].value)
        self.assertIn("未人工确认预览", workbook["文件"]["B3"].value)
        trace = dict(workbook["生成记录"].values)
        self.assertIsNone(trace["人工确认人"])
        self.assertEqual(trace["预览申请账号"], self.actor.user_id)
        origins = list(workbook["字段来源"].values)
        self.assertTrue(any(row[0] == "buyer" and row[1] == "Synthetic source buyer" for row in origins))
        self.assertTrue(any("B2" in str(row) for row in origins))
        self.assertEqual(output.extension, "xlsx")
        self.assertEqual(output.media_type, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_preview_is_reproducible_and_leaves_prior_confirmed_outputs_untouched(self):
        self.complete()
        confirmed = self.generate().artifacts[-1]
        before = to_dict(self.repo.get(self.case_id))
        first = self.service.preview(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE)
        second = self.service.preview(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE)
        self.assertEqual(first.content, second.content)
        self.assertNotEqual(sha256(first.content).hexdigest(), confirmed.sha256)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        self.assertEqual(sha256(self.blobs.get(confirmed.storage_path)).hexdigest(), confirmed.sha256)
        with self.assertRaises(CrossBotError) as raised:
            self.service.generate(self.case_id, self.actor,
                                  DocumentType.COMMERCIAL_INVOICE, human_confirmed=False)
        self.assertEqual(raised.exception.code, ErrorCode.REVIEW_REQUIRED)

    def test_preview_denies_external_invalid_and_unauthorized_requests_without_writes(self):
        before = to_dict(self.repo.get(self.case_id))
        for kind in (DocumentType.BILL_OF_LADING, DocumentType.TEST_REPORT):
            with self.assertRaises(CrossBotError) as raised:
                self.service.preview(self.case_id, self.actor, kind)
            self.assertEqual(raised.exception.code, ErrorCode.FILE_TYPE_UNSUPPORTED)
        with self.assertRaises(CrossBotError):
            self.service.preview(self.case_id, self.actor, "invalid-document-type")
        for actor in (Actor("guest", ["sales"], authenticated=False),
                      Actor("unknown", ["unknown"], authenticated=True),
                      Actor("ai", ["admin"], authenticated=True, kind="ai")):
            with self.assertRaises(CrossBotError) as raised:
                self.service.preview(self.case_id, actor, DocumentType.COMMERCIAL_INVOICE)
            self.assertEqual(raised.exception.code, ErrorCode.FORBIDDEN)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)

    def test_assessment_is_read_only_and_successful_generation_binds_strict_report(self):
        self.complete()
        before = to_dict(self.repo.get(self.case_id))
        report = self.service.assess(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE)
        self.assertFalse(report.ready)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        result = self.generate(expected_review_digest=report.input_digest)
        artifact = result.artifacts[-1]
        self.assertEqual(artifact.rule_version, report.rule_version)
        self.assertTrue(all(check.status.value == "pass" for check in artifact.checks if check.required))
        event = next(e for e in result.audit if e.action == "simple_export_confirmed")
        self.assertTrue(event.after["strict_review"])
        self.assertEqual(event.after["review_input_digest"], report.input_digest)
        self.assertEqual(event.after["sha256"], artifact.sha256)
        self.assertFalse(result.approvals)
        self.assertIsNone(result.trade.confirmed_by)
        self.assertIsNone(result.document_forms[0].confirmed_by)
        self.assertNotEqual(result.case.status, CaseStatus.PASSED)

    def test_stale_review_digest_blocks_without_writing_any_output(self):
        self.complete()
        report = self.service.assess(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE)
        self.complete(buyer="Revised Buyer")
        before = to_dict(self.repo.get(self.case_id))
        files = sorted(p.name for p in Path(self.temp.name).iterdir())
        with self.assertRaises(CrossBotError) as raised:
            self.generate(expected_review_digest=report.input_digest)
        self.assertEqual(raised.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), files)

    def test_conflicts_block_formal_generation_before_output_writes(self):
        self.complete(rows=[{"product_code": "SYN-A", "description": "Synthetic item",
            "quantity": "2", "unit": "pcs", "unit_price": "3", "amount": "999"}])
        before = to_dict(self.repo.get(self.case_id))
        with self.assertRaises(CrossBotError) as raised:
            self.generate()
        self.assertEqual(raised.exception.code, ErrorCode.CROSS_CHECK_FAILED)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        self.assertFalse(list(Path(self.temp.name).iterdir()))

    def test_binary_metadata_tampering_and_missing_blob_block_without_writes(self):
        self.complete()
        original = b"synthetic linked source"
        key = self.blobs.put(original)
        for failure in ("metadata", "content", "missing"):
            with self.subTest(failure=failure):
                workspace = self.repo.get(self.case_id)
                doc = Document(case_id=self.case_id, document_type=DocumentType.COMMERCIAL_INVOICE,
                               title="synthetic.txt")
                version = DocumentVersion(document_id=doc.document_id, storage_path=key,
                    sha256="0" * 64 if failure == "metadata" else sha256(original).hexdigest(),
                    original_filename="synthetic.txt")
                doc.versions.append(version)
                doc.current_version_id = version.version_id
                workspace.documents = [doc]
                workspace.document_forms[0].source_document_ids = [doc.document_id]
                self.repo.save(workspace, expected_revision=workspace.revision)
                if failure == "missing":
                    (Path(self.temp.name) / key).unlink()
                elif failure == "content":
                    (Path(self.temp.name) / key).write_bytes(b"synthetic corrupted source")
                before = to_dict(self.repo.get(self.case_id))
                files = sorted(p.name for p in Path(self.temp.name).iterdir())
                report = self.service.assess(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE,
                                             human_confirmed=True)
                self.assertTrue(any(c.rule_code == "SOURCE_INTEGRITY" and c.status.value == "fail"
                                    and c.left_document_id == doc.document_id for c in report.checks))
                with self.assertRaises(CrossBotError) as raised:
                    self.generate()
                self.assertEqual(raised.exception.code, ErrorCode.CROSS_CHECK_FAILED)
                self.assertTrue(self.preview().content)
                self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
                self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), files)

    def test_unrelated_photo_blob_is_not_required_for_manual_document_export(self):
        self.complete()
        workspace = self.repo.get(self.case_id)
        photo = Document(case_id=self.case_id, document_type=DocumentType.PHOTO, title="synthetic-photo.png")
        version = DocumentVersion(document_id=photo.document_id, original_filename=photo.title,
                                  storage_path="0" * 64, sha256="0" * 64)
        photo.versions.append(version)
        photo.current_version_id = version.version_id
        workspace.documents.append(photo)
        self.repo.save(workspace, expected_revision=workspace.revision)
        report = self.service.assess(self.case_id, self.actor, DocumentType.COMMERCIAL_INVOICE,
                                     human_confirmed=True)
        self.assertTrue(report.ready)
        self.assertFalse(any(c.rule_code == "SOURCE_INTEGRITY" and c.left_document_id == photo.document_id
                             for c in report.checks))
        self.assertTrue(self.generate().artifacts)


if __name__ == "__main__":
    unittest.main()

