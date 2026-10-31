"""Daily work precedes collapsed setup/history; shortcuts select the right file."""
import unittest
import json
from unittest.mock import Mock, patch

from core.models import Actor, AuditEvent, Case, DocumentType as D, GeneratedArtifact, TradeData, Workspace
from case_workflow.service import CaseService
from documents.simple_workbench import _latest_download
from validation.rules import DeterministicRules
from tests import test_w3_simple_ui as ui_harness


def position(elements, kind, label=None, value=None):
    return next(i for i, item in enumerate(elements) if item.type == kind
        and (label is None or getattr(item, "label", None) == label)
        and (value is None or getattr(item, "value", None) == value))


class WorkbenchPriorityUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = ui_harness.SimpleUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.admin = Actor("synthetic-admin", ["admin"], authenticated=True)

    def create_case(self):
        return CaseService(self.fixture.repo, DeterministicRules()).create(
            self.admin, TradeData(order_no="SYN-DAILY-PRIORITY"))

    def test_existing_transaction_daily_tabs_precede_settings_creation_and_accounts(self):
        self.create_case()
        app = self.fixture.app(self.admin)
        nodes = list(app.main)
        selected = position(nodes, "selectbox", "选择交易")
        first_tab = position(nodes, "tab", "文件分类与查看")
        daily_export = position(nodes, "tab", "生成与导出")
        self.assertLess(selected, first_tab)
        self.assertLess(first_tab, daily_export)
        checklist=position(nodes,"expander","调整资料与交付清单")
        self.assertLess(position(nodes,"markdown",value="**需生成的内容**"),checklist)
        self.assertLess(checklist,first_tab)
        for label in ("编辑订单资料", "全部已提供资料与用途",
                      "逐项对应生成所需数据与来源", "新建交易 / 导入资料", "账号管理"):
            self.assertLess(daily_export, position(nodes,"expander",label), label)
        self.assertEqual([tab.label for tab in app.tabs], ["文件分类与查看", "生成与导出"])

    def test_review_and_generation_precede_low_frequency_editor_and_source_tools(self):
        self.create_case()
        app = self.fixture.app(self.admin)
        nodes = list(app.main)
        review = position(nodes, "markdown", value="**审核结果**")
        self.assertLess(review, position(nodes,"expander","填写与修改当前文件"))
        generate = position(nodes,"button","确认并生成")
        for label in ("版式与导出选项", "当前填写内容及来源", "从已有资料填入（可选）",
                      "查看审核依据", "历史版本与多文件打包"):
            self.assertLess(review, position(nodes,"expander",label),label)
            self.assertLess(generate, position(nodes,"expander",label),label)
        for expander in app.expander:
            if expander.label != "上传文件":
                self.assertFalse(expander.proto.expanded, expander.label)

    def test_template_management_stays_in_collapsed_checklist_below_generation_summary(self):
        self.create_case()
        app=self.fixture.app(self.admin)
        ui_harness.widget(app.radio,"设置内容").set_value("模板管理").run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        nodes=list(app.main)
        self.assertLess(position(nodes,"markdown",value="**需生成的内容**"),
                        position(nodes,"file_uploader","上传此文件的 Excel 模板"))
        self.assertLess(position(nodes,"file_uploader","上传此文件的 Excel 模板"),
                        position(nodes,"tab","文件分类与查看"))
        settings=ui_harness.widget(app.expander,"调整资料与交付清单")
        self.assertFalse(settings.proto.expanded)

    def test_empty_workspace_keeps_creation_available_without_daily_tabs(self):
        app=self.fixture.app(self.admin)
        self.assertFalse(app.tabs)
        self.assertTrue(any(b.label=="新建交易" for b in app.button))
        nodes=list(app.main)
        self.assertLess(position(nodes,"expander","新建交易 / 导入资料"),
                        position(nodes,"expander","账号管理"))

    def test_fee_issue_editor_keeps_checkbox_boolean_when_saving_amount(self):
        from core.models import TradeAdjustment
        from documents.simple_export import SimpleExportService
        ws=self.create_case()
        ws.trade.adjustments=[TradeAdjustment(name="Synthetic freight",amount="-1",currency="USD",
                                               included_in_invoice_price=False)]
        next(f for f in ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE).included_context_fields=["adjustments"]
        self.fixture.repo.save(ws,expected_revision=ws.revision)
        report=SimpleExportService(self.fixture.repo,self.fixture.blobs).assess(
            ws.case.case_id,self.admin,D.COMMERCIAL_INVOICE,human_confirmed=True)
        finding=next(c for c in report.checks if c.rule_code=="ADJUSTMENT_VALID")
        self.assertTrue(any(t.field_name=="adjustments" and t.area.value=="trade" for t in finding.edit_targets))
        app=self.fixture.app(self.admin)
        picker=ui_harness.widget(app.selectbox,"选择要处理的审核问题")
        option=next(label for label in picker.options if "订单资料 · 费用与调整" in label)
        picker.select(int(option.split(".",1)[0])-1).run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        editor=next(frame for frame in app.dataframe if "included_in_invoice_price" in frame.value.columns and "w3-draft-widget-" in frame.proto.id)
        columns=json.loads(editor.proto.columns)
        self.assertEqual(columns["included_in_invoice_price"]["type_config"]["type"],"checkbox")
        key=editor.proto.id[editor.proto.id.index("w3-draft-widget-"): ]
        app.session_state[key]={"edited_rows":{0:{"amount":"2.25"}},"added_rows":[],"deleted_rows":[]}
        ui_harness.widget(app.button,"保存修改并重新审核").click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(app.error,[e.value for e in app.error])
        saved=self.fixture.repo.get(ws.case.case_id)
        self.assertEqual(saved.trade.adjustments[0].amount,"2.25")
        self.assertIs(saved.trade.adjustments[0].included_in_invoice_price,False)
        self.assertFalse(saved.approvals)
        self.assertFalse(saved.artifacts)

    def test_unique_scalar_source_product_code_uses_direct_field_editor_without_table(self):
        from streamlit.testing.v1 import AppTest
        from core.models import SourceFieldSelection
        from documents.service import DocumentService
        from documents.workbench import collect_document
        ws=self.create_case()
        ws=DocumentService(self.fixture.repo,self.fixture.blobs).upload(ws.case.case_id,self.admin,
            "synthetic-product-code.txt",b"product_code: SYN-ORIGINAL",D.PURCHASE_ORDER)
        doc=ws.documents[-1]
        ws=collect_document(self.fixture.repo,self.fixture.blobs,ws.case.case_id,doc.document_id,self.admin,prefill=False)
        version=ws.documents[-1].versions[-1]
        self.assertFalse(version.source_tables)
        candidate=next(f for f in version.fields if f.name=="product_code")
        code=f'''import streamlit as st
import w3_app
from core.models import CrossCheckResult, ReviewEditTarget, ReviewEditArea, DocumentType as D
from validation.export_review import review_digest
from review.simple_editor import render_issue_editor
ws=w3_app.repository.get({ws.case.case_id!r})
target=ReviewEditTarget(ReviewEditArea.SOURCE,D.COMMERCIAL_INVOICE,"product_code",document_id={doc.document_id!r},version_id={version.version_id!r})
check=CrossCheckResult(rule_code="RAW_CROSS_DOCUMENT",message="Unique source SKU",edit_targets=[target])
render_issue_editor(st,ws,st.session_state["w3_actor"],D.COMMERCIAL_INVOICE,[check],w3_app.repository,w3_app.cases,review_digest(ws,D.COMMERCIAL_INVOICE),lambda c,k:c.message)
'''
        app=AppTest.from_string(code,default_timeout=25)
        app.session_state["w3_actor"]=self.admin
        # Supply a single eligible scalar field to isolate the shortcut UI.
        # Eligibility of source fields remains covered by quality-input tests.
        selection=SourceFieldSelection(doc.document_id,version.version_id,"product_code",candidate.value)
        with patch("review.quality_inputs.pending_source_fields",return_value=[selection]):
            app.run()
            ui_harness.widget(app.selectbox,"选择要处理的审核问题").select(0).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            field=ui_harness.widget(app.text_input,"原件核对值")
            self.assertEqual(field.value,"SYN-ORIGINAL")
            field.set_value("SYN-CORRECTED").run()
            ui_harness.widget(app.checkbox,"我已对照原件核实此修改").check()
            ui_harness.widget(app.button,"保存原件核对值并重新审核").click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertFalse(app.error,[e.value for e in app.error])
        saved=self.fixture.repo.get(ws.case.case_id)
        field=next(f for f in saved.documents[-1].versions[-1].fields if f.name=="product_code")
        self.assertEqual(field.value,"SYN-ORIGINAL")
        self.assertEqual(field.confirmed_value,"SYN-CORRECTED")
        self.assertFalse(saved.approvals)


