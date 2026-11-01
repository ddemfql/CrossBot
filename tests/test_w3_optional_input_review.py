"""Selected optional values require evidence; absent optional counterparts do not."""
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from core.errors import CrossBotError
from core.models import (Document, DocumentVersion, DocumentType as D, DocumentStatus as DS,
    ExtractedField, FieldValueStatus as F, ReviewEditArea as A, TradeData, TradeLine, utc_now, to_dict)
from documents.forms import get_form
from documents.requirements import data_requirements
from documents.simple_export import SimpleExportService
from review.editing import ReviewEditingService
from review.policy import current_artifacts
from storage.blobs import LocalBlobStore
from tests import test_w3_export_review as harness
from tests import test_w3_simple_export as export_harness


class OptionalInputReviewTests(unittest.TestCase):
    def setUp(self):
        self.h = harness.ExportReviewTests()
        self.h.setUp()
        self.h.complete()

    def current_form(self):
        return next(form for form in self.h.ws.document_forms if form.document_type is D.COMMERCIAL_INVOICE)

    def test_selected_unknown_context_blocks_even_with_human_checkbox_and_has_edit_target(self):
        h = self.h
        form = self.current_form()
        form.included_context_fields = ["seller_address"]
        h.ws.trade.seller_address = "Unverified legacy address"
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        item = next(item for item in data_requirements(h.ws, D.COMMERCIAL_INVOICE)
                    if item.field_name == "seller_address")
        self.assertFalse(item.required)
        self.assertEqual(item.status, "review_required")
        self.assertFalse(item.origin.actor_id)
        before = to_dict(h.ws)
        report = h.report(human=True)
        check = next(check for check in report.checks if check.rule_code == "INPUT_DATA_REQUIREMENTS")
        self.assertFalse(report.ready)
        target = next(target for target in check.edit_targets if target.field_name == "seller_address")
        self.assertIs(target.area, A.TRADE)
        with TemporaryDirectory() as root:
            exports = SimpleExportService(h.repo, LocalBlobStore(root))
            with patch.object(exports.blobs, "put", wraps=exports.blobs.put) as put:
                with self.assertRaises(CrossBotError):
                    exports.generate(h.ws.case.case_id, h.actor, D.COMMERCIAL_INVOICE, human_confirmed=True)
                put.assert_not_called()
        self.assertEqual(to_dict(h.repo.get(h.ws.case.case_id)), before)
        h.ws = ReviewEditingService(h.repo, h.cases).save(h.ws.case.case_id, h.actor,
            D.COMMERCIAL_INVOICE, target, value="Unverified legacy address", reason="Human checks this exact address",
            expected_revision=h.ws.revision, expected_review_digest=report.input_digest)
        self.assertTrue(h.report().ready, [(c.rule_code, c.message) for c in h.blockers()])
        self.assertFalse(h.ws.documents)
        self.assertFalse(h.ws.approvals)

    def test_optional_off_or_empty_is_allowed_and_does_not_delete_legacy_data(self):
        h = self.h
        form = self.current_form()
        form.included_context_fields = ["seller_address"]
        self.assertTrue(h.report().ready)
        h.ws.trade.seller_address = "Preserved legacy address"
        self.assertFalse(h.report().ready)
        form.included_context_fields = []
        self.assertTrue(h.report().ready)
        self.assertEqual(h.ws.trade.seller_address, "Preserved legacy address")

    def test_selected_unknown_product_extra_requires_exact_source_row_review(self):
        h = self.h
        trade = deepcopy(h.ws.trade)
        trade.currency = "USD"
        trade.line_items = [TradeLine("SYN-A", "Synthetic item", "2", "10", unit="pcs",
                                     packages=1, gross_weight_kg="2", net_weight_kg="1")]
        h.ws = h.cases.update_trade(h.ws.case.case_id, h.actor, trade, "Known audited base product")
        self.current_form().included_context_fields = ["line_items"]
        h.ws.trade.line_items[0].hs_code = "Unverified legacy tariff code"
        h.repo.save(h.ws, expected_revision=h.ws.revision)
        report = h.report()
        self.assertFalse(report.ready)
        check = next(c for c in report.checks if c.rule_code == "INPUT_DATA_REQUIREMENTS")
        self.assertTrue(any(t.area is A.TRADE and t.row_number == 1 and t.field_name == "hs_code"
                            for t in check.edit_targets))

    def test_selected_unverified_system_default_also_requires_explicit_review(self):
        h = self.h
        h.ws = h.cases.create(h.actor, TradeData())
        h.complete()
        # Explicitly filled form values do not adopt unrelated order defaults.
        self.assertTrue(h.report().ready)
        self.current_form().included_context_fields = ["incoterm_version"]
        item = next(item for item in data_requirements(h.ws, D.COMMERCIAL_INVOICE)
                    if item.field_name == "incoterm_version")
        self.assertEqual(item.origin.origin_kind, "default")
        self.assertFalse(item.required)
        self.assertFalse(h.report(human=True).ready)

    def raw_invoice(self, *, incoterm=None):
        h = self.h
        fields = [("buyer", "Synthetic Buyer"), ("currency", "USD"), ("total_amount", "20"),
                  ("quantity", "2"), ("product_code", "SYN-A"), ("unit", "pcs")]
        if incoterm is not None:
            fields.append(("incoterm", incoterm))
        doc = Document(case_id=h.ws.case.case_id, document_type=D.COMMERCIAL_INVOICE,
                       status=DS.PARSED, title="Synthetic current invoice")
        version = DocumentVersion(document_id=doc.document_id, sha256="a" * 64,
            status=DS.PARSED, parser_name="fixed-template-synthetic",
            fields=[ExtractedField(name=name, value=value, confirmed_value=value, status=F.CONFIRMED,
                confirmed_by=h.actor.user_id, confirmed_at=utc_now()) for name, value in fields])
        doc.versions = [version]
        doc.current_version_id = version.version_id
        h.ws.documents.append(doc)
        return doc, version

    def clear_incoterm(self):
        h = self.h
        form = get_form(h.ws, D.COMMERCIAL_INVOICE)
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.COMMERCIAL_INVOICE,
            dict(form.values, incoterm=""), form.rows, "No Incoterm provided for this invoice")

    def test_raw_optional_both_absent_skips_without_promoting_other_fields(self):
        self.clear_incoterm()
        _, version = self.raw_invoice()
        before = to_dict(version)
        report = self.h.report()
        self.assertTrue(report.ready, [(c.rule_code, c.message) for c in self.h.blockers()])
        self.assertFalse(any(c.rule_code == "RAW_CROSS_DOCUMENT" and c.left_field == "incoterm" for c in report.checks))
        self.assertEqual(to_dict(version), before)

    def test_raw_optional_one_side_present_and_required_missing_still_block(self):
        self.clear_incoterm()
        self.raw_invoice(incoterm="FCA Shanghai")
        report = self.h.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "RAW_CROSS_DOCUMENT" and c.left_field == "incoterm"
                            and c.status.value == "pending_review" for c in report.checks))
        self.h.ws.documents.clear()
        self.raw_invoice()
        self.current_form().required_input_fields = ["incoterm"]
        report = self.h.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "RAW_CROSS_DOCUMENT" and c.left_field == "incoterm"
                            for c in report.checks))

    def test_related_required_absent_optional_field_is_not_silently_skipped(self):
        h = self.h
        self.clear_incoterm()
        h.complete(D.SALES_CONTRACT)
        contract = get_form(h.ws, D.SALES_CONTRACT)
        h.ws = h.forms.save(h.ws.case.case_id, h.actor, D.SALES_CONTRACT,
            dict(contract.values, incoterm=""), contract.rows, "Empty optional term")
        self.assertTrue(h.report().ready)
        next(form for form in h.ws.document_forms if form.document_type is D.SALES_CONTRACT).required_input_fields = ["incoterm"]
        report = h.report()
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "CROSS_DOCUMENT" and c.left_field == "incoterm" for c in report.checks))

    def test_rule_upgrade_invalidates_old_receipt_without_removing_history(self):
        setup = export_harness.SimpleExportTests()
        setup.setUp()
        try:
            setup.complete()
            ws = setup.generate()
            self.assertEqual(len(current_artifacts(ws)), 1)
            ws.artifacts[-1].rule_version = "w3-export-review-7"
            receipt = next(e for e in ws.audit if e.action == "simple_export_confirmed")
            receipt.after["rule_version"] = "w3-export-review-7"
            self.assertFalse(current_artifacts(ws))
            self.assertEqual(len(ws.artifacts), 1)
        finally:
            setup.doCleanups()


if __name__ == "__main__":
    unittest.main()

