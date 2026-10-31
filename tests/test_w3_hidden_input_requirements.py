"""Internal required inputs must be reviewed without becoming customer output."""
from copy import deepcopy
from io import BytesIO
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook
from core.models import DocumentType as D
from documents.forms import get_form, effective_values
from documents.requirements import data_requirements
from documents.simple_export import SimpleExportService
from storage.blobs import LocalBlobStore
from tests import test_w3_export_review as fixture


class HiddenInputRequirementTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.ExportReviewTests()
        self.h.setUp()
        self.h.complete()

    def test_required_internal_scalar_blocks_until_saved_but_never_prints(self):
        h = self.h
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.included_fields = [key for key in (form.included_fields or []) if key != "port_of_loading"]
        form.required_input_fields = ["port_of_loading"]
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        item = next(item for item in data_requirements(h.ws, D.COMMERCIAL_INVOICE) if item.field_name == "port_of_loading")
        self.assertTrue(item.required)
        self.assertEqual(item.status, "missing")
        self.assertFalse(h.report().ready)
        marker = "INTERNAL-HIDDEN-PORT-928"
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.COMMERCIAL_INVOICE,
            dict(form.values, port_of_loading=marker), form.rows, "Human verifies internal loading port")
        self.assertTrue(h.report().ready, [(c.rule_code, c.message) for c in h.blockers()])
        saved = get_form(h.ws, D.COMMERCIAL_INVOICE)
        self.assertNotIn("port_of_loading", effective_values(h.ws.trade, saved))
        with TemporaryDirectory() as root:
            output = SimpleExportService(h.repo, LocalBlobStore(root)).preview(
                h.ws.case.case_id, h.actor, D.COMMERCIAL_INVOICE, audience="customer")
            book = load_workbook(BytesIO(output.content))
            try:
                text = " ".join(str(cell.value or "") for sheet in book for row in sheet for cell in row)
                self.assertNotIn(marker, text)
            finally:
                book.close()

    def test_invalid_hidden_required_context_still_runs_business_rules(self):
        h = self.h
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.included_context_fields = []
        form.required_input_context_fields = ["transaction_date"]
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        self.assertFalse(h.report().ready)
        trade = deepcopy(h.ws.trade)
        trade.transaction_date = "yesterday"
        h.ws.trade = trade
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        self.assertTrue(any(c.rule_code == "TRADE_DATE_VALID" and c.status.value == "fail" for c in h.report().checks))
        trade.transaction_date = "2026-10-31"
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Human records internal date")
        self.assertTrue(h.report().ready, [(c.rule_code, c.message) for c in h.blockers()])

    def test_non_goods_payment_notice_does_not_require_product_rows(self):
        h = self.h
        from core.models import TradeLine
        trade = deepcopy(h.ws.trade)
        trade.currency = "USD"
        trade.line_items = [TradeLine("A", "Item A", "2", "10", unit="pcs")]
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Known payment basis")
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.PAYMENT_NOTICE,
            {"document_no": "PAY-1", "document_date": "2026-10-31", "seller": "Synthetic Seller",
             "buyer": "Synthetic Buyer", "currency": "USD", "amount_due": "5",
             "due_date": "2026-11-01", "bank_account": "Synthetic account"}, [], "Human request for known deposit")
        self.assertTrue(h.report(D.PAYMENT_NOTICE).ready,
            [(c.rule_code, c.message) for c in h.blockers(h.report(D.PAYMENT_NOTICE))])


if __name__ == "__main__":
    unittest.main()
