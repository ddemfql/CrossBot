"""Document-specific defaults, administrator templates and clean customer copies."""
from io import BytesIO
import unittest

from openpyxl import load_workbook
from pypdf import PdfReader
from core.models import Actor, DocumentType as D
from core.errors import CrossBotError, ErrorCode
from documents.catalog import DEFAULT_FIELDS, DEFAULT_ROW_FIELDS, DEFAULT_CONTEXT_FIELDS, ROW_REQUIRED_TYPES
from documents.forms import get_form, FormService, form_fields, form_row_fields
from documents.templates import TemplateService
from tests import test_w3_simple_export as export_fixture
from tests.test_w3_export_formats_templates import template_bytes


class DefaultsAndAudienceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = export_fixture.SimpleExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.actor, self.case_id = self.fixture.repo, self.fixture.actor, self.fixture.case_id

    def test_new_defaults_are_specific_to_document_purpose(self):
        self.assertNotIn("order_no", DEFAULT_FIELDS[D.PRODUCT_SPEC])
        self.assertNotIn("buyer", DEFAULT_FIELDS[D.PACKAGING_ARTWORK])
        self.assertNotIn("unit_price", DEFAULT_ROW_FIELDS[D.PRODUCTION_ORDER])
        self.assertNotIn("gross_weight_kg", DEFAULT_ROW_FIELDS[D.PACKAGING_ARTWORK])
        self.assertNotIn("unit_price", DEFAULT_ROW_FIELDS[D.ISF_WORKSHEET])
        self.assertEqual(DEFAULT_ROW_FIELDS[D.PAYMENT_NOTICE], [])
        self.assertEqual(DEFAULT_ROW_FIELDS[D.COMPLIANCE_DRAFT], [])
        self.assertNotIn(D.PAYMENT_NOTICE, ROW_REQUIRED_TYPES)
        self.assertNotIn(D.COMPLIANCE_DRAFT, ROW_REQUIRED_TYPES)
        ws = self.repo.get(self.case_id)
        self.assertNotIn("unit_price", form_row_fields(get_form(ws,D.PRODUCTION_ORDER)))
        self.assertEqual(form_row_fields(get_form(ws,D.PAYMENT_NOTICE)), {})

    def test_template_creation_requires_administrator_but_sales_can_select(self):
        service = TemplateService(self.repo,self.fixture.blobs)
        with self.assertRaises(CrossBotError) as raised:
            service.upload(self.case_id,self.actor,D.COMMERCIAL_INVOICE,"synthetic.xlsx",template_bytes(),"Denied")
        self.assertIs(raised.exception.code, ErrorCode.FORBIDDEN)
        self.assertFalse(self.repo.get(self.case_id).document_templates)
        admin = Actor("synthetic-admin",["admin"],authenticated=True)
        ws=service.upload(self.case_id,admin,D.COMMERCIAL_INVOICE,"synthetic.xlsx",template_bytes(),"Admin template")
        selected=service.select(self.case_id,self.actor,D.COMMERCIAL_INVOICE,ws.document_templates[0].template_id,"Select existing")
        self.assertEqual(selected.selected_template_ids[D.COMMERCIAL_INVOICE.value],ws.document_templates[0].template_id)

    def test_customer_copy_excludes_internal_provenance_xlsx_and_pdf(self):
        self.fixture.complete()
        ws=self.repo.get(self.case_id)
        form=get_form(ws,D.COMMERCIAL_INVOICE)
        FormService(self.repo).save(self.case_id,self.actor,D.COMMERCIAL_INVOICE,form.values,form.rows,
                                   "INTERNAL-REASON-SENTINEL")
        internal=self.fixture.service.preview(self.case_id,self.actor,D.COMMERCIAL_INVOICE,audience="internal")
        customer=self.fixture.service.preview(self.case_id,self.actor,D.COMMERCIAL_INVOICE,audience="customer")
        wi=load_workbook(BytesIO(internal.content)); wc=load_workbook(BytesIO(customer.content))
        self.addCleanup(wi.close);self.addCleanup(wc.close)
        self.assertIn("字段来源",wi.sheetnames)
        all_internal="\n".join(str(c.value) for sheet in wi for row in sheet for c in row)
        self.assertIn(self.actor.user_id,all_internal)
        self.assertNotIn("字段来源",wc.sheetnames)
        self.assertNotIn("生成记录",wc.sheetnames)
        self.assertNotIn("缺失与提示",wc.sheetnames)
        all_customer="\n".join(str(c.value) for sheet in wc for row in sheet for c in row)
        self.assertNotIn("INTERNAL-REASON-SENTINEL",all_customer)
        self.assertNotIn(self.actor.user_id,all_customer)
        self.assertIn("未人工确认预览",all_customer)
        pdf=self.fixture.service.preview(self.case_id,self.actor,D.COMMERCIAL_INVOICE,audience="customer",output_format="pdf")
        text="".join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
        self.assertNotIn("字段来源",text)
        self.assertNotIn("INTERNAL-REASON-SENTINEL",text)
        self.assertIn("Synthetic item",text)

    def test_customer_and_internal_versions_coexist_and_receipts_bind_audience(self):
        self.fixture.complete()
        self.fixture.generate(audience="customer")
        ws=self.fixture.generate(audience="internal")
        self.assertEqual([a.status for a in ws.artifacts],["draft","draft"])
        self.assertIn("-customer.xlsx",ws.artifacts[0].filename)
        receipts=[e.after for e in ws.audit if e.action=="simple_export_confirmed"]
        self.assertEqual([r["audience"] for r in receipts],["customer","internal"])
        self.assertNotEqual(receipts[0]["layout_schema_sha256"],receipts[1]["layout_schema_sha256"])

    def test_custom_customer_template_does_not_append_internal_audit(self):
        self.fixture.complete()
        service=TemplateService(self.repo,self.fixture.blobs)
        admin=Actor("synthetic-admin",["admin"],authenticated=True)
        ws=service.upload(self.case_id,admin,D.COMMERCIAL_INVOICE,"synthetic.xlsx",template_bytes(),"Admin template")
        service.select(self.case_id,self.actor,D.COMMERCIAL_INVOICE,ws.document_templates[0].template_id,"Select")
        output=self.fixture.service.preview(self.case_id,self.actor,D.COMMERCIAL_INVOICE,audience="customer")
        wb=load_workbook(BytesIO(output.content));self.addCleanup(wb.close)
        self.assertIn("我方模板",wb.sheetnames)
        self.assertIn("预览说明",wb.sheetnames)
        self.assertFalse({"字段来源","缺失与提示","生成记录","CrossBot核对记录"}&set(wb.sheetnames))


if __name__=="__main__":
    unittest.main()

