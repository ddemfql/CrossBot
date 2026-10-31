"""Two-tab UI keeps review findings beside the formal-generation gate."""
import unittest

from core.models import CaseStatus, DocumentType as D, TradeData
from documents.forms import FormService
from tests import test_w3_simple_ui as ui_harness
from tests.test_w3_strict_review_integration import complete_row, complete_values


widget = ui_harness.widget


class StrictReviewUITests(unittest.TestCase):
    setUp = ui_harness.SimpleUITests.setUp
    tearDown = ui_harness.SimpleUITests.tearDown
    app = ui_harness.SimpleUITests.app

    def create(self, complete=False):
        from case_workflow.service import CaseService
        from validation.rules import DeterministicRules
        workspace = CaseService(self.repo, DeterministicRules()).create(self.actor, TradeData(),
            required_types=[D.COMMERCIAL_INVOICE, D.PACKING_LIST, D.PROFORMA_INVOICE])
        if complete:
            FormService(self.repo).save(workspace.case.case_id, self.actor, D.PROFORMA_INVOICE,
                complete_values(D.PROFORMA_INVOICE), [complete_row(D.PROFORMA_INVOICE)],
                "Synthetic complete manual PI")
        return workspace.case.case_id

    def test_incomplete_review_is_visible_without_adding_workbench_tabs(self):
        case_id = self.create()
        app = self.app()
        self.assertEqual([tab.label for tab in app.tabs], ["文件分类与查看", "生成与导出"])
        self.assertTrue(any("审核结果" in item.value for item in app.markdown))
        widget(app.checkbox, "我已人工核对当前资料，确认按现有内容生成").check().run()
        self.assertTrue(widget(app.button, "确认并生成").disabled)
        widget(app.button, "预览文件").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertFalse(self.repo.get(case_id).artifacts)
        self.assertFalse(self.repo.get(case_id).approvals)

    def test_complete_manual_pi_enables_generation_only_after_human_confirmation(self):
        case_id = self.create(complete=True)
        app = self.app()
        widget(app.selectbox, "需要生成的文件").select(D.PROFORMA_INVOICE).run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertTrue(widget(app.button, "确认并生成").disabled)
        widget(app.checkbox, "我已人工核对当前资料，确认按现有内容生成").check().run()
        self.assertFalse(widget(app.button, "确认并生成").disabled)
        widget(app.button, "确认并生成").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        result = self.repo.get(case_id)
        self.assertEqual(len(result.artifacts), 1)
        self.assertFalse(result.documents)
        self.assertFalse(result.approvals)
        self.assertNotEqual(result.case.status, CaseStatus.PASSED)
        self.assertTrue(widget(app.button, "确认并生成").disabled)


if __name__ == "__main__":
    unittest.main()
