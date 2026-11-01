"""Canonical typed input graph and persisted independent-source contracts."""
import unittest

from case_workflow.service import CaseService
from core.models import (DataRequirement, DocumentType as D, TradeData, TradeLine, TradeAdjustment,
                         TradePackage, GeneratedArtifact, Workspace,
                         from_dict, to_dict)
from documents.forms import FormService
from documents.manual_inputs import ManualInputService
from documents.requirements import data_requirements, delivery_requirements, missing_required_data
from documents.simple_workbench import provided_inventory
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor, trade


class RequirementGraphTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = actor("synthetic-requirement-sales", "sales")
        self.cases = CaseService(self.repo, PendingRules())
        self.ws = self.cases.create(self.actor, TradeData(currency=""))
        self.forms = FormService(self.repo)

    def complete_invoice(self):
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            {"invoice_no": "SYN-CI-1", "invoice_date": "2026-10-31", "order_no": "SYN-1",
             "buyer": "Synthetic Buyer", "seller": "Synthetic Seller", "currency": "USD"},
            [{"product_code": "SYN-A", "description": "Synthetic item", "quantity": "2",
              "unit": "pcs", "unit_price": "10"}], "Human supplies complete synthetic invoice data")

    def test_graph_uses_typed_slots_and_identity_alias_sources(self):
        self.complete_invoice()
        graph = data_requirements(self.ws, D.COMMERCIAL_INVOICE)
        self.assertTrue(all(isinstance(item, DataRequirement) for item in graph))
        no = next(item for item in graph if item.field_name == "document_no")
        date = next(item for item in graph if item.field_name == "document_date")
        self.assertEqual((no.value, no.status), ("SYN-CI-1", "provided"))
        self.assertEqual((date.value, date.status), ("2026-10-31", "provided"))
        self.assertEqual(no.origin.field_name, "invoice_no")
        self.assertEqual(no.origin.actor_id, self.actor.user_id)
        restored = from_dict(DataRequirement, to_dict(no))
        self.assertIs(restored.document_type, D.COMMERCIAL_INVOICE)
        self.assertEqual(restored.origin.actor_id, self.actor.user_id)

    def test_packing_requires_box_and_weight_inputs_but_invoice_does_not(self):
        self.complete_invoice()
        invoice_fields = {item.field_name for item in missing_required_data(self.ws, D.COMMERCIAL_INVOICE)}
        self.assertNotIn("packages", invoice_fields)
        self.assertNotIn("gross_weight_kg", invoice_fields)
        self.assertNotIn("net_weight_kg", invoice_fields)
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, D.PACKING_LIST,
            {"packing_list_no": "SYN-PL-1", "packing_date": "2026-10-31", "order_no": "SYN-1",
             "buyer": "Synthetic Buyer", "seller": "Synthetic Seller"},
            [{"product_code": "SYN-A", "description": "Synthetic item", "quantity": "2", "unit": "pcs"}],
            "Known products but unknown packing weights")
        missing = {item.field_name for item in missing_required_data(self.ws, D.PACKING_LIST)}
        self.assertTrue({"packages", "gross_weight_kg", "net_weight_kg"} <= missing)
        self.assertNotIn("currency", missing)

    def test_empty_rows_and_required_headers_remain_missing(self):
        graph = data_requirements(self.ws, D.COMMERCIAL_INVOICE)
        missing = {item.field_name for item in graph if item.required and item.status == "missing"}
        self.assertTrue({"rows", "document_no", "document_date", "buyer", "seller", "order_no", "currency"} <= missing)
        self.assertFalse(any(item.field_name == "notes" for item in graph))
        self.assertFalse(data_requirements(self.ws, D.BILL_OF_LADING))

    def test_populated_optional_fields_are_in_graph_and_explicit_clears_stay_missing(self):
        self.complete_invoice()
        form = next(form for form in self.ws.document_forms if form.document_type is D.COMMERCIAL_INVOICE)
        self.ws = self.forms.save(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            dict(form.values, buyer="", notify_party="Synthetic Notify", notes="Known delivery note"), form.rows,
            "Human clears buyer and supplies optional known fields")
        self.ws = self.forms.configure_fields(self.ws.case.case_id, self.actor, D.COMMERCIAL_INVOICE,
            [*form.included_fields, "notify_party", "notes"], form.included_row_fields,
            "Include these optional business fields in this document")
        graph = data_requirements(self.ws, D.COMMERCIAL_INVOICE)
        buyer = next(item for item in graph if item.field_name == "buyer")
        self.assertEqual(buyer.status, "missing")
        notify = next(item for item in graph if item.field_name == "notify_party")
        self.assertFalse(notify.required)
        self.assertEqual(notify.value, "Synthetic Notify")
        self.assertIsNotNone(notify.origin)

    def test_graph_covers_each_selected_output_and_independent_source_is_listed(self):
        self.ws = self.cases.set_document_checklist(self.ws.case.case_id, self.actor,
            [D.PURCHASE_ORDER], [D.PROFORMA_INVOICE, D.COMMERCIAL_INVOICE, D.PACKING_LIST])
        service = ManualInputService(self.repo)
        self.ws = service.save_record(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            {"buyer": "Buyer A"}, [], "Independent A", title="Synthetic PO A")
        record_a = self.ws.manual_data_records[-1]
        self.ws = service.save_record(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            {"buyer": "Buyer B"}, [], "Independent B", title="Synthetic PO B")
        self.ws = service.apply(self.ws.case.case_id, self.actor, D.PURCHASE_ORDER,
            D.COMMERCIAL_INVOICE, "Use A", source_record_id=record_a.record_id)
        graph = delivery_requirements(self.ws)
        self.assertEqual(set(graph), {D.PROFORMA_INVOICE, D.COMMERCIAL_INVOICE, D.PACKING_LIST})
        self.assertTrue(all(graph[kind] for kind in graph))
        ci_buyer = next(item for item in graph[D.COMMERCIAL_INVOICE] if item.field_name == "buyer")
        self.assertEqual(ci_buyer.value, "Buyer A")
        self.assertIn(record_a.record_id, ci_buyer.origin.note)
        inventory = provided_inventory(self.ws)
        self.assertTrue(any(row["资料"] == "Synthetic PO A" and "商业发票" in row["用于生成"] for row in inventory))
        self.assertTrue(any(row["资料"] == "Synthetic PO B" and row["用于生成"] == "待指定用途" for row in inventory))
        restored = from_dict(Workspace, to_dict(self.ws))
        self.assertEqual(len(restored.manual_data_records), 2)
        self.assertIs(restored.manual_data_records[0].document_type, D.PURCHASE_ORDER)
        reference = next(form for form in restored.document_forms if form.document_type is D.COMMERCIAL_INVOICE).source_form_references[0]
        self.assertEqual(reference.record_id, record_a.record_id)
        self.assertEqual(reference.revision, 1)

    def test_us_context_baseline_is_conditional_and_traceable(self):
        draft = trade()
        draft.destination_country = "US"
        self.ws = self.cases.create(self.actor, draft)
        graph = data_requirements(self.ws, D.COMMERCIAL_INVOICE)
        missing = {item.field_name for item in graph if item.required and item.status != "provided"}
        self.assertTrue({"port_of_entry", "transaction_date", "transaction_place", "seller_address",
            "buyer_address", "responsible_exporter_person", "invoice_adjustments_confirmed",
            "country_of_origin", "grade_quality", "marks_and_numbers", "package_contents", "package_details"} <= missing)
        draft.port_of_entry = "Synthetic Port"
        draft.transaction_date = "2026-10-31"
        draft.transaction_place = "Synthetic Place"
        draft.seller_address = "Synthetic seller address"
        draft.buyer_address = "Synthetic buyer address"
        draft.responsible_exporter_person = "Synthetic responsible person"
        draft.invoice_adjustments_confirmed = True
        line = draft.line_items[0]
        line.country_of_origin, line.grade_quality = "CN", "A"
        line.marks_and_numbers, line.package_contents = "SYN-BOX-1", "Synthetic item x2"
        draft.packages = [TradePackage("SYN-BOX-1", "SYN-BOX-1", line.product_code,
            "Synthetic item x2", "2", "pcs", "1.2", "1.0")]
        draft.adjustments = [TradeAdjustment(name="Synthetic freight", amount="3", currency="USD")]
        self.ws = self.cases.update_trade(self.ws.case.case_id, self.actor, draft, "Human supplies known context facts")
        graph = data_requirements(self.ws, D.COMMERCIAL_INVOICE)
        port = next(item for item in graph if item.field_name == "port_of_entry")
        self.assertEqual(port.status, "provided")
        self.assertEqual(port.origin.actor_id, self.actor.user_id)
        self.assertIsNotNone(port.origin.occurred_at)
        self.assertFalse(any(item.field_name == "package_details" for item in graph))
        kinds = next(item for item in graph if item.field_name == "adjustments[1].kind")
        self.assertEqual(kinds.value, "other")
        self.assertEqual(kinds.origin.actor_id, self.actor.user_id)
        self.assertTrue(any(item.field_name == "line_items[1].grade_quality" for item in graph))
        self.assertTrue(any(item.field_name == "packages[1].marks_and_numbers" and item.required for item in graph))
        self.assertFalse(any(item.field_name == "port_of_entry" and item.required
                             for item in data_requirements(self.ws, D.QUOTATION)))

    def test_creation_defaults_need_adoption_but_explicit_zero_is_not_missing(self):
        draft = TradeData(line_items=[TradeLine("SYN-1", "Synthetic item", "1", "2")])
        self.ws = self.cases.create(self.actor, draft)
        graph = data_requirements(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(next(item for item in graph if item.field_name == "currency").status, "review_required")
        self.assertEqual(next(item for item in graph if item.field_name == "unit").status, "review_required")
        packing = data_requirements(self.ws, D.PACKING_LIST)
        weight = next(item for item in packing if item.field_name == "gross_weight_kg")
        self.assertEqual((weight.value, weight.status), ("0", "review_required"))
        draft.supplied_fields = ["currency"]
        draft.line_items[0].supplied_fields = ["unit", "packages", "gross_weight_kg", "net_weight_kg"]
        self.ws = self.cases.create(self.actor, draft)
        packing = data_requirements(self.ws, D.PACKING_LIST)
        self.assertEqual(next(item for item in packing if item.field_name == "gross_weight_kg").status, "provided")
        self.assertEqual(next(item for item in packing if item.field_name == "gross_weight_kg").value, "0")
        legacy = TradeData(line_items=[TradeLine("SYN-1", "Synthetic item", "1", "2")])
        self.ws = self.cases.create(self.actor, legacy)
        self.ws = self.cases.update_trade(self.ws.case.case_id, self.actor, legacy, "Saved draft without explicit default confirmation")
        self.assertEqual(next(item for item in data_requirements(self.ws, D.COMMERCIAL_INVOICE)
                              if item.field_name == "currency").status, "review_required")
        legacy.supplied_fields = ["currency"]
        self.ws = self.cases.update_trade(self.ws.case.case_id, self.actor, legacy, "Human explicitly supplied currency")
        self.assertEqual(next(item for item in data_requirements(self.ws, D.COMMERCIAL_INVOICE)
                              if item.field_name == "currency").status, "provided")

    def test_unknown_fee_input_does_not_invent_actor_and_artifact_targets_are_included(self):
        self.ws.trade.adjustments = [TradeAdjustment(name="Legacy freight", amount="0", currency="USD")]
        next(form for form in self.ws.document_forms
             if form.document_type is D.COMMERCIAL_INVOICE).included_context_fields = ["adjustments"]
        self.ws.artifacts.append(GeneratedArtifact(document_type=D.QUOTATION, status="stale"))
        graph = delivery_requirements(self.ws)
        self.assertIn(D.QUOTATION, graph)
        fee = next(item for item in graph[D.COMMERCIAL_INVOICE] if item.field_name == "adjustments[1].amount")
        self.assertEqual(fee.value, "0")
        self.assertEqual(fee.status, "review_required")
        self.assertFalse(fee.origin.actor_id)
        self.assertIsNone(fee.origin.occurred_at)


if __name__ == "__main__":
    unittest.main()

