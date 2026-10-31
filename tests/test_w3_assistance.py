import unittest
from core.models import (Actor, Case, Document, DocumentVersion, DocumentType,
                         ExtractedField, FieldValueStatus, Workspace)
from core.errors import CrossBotError
from review.assistance import apply_transcription
from tests.test_w3_workflow import MemoryRepository


class AssistanceTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.actor = Actor("synthetic-admin", ["admin"], authenticated=True)
        self.doc = Document(document_type=DocumentType.PURCHASE_ORDER,
            versions=[DocumentVersion(fields=[ExtractedField("quantity", "3")])])
        self.doc.current_version_id = self.doc.versions[0].version_id
        self.ws = Workspace(Case(), documents=[self.doc])
        self.repo.save(self.ws, expected_revision=0)
        self.entry = {"document_id": self.doc.document_id, "location": "page 1, qty column",
            "field_name": "quantity", "value": "4", "reason": "Visible source text transcribed"}

    def test_virtual_sales_values_remain_candidates_and_original_is_retained_in_history(self):
        updated = apply_transcription(self.repo, self.ws.case.case_id, self.actor, [self.entry])
        field = updated.documents[0].versions[0].fields[0]
        self.assertEqual(field.value, "3")
        self.assertEqual(field.assisted_value, "4")
        self.assertIs(field.status, FieldValueStatus.CANDIDATE)
        self.assertIsNone(field.confirmed_by)
        self.assertEqual(updated.assistance_records[0].previous_value, "3")
        self.assertEqual(updated.assistance_records[0].actor_kind, "virtual_sales")
        self.assertEqual(updated.approvals, [])
        again = apply_transcription(self.repo, self.ws.case.case_id, self.actor, [self.entry])
        self.assertEqual(len(again.assistance_records), 1)
        self.assertEqual(again.revision, updated.revision)

    def test_sources_are_mandatory_and_human_values_are_protected(self):
        with self.assertRaises(CrossBotError):
            apply_transcription(self.repo, self.ws.case.case_id, self.actor,
                                [{**self.entry, "location": ""}])
        version = self.ws.documents[0].versions[0]
        version.fields[0].confirmed_by = "human-reviewer"
        version.fields[0].confirmed_value = "3"
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        with self.assertRaises(CrossBotError):
            apply_transcription(self.repo, self.ws.case.case_id, self.actor, [self.entry])

    def test_returning_to_previous_suggestion_is_saved_but_immediate_repeat_is_noop(self):
        for proposed in ("4", "5", "4"):
            updated = apply_transcription(self.repo, self.ws.case.case_id, self.actor,
                [{**self.entry, "value": proposed}])
            field = updated.documents[0].versions[0].fields[0]
            self.assertEqual(field.value, "3")
            self.assertEqual(field.assisted_value, proposed)
            self.assertIsNone(field.confirmed_by)
        self.assertEqual([record.proposed_value for record in updated.assistance_records], ["4", "5", "4"])
        revision = updated.revision
        repeated = apply_transcription(self.repo, self.ws.case.case_id, self.actor, [self.entry])
        self.assertEqual(repeated.revision, revision)
        self.assertEqual(len(repeated.assistance_records), 3)

    def test_denied_assistance_is_audited_without_changing_candidates(self):
        unauthorized = Actor("synthetic-finance", ["finance"], authenticated=True)
        with self.assertRaises(CrossBotError):
            apply_transcription(self.repo, self.ws.case.case_id, unauthorized, [self.entry])
        stored = self.repo.get(self.ws.case.case_id)
        self.assertEqual(stored.audit[-1].action, "access_denied")
        self.assertEqual(stored.audit[-1].actor_id, unauthorized.user_id)
        self.assertEqual(stored.audit[-1].after["action"], "manual_field")
        self.assertEqual(stored.assistance_records, [])
        field = stored.documents[0].versions[0].fields[0]
        self.assertEqual(field.value, "3")
        self.assertIsNone(field.assisted_value)


if __name__ == "__main__":
    unittest.main()

