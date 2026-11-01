"""Synthetic fixtures only: no reference transaction files are used."""
from dataclasses import replace
from datetime import date
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from core.errors import CrossBotError, ErrorCode
from core.models import (Actor, Case, CaseStatus, CrossCheckStatus, DocumentStatus,
    DocumentType as Type, FieldValueStatus, TradeData, TradeLine, Workspace, utc_now)
from documents.parsers import FixedTemplateParser
from documents.rendering import HTMLRenderer
from documents.service import DocumentService, MAX_UPLOAD_BYTES
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository


def workbook_bytes(rows):
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.active.title = "Synthetic"
    for row in rows:
        workbook.active.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def pdf_bytes(lines):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    text = "BT /F1 12 Tf 40 750 Td " + " ".join(
        "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj 0 -18 Td"
        for line in lines) + " ET"
    stream.set_data(text.encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = SQLiteWorkspaceRepository(self.root / "workspace.sqlite")
        self.blobs = LocalBlobStore(self.root / "blobs")
        self.service = DocumentService(self.repository, self.blobs)
        self.actor = Actor("synthetic-document-user", ["admin"], authenticated=True)
        self.trade = TradeData(order_no="SYNTHETIC-ORDER", seller="Synthetic Seller",
            buyer="Synthetic Buyer", destination_country="DE", currency="USD", incoterm="FCA Testport",
            payment_terms="30 days", transport_mode="sea", line_items=[TradeLine(
                "SYN-1", "Synthetic widget", "2.5", "10.01", packages=1,
                gross_weight_kg="3", net_weight_kg="2")], confirmed_by=self.actor.user_id,
            confirmed_at=utc_now())
        workspace = Workspace(Case(case_number="SYN-CASE", order_no=self.trade.order_no,
            customer_name=self.trade.buyer, destination_country="DE", currency="USD", incoterm=self.trade.incoterm),
            trade=self.trade, required_document_types=[Type.COMMERCIAL_INVOICE, Type.PACKING_LIST])
        self.case_id = workspace.case.case_id
        self.repository.save(workspace, expected_revision=0)

    def tearDown(self):
        self.repository.close()
        self.temp.cleanup()

    def upload(self, filename="synthetic.xlsx", content=None, kind=Type.PACKING_LIST, **kwargs):
        return self.service.upload(self.case_id, self.actor, filename,
            content if content is not None else workbook_bytes([("quantity", 2.5)]), kind, **kwargs)

    def test_reupload_preserves_original_and_creates_new_version(self):
        first = self.upload(content=b"old synthetic bytes")
        document = first.documents[0]
        second = self.upload(content=b"new synthetic bytes", document_id=document.document_id)
        versions = second.documents[0].versions
        self.assertEqual([v.version_number for v in versions], [1, 2])
        self.assertEqual(versions[0].status, DocumentStatus.SUPERSEDED)
        self.assertEqual(self.blobs.get(versions[0].storage_path), b"old synthetic bytes")
        self.assertEqual(self.blobs.get(versions[1].storage_path), b"new synthetic bytes")
        self.assertEqual(versions[1].size_bytes, len(b"new synthetic bytes"))
        self.assertEqual(versions[1].uploaded_by, self.actor.user_id)
        self.assertEqual(self.service.download(self.case_id, self.actor,
            document_id=document.document_id, version_id=versions[0].version_id), b"old synthetic bytes")

    def test_admin_only_removal_hides_file_and_retains_original_and_history(self):
        workspace = self.upload(filename="synthetic.pdf", content=b"synthetic source bytes")
        document = workspace.documents[0]
        version = document.versions[0]
        removed = self.service.delete_document(self.case_id, document.document_id,
            self.actor, "Synthetic admin retention test")
        deleted = removed.documents[0]
        self.assertEqual(deleted.deleted_by, self.actor.user_id)
        self.assertEqual(deleted.deletion_reason, "Synthetic admin retention test")
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(len(deleted.versions), 1)
        self.assertEqual(self.blobs.get(version.storage_path), b"synthetic source bytes")
        self.assertEqual(removed.audit[-1].action, "document_removed_by_admin")
        with self.assertRaises(CrossBotError) as caught:
            self.service.download(self.case_id, self.actor, document_id=document.document_id)
        self.assertEqual(caught.exception.code, ErrorCode.NOT_FOUND)

    def test_unsupported_role_cannot_remove_document(self):
        workspace = self.upload(filename="synthetic.pdf", content=b"synthetic source bytes")
        document_id = workspace.documents[0].document_id
        from core.models import Actor
        with self.assertRaises(CrossBotError) as caught:
            self.service.delete_document(self.case_id, document_id,
                Actor("synthetic-outsider", ["unknown"], authenticated=True), "Not authorized")
        self.assertEqual(caught.exception.code, ErrorCode.FORBIDDEN)
        saved = self.repository.get(self.case_id)
        self.assertIsNone(saved.documents[0].deleted_at)
        self.assertEqual(saved.audit[-1].action, "access_denied")

    def test_duplicate_upload_still_has_independent_version_history(self):
        first = self.upload(content=b"same")
        document_id = first.documents[0].document_id
        second = self.upload(content=b"same", document_id=document_id)
        self.assertEqual(len(second.documents[0].versions), 2)
        self.assertNotEqual(*[v.version_id for v in second.documents[0].versions])
        self.assertEqual(*[v.sha256 for v in second.documents[0].versions])

    def test_upload_validation_and_expiry_metadata(self):
        for filename in ("../synthetic.pdf", "C:\\synthetic.pdf", "bad\x00.pdf", "evil.exe"):
            with self.subTest(filename=filename), self.assertRaises(CrossBotError):
                self.upload(filename=filename)
        with self.assertRaises(CrossBotError) as caught:
            self.upload(content=b"x" * (MAX_UPLOAD_BYTES + 1))
        self.assertEqual(caught.exception.code, ErrorCode.FILE_TOO_LARGE)
        with self.assertRaises(CrossBotError):
            self.upload(expires_on="invalid-date")
        workspace = self.upload(source_party="Synthetic laboratory", expires_on="2030-01-01")
        version = workspace.documents[0].versions[0]
        self.assertEqual(version.source_party, "Synthetic laboratory")
        self.assertEqual(version.expires_on, "2030-01-01")

    def test_fixed_excel_parser_keeps_cell_provenance_and_machine_candidate(self):
        workspace = self.upload(content=workbook_bytes([("order_no", "SYNTHETIC-ORDER"),
            ("quantity", 2.5), ("invoice_total", "25.025"), ("description", "Synthetic widget")]))
        document_id = workspace.documents[0].document_id
        parsed = self.service.parse(self.case_id, document_id, self.actor)
        version = parsed.documents[0].versions[0]
        self.assertEqual(version.status, DocumentStatus.PARSED)
        self.assertEqual(version.fields[1].value, "2.5")
        self.assertEqual(version.fields[1].sheet_name, "Synthetic")
        self.assertEqual(version.fields[1].cell_reference, "B2")
        self.assertEqual(version.fields[1].status, FieldValueStatus.CANDIDATE)
        self.assertIsNone(version.fields[1].confirmed_value)
        self.assertEqual([f.name for f in version.fields], ["order_no", "quantity", "total_amount", "product_name"])
        self.assertEqual(parsed.review_tasks[0].document_id, document_id)
        self.assertEqual(len(parsed.review_tasks), 1)
        repeated = self.service.parse(self.case_id, document_id, self.actor)
        self.assertEqual(repeated.revision, parsed.revision)
        self.assertEqual(len(repeated.review_tasks), 1)

    def test_text_pdf_parser_records_page(self):
        fields = FixedTemplateParser().parse(pdf_bytes([
            "order_no: SYNTHETIC-ORDER", "quantity: 2.5", "customer_name: Synthetic Buyer"]), ".pdf")
        self.assertEqual(fields[0].value, "SYNTHETIC-ORDER")
        self.assertEqual(fields[1].page_number, 1)
        self.assertIsNone(fields[1].sheet_name)

    def test_parse_failure_is_persisted_and_original_can_still_be_downloaded(self):
        workspace = self.upload(filename="broken.pdf", content=b"synthetic broken pdf")
        document_id = workspace.documents[0].document_id
        result = self.service.parse(self.case_id, document_id, self.actor)
        version = result.documents[0].versions[0]
        self.assertEqual(version.status, DocumentStatus.PARSE_FAILED)
        self.assertTrue(version.parse_error)
        self.assertEqual(self.repository.get(self.case_id).documents[0].status, DocumentStatus.PARSE_FAILED)
        self.assertEqual(self.service.download(self.case_id, self.actor, document_id=document_id), b"synthetic broken pdf")
        self.assertIn("parse_failed", [e.action for e in result.audit])

    def test_generic_attachments_have_custody_without_parser(self):
        workspace = self.upload(filename="synthetic.csv", content=b"synthetic generic attachment", kind=Type.PRODUCT_SPEC)
        document_id = workspace.documents[0].document_id
        with self.assertRaises(CrossBotError) as caught:
            self.service.parse(self.case_id, document_id, self.actor)
        self.assertEqual(caught.exception.code, ErrorCode.FILE_TYPE_UNSUPPORTED)
        stored = self.repository.get(self.case_id).documents[0]
        self.assertEqual(stored.status, DocumentStatus.UPLOADED)
        self.assertEqual(self.blobs.get(stored.versions[0].storage_path), b"synthetic generic attachment")

    def test_duplicate_keys_formulas_and_blank_pdf_do_not_appear_parsed(self):
        parser = FixedTemplateParser()
        for content, extension in (
            (workbook_bytes([("quantity", 2), ("quantity", 3)]), ".xlsx"),
            (pdf_bytes([]), ".pdf"),
        ):
            with self.subTest(extension=extension), self.assertRaises(CrossBotError):
                parser.parse(content, extension)
        formula = parser.parse(workbook_bytes([("quantity", "=2+3")]), ".xlsx")
        self.assertEqual(formula[0].status, FieldValueStatus.MISSING)
        self.assertIsNone(formula[0].value)

    def test_generation_is_reproducible_and_regeneration_preserves_old_outputs(self):
        first = self.service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        first_artifact = first.artifacts[0]
        self.assertEqual(first_artifact.status, "draft")
        self.assertTrue(all(c.status is CrossCheckStatus.PASS for c in first_artifact.checks))
        content = self.service.download(self.case_id, self.actor, artifact_id=first_artifact.artifact_id)
        self.assertEqual(sha256(content).hexdigest(), first_artifact.sha256)
        from openpyxl import load_workbook
        generated = load_workbook(BytesIO(content), data_only=True)
        self.assertEqual(generated.active["J14"].value, "25.025")
        self.assertEqual(first_artifact.media_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        second = self.service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        self.assertEqual([a.version_number for a in second.artifacts], [1, 2])
        self.assertEqual(second.artifacts[0].status, "stale")
        self.assertEqual(second.artifacts[1].sha256, first_artifact.sha256)
        self.assertEqual(second.artifacts[1].template_sha256, first_artifact.template_sha256)
        self.assertEqual(second.artifacts[1].input_snapshot["line_items"][0]["unit_price"], "10.01")
        self.assertNotEqual(second.artifacts[1].artifact_id, first_artifact.artifact_id)

    def test_generating_other_document_types_keeps_current_seller_drafts(self):
        for kind in (Type.PROFORMA_INVOICE, Type.COMMERCIAL_INVOICE, Type.PACKING_LIST):
            workspace = self.service.generate(self.case_id, self.actor, kind)
        self.assertEqual([a.status for a in workspace.artifacts], ["draft", "draft", "draft"])

    def test_unconfirmed_and_external_issuer_generation_are_rejected(self):
        workspace = self.repository.get(self.case_id)
        workspace.trade.confirmed_by = None
        self.repository.save(workspace, workspace.revision)
        with self.assertRaises(CrossBotError) as caught:
            self.service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        self.assertEqual(caught.exception.code, ErrorCode.REVIEW_REQUIRED)
        with self.assertRaises(CrossBotError) as caught:
            self.service.generate(self.case_id, self.actor, Type.BILL_OF_LADING)
        self.assertEqual(caught.exception.code, ErrorCode.FILE_TYPE_UNSUPPORTED)

    def test_renderer_escapes_user_content_and_remains_a_draft(self):
        trade = replace(self.trade, buyer="<script>alert('synthetic')</script>")
        content = HTMLRenderer().render(Type.PROFORMA_INVOICE, trade).content.decode()
        self.assertNotIn("<script>", content)
        self.assertIn("&lt;script&gt;", content)
        self.assertIn("DRAFT", content)

    def test_unresolved_template_cannot_become_a_valid_draft(self):
        class UnresolvedRenderer:
            def render(inner, document_type, trade):
                return replace(HTMLRenderer().render(document_type, trade), content=b"{{ total_amount }}")
        service = DocumentService(self.repository, self.blobs, UnresolvedRenderer())
        workspace = service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        self.assertEqual(workspace.artifacts[0].status, "invalid")
        self.assertEqual(workspace.artifacts[0].checks[-1].status, CrossCheckStatus.FAIL)

    def test_renderer_failure_is_audited_and_retry_can_succeed(self):
        class FailingRenderer:
            def render(inner, document_type, trade):
                raise RuntimeError("must never expose synthetic debug details")
        service = DocumentService(self.repository, self.blobs, FailingRenderer())
        with self.assertRaises(CrossBotError) as caught:
            service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        self.assertNotIn("debug", caught.exception.message)
        workspace = self.repository.get(self.case_id)
        self.assertFalse(workspace.artifacts)
        self.assertEqual(workspace.audit[-1].action, "generation_failed")
        workspace = self.service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        self.assertEqual(len(workspace.artifacts), 1)

    def test_source_changes_invalidate_existing_outputs(self):
        generated = self.service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        uploaded = self.upload(filename="synthetic.png", content=b"synthetic photo", kind=Type.PHOTO)
        self.assertEqual(uploaded.artifacts[0].status, "stale")
        self.assertEqual(uploaded.trade.confirmed_by, generated.trade.confirmed_by)
        self.assertFalse(uploaded.checks)

    def test_denied_and_closed_mutations_leave_audit_evidence(self):
        unauthorized = Actor("synthetic-anonymous")
        with self.assertRaises(CrossBotError):
            self.service.upload(self.case_id, unauthorized, "synthetic.png", b"synthetic", Type.PHOTO)
        self.assertTrue(any(e.action == "access_denied" for e in self.repository.get(self.case_id).audit))
        workspace = self.repository.get(self.case_id)
        workspace.case.status = CaseStatus.CLOSED
        self.repository.save(workspace, workspace.revision)
        with self.assertRaises(CrossBotError):
            self.upload(filename="synthetic.png", content=b"synthetic", kind=Type.PHOTO)

    def test_tampered_output_fails_download_integrity_check(self):
        workspace = self.service.generate(self.case_id, self.actor, Type.COMMERCIAL_INVOICE)
        artifact = workspace.artifacts[0]
        (self.root / "blobs" / artifact.storage_path).write_bytes(b"tampered synthetic output")
        with self.assertRaises(CrossBotError) as caught:
            self.service.download(self.case_id, self.actor, artifact_id=artifact.artifact_id)
        self.assertEqual(caught.exception.code, ErrorCode.CONFLICT)


if __name__ == "__main__":
    unittest.main()

