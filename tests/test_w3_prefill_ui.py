"""UI integration for selecting source candidates and exporting current edits."""
from io import BytesIO
import unittest

from openpyxl import load_workbook

from core.models import DocumentType as D, TradeData, TradeLine
from documents.forms import get_form
from documents.service import DocumentService
from documents.workbench import collect_document
from tests.test_w3_prefill_integration import synthetic_workbook
from tests import test_w3_simple_ui as ui_harness

widget = ui_harness.widget


class PrefillUITests(unittest.TestCase):
    # Reuse only the isolated harness, not its existing test methods.
    setUp = ui_harness.SimpleUITests.setUp
    tearDown = ui_harness.SimpleUITests.tearDown
    app = ui_harness.SimpleUITests.app

    def test_selected_source_prefills_visible_editable_form_then_exports_latest_edits(self):
        from case_workflow.service import CaseService
        from validation.rules import DeterministicRules
        workspace = CaseService(self.repo, DeterministicRules()).create(self.actor, TradeData())
        documents = DocumentService(self.repo, self.blobs)
        workspace = documents.upload(workspace.case.case_id, self.actor, "synthetic-po.xlsx",
            synthetic_workbook(), D.PURCHASE_ORDER)
        document = workspace.documents[-1]
        collect_document(self.repo, self.blobs, workspace.case.case_id, document.document_id, self.actor)
        app = self.app()
        widget(app.selectbox, "需要生成的文件").select(D.PROFORMA_INVOICE).run()
        widget(app.multiselect, "选择填表来源").select(document.document_id).run()
        widget(app.button, "将采集结果填入当前表单").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertEqual(widget(app.text_input, "买方").value, "Synthetic source buyer")
        stored = get_form(self.repo.get(workspace.case.case_id), D.PROFORMA_INVOICE)
        self.assertIsNone(stored.confirmed_by)
        self.assertEqual(len(stored.rows), 2)
        editor_key = f"simple-form-rows-{workspace.case.case_id}-{D.PROFORMA_INVOICE.value}-{stored.revision}"
        # AppTest exposes the data editor's public session-state delta protocol.
        app.session_state[editor_key] = {"edited_rows": {0: {"quantity": "9"}},
                                         "added_rows": [], "deleted_rows": []}
        widget(app.text_input, "买方").set_value("Synthetic UI edited buyer")
        widget(app.button, "保存补充内容").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        stored = get_form(self.repo.get(workspace.case.case_id), D.PROFORMA_INVOICE)
        self.assertEqual(stored.values["buyer"], "Synthetic UI edited buyer")
        self.assertEqual(stored.rows[0]["quantity"], "9")
        self.assertEqual(widget(app.text_input, "买方").value, "Synthetic UI edited buyer")
        widget(app.checkbox, "我已人工核对当前资料，确认按现有内容生成").check().run()
        widget(app.button, "生成 Excel").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        result = self.repo.get(workspace.case.case_id)
        self.assertFalse(result.approvals)
        self.assertEqual(result.artifacts[-1].form_snapshot["rows"][0]["quantity"], "9")
        workbook = load_workbook(BytesIO(self.blobs.get(result.artifacts[-1].storage_path)), read_only=True)
        try:
            rows = list(workbook["文件"].values)
            self.assertTrue(any("Synthetic UI edited buyer" in row for row in rows))
            self.assertTrue(any("Synthetic first item" in row and "9" in map(str, row) for row in rows))
        finally:
            workbook.close()

    def test_manual_trade_rows_visible_for_pi_without_source_file(self):
        from case_workflow.service import CaseService
        from validation.rules import DeterministicRules
        service = CaseService(self.repo, DeterministicRules())
        workspace = service.create(self.actor, TradeData(buyer="Synthetic manual buyer",
            line_items=[TradeLine("SYN-M", "Synthetic manual item", "2", "7", "pcs")]))
        app = self.app()
        widget(app.selectbox, "需要生成的文件").select(D.PROFORMA_INVOICE).run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertEqual(widget(app.text_input, "买方").value, "Synthetic manual buyer")
        self.assertTrue(any("Synthetic manual item" in str(item.value) for item in app.dataframe))
        widget(app.checkbox, "我已人工核对当前资料，确认按现有内容生成").check().run()
        widget(app.button, "生成 Excel").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        result = self.repo.get(workspace.case.case_id)
        self.assertFalse(result.documents)
        self.assertEqual(len(result.artifacts), 1)

    def test_deleting_trade_fallback_rows_does_not_restore_them_after_save(self):
        from case_workflow.service import CaseService
        from validation.rules import DeterministicRules
        from documents.forms import effective_rows
        workspace = CaseService(self.repo, DeterministicRules()).create(self.actor,
            TradeData(line_items=[TradeLine("SYN-CLEAR", "Synthetic row to clear", "2", "3")]))
        app = self.app()
        editor_key = f"simple-form-rows-{workspace.case.case_id}-{D.COMMERCIAL_INVOICE.value}-0"
        app.session_state[editor_key] = {"edited_rows": {}, "added_rows": [], "deleted_rows": [0]}
        widget(app.button, "保存补充内容").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        saved = self.repo.get(workspace.case.case_id)
        form = get_form(saved, D.COMMERCIAL_INVOICE)
        self.assertTrue(form.rows_manually_edited)
        self.assertEqual(effective_rows(saved.trade, form), [])
        self.assertTrue(saved.trade.line_items)


if __name__ == "__main__":
    unittest.main()
