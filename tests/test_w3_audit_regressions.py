"""Deterministic review fixes and complete version-bound external row review."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from core.errors import CrossBotError, ValidationError
from core.models import (Document, DocumentVersion, DocumentStatus as DS, DocumentType as D, FormFieldCandidate,
    SourceTable, SourceRowSelection, TradeLine, TradePackage, to_dict)
from documents.forms import get_form
from review.policy import current_artifacts
from review.quality_inputs import pending_source_rows, confirm_source_rows
from tests import test_w3_export_review as review_fixture
from tests import test_w3_simple_export as export_fixture
from validation.export_review import review_digest, review_export


class AuditRegressionTests(unittest.TestCase):
    def setUp(self):
        self.h = review_fixture.ExportReviewTests()
        self.h.setUp()

    def test_order_product_values_and_set_must_match_existing_master(self):
        h = self.h
        h.complete()
        trade = deepcopy(h.ws.trade)
        trade.line_items = [TradeLine("SYN-A", "Synthetic item", "999", "88", unit="pcs")]
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Known master values")
        report = h.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "ORDER_ROWS" and c.left_field == "quantity"
                            and c.status.value == "fail" for c in report.checks))

    def test_legacy_auto_identity_mapping_is_rechecked_against_current_roles(self):
        h = self.h
        h.complete()
        document = Document(case_id=h.ws.case.case_id, document_type=D.PURCHASE_ORDER,
                            title="Synthetic legacy PO", status=DS.PARSED)
        version = DocumentVersion(document_id=document.document_id, sha256="a" * 64,
            status=DS.PARSED, original_filename="legacy-PO.xlsx")
        document.versions = [version]
        document.current_version_id = version.version_id
        h.ws.documents.append(document)
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.manual_fields.remove("document_date")
        form.auto_values["document_date"] = form.values["document_date"]
        form.field_candidates["document_date"] = [FormFieldCandidate(form.values["document_date"],
            document.document_id, version.version_id, "document_date", sheet_name="PO", cell_reference="B2")]
        report = h.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "AUTO_FIELD_MAPPING" and c.left_field == "document_date"
                            and c.status.value == "pending_review" for c in report.checks))
        # The same current file can legitimately supply customer/order facts.
        form.auto_values = {"buyer": form.values["buyer"]}
        form.manual_fields.remove("buyer")
        form.manual_fields.append("document_date")
        form.field_candidates["buyer"] = [FormFieldCandidate(form.values["buyer"],
            document.document_id, version.version_id, "customer_name", sheet_name="PO", cell_reference="B3")]
        self.assertTrue(h.report().ready, [(c.rule_code, c.message) for c in h.blockers()])

    def test_omitted_optional_fields_do_not_keep_conflicts_or_source_dependencies(self):
        h = self.h
        h.complete()
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.included_fields = None  # Historical all-fields document.
        candidates = []
        for value in ("Port A", "Port B"):
            document = Document(case_id=h.ws.case.case_id, document_type=D.PURCHASE_ORDER,
                status=DS.PARSED, title="Synthetic optional port source")
            version = DocumentVersion(document_id=document.document_id, sha256="a" * 64, status=DS.PARSED)
            document.versions = [version]
            document.current_version_id = version.version_id
            h.ws.documents.append(document)
            form.source_document_ids.append(document.document_id)
            candidates.append(FormFieldCandidate(value, document.document_id, version.version_id, "port_of_loading"))
        form.field_candidates["port_of_loading"] = candidates
        self.assertFalse(h.report().ready)
        form.included_fields = []
        form.included_row_fields = []
        report = h.report()
        self.assertTrue(report.ready, [(c.rule_code, c.message) for c in h.blockers(report)])
        from documents.forms import form_fields, form_row_fields
        self.assertNotIn("port_of_loading", form_fields(form))
        self.assertIn("document_no", form_fields(form))
        self.assertIn("product_code", form_row_fields(form))
        self.assertFalse(any(c.rule_code == "SOURCE_FIELD_CONFLICT" for c in report.checks))

    def test_omitted_optional_context_is_not_validated_as_rendered_data(self):
        h = self.h
        h.complete()
        h.ws.trade.transaction_date = "invalid hidden date"
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.included_context_fields = None  # Historical all-context document.
        self.assertFalse(h.report().ready)
        form.included_context_fields = []
        report = h.report()
        self.assertTrue(report.ready, [(c.rule_code, c.message) for c in h.blockers(report)])
        self.assertFalse(any(c.rule_code == "TRADE_DATE_VALID" for c in report.checks))

    def test_contract_and_invoice_differences_block_both_directions(self):
        h = self.h
        h.complete()
        h.complete(D.SALES_CONTRACT, buyer="Different Buyer", quantity="9")
        self.assertFalse(h.report(D.SALES_CONTRACT).ready)
        self.assertFalse(h.report(D.COMMERCIAL_INVOICE).ready)
        # PI can cover an earlier or different quantity scope; never implicitly
        # assert it equals a final shipment.
        h.complete(D.PROFORMA_INVOICE, quantity="999")
        self.assertTrue(h.report(D.PROFORMA_INVOICE).ready)

    def test_matching_total_does_not_hide_changed_contract_unit_prices(self):
        h = self.h
        rows = [{"product_code": "A", "description": "A", "quantity": "1", "unit": "pcs", "unit_price": "10"},
                {"product_code": "B", "description": "B", "quantity": "1", "unit": "pcs", "unit_price": "20"}]
        h.complete(rows=rows)
        h.complete(D.SALES_CONTRACT, rows=[dict(rows[0], unit_price="20"), dict(rows[1], unit_price="10")])
        report = h.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(c.left_field == "unit_price" and c.status.value == "fail" for c in report.checks))

    def test_payment_and_statement_relationships_cannot_pass_format_checks(self):
        h = self.h
        h.complete(D.PAYMENT_NOTICE)
        form = get_form(h.ws, D.PAYMENT_NOTICE)
        trade = deepcopy(h.ws.trade)
        trade.currency = "USD"
        trade.line_items = [TradeLine("SYN-A", "Synthetic item", "2", "10", unit="pcs")]
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Known order total20")
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.PAYMENT_NOTICE,
            dict(form.values, amount_due="999999", due_date="2026-10-31", bank_account="Synthetic"),
            form.rows, "Synthetic payment over known total")
        self.assertTrue(any(c.rule_code == "PAYMENT_AMOUNT_LIMIT" and c.status.value == "fail"
                            for c in h.report(D.PAYMENT_NOTICE).checks))
        h.complete(D.STATEMENT_OF_ACCOUNT, rows=[{"date": "2026-10-31", "reference": "SYN",
            "debit": "0", "credit": "0", "balance": "0"}])
        form = get_form(h.ws, D.STATEMENT_OF_ACCOUNT)
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.STATEMENT_OF_ACCOUNT,
            dict(form.values, period_start="2026-10-31", period_end="2026-10-01",
                 opening_balance="0", closing_balance="999"), form.rows, "Contradictory period and balance")
        failures = {c.rule_code for c in h.report(D.STATEMENT_OF_ACCOUNT).checks if c.status.value == "fail"}
        self.assertTrue({"STATEMENT_PERIOD", "STATEMENT_CLOSING_BALANCE"} <= failures)

    def add_raw_rows(self):
        h = self.h
        rows = [{"product_code": "A", "description": "Item A", "quantity": "2", "unit": "pcs", "unit_price": "10"},
                {"product_code": "B", "description": "Item B", "quantity": "3", "unit": "pcs", "unit_price": "10"}]
        h.complete(rows=rows)
        document = Document(case_id=h.ws.case.case_id, document_type=D.PACKING_LIST,
                            title="Synthetic unbound PL", status=DS.PARSED)
        version = DocumentVersion(document_id=document.document_id, sha256="a" * 64,
            status=DS.PARSED, original_filename="synthetic-PL.xlsx", source_tables=[SourceTable("Products", [
                ["Product Code", "Description", "Quantity", "Unit"],
                ["A", "Item A", "2", "pcs"], ["B", "Item B", "3", "pcs"]])])
        document.versions = [version]
        document.current_version_id = version.version_id
        h.ws.documents.append(document)
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        return document, version

    def confirm_rows(self, rows, digest=None):
        h = self.h
        h.ws = confirm_source_rows(h.repo, h.ws.case.case_id, h.actor, D.COMMERCIAL_INVOICE,
            rows, expected_review_digest=digest or review_digest(h.ws, D.COMMERCIAL_INVOICE), human_confirmed=True)
        return h.ws

    def test_complete_reviewed_external_rows_can_pass_and_retain_immutable_original(self):
        document, version = self.add_raw_rows()
        original = to_dict(version)
        h = self.h
        self.assertFalse(h.report().ready)
        rows = pending_source_rows(h.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(len(rows), 2)
        self.confirm_rows(rows)
        self.assertTrue(h.report().ready, [(c.rule_code, c.message) for c in h.blockers()])
        actual = next(d for d in h.ws.documents if d.document_id == document.document_id).versions[0]
        self.assertEqual(to_dict(actual), original)
        self.assertFalse(h.ws.approvals)
        self.assertTrue(all(row.confirmed_by == h.actor.user_id for row in h.ws.reviewed_source_rows))
        changed = pending_source_rows(h.ws, D.COMMERCIAL_INVOICE)
        changed[0].values["quantity"] = "999"
        self.confirm_rows(changed)
        self.assertTrue(any(c.rule_code == "RAW_CROSS_DOCUMENT" and c.status.value == "fail"
                            for c in h.report().checks))

    def test_unbound_raw_is_not_hidden_by_an_independent_manual_form(self):
        document, _ = self.add_raw_rows()
        h = self.h
        # A manual seller PL does not claim to represent this raw file.
        rows = get_form(h.ws, D.COMMERCIAL_INVOICE).rows
        h.complete(D.PACKING_LIST, rows=[dict(row, packages="1", gross_weight_kg="2", net_weight_kg="1") for row in rows])
        report = h.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "RAW_ROW_SOURCE" and c.right_document_id == document.document_id
                            for c in report.checks))

    def test_row_drop_duplicate_and_stale_digest_are_atomic(self):
        self.add_raw_rows()
        h = self.h
        rows = pending_source_rows(h.ws, D.COMMERCIAL_INVOICE)
        before = to_dict(h.repo.get(h.ws.case.case_id))
        for selected in (rows[:1], [rows[0], rows[0]]):
            with self.assertRaises(ValidationError):
                self.confirm_rows(selected)
            self.assertEqual(to_dict(h.repo.get(h.ws.case.case_id)), before)
        with self.assertRaises(CrossBotError):
            self.confirm_rows(rows, "stale")
        self.confirm_rows(rows)
        h.ws.documents[0].versions[0].source_tables[0].rows[1][2] = "77"
        self.assertFalse(h.report().ready, "Reviewed rows cannot outlive a changed original basis")

    def test_incomplete_identity_row_cannot_disappear_from_original_review(self):
        _, version = self.add_raw_rows()
        h = self.h
        h.ws.documents[0].versions[0].source_tables[0].rows[2] = ["", "", "3", "pcs"]
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        rows = pending_source_rows(h.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(len(rows), 2, "A row with quantity/unit but no identity must still be reviewed")
        with self.assertRaises(ValidationError):
            self.confirm_rows(rows)
        rows[1].values.update(product_code="B", description="Item B")
        self.confirm_rows(rows)
        self.assertTrue(h.report().ready)

    def test_rule_upgrade_or_changed_snapshot_never_remains_current(self):
        fixture = export_fixture.SimpleExportTests()
        fixture.setUp()
        try:
            fixture.complete()
            ws = fixture.generate()
            self.assertEqual(len(current_artifacts(ws)), 1)
            with patch("validation.export_review.RULE_VERSION", "future-review-rule"):
                self.assertFalse(current_artifacts(ws))
            ws.selected_template_ids[D.COMMERCIAL_INVOICE.value] = "changed-template"
            self.assertFalse(current_artifacts(ws))
        finally:
            fixture.doCleanups()

    def test_rendered_extra_dates_and_package_values_are_checked(self):
        h = self.h
        h.complete()
        trade = deepcopy(h.ws.trade)
        trade.transaction_date = "yesterday"
        trade.packages = [TradePackage(package_number="BOX-1", product_code="SYN-A",
            quantity="-9", unit="pcs", gross_weight_kg="1", net_weight_kg="2")]
        # Exercise the export gate against a legacy persisted snapshot; current
        # input services also reject malformed package numbers before saving.
        h.ws.trade = deepcopy(trade)
        next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE).included_context_fields = None
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        failures = {c.rule_code for c in h.report().checks if c.status.value == "fail"}
        self.assertTrue({"TRADE_DATE_VALID", "PACKAGE_VALID"} <= failures)
        trade.transaction_date = "2026-10-31"
        trade.packages[0].quantity = "2"
        trade.packages[0].gross_weight_kg = "2"
        trade.packages[0].net_weight_kg = "1"
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Correct explicitly entered extras")
        self.assertTrue(h.report().ready, [(c.rule_code, c.message) for c in h.blockers()])
        trade.packages[0].quantity = "3"
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Changed per-package quantity")
        self.assertTrue(any(c.rule_code == "PACKAGE_TOTAL" and c.status.value == "fail" for c in h.report().checks))


if __name__ == "__main__":
    unittest.main()

