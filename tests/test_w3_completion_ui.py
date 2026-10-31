"""Real UI edges for the input-to-deliverable completion path, synthetic only."""
import unittest
from core.models import DocumentType as D, TradeData
from case_workflow.service import CaseService
from validation.rules import DeterministicRules
from documents.manual_inputs import ManualInputService
from documents.service import DocumentService
from tests import test_w3_simple_ui as ui_harness
widget = ui_harness.widget


class CompletionUITests(unittest.TestCase):
    setUp = ui_harness.SimpleUITests.setUp
    tearDown = ui_harness.SimpleUITests.tearDown
    app = ui_harness.SimpleUITests.app

    def create(self):
        return CaseService(self.repo, DeterministicRules()).create(self.actor, TradeData(currency=""))

    def test_switching_two_same_revision_manual_records_never_reuses_other_values(self):
        ws = self.create()
        service = ManualInputService(self.repo)
        first = service.save_record(ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            {"buyer": "Synthetic A"}, [], "Synthetic audit", title="PO A").manual_data_records[-1]
        second = service.save_record(ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            {"buyer": "Synthetic B"}, [], "Synthetic audit", title="PO B").manual_data_records[-1]
        app = self.app()
        widget(app.radio, "补充方式").set_value("手动填写").run()
        widget(app.selectbox, "填写记录").select(first.record_id).run()
        self.assertFalse(app.exception)
        self.assertEqual(widget(app.text_input, "买方（提供资料）").value, "Synthetic A")
        widget(app.text_input, "买方（提供资料）").set_value("Synthetic unsaved A")
        widget(app.selectbox, "填写记录").select(second.record_id).run()
        self.assertFalse(app.exception)
        self.assertEqual(widget(app.text_input, "买方（提供资料）").value, "Synthetic B")
        self.assertEqual(len(self.repo.get(ws.case.case_id).manual_data_records), 2)

    def test_missing_original_does_not_break_forms_or_other_tab(self):
        ws = self.create()
        ws = DocumentService(self.repo, self.blobs).upload(ws.case.case_id, self.actor,
            "synthetic.txt", b"synthetic data", D.PURCHASE_ORDER)
        version = ws.documents[-1].versions[-1]
        # Remove only this isolated test's blob, inside TemporaryDirectory.
        (self.blobs.root / version.storage_path).unlink()
        app = self.app()
        self.assertFalse(app.exception)
        self.assertTrue(any("不可读取" in e.value for e in app.error))
        self.assertIsNotNone(widget(app.button, "预览文件"))
        self.assertEqual(len(app.tabs), 2)

    def test_pdf_preview_uses_real_renderer_without_writing_official_artifact(self):
        ws = self.create()
        app = self.app()
        widget(app.selectbox, "导出格式").select("PDF").run()
        widget(app.button, "预览文件").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        saved = self.repo.get(ws.case.case_id)
        self.assertFalse(saved.artifacts)
        self.assertFalse(saved.approvals)
        self.assertTrue(widget(app.button, "确认并生成").disabled)

    def test_generation_choices_are_exactly_selected_deliverables(self):
        self.create()
        app = self.app()
        self.assertEqual(widget(app.selectbox, "需要生成的文件").options,
                         ["商业发票 CI", "装箱单 PL"])
        mapped = next(d.value for d in app.dataframe if "用于生成" in d.value.columns
                      and "资料项" in d.value.columns)
        self.assertEqual(set(mapped["用于生成"]), {"商业发票 CI", "装箱单 PL"})
        self.assertIn("产品/业务明细", set(mapped["资料项"]))

    def test_optional_fields_can_be_removed_and_restored_without_erasing_saved_data(self):
        from documents.forms import FormService, get_form, form_fields
        ws = self.create()
        FormService(self.repo).save(ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"notes": "Synthetic retained optional note"}, [], "Synthetic optional record")
        app = self.app()
        widget(app.radio, "设置内容").set_value("文件资料项").run()
        from documents.catalog import FORM_FIELDS
        index = list(FORM_FIELDS[D.COMMERCIAL_INVOICE]).index("notes")
        form = get_form(self.repo.get(ws.case.case_id), D.COMMERCIAL_INVOICE)
        editor = f"field-options-{ws.case.case_id}-{D.COMMERCIAL_INVOICE.value}-表头-{form.revision}"
        app.session_state[editor] = {"edited_rows": {index: {"导出显示": True}}, "added_rows": [], "deleted_rows": []}
        widget(app.button, "保存本文件资料项").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        form = get_form(self.repo.get(ws.case.case_id), D.COMMERCIAL_INVOICE)
        self.assertIn("notes", form_fields(form))
        editor = f"field-options-{ws.case.case_id}-{D.COMMERCIAL_INVOICE.value}-表头-{form.revision}"
        app.session_state[editor] = {"edited_rows": {index: {"导出显示": False}}, "added_rows": [], "deleted_rows": []}
        widget(app.button, "保存本文件资料项").click().run()
        self.assertFalse(app.exception)
        form = get_form(self.repo.get(ws.case.case_id), D.COMMERCIAL_INVOICE)
        self.assertNotIn("notes", form_fields(form))
        self.assertEqual(form.values["notes"], "Synthetic retained optional note")
        self.assertNotIn("备注", widget(app.multiselect, "显示填写项").options)

    def test_default_output_list_can_be_adjusted_and_reset(self):
        self.create()
        app = self.app()
        widget(app.multiselect, "需要制作并交付的文件").unselect(D.PACKING_LIST)
        widget(app.multiselect, "需要制作并交付的文件").select(D.QUOTATION)
        widget(app.button, "保存两类清单").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(set(widget(app.selectbox, "需要生成的文件").options), {"商业发票 CI", "报价单"})
        widget(app.button, "恢复默认生成清单（发票和装箱单）").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(widget(app.selectbox, "需要生成的文件").options, ["商业发票 CI", "装箱单 PL"])
        widget(app.radio, "补充方式").set_value("按缺项补充").run()
        widget(app.radio, "此项资料的提供方式").set_value("上传原件").run()
        self.assertTrue(widget(app.button, "上传并关联此资料项").disabled)


if __name__ == "__main__":
    unittest.main()
