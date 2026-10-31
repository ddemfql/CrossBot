"""One human cell edit must not adopt untouched inherited or unknown data."""
import unittest
from copy import deepcopy

from core.models import DocumentType as D, DocumentForm, TradeData, TradeLine, Workspace, from_dict, to_dict
from case_workflow.service import CaseService
from documents.forms import FormService
from documents.requirement_inputs import RequirementInputService
from documents.provenance import form_value_origins
from validation.export_review import review_export, review_digest
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor


class ScopedRowProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.creator, self.editor = actor("synthetic-creator", "sales"), actor("synthetic-editor", "sales")
        line = TradeLine("A", "Synthetic product", "2", "3", packages=1, gross_weight_kg="5",
                         supplied_fields=["unit", "packages", "gross_weight_kg"])
        self.ws = CaseService(self.repo, PendingRules()).create(self.creator,
            TradeData(order_no="SYN-1", buyer="Synthetic Buyer", seller="Synthetic Seller", currency="", line_items=[line]))
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.creator, D.PACKING_LIST,
            {"document_no": "PL-1", "document_date": "2026-10-31", "order_no": "SYN-1",
             "buyer": "Synthetic Buyer", "seller": "Synthetic Seller"}, [], "Only provide PL headers", preserve_inherited_rows=True)

    def origins(self):
        return {origin.field_name: origin for origin in form_value_origins(self.ws, D.PACKING_LIST) if origin.row_number == 1}

    def fill(self, field, value):
        self.ws = RequirementInputService(self.repo, None).fill(self.ws.case.case_id, self.editor, D.PACKING_LIST,
            field, 1, value=value, reason="Verify only the selected cell", expected_revision=self.ws.revision,
            expected_review_digest=review_digest(self.ws, D.PACKING_LIST))

    def test_single_cell_does_not_turn_unknown_net_zero_into_a_pass(self):
        before = self.origins()
        self.assertEqual(before["net_weight_kg"].origin_kind, "default")
        self.assertFalse(review_export(self.ws, D.PACKING_LIST, human_confirmed=True).ready)
        self.fill("quantity", "2")
        after = self.origins()
        self.assertEqual((after["quantity"].origin_kind, after["quantity"].actor_id), ("manual", self.editor.user_id))
        self.assertEqual(to_dict(after["net_weight_kg"]), to_dict(before["net_weight_kg"]))
        for name in ("product_code", "description", "unit", "packages", "gross_weight_kg"):
            self.assertEqual(to_dict(after[name]), to_dict(before[name]))
        self.assertFalse(review_export(self.ws, D.PACKING_LIST, human_confirmed=True).ready)
        self.fill("net_weight_kg", "0")
        self.assertEqual(self.origins()["net_weight_kg"].origin_kind, "manual")
        self.assertTrue(review_export(self.ws, D.PACKING_LIST, human_confirmed=True).ready)
        self.assertFalse(self.ws.approvals)

    def test_successive_edits_and_roundtrip_keep_prior_origin_identity(self):
        self.fill("quantity", "2")
        first = self.origins()
        self.ws = from_dict(Workspace, to_dict(self.ws))
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.fill("unit", "pcs")
        second = self.origins()
        self.assertEqual(to_dict(second["quantity"]), to_dict(first["quantity"]))
        self.assertEqual(second["net_weight_kg"].origin_kind, "default")
        self.assertFalse(second["net_weight_kg"].actor_id)

    def test_legacy_unknown_neighbor_cells_do_not_gain_an_actor(self):
        self.ws.document_forms = [DocumentForm(D.PACKING_LIST, rows=[{"product_code": "A", "description": "Legacy product",
            "quantity": "2", "unit": "pcs", "packages": "1", "gross_weight_kg": "5", "net_weight_kg": "0"}],
            rows_manually_edited=True)]
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        self.fill("quantity", "2")
        origins = self.origins()
        self.assertEqual(origins["quantity"].origin_kind, "manual")
        self.assertEqual(origins["net_weight_kg"].origin_kind, "unknown")
        self.assertFalse(origins["net_weight_kg"].actor_id)

    def test_unmarked_defaults_stay_unknown_after_generic_trade_draft_save(self):
        cases = CaseService(self.repo, PendingRules())
        self.ws = cases.create(self.creator, TradeData(line_items=[TradeLine("A", "Synthetic product", "2", "3")]))
        edited = deepcopy(self.ws.trade)
        edited.buyer = "Only new buyer data"
        self.ws = cases.update_trade(self.ws.case.case_id, self.editor, edited, "Save unrelated order draft field")
        invoice = {origin.field_name: origin for origin in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)}
        self.assertEqual(invoice["currency"].origin_kind, "default")
        self.assertEqual(invoice["unit"].origin_kind, "default")
        for name in ("packages", "gross_weight_kg", "net_weight_kg"):
            self.assertEqual(self.origins()[name].origin_kind, "default")
            self.assertFalse(self.origins()[name].actor_id)
        edited = deepcopy(self.ws.trade)
        edited.supplied_fields = ["currency"]
        edited.line_items[0].supplied_fields = ["unit", "packages", "gross_weight_kg", "net_weight_kg"]
        self.ws = cases.update_trade(self.ws.case.case_id, self.editor, edited, "Explicitly provide displayed zero values")
        invoice = {origin.field_name: origin for origin in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)}
        self.assertEqual(invoice["currency"].origin_kind, "trade")
        for name in ("packages", "gross_weight_kg", "net_weight_kg"):
            self.assertEqual((self.origins()[name].origin_kind, self.origins()[name].actor_id), ("trade", self.editor.user_id))

    def test_cleared_supply_marker_does_not_restore_an_older_known_zero(self):
        edited = deepcopy(self.ws.trade)
        edited.line_items[0].supplied_fields.append("net_weight_kg")
        cases = CaseService(self.repo, PendingRules())
        self.ws = cases.update_trade(self.ws.case.case_id, self.editor, edited, "Explicit zero was previously known")
        self.assertEqual(self.origins()["net_weight_kg"].origin_kind, "trade")
        edited = deepcopy(self.ws.trade)
        edited.line_items[0].supplied_fields.remove("net_weight_kg")
        edited.line_items[0].net_weight_kg = "0.00"
        self.ws = cases.update_trade(self.ws.case.case_id, self.editor, edited, "Now leave net weight unspecified")
        self.assertEqual(self.origins()["net_weight_kg"].origin_kind, "default")
        self.assertFalse(self.origins()["net_weight_kg"].actor_id)
