"""Synthetic end-to-end corrections directly from audit findings."""
import unittest
from core.models import TradeData, DocumentType as D
from case_workflow.service import CaseService
from validation.rules import DeterministicRules
from documents.forms import FormService, get_form
from documents.simple_export import SimpleExportService
from tests import test_w3_simple_ui as harness

widget = harness.widget


class ReviewEditorUITests(unittest.TestCase):
    setUp = harness.SimpleUITests.setUp
    tearDown = harness.SimpleUITests.tearDown
    app = harness.SimpleUITests.app

    def create(self, trade=None):
        return CaseService(self.repo, DeterministicRules()).create(self.actor,
            trade or TradeData(order_no="SYN-REVIEW-UI", currency=""))

    def pick_problem(self, app, case, code):
        report = SimpleExportService(self.repo, self.blobs).assess(case.case.case_id,
            self.actor, D.COMMERCIAL_INVOICE, human_confirmed=True)
        issues = [c for c in report.checks if c.required and c.status.value != "pass"
            and c.rule_code not in {"EXPORT_HUMAN_CONFIRMATION", "DOCUMENT_COMPLETENESS"}]
        index = next(i for i, c in enumerate(issues) if c.rule_code == code)
        widget(app.selectbox, "选择要处理的审核问题").select(index).run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        return app

    def pick_location(self, app, text):
        choices = widget(app.selectbox, "要修改的位置").options
        widget(app.selectbox, "要修改的位置").select(next(i for i, label in enumerate(choices) if text in label)).run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def test_missing_scalar_can_be_filled_from_review_and_confirmation_resets(self):
        case = self.create()
        app = self.app()
        widget(app.checkbox, "我已人工核对当前资料，确认按现有内容生成").check().run()
        self.pick_problem(app, case, "INPUT_DATA_REQUIREMENTS")
        self.pick_location(app, "单据日期")
        widget(app.text_input, "修改后的内容").set_value("2026-10-31")
        widget(app.button, "保存修改并重新审核").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        saved = self.repo.get(case.case.case_id)
        self.assertEqual(get_form(saved, D.COMMERCIAL_INVOICE).values["document_date"], "2026-10-31")
        self.assertFalse(widget(app.checkbox, "我已人工核对当前资料，确认按现有内容生成").value)
        self.assertFalse(saved.approvals)
        self.assertFalse(saved.artifacts)

    def test_order_conflict_offers_two_locations_and_edits_only_selected_form(self):
        case = self.create(TradeData(order_no="SYN-REVIEW-UI", buyer="Synthetic buyer A", currency=""))
        FormService(self.repo).save(case.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"buyer": "Synthetic buyer B"}, [], "Synthetic conflict")
        app = self.app()
        self.pick_problem(app, case, "ORDER_IDENTITY")
        labels = widget(app.selectbox, "要修改的位置").options
        self.assertTrue(any("订单资料" in label for label in labels))
        self.assertTrue(any("商业发票" in label for label in labels))
        self.pick_location(app, "商业发票")
        widget(app.text_input, "修改后的内容").set_value("Synthetic buyer A")
        widget(app.button, "保存修改并重新审核").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        saved = self.repo.get(case.case.case_id)
        self.assertEqual(saved.trade.buyer, "Synthetic buyer A")
        self.assertEqual(get_form(saved, D.COMMERCIAL_INVOICE).values["buyer"], "Synthetic buyer A")
        checks = SimpleExportService(self.repo, self.blobs).assess(case.case.case_id, self.actor,
            D.COMMERCIAL_INVOICE, human_confirmed=True).checks
        self.assertFalse(any(c.rule_code == "ORDER_IDENTITY" and c.status.value != "pass" for c in checks))

    def test_numeric_row_error_opens_precise_cell(self):
        case = self.create()
        FormService(self.repo).save(case.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {}, [{"product_code": "SYN-SKU", "description": "Synthetic item", "quantity": "invalid",
                  "unit": "pcs", "unit_price": "2"}], "Synthetic invalid row")
        app = self.app()
        self.pick_problem(app, case, "NUMBER_VALID")
        self.assertIn("明细第 1 行", widget(app.selectbox, "要修改的位置").options[0])
        self.assertEqual(widget(app.text_input, "修改后的内容").value, "invalid")
        widget(app.text_input, "修改后的内容").set_value("3")
        widget(app.button, "保存修改并重新审核").click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        row = get_form(self.repo.get(case.case.case_id), D.COMMERCIAL_INVOICE).rows[0]
        self.assertEqual((row["quantity"], row["product_code"], row["unit_price"]), ("3", "SYN-SKU", "2"))


if __name__ == "__main__":
    unittest.main()

