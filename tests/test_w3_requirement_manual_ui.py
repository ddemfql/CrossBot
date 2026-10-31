"""Direct missing-input entry shares the precise requirement selector with upload."""
from io import BytesIO
import unittest

from openpyxl import load_workbook
from case_workflow.service import CaseService
from core.models import (CaseStatus, DocumentType as D, DocumentStatus as DS,
                         ExtractedField, FieldValueStatus as F, TradeData)
from documents.forms import FormService, get_form, effective_values
from documents.service import DocumentService
from documents.simple_export import SimpleExportService
from validation.rules import DeterministicRules
from tests import test_w3_simple_ui as harness


class RequirementManualUITests(unittest.TestCase):
    setUp = harness.SimpleUITests.setUp
    tearDown = harness.SimpleUITests.tearDown
    app = harness.SimpleUITests.app

    def case(self, *, buyer="Synthetic Buyer", second_quantity="3", kind=D.COMMERCIAL_INVOICE):
        ws = CaseService(self.repo, DeterministicRules()).create(self.actor,
            TradeData(currency=""), required_types=[kind])
        values = {"document_no": "SYN-1", "document_date": "2026-10-31", "order_no": "SYN-ORDER",
                  "buyer": buyer, "seller": "Synthetic Seller"}
        if kind is D.COMMERCIAL_INVOICE:
            values["currency"] = "USD"
        rows = [{"product_code": "A", "description": "Item A", "quantity": "2", "unit": "pcs"},
                {"product_code": "B", "description": "Item B", "quantity": second_quantity, "unit": "pcs"}]
        for row in rows:
            row.update({"unit_price": "10"} if kind is D.COMMERCIAL_INVOICE else
                       {"packages": "1", "gross_weight_kg": "2", "net_weight_kg": "1"})
        return FormService(self.repo).save(ws.case.case_id, self.actor, kind, values, rows,
                                           "Synthetic known seller fields")

    def open_slot(self, app, kind, field, row=None):
        harness.widget(app.radio, "补充方式").set_value("按缺项补充").run()
        self.assertFalse(app.exception)
        harness.widget(app.selectbox, "本次补充哪一项").select((kind, field, row)).run()
        self.assertFalse(app.exception)
        self.assertEqual(harness.widget(app.radio, "此项资料的提供方式").value, "直接填写")
        return app

    def save_text(self, app, value):
        harness.widget(app.text_input, "填写内容").set_value(value)
        harness.widget(app.button, "保存此项资料").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])

    def assert_data_only(self, ws):
        self.assertFalse(ws.artifacts)
        self.assertFalse(ws.approvals)
        self.assertIsNot(ws.case.status, CaseStatus.PASSED)

    def test_missing_buyer_can_be_saved_directly_without_an_original(self):
        ws = self.case(buyer="")
        app = self.open_slot(self.app(), D.COMMERCIAL_INVOICE, "buyer")
        self.save_text(app, "Direct Synthetic Buyer")
        saved = self.repo.get(ws.case.case_id)
        self.assertEqual(get_form(saved, D.COMMERCIAL_INVOICE).values["buyer"], "Direct Synthetic Buyer")
        self.assertFalse(saved.documents)
        self.assert_data_only(saved)

    def test_quantity_entry_changes_only_the_chosen_product_row(self):
        ws = self.case(second_quantity="")
        app = self.open_slot(self.app(), D.COMMERCIAL_INVOICE, "quantity", 2)
        self.save_text(app, "7")
        saved = self.repo.get(ws.case.case_id)
        self.assertEqual([r["quantity"] for r in get_form(saved, D.COMMERCIAL_INVOICE).rows], ["2", "7"])
        self.assertFalse(saved.documents)
        self.assert_data_only(saved)

    def test_hidden_required_bank_input_is_saved_without_customer_disclosure(self):
        ws = self.case()
        form = get_form(ws, D.COMMERCIAL_INVOICE)
        FormService(self.repo).configure_fields(ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            form.included_fields, form.included_row_fields, "Keep bank only as internal required data",
            context_fields=[], required_input_fields=["bank_account"])
        app = self.open_slot(self.app(), D.COMMERCIAL_INVOICE, "bank_account")
        marker = "INTERNAL-DIRECT-BANK-938"
        self.save_text(app, marker)
        saved = self.repo.get(ws.case.case_id)
        form = get_form(saved, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["bank_account"], marker)
        self.assertNotIn("bank_account", effective_values(saved.trade, form))
        output = SimpleExportService(self.repo, self.blobs).preview(ws.case.case_id, self.actor,
            D.COMMERCIAL_INVOICE, audience="customer")
        book = load_workbook(BytesIO(output.content))
        try:
            text = " ".join(str(cell.value or "") for sheet in book for row in sheet for cell in row)
            self.assertNotIn(marker, text)
        finally:
            book.close()
        self.assertFalse(saved.documents)
        self.assert_data_only(saved)

    def test_input_mode_mounts_only_its_own_controls(self):
        self.case(buyer="")
        app = self.open_slot(self.app(), D.COMMERCIAL_INVOICE, "buyer")
        labels = lambda: [item.proto.label for item in app.get("file_uploader")]
        self.assertNotIn("为此资料项上传文件", labels())
        self.assertIsNotNone(harness.widget(app.text_input, "填写内容"))
        harness.widget(app.radio, "此项资料的提供方式").set_value("上传原件").run()
        self.assertFalse(app.exception)
        self.assertIn("为此资料项上传文件", labels())
        self.assertFalse(any(item.label == "填写内容" for item in app.text_input))
        self.assertTrue(harness.widget(app.button, "上传并关联此资料项").disabled)
        harness.widget(app.radio, "此项资料的提供方式").set_value("直接填写").run()
        self.assertNotIn("为此资料项上传文件", labels())
        self.assertFalse(any(item.label == "此原件的文件分类" for item in app.selectbox))

    def test_entering_seller_data_does_not_confirm_a_carrier_original(self):
        ws = self.case(kind=D.PACKING_LIST)
        ws = DocumentService(self.repo, self.blobs).upload(ws.case.case_id, self.actor,
            "synthetic-bill.txt", b"Shipper: Synthetic Shipper", D.BILL_OF_LADING)
        document = ws.documents[-1]
        version = document.versions[-1]
        document.status = version.status = DS.PARSED
        version.fields = [ExtractedField("shipper", "Synthetic Shipper", status=F.CANDIDATE)]
        self.repo.save(ws, expected_revision=ws.revision)
        app = self.open_slot(self.app(), D.PACKING_LIST, "shipper")
        self.save_text(app, "Synthetic Shipper")
        saved = self.repo.get(ws.case.case_id)
        self.assertEqual(len(saved.documents), 1)
        field = saved.documents[0].versions[0].fields[0]
        self.assertIs(field.status, F.CANDIDATE)
        self.assertIsNone(field.confirmed_by)
        report = SimpleExportService(self.repo, self.blobs).assess(ws.case.case_id, self.actor,
            D.PACKING_LIST, human_confirmed=True)
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "RAW_CROSS_DOCUMENT" and c.status.value == "pending_review"
                            for c in report.checks))
        self.assert_data_only(saved)


if __name__ == "__main__":
    unittest.main()

