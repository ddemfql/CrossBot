from pathlib import Path
import tempfile
import unittest

from core.errors import CrossBotError, ErrorCode
from core.models import (Actor, Case, Document, DocumentStatus, DocumentType,
    DocumentVersion, ExtractedField, FieldSource, FieldValueStatus, TradeData, Workspace)
from review.service import ReviewService
from storage.sqlite import SQLiteWorkspaceRepository


class ManualFieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteWorkspaceRepository(Path(self.temp.name) / "workspace.db")
        self.actor = Actor("sales-1", ["sales"], authenticated=True)
        workspace = Workspace(Case(case_number="SYNTHETIC-MANUAL"), trade=TradeData())
        document = Document(case_id=workspace.case.case_id,
            document_type=DocumentType.BILL_OF_LADING, title="synthetic-unreadable.pdf")
        version = DocumentVersion(document_id=document.document_id,
            original_filename=document.title, status=DocumentStatus.PARSE_FAILED,
            parse_error="Synthetic unsupported layout")
        document.versions.append(version)
        document.current_version_id = version.version_id
        workspace.documents.append(document)
        self.document_id = document.document_id
        self.version_id = version.version_id
        self.repository.save(workspace, expected_revision=0)
        self.service = ReviewService(self.repository, rules=object())

    def tearDown(self):
        self.repository.close()
        self.temp.cleanup()

    def test_manual_fields_on_parse_failure_are_saved_as_confirmed_human_data(self):
        result = self.service.enter_manual_field(self.repository.get(
            self.repository.list()[0].case.case_id).case.case_id, self.document_id,
            "consignee", "Synthetic Receiver", self.actor, "Read from the source document")
        saved = self.repository.get(result.case.case_id)
        version = saved.documents[0].versions[0]
        field = version.fields[0]
        self.assertEqual(version.status, DocumentStatus.MANUALLY_ENTERED)
        self.assertEqual(version.parse_error, None)
        self.assertEqual(field.source, FieldSource.HUMAN)
        self.assertEqual(field.status, FieldValueStatus.CONFIRMED)
        self.assertIsNone(field.value)
        self.assertEqual(field.confirmed_value, "Synthetic Receiver")
        self.assertEqual(field.confirmed_by, self.actor.user_id)
        self.assertTrue(any(event.action == "field_entered_manually" for event in saved.audit))

    def test_human_correction_preserves_the_machine_candidate(self):
        workspace = self.repository.list()[0]
        version = workspace.documents[0].versions[0]
        version.status = DocumentStatus.PARSED
        version.fields.append(ExtractedField(name="quantity", value="10", normalized_value="10"))
        workspace.documents[0].status = DocumentStatus.PARSED
        self.repository.save(workspace, expected_revision=workspace.revision)
        result = self.service.enter_manual_field(workspace.case.case_id, self.document_id,
            "quantity", "12", self.actor, "Corrected from the signed order")
        field = result.documents[0].versions[0].fields[0]
        self.assertEqual(field.value, "10")
        self.assertEqual(field.confirmed_value, "12")
        self.assertEqual(field.source, FieldSource.MACHINE)
        self.assertEqual(result.documents[0].versions[0].status, DocumentStatus.PARSED)

    def test_non_document_role_cannot_enter_manual_fields(self):
        case_id = self.repository.list()[0].case.case_id
        with self.assertRaises(CrossBotError) as caught:
            self.service.enter_manual_field(case_id, self.document_id, "consignee", "Synthetic",
                Actor("finance-1", ["finance"], authenticated=True), "No permission")
        self.assertEqual(caught.exception.code, ErrorCode.FORBIDDEN)
        self.assertEqual(self.repository.get(case_id).audit[-1].action, "access_denied")

    def test_arbitrary_field_names_are_rejected(self):
        case_id = self.repository.list()[0].case.case_id
        with self.assertRaises(CrossBotError) as caught:
            self.service.enter_manual_field(case_id, self.document_id, "signature_image",
                "synthetic", self.actor, "Do not manufacture a signature")
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)


if __name__ == "__main__":
    unittest.main()

