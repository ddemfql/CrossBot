"""UI recovery for cleared data and superseded manually supplied sources."""
import unittest

from tests import test_w3_simple_ui as ui
from case_workflow.service import CaseService
from core.models import DocumentType as D, TradeData
from documents.forms import get_form
from documents.manual_inputs import ManualInputService, has_manual_data
from validation.rules import DeterministicRules


class ManualInputUIEdgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ui.SimpleUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.repo, self.actor = self.fixture.repo, self.fixture.actor
        cases = CaseService(self.repo, DeterministicRules())
        self.ws = cases.create(self.actor, TradeData(currency=""), required_types=[])
        cases.set_document_checklist(self.ws.case.case_id, self.actor,
                                     [D.PURCHASE_ORDER], [D.COMMERCIAL_INVOICE])
        self.service = ManualInputService(self.repo)
        self.service.save(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
                          {"buyer": "Synthetic Buyer"}, [], "Known buyer manually supplied")

    def test_clearing_last_manual_value_restores_missing_input(self):
        app = self.fixture.app()
        ui.widget(app.radio, "补充方式").set_value("手动填写").run()
        ui.widget(app.text_input, "买方（提供资料）").set_value("")
        ui.widget(app.button, "保存手动提供的资料").click().run()
        self.assertFalse(app.exception)
        current = self.repo.get(self.ws.case.case_id)
        self.assertFalse(has_manual_data(get_form(current, D.PURCHASE_ORDER)))
        self.assertTrue(any("待提供来源资料" in item.value for item in app.caption))
        self.assertFalse(current.documents)

    def test_cleared_source_can_be_detached_with_explicit_human_action(self):
        self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
                           D.COMMERCIAL_INVOICE, "Use known buyer")
        self.service.save(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
                          {}, [], "Clear superseded source")
        app = self.fixture.app()
        button = "解除所选资料关联，保留填写内容"
        self.assertTrue(ui.widget(app.button, button).disabled)
        ui.widget(app.checkbox, "我已核对保留内容，改为独立维护此文件").check().run()
        ui.widget(app.button, button).click().run()
        self.assertFalse(app.exception)
        current = self.repo.get(self.ws.case.case_id)
        form = get_form(current, D.COMMERCIAL_INVOICE)
        self.assertFalse(form.source_form_references)
        self.assertEqual(form.values["buyer"], "Synthetic Buyer")
        self.assertFalse(current.approvals)
        self.assertFalse(current.artifacts)
        self.assertTrue(ui.widget(app.button, "确认并生成").disabled)


if __name__ == "__main__":
    unittest.main()
