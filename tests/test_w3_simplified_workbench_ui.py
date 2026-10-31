"""Role-aware compact workspace controls and personal-default UI wiring."""
import unittest

from core.models import Actor, DocumentType as D, TradeData, to_dict
from documents.forms import FormService, get_form
from case_workflow.service import CaseService
from validation.rules import DeterministicRules
import tests.test_w3_simple_ui as harness


def widget(items, label):
    return harness.widget(items, label)


def upload_labels(app):
    return [item.proto.label for item in app.get("file_uploader")]


class SimplifiedWorkbenchUITests(unittest.TestCase):
    setUp = harness.SimpleUITests.setUp
    tearDown = harness.SimpleUITests.tearDown
    app = harness.SimpleUITests.app

    def workspace(self):
        return CaseService(self.repo, DeterministicRules()).create(self.actor,
            TradeData(order_no="SYN-UI-SETTINGS", currency=""))

    def assert_clean(self, app):
        self.assertFalse(app.exception, [item.message for item in app.exception])

    def test_sales_keeps_own_review_but_has_no_template_management(self):
        self.workspace()
        app = self.app()
        self.assertEqual(widget(app.radio, "设置内容").options, ["文件清单", "文件资料项", "我的默认"])
        self.assertFalse(any("模板" in label for label in upload_labels(app)))
        self.assertFalse(any(item.label == "保存模板版本" for item in app.button))
        self.assertIsNotNone(widget(app.checkbox, "我已人工核对当前资料，确认按现有内容生成"))
        self.assertTrue(widget(app.button, "确认并生成").disabled)
        self.assertIsNotNone(widget(app.button, "预览文件"))

    def test_admin_explicit_template_setting_exposes_upload(self):
        self.workspace()
        app = self.app(Actor("synthetic-admin", ["admin"], authenticated=True))
        self.assertIn("模板管理", widget(app.radio, "设置内容").options)
        self.assertFalse(any("模板" in label for label in upload_labels(app)))
        widget(app.radio, "设置内容").set_value("模板管理").run()
        self.assert_clean(app)
        self.assertIn("上传此文件的 Excel 模板", upload_labels(app))
        self.assertTrue(widget(app.button, "保存模板版本").disabled)

    def test_large_reference_tables_collapsed_and_input_mode_mounts_only_selected_controls(self):
        self.workspace()
        app = self.app()
        for label in ("全部已提供资料与用途", "逐项对应生成所需数据与来源", "当前填写内容及来源"):
            expander = next(item for item in app.expander if item.label == label)
            self.assertFalse(expander.proto.expanded, label)
        self.assertEqual(widget(app.radio, "补充方式").value, "上传文件")
        self.assertIn("选择原始文件", upload_labels(app))
        self.assertFalse(any(item.label == "手动提供的资料类型" for item in app.selectbox))
        self.assertFalse(any(item.label == "本次补充哪一项" for item in app.selectbox))
        widget(app.radio, "补充方式").set_value("手动填写").run()
        self.assert_clean(app)
        self.assertIsNotNone(widget(app.selectbox, "手动提供的资料类型"))
        self.assertNotIn("选择原始文件", upload_labels(app))
        self.assertFalse(any(item.label == "本次补充哪一项" for item in app.selectbox))
        widget(app.radio, "补充方式").set_value("按缺项补充").run()
        self.assert_clean(app)
        self.assertIsNotNone(widget(app.selectbox, "本次补充哪一项"))
        self.assertNotIn("为此资料项上传文件", upload_labels(app))
        widget(app.radio, "此项资料的提供方式").set_value("上传原件").run()
        self.assertIn("为此资料项上传文件", upload_labels(app))
        self.assertNotIn("选择原始文件", upload_labels(app))
        self.assertFalse(any(item.label == "手动提供的资料类型" for item in app.selectbox))

    def test_required_hidden_input_is_editable_without_printing_it(self):
        case = self.workspace()
        FormService(self.repo).configure_fields(case.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            ["notes"], [], "Synthetic hidden input", context_fields=[],
            required_input_fields=["bank_account"])
        app = self.app()
        self.assert_clean(app)
        from documents.catalog import FORM_FIELDS
        label = FORM_FIELDS[D.COMMERCIAL_INVOICE]["bank_account"]
        widget(app.text_input, label).set_value("SYN-BANK-INPUT")
        widget(app.button, "保存补充内容").click().run()
        self.assert_clean(app)
        from documents.forms import input_values, effective_values
        saved = self.repo.get(case.case.case_id)
        form = get_form(saved, D.COMMERCIAL_INVOICE)
        self.assertEqual(input_values(saved.trade, form)["bank_account"], "SYN-BANK-INPUT")
        self.assertNotIn("bank_account", effective_values(saved.trade, form))

    def test_personal_default_ui_saves_only_choices_and_next_case_uses_them(self):
        case = self.workspace()
        untouched = CaseService(self.repo, DeterministicRules()).create(self.actor,
            TradeData(order_no="SYN-UNTOUCHED", currency=""))
        original = to_dict(untouched)
        cases = CaseService(self.repo, DeterministicRules())
        cases.set_document_checklist(case.case.case_id, self.actor, [D.PURCHASE_ORDER], [D.COMMERCIAL_INVOICE])
        FormService(self.repo).configure_fields(case.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            ["notes"], [], "Fixture of configured personal choices", context_fields=[],
            required_input_fields=["bank_account"])
        app = self.app()
        widget(app.selectbox, "选择交易").select(case.case.case_id).run()
        widget(app.radio, "设置内容").set_value("文件资料项").run()
        self.assert_clean(app)
        tables = [item.value for item in app.dataframe]
        options = next(table for table in tables if {"资料项", "需要提供", "导出显示"} <= set(table.columns))
        bank = options[options["字段"] == "bank_account"].iloc[0]
        self.assertTrue(bank["需要提供"])
        self.assertFalse(bank["导出显示"])
        widget(app.radio, "设置内容").set_value("我的默认").run()
        widget(app.button, "将本单配置保存为我的默认").click().run()
        self.assert_clean(app)
        module = __import__("w3_app")
        saved = module.defaults_store.get(self.actor.user_id)
        self.assertEqual(saved.revision, 1)
        self.assertEqual(saved.delivery_types, [D.COMMERCIAL_INVOICE])
        ci = next(item for item in saved.documents if item.document_type is D.COMMERCIAL_INVOICE)
        self.assertEqual(ci.required_input_fields, ["bank_account"])
        before = {item.case.case_id for item in self.repo.list()}
        widget(app.button, "新建交易").click().run()
        self.assert_clean(app)
        created = next(item for item in self.repo.list() if item.case.case_id not in before)
        self.assertEqual(created.required_delivery_types, [D.COMMERCIAL_INVOICE])
        self.assertEqual(created.required_source_types, [D.PURCHASE_ORDER])
        self.assertEqual(get_form(created, D.COMMERCIAL_INVOICE).required_input_fields, ["bank_account"])
        self.assertEqual((created.defaults_user_id, created.defaults_revision), (self.actor.user_id, 1))
        self.assertEqual(to_dict(self.repo.get(untouched.case.case_id)), original)


if __name__ == "__main__":
    unittest.main()
