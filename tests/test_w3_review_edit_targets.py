"""Review corrections use typed ownership and exact source row identities."""
from copy import deepcopy
import unittest

from core.models import (CrossCheckResult, Document, DocumentVersion, DocumentStatus as DS,
    DocumentType as D, ExtractedField, FormFieldCandidate, ReviewEditArea as A,
    ReviewEditTarget, TradeLine, TradeAdjustment, TradePackage, AuditEvent, from_dict, to_dict)
from documents.forms import get_form
from documents.manual_inputs import ManualInputService
from tests import test_w3_export_review as fixture
from tests import test_w3_audit_regressions as row_fixture


class ReviewEditTargetTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.ExportReviewTests()
        self.h.setUp()
        self.h.complete()

    def test_model_round_trip_restores_enums_and_legacy_has_no_targets(self):
        check = CrossCheckResult(edit_targets=[ReviewEditTarget(A.SOURCE, D.PACKING_LIST,
            "quantity", 8, "doc-synthetic", "version-synthetic", table_index=2)])
        restored = from_dict(CrossCheckResult, to_dict(check))
        self.assertIs(restored.edit_targets[0].area, A.SOURCE)
        self.assertIs(restored.edit_targets[0].document_type, D.PACKING_LIST)
        self.assertEqual(restored.edit_targets[0].table_index, 2)
        self.assertFalse(from_dict(CrossCheckResult, {"rule_code": "legacy"}).edit_targets)

    def test_missing_headers_rows_and_internal_context_have_precise_targets(self):
        h = self.h
        form = get_form(h.ws, D.COMMERCIAL_INVOICE)
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.COMMERCIAL_INVOICE,
            dict(form.values, document_date="", invoice_date=""),
            [dict(form.rows[0], quantity="")], "Human records missing values")
        actual = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        actual.required_input_context_fields = ["transaction_date"]
        check = next(c for c in h.report().checks if c.rule_code == "INPUT_DATA_REQUIREMENTS")
        locations = {(t.area, t.document_type, t.field_name, t.row_number) for t in check.edit_targets}
        self.assertIn((A.FORM, D.COMMERCIAL_INVOICE, "document_date", None), locations)
        self.assertIn((A.FORM, D.COMMERCIAL_INVOICE, "quantity", 1), locations)
        self.assertIn((A.TRADE, D.COMMERCIAL_INVOICE, "transaction_date", None), locations)
        self.assertFalse(any(t.field_name == "document_form" for t in check.edit_targets))

    def test_order_conflict_points_to_form_and_actual_trade_row(self):
        h = self.h
        trade = deepcopy(h.ws.trade)
        trade.line_items = [TradeLine("SYN-A", "Synthetic item", "999", "10", unit="pcs")]
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Known differing master quantity")
        check = next(c for c in h.report().checks if c.rule_code == "ORDER_ROWS" and c.left_field == "quantity")
        locations = {(t.area, t.field_name, t.row_number) for t in check.edit_targets}
        self.assertIn((A.FORM, "quantity", 1), locations)
        self.assertIn((A.TRADE, "quantity", 1), locations)

    def test_peer_form_conflict_has_both_document_types(self):
        h = self.h
        h.complete(D.SALES_CONTRACT, buyer="Different Buyer")
        check = next(c for c in h.report().checks if c.rule_code == "CROSS_DOCUMENT"
                     and c.left_field == "buyer" and c.status.value == "fail")
        self.assertEqual({(t.area, t.document_type, t.field_name) for t in check.edit_targets},
            {(A.FORM, D.COMMERCIAL_INVOICE, "buyer"), (A.FORM, D.SALES_CONTRACT, "buyer")})

    def test_invalid_number_points_to_exact_form_cell(self):
        h = self.h
        h.complete(quantity="1e3")
        check = next(c for c in h.report().checks if c.rule_code == "NUMBER_VALID"
                     and c.left_field == "quantity" and c.status.value == "fail")
        self.assertEqual([(t.area, t.document_type, t.field_name, t.row_number) for t in check.edit_targets],
                         [(A.FORM, D.COMMERCIAL_INVOICE, "quantity", 1)])

    def test_file_provenance_does_not_turn_editable_form_date_into_source_edit(self):
        h = self.h
        doc = Document(case_id=h.ws.case.case_id, document_type=D.COMMERCIAL_INVOICE,
                       status=DS.PARSED, title="Synthetic source date")
        version = DocumentVersion(document_id=doc.document_id, sha256="a" * 64, status=DS.PARSED,
            fields=[ExtractedField("document_date", "bad date")])
        doc.versions = [version]
        doc.current_version_id = version.version_id
        h.ws.documents.append(doc)
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.values["document_date"] = "bad date"
        form.manual_fields.remove("document_date")
        form.auto_values["document_date"] = "bad date"
        form.source_document_ids = [doc.document_id]
        form.field_candidates["document_date"] = [FormFieldCandidate("bad date", doc.document_id,
            version.version_id, "document_date", sheet_name="Data", cell_reference="B1")]
        check = next(c for c in h.report().checks if c.rule_code == "DATE_VALID" and c.left_field == "document_date")
        self.assertEqual(len(check.edit_targets), 1)
        target = check.edit_targets[0]
        self.assertIs(target.area, A.FORM)
        self.assertEqual(target.field_name, "document_date")
        self.assertFalse(target.document_id)

    def test_manual_record_conflict_keeps_exact_record_identity(self):
        h = self.h
        service = ManualInputService(h.repo)
        h.ws = service.save_record(h.ws.case.case_id, h.actor, D.PURCHASE_ORDER,
            {"buyer": "Synthetic Buyer"}, [], "Human supplied customer", title="Synthetic chosen PO")
        record = h.ws.manual_data_records[-1]
        h.ws = service.apply(h.ws.case.case_id, h.actor, D.PURCHASE_ORDER, D.COMMERCIAL_INVOICE,
            "Use chosen record", source_record_id=record.record_id)
        form = get_form(h.ws, D.COMMERCIAL_INVOICE)
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.COMMERCIAL_INVOICE,
            dict(form.values, buyer="Conflicting customer"), form.rows, "Human changes copied value")
        check = next(c for c in h.report().checks if c.rule_code == "MANUAL_SOURCE_VALUE" and c.left_field == "buyer")
        target = next(t for t in check.edit_targets if t.area is A.MANUAL_SOURCE)
        self.assertEqual(target.record_id, record.record_id)
        self.assertIs(target.document_type, D.PURCHASE_ORDER)
        self.assertEqual(target.field_name, "buyer")
        h.ws.manual_data_records[-1].revision += 1
        stale = next(c for c in h.report().checks if c.rule_code == "MANUAL_SOURCE_CURRENT")
        self.assertEqual([(t.area, t.document_type, t.record_id, t.field_name) for t in stale.edit_targets],
            [(A.SOURCE_LINKS, D.PURCHASE_ORDER, record.record_id, "source_form_references")])

    def test_raw_source_row_points_to_original_table_and_row(self):
        setup = row_fixture.AuditRegressionTests()
        setup.setUp()
        document, version = setup.add_raw_rows()
        from review.quality_inputs import pending_source_rows
        rows = pending_source_rows(setup.h.ws, D.COMMERCIAL_INVOICE)
        rows[0].values["quantity"] = "999"
        setup.confirm_rows(rows)
        check = next(c for c in setup.h.report().checks if c.rule_code == "RAW_CROSS_DOCUMENT"
            and c.left_field == "quantity" and c.status.value == "fail")
        target = next(t for t in check.edit_targets if t.area is A.SOURCE)
        self.assertEqual((target.document_id, target.version_id, target.table_index, target.row_number,
                          target.field_name), (document.document_id, version.version_id, 0, 2, "quantity"))
        self.assertTrue(any(t.area is A.FORM and t.row_number == 1 for t in check.edit_targets))

    def test_equal_values_pending_only_for_human_confirmation_have_no_edit_action(self):
        h = self.h
        h.complete(D.SALES_CONTRACT)
        report = h.report(human=False)
        equal = [c for c in report.checks if c.rule_code == "CROSS_DOCUMENT" and c.left_field == "buyer"]
        self.assertTrue(equal)
        self.assertTrue(all(c.comparison_policy.get("human_confirmation_only") for c in equal))
        self.assertTrue(all(not c.edit_targets for c in equal))

    def test_collection_checks_point_to_supported_collection_editors(self):
        h = self.h
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.required_input_context_fields = ["adjustments", "packages"]
        h.ws.trade.adjustments = [TradeAdjustment(name="Legacy fee", amount="bad", currency="USD")]
        h.ws.trade.packages = [TradePackage(package_number="BOX-1", product_code="SYN-A",
            quantity="-1", unit="pcs", gross_weight_kg="2", net_weight_kg="1")]
        report = h.report()
        for code, group in (("ADJUSTMENT_VALID", "adjustments"), ("PACKAGE_VALID", "packages")):
            check = next(c for c in report.checks if c.rule_code == code)
            self.assertEqual([(t.area, t.field_name) for t in check.edit_targets], [(A.TRADE, group)])

    def test_targeted_trade_edit_does_not_adopt_untouched_legacy_fee_values(self):
        h = self.h
        form = next(f for f in h.ws.document_forms if f.document_type is D.COMMERCIAL_INVOICE)
        form.required_input_context_fields = ["adjustments"]
        h.ws.trade.adjustments = [TradeAdjustment(name="Legacy freight", amount="100", currency="USD")]
        from documents.requirements import data_requirements

        def fee(name):
            return next(item for item in data_requirements(h.ws, D.COMMERCIAL_INVOICE)
                        if item.field_name == "adjustments[1]." + name)

        h.ws.audit.append(AuditEvent(actor_id=h.actor.user_id, action="trade_updated", after={
            "trade": to_dict(h.ws.trade), "review_edit_target": to_dict(ReviewEditTarget(A.TRADE,
                field_name="transaction_date"))}))
        self.assertEqual(fee("amount").status, "review_required")
        self.assertFalse(fee("amount").origin.actor_id)
        h.ws.audit.append(AuditEvent(actor_id=h.actor.user_id, action="trade_updated", after={
            "trade": to_dict(h.ws.trade), "review_edit_target": to_dict(ReviewEditTarget(A.TRADE,
                field_name="adjustments[1].name"))}))
        self.assertEqual(fee("name").status, "provided")
        self.assertEqual(fee("amount").status, "review_required")
        h.ws.audit.append(AuditEvent(actor_id=h.actor.user_id, action="trade_updated", after={
            "trade": to_dict(h.ws.trade), "review_edit_target": to_dict(ReviewEditTarget(A.TRADE,
                field_name="adjustments"))}))
        self.assertEqual(fee("amount").status, "provided")
        self.assertEqual(fee("currency").status, "provided")


if __name__ == "__main__":
    unittest.main()

