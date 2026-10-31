"""Independent checks that displayed/exported values have honest origins."""
from copy import deepcopy
from io import BytesIO
import unittest

from openpyxl import load_workbook

from core.models import DocumentForm, DocumentType as D, TradeData, TradeLine
from documents.forms import FormService, get_form
from documents.prefill import PrefillService
from documents.simple_export import SimpleExportService
from tests import test_w3_prefill_integration as integration_harness
from tests import test_w3_simple_ui as ui_harness


def origin(origins, field, row=None):
    return next(item for item in origins if item.field_name == field and item.row_number == row)


class ProvenanceIntegrationTests(unittest.TestCase):
    setUp = integration_harness.PrefillIntegrationTests.setUp
    tearDown = integration_harness.PrefillIntegrationTests.tearDown
    source = integration_harness.PrefillIntegrationTests.source

    def origins(self):
        from documents.provenance import form_value_origins
        return form_value_origins(self.repo.get(self.case_id), D.PROFORMA_INVOICE)

    def filled(self):
        workspace, doc_id, _ = self.source(D.PURCHASE_ORDER)
        workspace = PrefillService(self.repo).fill(self.case_id, self.actor, D.PROFORMA_INVOICE, [doc_id])
        return workspace, doc_id

    def test_every_auto_cell_has_filename_version_and_exact_location(self):
        workspace, doc_id = self.filled()
        form = get_form(workspace, D.PROFORMA_INVOICE)
        origins = self.origins()
        cells = [(name, None) for name, value in form.auto_values.items() if value]
        cells += [(name, index) for index, row in enumerate(form.rows, 1)
                  for name, value in row.items() if value]
        self.assertGreater(len(cells), 10)
        for field, row in cells:
            with self.subTest(field=field, row=row):
                item = origin(origins, field, row)
                self.assertEqual(item.origin_kind, "file")
                self.assertEqual(item.document_id, doc_id)
                self.assertEqual(item.filename, "synthetic-source.xlsx")
                self.assertEqual(item.version_number, 1)
                self.assertTrue(item.version_id)
                self.assertIn("Source", item.location)
                self.assertTrue(any(char.isdigit() for char in item.location))
        amount = origin(origins, "amount", 1)
        self.assertEqual(amount.origin_kind, "derived")
        self.assertEqual(amount.value, "10.00")

    def test_editing_one_cell_does_not_relabel_unchanged_source_cells_as_manual(self):
        workspace, _ = self.filled()
        form = get_form(workspace, D.PROFORMA_INVOICE)
        rows = deepcopy(form.rows)
        rows[0]["quantity"] = "9"
        FormService(self.repo).save(self.case_id, self.actor, D.PROFORMA_INVOICE,
            dict(form.values, buyer="Synthetic corrected buyer"), rows, "Synthetic specific corrections")
        origins = self.origins()
        for field, row in (("buyer", None), ("quantity", 1)):
            item = origin(origins, field, row)
            self.assertEqual(item.origin_kind, "manual")
            self.assertEqual(item.actor_id, self.actor.user_id)
            self.assertIsNotNone(item.occurred_at)
        for field, row in (("order_no", None), ("description", 1), ("quantity", 2)):
            self.assertEqual(origin(origins, field, row).origin_kind, "file")
        preview = SimpleExportService(self.repo, self.blobs).preview(
            self.case_id, self.actor, D.PROFORMA_INVOICE)
        workbook = load_workbook(BytesIO(preview.content), read_only=True)
        try:
            source_rows = list(workbook["字段来源"].values)
            self.assertTrue(any("synthetic-source.xlsx" in row for row in source_rows))
            self.assertTrue(any(self.actor.user_id in row for row in source_rows))
        finally:
            workbook.close()

    def test_untouched_defaults_and_unknown_legacy_values_are_not_fake_manual_entries(self):
        origins = self.origins()
        # An existing create audit records the order value; it cannot reveal
        # whether USD was explicitly chosen or left at the initial UI default.
        self.assertEqual(origin(origins, "currency").origin_kind, "default")
        workspace = self.repo.get(self.case_id)
        workspace.audit = []
        self.repo.save(workspace, expected_revision=workspace.revision)
        self.assertEqual(origin(self.origins(), "currency").origin_kind, "default")
        self.assertEqual(origin(self.origins(), "currency").actor_id, "")
        workspace.document_forms.append(DocumentForm(D.PROFORMA_INVOICE,
            values={"buyer": "Synthetic legacy buyer"},
            rows=[{"description": "Synthetic legacy row", "quantity": "4"}]))
        self.repo.save(workspace, expected_revision=workspace.revision)
        origins = self.origins()
        for field, row in (("buyer", None), ("description", 1), ("quantity", 1)):
            self.assertEqual(origin(origins, field, row).origin_kind, "unknown")
            self.assertEqual(origin(origins, field, row).actor_id, "")

    def test_deleting_first_row_keeps_unchanged_second_row_tied_to_original_cells(self):
        workspace, _ = self.filled()
        form = get_form(workspace, D.PROFORMA_INVOICE)
        FormService(self.repo).save(self.case_id, self.actor, D.PROFORMA_INVOICE,
            form.values, [form.rows[1]], "Synthetic first row removed")
        origins = self.origins()
        for name, cell in (("description", "B6"), ("quantity", "C6"), ("unit_price", "D6")):
            item = origin(origins, name, 1)
            self.assertEqual(item.origin_kind, "file")
            self.assertIn(cell, item.location)

    def test_order_fallback_is_identified_as_order_data_and_not_source_file(self):
        self.cases.update_trade(self.case_id, self.actor,
            TradeData(buyer="Synthetic master buyer", line_items=[
                TradeLine("SYN-M", "Synthetic master row", "2", "7", "pcs")]),
            "Synthetic master values")
        origins = self.origins()
        for field, row in (("buyer", None), ("description", 1), ("quantity", 1)):
            self.assertEqual(origin(origins, field, row).origin_kind, "trade")
            self.assertEqual(origin(origins, field, row).filename, "")


