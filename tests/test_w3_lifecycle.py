"""Synthetic lifecycle evidence and complete portable export checks."""
from io import BytesIO
import json
import unittest
from zipfile import ZipFile

from core.models import (Actor, AssistanceRecord, Case, Document, DocumentForm,
    DocumentType as D, DocumentVersion, ExtractedField, ImportContext, SourceTable,
    Workspace)
from documents.catalog import DOC_LABELS, SELLER_TYPES
from documents.lifecycle import (STAGES, assistance_rows, render_dossier, rows_csv,
                                 stage_summary, transaction_title)
from documents.workbench import export_bundle


def synthetic_workspace():
    document = Document(document_type=D.PURCHASE_ORDER, title="Synthetic PO.txt",
                        responsibility="external")
    version = DocumentVersion(document_id=document.document_id, original_filename=document.title,
        original_path="Synthetic order/Synthetic PO.txt", storage_path="synthetic-key", sha256="a" * 64,
        source_text="Synthetic source <script>alert('source')</script>",
        source_tables=[SourceTable("Synthetic sheet", [["Order", "SYN-LIFE-1"], ["Missing", ""]])],
        fields=[ExtractedField("order_no", "SYN-LIFE-1")])
    document.versions = [version]
    document.current_version_id = version.version_id
    workspace = Workspace(Case(case_number="SYN-LIFE-1"), documents=[document],
        required_document_types=[D.COMMERCIAL_INVOICE, D.PACKING_LIST],
        document_forms=[DocumentForm(D.PURCHASE_ORDER, values={"order_no": "SYN-LIFE-1"},
                                    source_document_ids=[document.document_id])],
        import_context=ImportContext(archive_name="Synthetic shipment awaiting payment.zip",
            reported_trade_state="Source folder says shipped", reported_receivable_state="Source folder says unpaid"),
        assistance_records=[AssistanceRecord(method="visual_transcription", document_id=document.document_id,
            version_id=version.version_id, location="page 1", field_name="order_no",
            proposed_value="SYN-LIFE-1", reason="Transcribed explicit order label")])
    return workspace


class LifecycleTests(unittest.TestCase):
    def test_all_catalog_types_have_one_lifecycle_stage(self):
        kinds = [kind for _, stage_kinds, _ in STAGES for kind in stage_kinds]
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertEqual(set(kinds), set(DOC_LABELS))

    def test_source_claim_does_not_mark_any_stage_complete(self):
        workspace = synthetic_workspace()
        result = stage_summary(workspace)
        self.assertEqual([item["stage"] for item in result],
            ["询盘报价", "订单确认", "产品合规", "生产验货", "出运单据", "运输申报", "收款归档"])
        self.assertFalse(any("完成" in item["status"] for item in result))
        self.assertEqual(result[1]["document_count"], 1)
        self.assertEqual(result[1]["table_count"], 1)
        self.assertEqual(result[1]["confirmed_count"], 0)
        self.assertEqual(result[4]["missing_required"], [DOC_LABELS[D.COMMERCIAL_INVOICE], DOC_LABELS[D.PACKING_LIST]])
        self.assertEqual(result[0]["missing_required"], [])

    def test_dossier_preserves_source_form_missing_values_and_assistance(self):
        workspace = synthetic_workspace()
        html = render_dossier(workspace).decode("utf-8")
        self.assertIn(workspace.import_context.archive_name, html)
        self.assertIn("Synthetic sheet", html)
        self.assertIn("SYN-LIFE-1", html)
        self.assertIn("Transcribed explicit order label", html)
        self.assertIn("visual_transcription", html)
        self.assertIn("<td></td>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("虚拟业务员整理记录", html)

    def test_assistance_table_retains_identity_and_evidence(self):
        workspace = synthetic_workspace()
        records = assistance_rows(workspace)
        self.assertEqual(records[0]["来源文件"], "Synthetic PO.txt")
        self.assertEqual(records[0]["原值"], "")
        self.assertEqual(records[0]["执行身份"], "virtual_sales")
        self.assertEqual(records[0]["来源位置"], "page 1")
        self.assertEqual(transaction_title(workspace), workspace.import_context.archive_name)

    def test_bundle_includes_all_empty_forms_and_only_seller_templates(self):
        workspace = synthetic_workspace()
        actor = Actor("Synthetic admin", ["admin"], authenticated=True)
        class SyntheticDocuments:
            def download(self, *args, **kwargs):
                return b"Synthetic original source"
        content = export_bundle(workspace, SyntheticDocuments(), actor)
        with ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
            self.assertIn("订单档案.html", names)
            self.assertIn("虚拟业务员整理记录.json", names)
            self.assertIn("虚拟业务员整理记录.csv", names)
            for kind, label in DOC_LABELS.items():
                group = "我方需要准备的文件" if kind in SELLER_TYPES else "外部资料"
                prefix = group + "/" + label + "/"
                self.assertIn(prefix + "表单数据.json", names)
                self.assertIn(prefix + "表单字段.csv", names)
                self.assertIn(prefix + "表单明细.csv", names)
                self.assertEqual(prefix + "空白填写模板.html" in names, kind in SELLER_TYPES)
            blank = json.loads(archive.read("我方需要准备的文件/报价单/表单数据.json"))
            self.assertFalse(any(blank["values"].values()))
            self.assertIsNone(blank["confirmed_by"])

    def test_csv_values_keep_blanks_and_protect_formula_cells(self):
        text = rows_csv([{"value": "=2+2", "missing": ""}]).decode("utf-8-sig")
        self.assertIn("'=2+2", text)
        self.assertIn(",\r\n", text)


if __name__ == "__main__":
    unittest.main()