class CurrentDownloadSelectionTests(unittest.TestCase):
    def workspace(self):
        ws=Workspace(Case())
        definitions=[(D.COMMERCIAL_INVOICE,"customer","xlsx"),
                     (D.PACKING_LIST,"customer","xlsx"),
                     (D.COMMERCIAL_INVOICE,"customer","pdf"),
                     (D.COMMERCIAL_INVOICE,"internal","xlsx"),
                     (D.COMMERCIAL_INVOICE,"customer","xlsx")]
        for index,(kind,audience,fmt) in enumerate(definitions,1):
            artifact=GeneratedArtifact(document_type=kind,version_number=index,
                filename=f"synthetic-{index}-{audience}.{fmt}",status="stale" if index==5 else "draft")
            ws.artifacts.append(artifact)
            ws.audit.append(AuditEvent(entity_id=artifact.artifact_id,action="simple_export_confirmed",
                                       after={"audience":audience}))
        return ws

    def test_latest_shortcut_filters_type_format_audience_and_current_state(self):
        ws=self.workspace()
        for kind,fmt,audience,index in ((D.COMMERCIAL_INVOICE,"xlsx","customer",0),
                (D.PACKING_LIST,"xlsx","customer",1),(D.COMMERCIAL_INVOICE,"pdf","customer",2),
                (D.COMMERCIAL_INVOICE,"xlsx","internal",3)):
            with self.subTest(kind=kind,fmt=fmt,audience=audience):
                st=Mock()
                with patch("review.policy.current_artifacts",return_value=ws.artifacts[:4]), \
                        patch("documents.simple_workbench._download",return_value=b"synthetic") as download:
                    _latest_download(st,ws,Actor("synthetic",["sales"],True),Mock(),kind,fmt,audience)
                self.assertEqual(download.call_args.kwargs["artifact_id"],ws.artifacts[index].artifact_id)
                self.assertEqual(st.download_button.call_args.args[2],ws.artifacts[index].filename)

    def test_no_matching_current_artifact_does_not_offer_historical_download(self):
        ws=self.workspace();st=Mock()
        with patch("review.policy.current_artifacts",return_value=[]), \
                patch("documents.simple_workbench._download") as download:
            _latest_download(st,ws,Actor("synthetic",["sales"],True),Mock(),D.COMMERCIAL_INVOICE,"xlsx","customer")
        download.assert_not_called()
        st.download_button.assert_not_called()


if __name__=="__main__":
    unittest.main()