class ProvenanceUITests(unittest.TestCase):
    setUp = ui_harness.SimpleUITests.setUp
    tearDown = ui_harness.SimpleUITests.tearDown
    app = ui_harness.SimpleUITests.app

    def test_current_value_source_table_shows_actual_file_version_and_cell(self):
        from case_workflow.service import CaseService
        from documents.service import DocumentService
        from documents.workbench import collect_document
        from validation.rules import DeterministicRules
        workspace = CaseService(self.repo, DeterministicRules()).create(self.actor, TradeData(),
            required_types=[D.PROFORMA_INVOICE, D.COMMERCIAL_INVOICE, D.PACKING_LIST])
        documents = DocumentService(self.repo, self.blobs)
        workspace = documents.upload(workspace.case.case_id, self.actor, "synthetic-visible-source.xlsx",
            integration_harness.synthetic_workbook(), D.PROFORMA_INVOICE)
        collect_document(self.repo, self.blobs, workspace.case.case_id,
                         workspace.documents[-1].document_id, self.actor)
        app = self.app()
        ui_harness.widget(app.selectbox, "需要生成的文件").select(D.PROFORMA_INVOICE).run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        tables = [item.value.astype(str).to_numpy().ravel().tolist() for item in app.dataframe]
        self.assertTrue(any("synthetic-visible-source.xlsx" in table for table in tables))
        self.assertTrue(any(any("Source" in str(cell) for cell in table)
                            and "Synthetic source buyer" in table for table in tables))
        current = next(item.value for item in app.dataframe if "来源类型" in item.value.columns
                       and "当前内容" in item.value.columns)
        buyer = current[current["填写项"] == "买方"].iloc[0]
        self.assertEqual(buyer["来源类型"], "来源文件")
        self.assertEqual(buyer["版本"], "v1")
        self.assertIn("B1", buyer["表格/位置"])
        first_quantity = current[(current["填写位置"] == "明细第 1 行")
                                 & (current["填写项"] == "数量")].iloc[0]
        self.assertEqual(first_quantity["来源类型"], "来源文件")
        self.assertIn("C5", first_quantity["表格/位置"])
        form = get_form(self.repo.get(workspace.case.case_id), D.PROFORMA_INVOICE)
        from documents.drafts import DraftStore
        editor_key = DraftStore(app.session_state).widget_key(workspace.case.case_id,
            self.actor.user_id + ":full-file", D.PROFORMA_INVOICE.value, "rows")
        app.session_state[editor_key] = {"edited_rows": {0: {"quantity": "9"}},
                                         "added_rows": [], "deleted_rows": []}
        ui_harness.widget(app.button, "保存补充内容").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        current = next(item.value for item in app.dataframe if "来源类型" in item.value.columns
                       and "当前内容" in item.value.columns)
        first_quantity = current[(current["填写位置"] == "明细第 1 行")
                                 & (current["填写项"] == "数量")].iloc[0]
        self.assertEqual(first_quantity["当前内容"], "9")
        self.assertEqual(first_quantity["来源类型"], "人工填写")
        self.assertEqual(first_quantity["办理人"], self.actor.user_id)
        unchanged_buyer = current[current["填写项"] == "买方"].iloc[0]
        self.assertEqual(unchanged_buyer["来源类型"], "来源文件")


if __name__ == "__main__":
    unittest.main()

