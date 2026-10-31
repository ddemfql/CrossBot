"""Synthetic direct-data inputs never impersonate uploaded issuer documents."""
import unittest

from case_workflow.service import CaseService
from core.errors import CrossBotError, ValidationError
from core.models import DocumentForm, DocumentType as D, TradeData, to_dict
from documents.forms import FormService, get_form
from documents.manual_inputs import ManualInputService, has_manual_data
from documents.provenance import form_value_origins
from validation.export_review import review_digest, review_export
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor


class ManualInputTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-human", "sales")
        self.ws = CaseService(self.repo, PendingRules()).create(self.actor, TradeData(currency=""))
        self.service = ManualInputService(self.repo)
        self.values = {"document_no": "SYN-1", "document_date": "2026-10-31",
            "order_no": "SYN-ORDER", "buyer": "Synthetic Buyer", "seller": "Synthetic Seller",
            "currency": "USD", "destination_country": "DE", "incoterm": "FCA Shanghai"}
        self.rows = [{"product_code": "SYN-A", "description": "Synthetic Item", "quantity": "2",
                      "unit": "pcs", "unit_price": "10"}]

    def save(self, values=None, rows=None):
        self.ws = self.service.save(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            self.values if values is None else values, self.rows if rows is None else rows,
            "Synthetic data explicitly supplied by human")
        return self.ws

    def apply(self):
        self.ws = self.service.apply(self.ws.case.case_id, self.actor,
            D.PURCHASE_ORDER, D.COMMERCIAL_INVOICE, "Use synthetic order data")
        return self.ws

    def test_manual_data_replaces_upload_without_creating_original_or_approval(self):
        self.save()
        form = get_form(self.ws, D.PURCHASE_ORDER)
        self.assertTrue(has_manual_data(form))
        self.assertIsNone(form.confirmed_by)
        self.assertFalse(self.ws.documents)
        self.assertFalse(self.ws.artifacts)
        self.assertFalse(self.ws.approvals)
        self.apply()
        target = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(target.rows, self.rows)
        self.assertFalse(target.manually_provided)
        self.assertEqual(target.source_form_references[0].revision, form.revision)
        self.assertNotIn("document_no", target.values)
        self.assertNotIn("document_date", target.values)
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            dict(target.values, document_no="SYN-INVOICE-1", document_date="2026-10-31"),
            target.rows, "Human supplies invoice identity")
        self.assertTrue(review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True).ready)
        self.assertFalse(review_export(self.ws, D.COMMERCIAL_INVOICE).ready)
        origins = form_value_origins(self.ws, D.COMMERCIAL_INVOICE)
        self.assertTrue(all(origin.origin_kind in {"manual", "derived"} for origin in origins))
        self.assertTrue(any("买方订单 PO" in origin.note for origin in origins))

    def test_empty_and_cleared_input_never_counts_as_provided(self):
        self.save({}, [])
        self.assertFalse(has_manual_data(get_form(self.ws, D.PURCHASE_ORDER)))
        with self.assertRaises(ValidationError):
            self.apply()
        self.save()
        self.save({"buyer": "  "}, [])
        self.assertFalse(has_manual_data(get_form(self.ws, D.PURCHASE_ORDER)))

    def test_explicit_human_adoption_of_legacy_text_records_current_actor(self):
        self.ws.document_forms.append(DocumentForm(D.PURCHASE_ORDER,
            values=dict(self.values), rows=list(self.rows), revision=1))
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.save()
        origins = form_value_origins(self.ws, D.PURCHASE_ORDER)
        self.assertTrue(all(item.origin_kind in {"manual", "derived"} for item in origins))
        self.assertTrue(all(item.actor_id == self.actor.user_id for item in origins if item.origin_kind == "manual"))

    def test_source_edit_or_clear_requires_new_review(self):
        self.save()
        self.apply()
        original_digest = review_digest(self.ws, D.COMMERCIAL_INVOICE)
        self.save(dict(self.values, buyer="Changed Buyer"))
        self.assertNotEqual(original_digest, review_digest(self.ws, D.COMMERCIAL_INVOICE))
        report = review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True)
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "MANUAL_SOURCE_CURRENT" and c.status.value == "pending_review"
                            for c in report.checks))

    def test_target_manual_change_cannot_hide_difference_with_linked_manual_source(self):
        self.save()
        self.apply()
        target = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            dict(target.values, document_no="CI-1", document_date="2026-10-31", buyer="Different Buyer"),
            [dict(target.rows[0], quantity="3")], "Explicit human change, still needs deterministic check")
        report = review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True)
        self.assertFalse(report.ready)
        self.assertTrue(any(c.rule_code == "MANUAL_SOURCE_VALUE" and c.status.value == "fail"
                            for c in report.checks))

    def test_apply_preserves_manual_values_and_clears_atomically(self):
        self.save()
        FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"buyer": ""}, [], "Human intentionally clears buyer", manual_fields=["buyer"])
        before = to_dict(self.repo.get(self.ws.case.case_id))
        with self.assertRaises(ValidationError):
            self.apply()
        self.assertEqual(before, to_dict(self.repo.get(self.ws.case.case_id)))

    def test_repeated_apply_is_idempotent_and_source_flag_survives_edit(self):
        self.save()
        self.apply()
        before = to_dict(self.ws)
        self.apply()
        self.assertEqual(before, to_dict(self.ws))
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            self.values, self.rows, "Normal editor preserves source flag")
        self.assertTrue(get_form(self.ws, D.PURCHASE_ORDER).manually_provided)

    def test_unauthenticated_actor_and_external_target_cannot_apply(self):
        self.save()
        with self.assertRaises(CrossBotError):
            self.service.save(self.ws.case.case_id, actor("robot", "invalid"), D.PURCHASE_ORDER,
                              self.values, self.rows, "Denied")
        with self.assertRaises(ValidationError):
            self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
                               D.BILL_OF_LADING, "Must not issue carrier record")
        with self.assertRaises(ValidationError):
            self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
                               D.PURCHASE_ORDER, "No self reference")

    def test_detach_cleared_source_preserves_data_history_and_requires_current_checks(self):
        self.save()
        self.apply()
        target = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            dict(target.values, document_no="CI-1", document_date="2026-10-31"), target.rows,
            "Human supplied output identity")
        self.save({}, [])
        self.assertFalse(review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True).ready)
        before = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.ws = self.service.detach(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            D.PURCHASE_ORDER, "Human checked retained values and will maintain independently")
        target = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(target.values, before.values)
        self.assertEqual(target.rows, before.rows)
        self.assertFalse(target.source_form_references)
        self.assertIsNone(target.confirmed_by)
        self.assertTrue(review_export(self.ws, D.COMMERCIAL_INVOICE, human_confirmed=True).ready)
        self.assertFalse(review_export(self.ws, D.COMMERCIAL_INVOICE).ready)
        record = next(event for event in self.ws.audit if event.action == "manual_source_detached")
        self.assertEqual(record.actor_id, self.actor.user_id)
        self.assertEqual(record.before["source_form_references"][0]["document_type"], D.PURCHASE_ORDER.value)
        self.assertFalse(self.ws.documents)
        self.assertFalse(self.ws.approvals)
        self.assertTrue(any("买方订单 PO" in item.note for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)))

    def test_detach_validates_scope_actor_and_reason_and_noop_is_readonly(self):
        self.save()
        for target, source, reason in ((D.BILL_OF_LADING, D.PURCHASE_ORDER, "No issuer output"),
                (D.COMMERCIAL_INVOICE, D.COMMERCIAL_INVOICE, "No self"),
                (D.COMMERCIAL_INVOICE, D.PURCHASE_ORDER, "")):
            with self.assertRaises(ValidationError):
                self.service.detach(self.ws.case.case_id, self.actor, target, source, reason)
        with self.assertRaises(CrossBotError):
            self.service.detach(self.ws.case.case_id, actor("invalid", "invalid"),
                D.COMMERCIAL_INVOICE, D.PURCHASE_ORDER, "Denied")
        before = to_dict(self.repo.get(self.ws.case.case_id))
        changed = self.service.detach(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            D.PURCHASE_ORDER, "No association exists")
        self.assertEqual(before, to_dict(changed))


if __name__ == "__main__":
    unittest.main()
