"""Synthetic UI regressions for daily controls, real materials and unsaved drafts."""
import unittest

from core.models import (CrossCheckResult, CrossCheckStatus, DocumentType as D,
    GeneratedArtifact, TradeData, to_dict)
from case_workflow.service import CaseService
from documents.forms import FormService, get_form
from documents.manual_inputs import ManualInputService
from documents.requirements import data_requirements
from documents.service import DocumentService
from documents.simple_export import SimpleExportService
from documents.simple_workbench import file_inventory
from validation.rules import DeterministicRules
from tests import test_w3_simple_ui as harness

widget=harness.widget


class CompletionCleanupUITests(unittest.TestCase):
    def setUp(self):
        self.h=harness.SimpleUITests()
        self.h.setUp()
        self.addCleanup(self.h.tearDown)

    def case(self,kind=D.COMMERCIAL_INVOICE,buyer="Synthetic Buyer"):
        ws=CaseService(self.h.repo,DeterministicRules()).create(self.h.actor,
            TradeData(order_no="SYN-CLEANUP",currency="USD"),required_types=[kind])
        if kind is not D.COMMERCIAL_INVOICE:
            return ws
        return FormService(self.h.repo).save(ws.case.case_id,self.h.actor,kind,
            {"document_no":"SYN-CI","document_date":"2026-10-31","order_no":"SYN-CLEANUP",
             "seller":"Synthetic Seller","buyer":buyer,"currency":"USD"},
            [{"product_code":"SYN-A","description":"Synthetic Item","quantity":"2","unit":"pcs","unit_price":"3"}],
            "Synthetic saved business data")

    def open_slot(self,app,field="buyer"):
        widget(app.radio,"补充方式").set_value("按缺项补充").run()
        widget(app.selectbox,"本次补充哪一项").select((D.COMMERCIAL_INVOICE,field,None)).run()
        if widget(app.radio,"此项资料的提供方式").value!="直接填写":
            widget(app.radio,"此项资料的提供方式").set_value("直接填写").run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        return app

    def test_one_collapsed_checklist_below_generation_summary_ignores_legacy_open_state(self):
        ws=self.case();app=self.h.app()
        app.session_state[f"settings-open-{ws.case.case_id}"]=True
        app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(any(item.label in {"调整资料清单","调整生成清单","收起清单设置"} for item in app.button))
        panels=[e for e in app.expander if e.label=="调整资料与交付清单"]
        self.assertEqual(len(panels),1)
        self.assertFalse(panels[0].proto.expanded)
        nodes=list(app.main)
        settings=next(i for i,e in enumerate(nodes) if e.type=="expander" and e.label=="调整资料与交付清单")
        summary=next(i for i,e in enumerate(nodes) if e.type=="markdown" and e.value=="**需生成的内容**")
        self.assertGreater(settings,summary)
        self.assertLess(settings,min(i for i,e in enumerate(nodes) if e.type=="tab"))
        widget(app.multiselect,"需要提供的资料（上传或手动填写）")
        widget(app.multiselect,"需要制作并交付的文件").set_value([D.PACKING_LIST])
        widget(app.button,"保存两类清单").click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(self.h.repo.get(ws.case.case_id).required_delivery_types,[D.PACKING_LIST])

    def test_saving_unchanged_payment_detail_settings_does_not_require_goods_rows(self):
        ws=self.case(D.PAYMENT_NOTICE);app=self.h.app()
        self.assertFalse(any(i.field_name=="rows" for i in data_requirements(ws,D.PAYMENT_NOTICE)))
        widget(app.radio,"设置内容").set_value("文件资料项").run()
        widget(app.selectbox,"调整哪份文件").select(D.PAYMENT_NOTICE).run()
        widget(app.radio,"资料分组").set_value("明细").run()
        frame=next(d.value for d in app.dataframe if "导出显示" in getattr(d.value,"columns",[]))
        sku=frame.loc[frame["字段"]=="product_code"].iloc[0]
        self.assertFalse(sku["必需"])
        self.assertFalse(sku["需要提供"])
        widget(app.button,"保存本文件资料项").click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        saved=self.h.repo.get(ws.case.case_id)
        self.assertEqual(get_form(saved,D.PAYMENT_NOTICE).required_input_row_fields,[])
        self.assertFalse(any(i.field_name=="rows" for i in data_requirements(saved,D.PAYMENT_NOTICE)))

    def test_daily_material_table_shows_manual_record_and_original_without_count_inflation(self):
        ws=self.case()
        ManualInputService(self.h.repo).save_record(ws.case.case_id,self.h.actor,D.PURCHASE_ORDER,
            {"buyer":"Synthetic manual buyer"},[],"Synthetic manual record",title="SYN-MANUAL-PO")
        DocumentService(self.h.repo,self.h.blobs).upload(ws.case.case_id,self.h.actor,
            "synthetic-upload.txt",b"Synthetic original",D.OTHER)
        app=self.h.app()
        frame=next(d.value for d in app.dataframe if {"资料","提供方式"}<=set(getattr(d.value,"columns",[])))
        manual=frame.loc[frame["资料"]=="SYN-MANUAL-PO"].iloc[0]
        original=frame.loc[frame["资料"]=="synthetic-upload.txt"].iloc[0]
        self.assertEqual(manual["提供方式"],"手动填写")
        self.assertEqual(original["提供方式"],"上传原件")
        self.assertEqual(sum(row["原件"] for row in file_inventory(self.h.repo.get(ws.case.case_id))),1)

    def test_old_rule_draft_is_history_and_is_not_a_current_generated_file(self):
        ws=self.case()
        ws.artifacts.append(GeneratedArtifact(document_type=D.COMMERCIAL_INVOICE,status="draft",
            filename="synthetic-old-rule.xlsx",template_id="simple-xlsx",rule_version="old-rule",
            storage_path=self.h.blobs.put(b"Synthetic old output"),input_revision=ws.trade.revision,
            input_snapshot=to_dict(ws.trade),checks=[CrossCheckResult(status=CrossCheckStatus.PASS)]))
        self.h.repo.save(ws,expected_revision=ws.revision)
        row=next(r for r in file_inventory(ws) if r["需要制作交付"])
        self.assertEqual(row["生成文件"],0)
        self.assertEqual(row["历史生成文件"],1)
        app=self.h.app()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        table=next(d.value for d in app.dataframe if {"文件","情况","待补/待核实"}<=set(getattr(d.value,"columns",[])))
        self.assertNotIn("已生成",str(table.iloc[0]["情况"]))
        self.assertFalse(any(d.label=="下载当前文件" for d in app.get("download_button")))

    def test_focused_unsaved_value_survives_slot_method_and_export_format_navigation(self):
        ws=self.case(buyer="");app=self.open_slot(self.h.app())
        widget(app.text_input,"填写内容").set_value("SYN-UNSAVED-BUYER").run()
        widget(app.selectbox,"本次补充哪一项").select((D.COMMERCIAL_INVOICE,"seller",None)).run()
        widget(app.selectbox,"本次补充哪一项").select((D.COMMERCIAL_INVOICE,"buyer",None)).run()
        widget(app.radio,"此项资料的提供方式").set_value("上传原件").run()
        widget(app.radio,"此项资料的提供方式").set_value("直接填写").run()
        widget(app.selectbox,"导出格式").select("PDF").run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(widget(app.text_input,"填写内容").value,"SYN-UNSAVED-BUYER")
        self.assertEqual(get_form(self.h.repo.get(ws.case.case_id),D.COMMERCIAL_INVOICE).values["buyer"],"")

    def test_concurrent_backend_change_retains_draft_and_disables_save(self):
        ws=self.case(buyer="");app=self.open_slot(self.h.app())
        widget(app.text_input,"填写内容").set_value("SYN-LOCAL-DRAFT").run()
        saved=self.h.repo.get(ws.case.case_id);form=get_form(saved,D.COMMERCIAL_INVOICE)
        FormService(self.h.repo).save(ws.case.case_id,self.h.actor,D.COMMERCIAL_INVOICE,
            dict(form.values,seller="SYN-OTHER-HUMAN"),form.rows,"Concurrent saved edit")
        app.run()
        self.assertEqual(widget(app.text_input,"填写内容").value,"SYN-LOCAL-DRAFT")
        self.assertTrue(widget(app.button,"保存此项资料").disabled)
        self.assertTrue(any("较早版本" in w.value for w in app.warning))
        current=get_form(self.h.repo.get(ws.case.case_id),D.COMMERCIAL_INVOICE)
        self.assertEqual(current.values["seller"],"SYN-OTHER-HUMAN")
        self.assertEqual(current.values["buyer"],"")

    def test_full_file_unsaved_buyer_survives_format_switch_and_blocks_formal_generation(self):
        ws=self.case();app=self.h.app()
        self.assertTrue(SimpleExportService(self.h.repo,self.h.blobs).assess(ws.case.case_id,self.h.actor,
            D.COMMERCIAL_INVOICE,human_confirmed=True).ready)
        widget(app.text_input,"买方").set_value("SYN-FULL-FILE-DRAFT").run()
        widget(app.selectbox,"导出格式").select("PDF").run()
        self.assertEqual(widget(app.text_input,"买方").value,"SYN-FULL-FILE-DRAFT")
        widget(app.checkbox,"我已人工核对当前资料，确认按现有内容生成").check().run()
        self.assertTrue(widget(app.button,"确认并生成").disabled)
        self.assertTrue(any("未保存" in w.value for w in app.warning))
        stored=self.h.repo.get(ws.case.case_id)
        self.assertEqual(get_form(stored,D.COMMERCIAL_INVOICE).values["buyer"],"Synthetic Buyer")
        self.assertFalse(stored.artifacts)

    def test_download_audit_revision_with_same_business_snapshot_does_not_stale_draft(self):
        ws=self.case(buyer="")
        documents=DocumentService(self.h.repo,self.h.blobs)
        ws=documents.upload(ws.case.case_id,self.h.actor,"synthetic-reference.txt",b"Synthetic reference",D.OTHER)
        doc=ws.documents[-1]
        app=self.open_slot(self.h.app())
        widget(app.text_input,"填写内容").set_value("SYN-DRAFT-AFTER-DOWNLOAD").run()
        documents.download(ws.case.case_id,self.h.actor,document_id=doc.document_id)
        app.run()
        self.assertEqual(widget(app.text_input,"填写内容").value,"SYN-DRAFT-AFTER-DOWNLOAD")
        self.assertFalse(widget(app.button,"保存此项资料").disabled)
        self.assertFalse(any("较早版本" in w.value for w in app.warning))
        self.assertEqual(get_form(self.h.repo.get(ws.case.case_id),D.COMMERCIAL_INVOICE).values["buyer"],"")

    def test_bulk_manual_buyer_draft_survives_source_type_switch_without_backend_save(self):
        ws=self.case();app=self.h.app()
        widget(app.radio,"补充方式").set_value("手动填写").run()
        widget(app.selectbox,"手动提供的资料类型").select(D.PURCHASE_ORDER).run()
        widget(app.text_input,"买方（提供资料）").set_value("SYN-MANUAL-UNSAVED").run()
        widget(app.selectbox,"手动提供的资料类型").select(D.OTHER).run()
        widget(app.selectbox,"手动提供的资料类型").select(D.PURCHASE_ORDER).run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(widget(app.text_input,"买方（提供资料）").value,"SYN-MANUAL-UNSAVED")
        self.assertFalse(get_form(self.h.repo.get(ws.case.case_id),D.PURCHASE_ORDER).values.get("buyer"))

    def test_whole_order_customer_draft_survives_export_format_switch(self):
        ws=self.case();app=self.h.app()
        widget(app.text_input,"客户").set_value("SYN-ORDER-UNSAVED").run()
        widget(app.selectbox,"导出格式").select("PDF").run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(widget(app.text_input,"客户").value,"SYN-ORDER-UNSAVED")
        self.assertEqual(self.h.repo.get(ws.case.case_id).trade.buyer,"")

    def test_order_save_quantity_keeps_blank_unknown_box_count_unprovided(self):
        from core.models import TradeLine
        ws=CaseService(self.h.repo,DeterministicRules()).create(self.h.actor,
            TradeData(order_no="SYN-UNKNOWN-BOX",line_items=[TradeLine("A","Synthetic item","2","3")]),
            required_types=[D.COMMERCIAL_INVOICE])
        app=self.h.app()
        editor=next(d for d in app.dataframe if "_line_id" in getattr(d.value,"columns",[]))
        self.assertEqual(editor.value.iloc[0]["packages"],"")
        key=editor.proto.id[editor.proto.id.index("w3-draft-widget-"):]
        app.session_state[key]={"edited_rows":{0:{"quantity":"7"}},"added_rows":[],"deleted_rows":[]}
        widget(app.button,"保存订单资料").click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(app.error,[e.value for e in app.error])
        line=self.h.repo.get(ws.case.case_id).trade.line_items[0]
        self.assertEqual(line.quantity,"7")
        self.assertEqual(line.packages,0)
        self.assertNotIn("packages",line.supplied_fields)

    def test_full_file_identity_fields_offer_one_invoice_number_and_date(self):
        self.case();app=self.h.app()
        picker=widget(app.multiselect,"显示填写项")
        self.assertEqual(picker.options.count("发票号"),1)
        self.assertEqual(picker.options.count("发票日期"),1)
        self.assertNotIn("单据编号",picker.options)
        self.assertNotIn("单据日期",picker.options)
        self.assertIn("document_no",picker.value)
        self.assertIn("document_date",picker.value)
        self.assertNotIn("invoice_no",picker.value)
        self.assertNotIn("invoice_date",picker.value)

    def test_manual_material_continue_opens_the_selected_type_and_record(self):
        ws=self.case();service=ManualInputService(self.h.repo)
        service.save_record(ws.case.case_id,self.h.actor,D.PURCHASE_ORDER,{"buyer":"SYN-FIRST"},[],
            "First synthetic record",title="SYN-PO-FIRST")
        saved=service.save_record(ws.case.case_id,self.h.actor,D.PURCHASE_ORDER,{"buyer":"SYN-SECOND"},[],
            "Second synthetic record",title="SYN-PO-SECOND")
        record=saved.manual_data_records[-1]
        app=self.h.app()
        widget(app.selectbox,"查看手填或制作资料").select(record.record_id).run()
        widget(app.button,"继续填写这份资料").click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(widget(app.radio,"补充方式").value,"手动填写")
        self.assertEqual(widget(app.selectbox,"手动提供的资料类型").value,D.PURCHASE_ORDER)
        self.assertEqual(widget(app.selectbox,"填写记录").value,record.record_id)
        self.assertEqual(widget(app.text_input,"资料名称").value,"SYN-PO-SECOND")
        self.assertEqual(widget(app.text_input,"买方（提供资料）").value,"SYN-SECOND")

    def test_draft_manager_can_discard_hidden_optional_field_without_writing_its_draft(self):
        from documents.forms import form_fields, form_row_fields
        from documents.drafts import NAMESPACE
        ws=self.case();form=get_form(ws,D.COMMERCIAL_INVOICE)
        service=FormService(self.h.repo)
        ws=service.configure_fields(ws.case.case_id,self.h.actor,D.COMMERCIAL_INVOICE,
            [*form_fields(form),"bank_account"],list(form_row_fields(form)),"Enable optional bank field")
        form=get_form(ws,D.COMMERCIAL_INVOICE)
        service.save(ws.case.case_id,self.h.actor,D.COMMERCIAL_INVOICE,
            dict(form.values,bank_account="SYN-SAVED-BANK"),form.rows,"Known saved bank account")
        app=self.h.app()
        widget(app.text_input,"收款账号").set_value("SYN-UNSAVED-BANK").run()
        ws=self.h.repo.get(ws.case.case_id);form=get_form(ws,D.COMMERCIAL_INVOICE)
        service.configure_fields(ws.case.case_id,self.h.actor,D.COMMERCIAL_INVOICE,
            [name for name in form_fields(form) if name!="bank_account"],list(form_row_fields(form)),
            "Hide optional bank output")
        app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(any(item.label=="收款账号" for item in app.text_input))
        self.assertTrue(any(e.label=="查看和处理未保存内容" for e in app.expander))
        self.assertTrue(any("SYN-UNSAVED-BANK" in str(frame.value.values.tolist()) for frame in app.dataframe))
        self.assertTrue(any("本单还有未保存" in w.value for w in app.warning))
        widget(app.button,"放弃这份暂存").click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(any(e.label=="查看和处理未保存内容" for e in app.expander))
        records=app.session_state[NAMESPACE]
        self.assertFalse(any(r["values"]!=r["base_values"] for r in records.values()))
        self.assertFalse(any("本单还有未保存" in w.value for w in app.warning))
        widget(app.checkbox,"我已人工核对当前资料，确认按现有内容生成").check().run()
        self.assertFalse(widget(app.button,"确认并生成").disabled)
        stored=self.h.repo.get(ws.case.case_id)
        self.assertEqual(get_form(stored,D.COMMERCIAL_INVOICE).values["bank_account"],"SYN-SAVED-BANK")
        self.assertFalse(stored.artifacts)
        self.assertFalse(stored.approvals)

    def test_cleared_checklists_do_not_restore_historical_targets_but_keep_downloads(self):
        from hashlib import sha256
        ws=self.case()
        content=b"Synthetic retained historical artifact"
        artifact=GeneratedArtifact(document_type=D.COMMERCIAL_INVOICE,status="draft",
            filename="synthetic-retained-history.xlsx",storage_path=self.h.blobs.put(content),
            sha256=sha256(content).hexdigest(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        ws.artifacts.append(artifact)
        self.h.repo.save(ws,expected_revision=ws.revision)
        CaseService(self.h.repo,DeterministicRules()).set_document_checklist(
            ws.case.case_id,self.h.actor,[],[],"Explicitly clear this transaction's lists")
        app=self.h.app()
        self.assertFalse(any(item.label=="需要生成的文件" for item in app.selectbox))
        self.assertFalse(any("待处理资料" in getattr(frame.value,"columns",[]) for frame in app.dataframe))
        self.assertFalse(any({"文件","情况","待补/待核实"}<=set(getattr(frame.value,"columns",[]))
                             for frame in app.dataframe))
        self.assertFalse(any("缺失文件（待制作）" in warning.value for warning in app.warning))
        self.assertTrue(any(e.label=="历史版本与多文件打包" for e in app.expander))
        history=next(frame.value for frame in app.dataframe
                     if {"文件","规则审核"}<=set(getattr(frame.value,"columns",[])))
        self.assertIn(artifact.filename,history["文件"].tolist())
        widget(app.multiselect,"选择需要导出的生成文件").select(artifact.artifact_id).run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(app.error,[e.value for e in app.error])
        self.assertTrue(any(item.label=="下载 "+artifact.filename for item in app.get("download_button")))
        stored=self.h.repo.get(ws.case.case_id)
        self.assertEqual(stored.required_delivery_types,[])
        self.assertEqual(stored.required_source_types,[])
        self.assertEqual(len(stored.artifacts),1)
        self.assertEqual(self.h.blobs.get(stored.artifacts[0].storage_path),content)


if __name__=="__main__":
    unittest.main()
