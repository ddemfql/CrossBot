"""Synthetic custom templates, real PDF, integrity and rendering failure checks."""
from io import BytesIO
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import unittest

from openpyxl import Workbook, load_workbook
from pypdf import PdfReader
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import Actor, DocumentType as D, to_dict
from documents.simple_export import layout_hash
from documents.templates import TemplateService
from tests import test_w3_simple_export as export_fixture


def template_bytes(value="{{buyer}}", rows=True):
    wb = Workbook()
    ws = wb.active
    ws.title = "我方模板"
    ws.append(["客户", value])
    ws.append(["产品", "数量", "金额"])
    if rows:
        ws.append(["{{rows.description}}", "{{rows.quantity}}", "{{rows.amount}}"])
    output = BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


class ExportFormatsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = export_fixture.SimpleExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.blobs = self.fixture.repo, self.fixture.blobs
        self.service, self.actor = self.fixture.service, self.fixture.actor
        self.case_id = self.fixture.case_id
        self.templates = TemplateService(self.repo, self.blobs)
        self.admin = Actor("synthetic-admin", ["admin"], authenticated=True)

    def upload(self, content=None, **kwargs):
        return self.templates.upload(self.case_id, self.admin, D.COMMERCIAL_INVOICE,
            "synthetic-template.xlsx", content or template_bytes(), "Synthetic template", **kwargs)

    def select(self, workspace):
        return self.templates.select(self.case_id, self.actor, D.COMMERCIAL_INVOICE,
            workspace.document_templates[-1].template_id, "Select synthetic template")

    def test_template_upload_select_versions_and_custom_render(self):
        self.fixture.complete()
        first = self.upload()
        self.select(first)
        before = self.repo.get(self.case_id).revision
        preview = self.service.preview(self.case_id, self.actor, D.COMMERCIAL_INVOICE)
        self.assertEqual(self.repo.get(self.case_id).revision, before)
        wb = load_workbook(BytesIO(preview.content))
        self.addCleanup(wb.close)
        self.assertEqual(wb["我方模板"]["B1"].value, "Synthetic Buyer")
        self.assertEqual(wb["我方模板"]["A3"].value, "Synthetic item")
        self.assertIn("字段来源", wb.sheetnames)
        self.assertEqual(wb["CrossBot核对记录"]["B1"].value, "未人工确认预览")
        result = self.fixture.generate()
        self.assertEqual(result.artifacts[-1].template_id, first.document_templates[-1].template_id)
        second = self.upload(template_bytes("{{seller}}"))
        self.assertEqual([t.version_number for t in second.document_templates], [1, 2])
        self.assertEqual(second.artifacts[-1].status, "stale")

    def test_unknown_formula_and_multiple_repeat_rows_rejected(self):
        for content in (template_bytes("{{unknown}}"), template_bytes("=1+1"),
                        template_bytes("{{rows.description}}")):
            with self.subTest(content=sha256(content).hexdigest()), self.assertRaises(ValidationError):
                self.upload(content)
        self.assertFalse(self.repo.get(self.case_id).document_templates)

    def test_missing_repeat_row_blocks_selected_template_and_binary_missing_blocks(self):
        self.fixture.complete()
        ws = self.select(self.upload(template_bytes(rows=False)))
        report = self.service.assess(self.case_id, self.actor, D.COMMERCIAL_INVOICE, human_confirmed=True)
        self.assertTrue(any(c.rule_code == "TEMPLATE_INTEGRITY" for c in report.checks))
        with self.assertRaises(CrossBotError):
            self.fixture.generate()
        ws = self.select(self.upload())
        (Path(self.fixture.temp.name) / ws.document_templates[-1].storage_path).unlink()
        with self.assertRaises(CrossBotError):
            self.fixture.generate()

    def test_template_selection_detects_stale_revision_and_type_mismatch(self):
        ws = self.upload()
        with self.assertRaises(CrossBotError) as caught:
            self.select_stale(ws)
        self.assertIs(caught.exception.code, ErrorCode.CONFLICT)
        with self.assertRaises(ValidationError):
            self.templates.select(self.case_id, self.actor, D.PACKING_LIST,
                ws.document_templates[0].template_id, "Wrong type")

    def select_stale(self, ws):
        return self.templates.select(self.case_id, self.actor, D.COMMERCIAL_INVOICE,
            ws.document_templates[0].template_id, "Stale", expected_revision=ws.revision - 1)

    def test_pdf_is_real_chinese_multipage_reproducible_preview_and_confirmed_output(self):
        rows = [{"product_code": "SYN-%03d" % i, "description": "合成商品 %d 长中文说明" % i,
                 "quantity": "2", "unit": "pcs", "unit_price": "3"} for i in range(45)]
        self.fixture.complete(rows=rows)
        before = to_dict(self.repo.get(self.case_id))
        first = self.service.preview(self.case_id, self.actor, D.COMMERCIAL_INVOICE, output_format="pdf")
        second = self.service.preview(self.case_id, self.actor, D.COMMERCIAL_INVOICE, output_format="pdf")
        self.assertEqual(first.content, second.content)
        reader = PdfReader(BytesIO(first.content))
        self.assertGreater(len(reader.pages), 1)
        text = "".join(page.extract_text() for page in reader.pages)
        self.assertIn("合成商品", text)
        self.assertIn("未人工确认预览", text)
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        result = self.fixture.generate(output_format="pdf")
        item = result.artifacts[-1]
        self.assertEqual(item.media_type, "application/pdf")
        self.assertTrue(item.filename.endswith(".pdf"))
        self.assertEqual(sha256(self.blobs.get(item.storage_path)).hexdigest(), item.sha256)

    def test_render_failures_are_domain_errors_without_artifact_or_approval(self):
        self.fixture.complete()
        with patch("documents.simple_export._render", side_effect=RuntimeError("synthetic error")):
            with self.assertRaises(CrossBotError):
                self.fixture.preview()
            with self.assertRaises(CrossBotError):
                self.fixture.generate()
        ws = self.repo.get(self.case_id)
        self.assertFalse(ws.artifacts)
        self.assertFalse(ws.approvals)
        self.assertEqual(ws.audit[-1].action, "generation_failed")

    def test_same_snapshot_excel_and_pdf_both_remain_current(self):
        self.fixture.complete()
        self.fixture.generate()
        result = self.fixture.generate(output_format="pdf")
        self.assertEqual([a.status for a in result.artifacts], ["draft", "draft"])
        result = self.fixture.generate()
        self.assertEqual([a.status for a in result.artifacts], ["stale", "draft", "draft"])

    def test_transaction_context_packages_and_all_fee_categories_survive_both_formats(self):
        from core.models import TradeAdjustment, TradeAdjustmentKind, TradePackage, TradeLine
        self.fixture.complete()
        ws = self.repo.get(self.case_id)
        ws.trade.transaction_place = "合成交易地点"
        ws.trade.seller_address = "合成卖方地址"
        ws.trade.port_of_entry = "合成进口口岸"
        ws.trade.packages = [TradePackage(package_number="SYN-BOX", product_code="SYN-A",
            contents_description="合成逐包内容", quantity="2", unit="pcs",
            gross_weight_kg="2", net_weight_kg="1")]
        ws.trade.line_items = [TradeLine("SYN-A", "Synthetic item", "2", "3",
            hs_code="SYN-HS", grade_quality="合成质量", country_of_origin="CN")]
        ws.trade.adjustments = [TradeAdjustment(kind=kind, name="合成费用", amount="1", currency="USD")
                                for kind in TradeAdjustmentKind]
        self.repo.save(ws, expected_revision=ws.revision)
        from documents.forms import FormService, get_form, form_fields, form_row_fields
        from documents.simple_export import TRADE_EXTRA_FIELDS
        FormService(self.repo).configure_fields(self.case_id,self.actor,D.COMMERCIAL_INVOICE,
            fields=list(form_fields(get_form(ws,D.COMMERCIAL_INVOICE))),
            row_fields=list(form_row_fields(get_form(ws,D.COMMERCIAL_INVOICE))),
            context_fields=[*TRADE_EXTRA_FIELDS,"packages","adjustments","line_items"], reason="Include selected business context")
        output = self.fixture.preview()
        wb = load_workbook(BytesIO(output.content))
        self.addCleanup(wb.close)
        self.assertIn("逐包内容", wb.sheetnames)
        self.assertEqual(wb["逐包内容"]["D2"].value, "合成逐包内容")
        self.assertEqual(wb["订单补充明细"]["B2"].value, "SYN-HS")
        self.assertEqual([wb["费用与调整"].cell(i+2, 1).value for i in range(len(TradeAdjustmentKind))],
                         [kind.value for kind in TradeAdjustmentKind])
        pdf = self.service.preview(self.case_id, self.actor, D.COMMERCIAL_INVOICE, output_format="pdf")
        text = "".join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
        for value in ("合成交易地点", "合成卖方地址", "合成进口口岸", "合成逐包内容", "SYN-HS", "退税", "补贴"):
            self.assertIn(value, text)

    def test_layout_digest_changes_when_specification_changes(self):
        from documents.catalog import FORM_FIELDS
        before = layout_hash(D.COMMERCIAL_INVOICE)
        with patch.dict(FORM_FIELDS[D.COMMERCIAL_INVOICE], {"notes": "Changed label"}):
            self.assertNotEqual(before, layout_hash(D.COMMERCIAL_INVOICE))


if __name__ == "__main__":
    unittest.main()

