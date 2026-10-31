"""Purpose-separated checklist compatibility, persistence and authorization."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from case_workflow.service import CaseService
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (Actor, Case, DocumentForm, DocumentType as D,
                         FormSourceReference, RequirementPolicy, Workspace,
                         from_dict, to_dict)
from documents.checklist import requirement_groups
from storage.sqlite import SQLiteWorkspaceRepository
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor, trade


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.service = CaseService(self.repo, PendingRules())
        self.actor = actor("synthetic-sales", "sales")

    def test_legacy_snapshot_infers_purposes_without_mutation(self):
        old = {"case": to_dict(Case()), "required_document_types":
               [D.PURCHASE_ORDER.value, D.COMMERCIAL_INVOICE.value]}
        ws = from_dict(Workspace, old)
        before = to_dict(ws)
        self.assertEqual(requirement_groups(ws), ([D.PURCHASE_ORDER], [D.COMMERCIAL_INVOICE]))
        self.assertEqual(to_dict(ws), before)
        self.assertIsNone(ws.required_source_types)
        self.assertFalse(from_dict(DocumentForm, {"document_type": D.PURCHASE_ORDER.value}).manually_provided)

    def test_explicit_groups_and_form_references_round_trip_sqlite(self):
        ws = Workspace(Case(), required_source_types=[D.PURCHASE_ORDER],
            required_delivery_types=[], document_forms=[DocumentForm(D.COMMERCIAL_INVOICE,
                manually_provided=True, source_form_references=[FormSourceReference(D.PURCHASE_ORDER, 3)])])
        with TemporaryDirectory() as root, closing(SQLiteWorkspaceRepository(Path(root) / "synthetic.sqlite3")) as repository:
            repository.save(ws, expected_revision=0)
            loaded = repository.get(ws.case.case_id)
            self.assertEqual(to_dict(loaded), to_dict(ws))
            reference = loaded.document_forms[0].source_form_references[0]
            self.assertIs(reference.document_type, D.PURCHASE_ORDER)
            self.assertEqual(reference.revision, 3)
            self.assertEqual(loaded.required_delivery_types, [])

    def test_overlap_keeps_both_purposes_and_aggregate_is_unique(self):
        ws = self.service.create(self.actor, trade())
        ws = self.service.set_document_checklist(ws.case.case_id, self.actor,
            [D.PURCHASE_ORDER, D.PACKING_LIST, D.PURCHASE_ORDER], [D.PACKING_LIST])
        self.assertEqual(requirement_groups(ws),
            ([D.PURCHASE_ORDER, D.PACKING_LIST], [D.PACKING_LIST]))
        self.assertEqual(ws.required_document_types, [D.PURCHASE_ORDER, D.PACKING_LIST])
        self.assertEqual(ws.case.required_document_types, ws.required_document_types)
        self.assertEqual(ws.audit[-1].action, "document_checklist_updated")
        self.assertFalse(ws.approvals)

    def test_conditional_extras_are_preserved_and_recomputed(self):
        policies = [RequirementPolicy("de", [D.TEST_REPORT, D.SALES_CONTRACT], destination_country="DE")]
        ws = self.service.create(self.actor, trade(), policies=policies)
        ws = self.service.set_document_checklist(ws.case.case_id, self.actor, [], [])
        self.assertEqual(requirement_groups(ws), ([D.TEST_REPORT], [D.SALES_CONTRACT]))
        edited = deepcopy(ws.trade)
        edited.destination_country = "FR"
        ws = self.service.update_trade(ws.case.case_id, self.actor, edited, "change destination")
        self.assertEqual(requirement_groups(ws), ([], []))
        self.assertEqual(ws.required_document_types, [])

    def test_legacy_update_discards_prior_explicit_selections(self):
        ws = self.service.create(self.actor, trade())
        ws = self.service.set_document_checklist(ws.case.case_id, self.actor, [D.PACKING_LIST], [])
        ws = self.service.set_required_documents(ws.case.case_id, self.actor, [D.COMMERCIAL_INVOICE])
        self.assertIsNone(ws.required_source_types)
        self.assertIsNone(ws.required_delivery_types)
        self.assertEqual(requirement_groups(ws), ([], [D.COMMERCIAL_INVOICE]))

    def test_invalid_or_external_delivery_does_not_change_selection(self):
        ws = self.service.create(self.actor, trade())
        revision = ws.revision
        for sources, deliveries in ((None, []), ("purchase_order", []),
                (["unknown"], []), ([], [D.BILL_OF_LADING]), ([], [D.TEST_REPORT])):
            with self.subTest(sources=sources, deliveries=deliveries), self.assertRaises(ValidationError):
                self.service.set_document_checklist(ws.case.case_id, self.actor, sources, deliveries)
            self.assertEqual(self.repo.get(ws.case.case_id).revision, revision)

    def test_unauthenticated_or_ai_actor_cannot_modify_checklist(self):
        ws = self.service.create(self.actor, trade())
        for denied in (Actor("anonymous", ["sales"]),
                       Actor("synthetic-model", ["sales"], authenticated=True, kind="ai")):
            with self.subTest(denied=denied), self.assertRaises(CrossBotError) as raised:
                self.service.set_document_checklist(ws.case.case_id, denied, [], [])
            self.assertIs(raised.exception.code, ErrorCode.FORBIDDEN)
            self.assertIsNone(self.repo.get(ws.case.case_id).required_source_types)


if __name__ == "__main__":
    unittest.main()

