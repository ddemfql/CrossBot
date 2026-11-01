"""Typed independent source records, safe mapping and concurrency regressions."""
import unittest
from copy import deepcopy

from core.errors import CrossBotError, ValidationError
from core.models import DocumentType as D, Document, DocumentVersion, DocumentStatus, ExtractedField, TradeData
from case_workflow.service import CaseService
from documents.forms import FormService, get_form, effective_rows
from documents.manual_inputs import ManualInputService, has_manual_data
from documents.prefill import prefill_form
from documents.provenance import form_value_origins
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor, trade


class ManualRecordTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-human", "sales")
        self.cases = CaseService(self.repo, PendingRules())
        self.ws = self.cases.create(self.actor, TradeData(currency=""))
        self.service = ManualInputService(self.repo)

    def save(self, values, rows=None, **kwargs):
        self.ws = self.service.save_record(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            values, rows or [], "Human supplied synthetic data", **kwargs)
        return self.ws.manual_data_records[-1]

    def test_multiple_orders_distinct_ids_and_immutable_history(self):
        one = self.save({"buyer": "Buyer A"}, title="Customer PO A")
        two = self.save({"buyer": "Buyer B"}, title="Customer PO B")
        self.assertNotEqual(one.record_id, two.record_id)
        self.assertEqual(len(self.ws.manual_data_records), 2)
        edited = self.save({"buyer": "Buyer A2"}, record_id=one.record_id, title="Customer PO A")
        self.assertEqual(edited.record_id, one.record_id)
        self.assertEqual(edited.revision, 2)
        events = [event for event in self.ws.audit if event.action == "manual_data_saved"
                  and event.entity_id == one.record_id]
        self.assertEqual(events[1].before["values"]["buyer"], "Buyer A")
        self.assertEqual(events[1].after["values"]["buyer"], "Buyer A2")

    def test_selected_record_reference_and_idempotent_save(self):
        selected = self.save({"buyer": "Buyer A", "document_no": "PO-A", "document_date": "2026-01-01"}, title="A")
        self.save({"buyer": "Buyer B"}, title="B")
        self.ws = self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            D.COMMERCIAL_INVOICE, "Copy selected A", source_record_id=selected.record_id)
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values["buyer"], "Buyer A")
        self.assertNotIn("document_no", form.values)
        self.assertNotIn("document_date", form.values)
        self.assertEqual(form.source_form_references[0].record_id, selected.record_id)
        self.assertTrue(any(selected.record_id in origin.note and self.actor.user_id in origin.note
                            for origin in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)))
        before = self.ws.revision
        self.save(selected.values, selected.rows, record_id=selected.record_id, title=selected.title)
        self.assertEqual(self.ws.revision, before)

    def test_header_only_apply_preserves_dynamic_trade_rows_and_origins(self):
        self.ws = self.cases.create(self.actor, trade())
        selected = self.save({"buyer": self.ws.trade.buyer})
        self.ws = self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            D.COMMERCIAL_INVOICE, "Only copy header", source_record_id=selected.record_id)
        target = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertFalse(target.rows)
        self.assertFalse(target.rows_manually_edited)
        self.assertEqual(len(effective_rows(self.ws.trade, target)), 1)
        row_origins = [item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE) if item.row_number]
        self.assertTrue(all(item.origin_kind in {"trade", "derived", "default"} for item in row_origins))
        self.assertEqual(next(item for item in row_origins if item.field_name == "unit").origin_kind, "default")

    def test_procurement_parties_financial_terms_not_copied_into_export_sale(self):
        self.ws = self.service.save_record(self.ws.case.case_id, self.actor, D.PURCHASE_CONTRACT,
            {"buyer": "Our procurement company", "currency": "CNY", "payment_terms": "Factory terms",
             "order_no": "SUPPLIER-1", "contract_no": "PC-1"}, [], "Supplier source")
        record = self.ws.manual_data_records[-1]
        with self.assertRaises(ValidationError):
            self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_CONTRACT,
                D.COMMERCIAL_INVOICE, "Do not copy incompatible parties", source_record_id=record.record_id)
        self.assertFalse(get_form(self.repo.get(self.ws.case.case_id), D.COMMERCIAL_INVOICE).values)

    def test_raw_prefill_uses_same_role_and_identity_mapping(self):
        for source_type in (D.PURCHASE_ORDER, D.PURCHASE_CONTRACT):
            with self.subTest(source_type=source_type):
                workspace = self.cases.create(self.actor, TradeData(currency=""))
                doc = Document(case_id=workspace.case.case_id, document_type=source_type, status=DocumentStatus.PARSED)
                version = DocumentVersion(document_id=doc.document_id, sha256="a"*64,
                    status=DocumentStatus.PARSED, fields=[ExtractedField("document_date", "2026-01-01"),
                    ExtractedField("invoice_no", "SOURCE-1"), ExtractedField("buyer", "Source Buyer"),
                    ExtractedField("currency", "CNY")])
                doc.versions = [version]; doc.current_version_id = version.version_id
                workspace.documents = [doc]
                prefill_form(workspace, D.COMMERCIAL_INVOICE, [doc.document_id])
                form = get_form(workspace, D.COMMERCIAL_INVOICE)
                self.assertNotIn("document_date", form.values)
                self.assertNotIn("invoice_no", form.values)
                if source_type is D.PURCHASE_CONTRACT:
                    self.assertNotIn("buyer", form.values)
                    self.assertNotIn("currency", form.values)
                else:
                    self.assertEqual(form.values["buyer"], "Source Buyer")

    def test_control_characters_rejected_and_note_only_not_provided(self):
        record = self.save({"notes": "Will ask buyer later"})
        self.assertFalse(has_manual_data(record))
        with self.assertRaises(ValidationError):
            self.save({"buyer": "Bad\x00Name"})
        with self.assertRaises(ValidationError):
            FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
                {"buyer": "Bad\x0bName"}, [], "Invalid control")
        draft = deepcopy(self.ws.trade); draft.seller = "Bad\x01Company"
        with self.assertRaises(ValidationError):
            self.cases.update_trade(self.ws.case.case_id, self.actor, draft, "Invalid control")

    def test_optimistic_revision_and_foreign_record_rejected(self):
        record = self.save({"buyer": "Buyer A"})
        with self.assertRaises(CrossBotError):
            self.save({"buyer": "Overwrite"}, record_id=record.record_id, expected_revision=0)
        with self.assertRaises(CrossBotError):
            self.cases.update_trade(self.ws.case.case_id, self.actor, self.ws.trade, "Stale", expected_revision=0)
        foreign = self.cases.create(self.actor, TradeData(currency=""))
        with self.assertRaises(ValidationError):
            self.service.apply(foreign.case.case_id, self.actor, D.PURCHASE_ORDER,
                D.COMMERCIAL_INVOICE, "Foreign source", source_record_id=record.record_id)

    def test_detach_only_selected_record_and_source_correction_can_be_reapplied(self):
        record = self.save({"buyer": "Buyer A"})
        self.ws = self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            D.COMMERCIAL_INVOICE, "Copy A", source_record_id=record.record_id)
        self.save({"buyer": "Buyer B"}, record_id=record.record_id)
        with self.assertRaises(ValidationError):
            self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
                D.COMMERCIAL_INVOICE, "Do not overwrite target", source_record_id=record.record_id)
        target = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            dict(target.values, buyer="Buyer B"), target.rows, "Human checks new source")
        self.ws = self.service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            D.COMMERCIAL_INVOICE, "Rebind corrected source", source_record_id=record.record_id)
        self.assertEqual(get_form(self.ws, D.COMMERCIAL_INVOICE).source_form_references[0].revision, 2)
        self.ws = self.service.detach(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            D.PURCHASE_ORDER, "Maintain independent data", source_record_id=record.record_id)
        self.assertFalse(get_form(self.ws, D.COMMERCIAL_INVOICE).source_form_references)
        self.assertEqual(get_form(self.ws, D.COMMERCIAL_INVOICE).values["buyer"], "Buyer B")

