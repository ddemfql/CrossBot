"""Adjustable optional schemas and exact input-slot source associations."""
import unittest

from core.errors import ValidationError, CrossBotError
from core.models import Document, DocumentVersion, DocumentType as D, DocumentStatus as S, ExtractedField, SourceTable, TradeData
from case_workflow.service import CaseService
from documents.forms import FormService, get_form, effective_values, effective_rows, form_fields, form_row_fields, form_context_fields, input_context_fields
from documents.prefill import PrefillService, refresh_prefills
from documents.provenance import form_value_origins
from documents.requirements import data_requirements
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor


class FieldConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-schema-human", "sales")
        self.ws = CaseService(self.repo, PendingRules()).create(self.actor, TradeData(currency=""))
        self.forms, self.prefill = FormService(self.repo), PrefillService(self.repo)

    def source(self, fields=None, tables=None):
        document = Document(case_id=self.ws.case.case_id, document_type=D.PURCHASE_ORDER, status=S.PARSED)
        version = DocumentVersion(document_id=document.document_id, sha256="a"*64,
            original_filename="synthetic-source.xlsx", status=S.PARSED, fields=fields or [], source_tables=tables or [])
        document.versions = [version]; document.current_version_id = version.version_id
        self.ws.documents.append(document)
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        return document

    def test_optional_fields_hidden_retained_and_reenabled_without_required_bypass(self):
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"buyer": "Buyer", "notes": "Hidden note", "notify_party": "Known notify"},
            [{"product_code": "SKU", "description": "Item", "quantity": "2", "unit": "pcs", "unit_price": "3", "amount": "6"}],
            "Initial manual record")
        self.ws = self.forms.configure_fields(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            [], [], "Minimal fields", context_fields=[])
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertTrue({"buyer", "seller", "order_no", "document_no", "document_date", "currency"} <= form_fields(form).keys())
        self.assertTrue({"product_code", "description", "quantity", "unit", "unit_price"} <= form_row_fields(form).keys())
        self.assertNotIn("notes", effective_values(self.ws.trade, form))
        self.assertNotIn("amount", effective_rows(self.ws.trade, form)[0])
        self.assertFalse(any(item.field_name == "notes" for item in data_requirements(self.ws, D.COMMERCIAL_INVOICE)))
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"buyer": "New Buyer"}, effective_rows(self.ws.trade, form), "Edit active values only")
        saved = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(saved.values["notes"], "Hidden note")
        self.assertEqual(saved.rows[0]["amount"], "6")
        self.assertEqual(saved.included_context_fields, [])
        self.ws = self.forms.configure_fields(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            ["notes", "notify_party"], ["amount"], "Reenable optional content")
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(effective_values(self.ws.trade, form)["notes"], "Hidden note")
        self.assertEqual(effective_rows(self.ws.trade, form)[0]["amount"], "6")

    def test_selected_blank_optional_visible_and_context_policy_cannot_be_hidden(self):
        self.ws = self.forms.configure_fields(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            ["notes"], [], "Explicit optional fields", context_fields=["transaction_place"])
        graph = data_requirements(self.ws, D.COMMERCIAL_INVOICE)
        notes = next(item for item in graph if item.field_name == "notes")
        self.assertFalse(notes.required)
        self.assertEqual(notes.status, "missing")
        self.assertTrue(any(item.field_name == "transaction_place" and not item.required for item in graph))
        self.ws.trade.destination_country = "US"
        self.ws.trade.transport_mode = "sea"
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        active = input_context_fields(form, self.ws)
        self.assertTrue({"transaction_date", "seller_address", "line_items", "packages"} <= active)
        self.assertFalse({"transaction_date", "seller_address", "line_items", "packages"} & form_context_fields(form, self.ws))
        with self.assertRaises(ValidationError):
            self.forms.configure_fields(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
                ["not_a_real_field"], [], "Reject unsupported field")
        with self.assertRaises(CrossBotError):
            self.forms.configure_fields(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
                [], [], "Reject stale snapshot", expected_revision=0)

    def test_targeted_header_only_fills_chosen_value_and_keeps_current_file_origin(self):
        doc = self.source([ExtractedField("buyer", "Synthetic Buyer", sheet_name="Order", cell_reference="B2"),
            ExtractedField("seller", "Must not fill seller"), ExtractedField("currency", "USD")])
        self.ws = self.prefill.fill_requirement(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            "buyer", doc.document_id)
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form.values, {"buyer": "Synthetic Buyer"})
        self.assertFalse(form.rows)
        origin = next(item for item in form_value_origins(self.ws, D.COMMERCIAL_INVOICE) if item.field_name == "buyer")
        self.assertEqual(origin.origin_kind, "file")
        self.assertEqual(origin.location, "工作表 Order · B2")
        refresh_prefills(self.ws)
        self.assertEqual(get_form(self.ws, D.COMMERCIAL_INVOICE).values, {"buyer": "Synthetic Buyer"})
        self.assertEqual(self.ws.source_requirement_links[0].field_name, "buyer")

    def test_targeted_header_respects_manual_clear_and_unresolved_still_has_link(self):
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"buyer": ""}, [], "Explicit human clear", manual_fields=["buyer"])
        doc = self.source([ExtractedField("buyer", "Do not overwrite clear")])
        self.ws = self.prefill.fill_requirement(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            "buyer", doc.document_id)
        self.assertEqual(get_form(self.ws, D.COMMERCIAL_INVOICE).values["buyer"], "")
        self.assertEqual(len(self.ws.source_requirement_links), 1)
        self.ws = self.prefill.fill_requirement(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            "seller", doc.document_id)
        self.assertFalse(get_form(self.ws, D.COMMERCIAL_INVOICE).values.get("seller"))
        self.assertEqual(len(self.ws.source_requirement_links), 2)

    def test_targeted_row_cell_requires_unique_sku_and_retains_other_human_values(self):
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, D.PACKING_LIST, {},
            [{"product_code": "SYN-A", "description": "Human description", "quantity": "2", "unit": "pcs"}],
            "Human product identity")
        doc = self.source(tables=[SourceTable("Rows", [["SKU", "Description", "Quantity", "Gross weight kg"],
            ["SYN-A", "Source description", "99", "3.4"]])])
        self.ws = self.prefill.fill_requirement(self.ws.case.case_id, self.actor, D.PACKING_LIST,
            "gross_weight_kg", doc.document_id, row_number=1)
        form = get_form(self.ws, D.PACKING_LIST)
        self.assertEqual(form.rows[0]["gross_weight_kg"], "3.4")
        self.assertEqual(form.rows[0]["description"], "Human description")
        self.assertEqual(form.rows[0]["quantity"], "2")
        origins = form_value_origins(self.ws, D.PACKING_LIST)
        self.assertEqual(next(item for item in origins if item.field_name == "gross_weight_kg").origin_kind, "file")
        self.assertEqual(next(item for item in origins if item.field_name == "description").origin_kind, "manual")
