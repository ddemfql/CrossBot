"""Scoped source uploads preserve originals and do not fill unrelated slots."""
from io import BytesIO
import unittest

from openpyxl import load_workbook
from pypdf import PdfReader
from core.errors import CrossBotError, ErrorCode
from core.models import DocumentStatus, DocumentType as D, TradeAdjustment, TradePackage
from documents.forms import FormService, get_form, form_fields, form_row_fields
from documents.requirement_inputs import RequirementInputService
from documents.simple_export import layout_hash
from tests import test_w3_simple_export as export_fixture


class RequirementUploadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = export_fixture.SimpleExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.actor = self.fixture.repo, self.fixture.actor
        self.case_id = self.fixture.case_id
        self.service = RequirementInputService(self.repo, self.fixture.blobs)

    def upload(self, content=b"buyer: Synthetic Target\nseller: Must Not Fill", filename="synthetic.txt", **kwargs):
        return self.service.upload(self.case_id, self.actor, D.COMMERCIAL_INVOICE,
            "buyer", None, filename, content, source_type=D.PURCHASE_ORDER, **kwargs)

    def test_only_requested_scalar_is_filled_and_source_link_is_versioned(self):
        ws = self.upload()
        form = get_form(ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values.get("buyer"), "Synthetic Target")
        self.assertFalse(form.values.get("seller"))
        self.assertEqual(len(ws.source_requirement_links), 1)
        link = ws.source_requirement_links[0]
        self.assertEqual(link.field_name, "buyer")
        self.assertEqual(link.document_id, ws.documents[0].document_id)
        self.assertEqual(link.version_id, ws.documents[0].current_version_id)
        self.assertFalse(form.confirmed_by)
        self.assertFalse(ws.approvals)

    def test_failed_parse_retains_original_and_leaves_requirement_missing(self):
        ws = self.upload(content=b"Synthetic broken PDF", filename="synthetic.pdf")
        document = ws.documents[0]
        version = document.versions[0]
        self.assertIs(version.status, DocumentStatus.PARSE_FAILED)
        self.assertEqual(self.fixture.blobs.get(version.storage_path), b"Synthetic broken PDF")
        self.assertFalse(get_form(ws, D.COMMERCIAL_INVOICE).values.get("buyer"))
        self.assertEqual(ws.source_requirement_links[0].version_id, version.version_id)

    def test_unmatched_content_is_linked_but_never_fabricates_a_value(self):
        ws = self.upload(content=b"Unlabelled synthetic reference")
        self.assertFalse(get_form(ws, D.COMMERCIAL_INVOICE).values.get("buyer"))
        self.assertTrue(ws.source_requirement_links)

    def test_stale_revision_blocks_before_uploading_a_file(self):
        with self.assertRaises(CrossBotError) as raised:
            self.upload(expected_revision=self.repo.get(self.case_id).revision - 1)
        self.assertIs(raised.exception.code, ErrorCode.CONFLICT)
        self.assertFalse(self.repo.get(self.case_id).documents)

    def test_omitted_optional_data_not_in_any_excel_or_pdf_sheet(self):
        self.fixture.complete()
        ws = self.repo.get(self.case_id)
        form = get_form(ws, D.COMMERCIAL_INVOICE)
        values = dict(form.values, notes="HIDDEN-NOTES-SENTINEL")
        FormService(self.repo).save(self.case_id, self.actor, D.COMMERCIAL_INVOICE,
            values, form.rows, "Synthetic optional fields")
        ws = self.repo.get(self.case_id)
        ws.trade.seller_address = "HIDDEN-ADDRESS-SENTINEL"
        ws.trade.adjustments = [TradeAdjustment(name="HIDDEN-FEE-SENTINEL")]
        ws.trade.packages = [TradePackage(contents_description="HIDDEN-PACKAGE-SENTINEL")]
        self.repo.save(ws, expected_revision=ws.revision)
        form = get_form(ws, D.COMMERCIAL_INVOICE)
        from documents.simple_export import TRADE_EXTRA_FIELDS
        FormService(self.repo).configure_fields(self.case_id,self.actor,D.COMMERCIAL_INVOICE,
            fields=[*form_fields(form), "notes"], row_fields=list(form_row_fields(form)),
            context_fields=[*TRADE_EXTRA_FIELDS,"packages","adjustments","line_items"], reason="Show optional data before removal")
        form = get_form(self.repo.get(self.case_id), D.COMMERCIAL_INVOICE)
        before_hash = layout_hash(D.COMMERCIAL_INVOICE, form=form)
        FormService(self.repo).configure_fields(self.case_id, self.actor, D.COMMERCIAL_INVOICE,
            fields=[name for name in form_fields(form) if name != "notes"],
            row_fields=list(form_row_fields(form)), context_fields=[], reason="Remove optional data")
        ws = self.repo.get(self.case_id)
        self.assertNotEqual(before_hash, layout_hash(D.COMMERCIAL_INVOICE, form=get_form(ws,D.COMMERCIAL_INVOICE)))
        xlsx = self.fixture.preview()
        wb = load_workbook(BytesIO(xlsx.content))
        self.addCleanup(wb.close)
        contents = "\n".join(str(c.value) for s in wb for row in s for c in row)
        self.assertNotIn("备注", contents)
        self.assertNotIn("HIDDEN-", contents)
        pdf = self.fixture.service.preview(self.case_id,self.actor,D.COMMERCIAL_INVOICE,output_format="pdf")
        text = "".join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
        self.assertNotIn("HIDDEN-", text)


if __name__ == "__main__":
    unittest.main()

