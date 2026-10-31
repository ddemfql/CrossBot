import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from core.models import Case, CaseStatus, CrossCheckResult, CrossCheckStatus, Document, DocumentType, DocumentVersion, ExtractedField, FieldSource, ReviewTask, ReviewStatus
from core.validators import transition_case
from core.errors import ValidationError
from storage.sqlite_store import SQLiteStore

class StateAndStorageTests(unittest.TestCase):
    def test_case_cannot_pass_without_review_and_checks(self):
        case = Case(case_number="CASE-001", customer_name="Acme", destination_country="DE")
        task = ReviewTask(case_id=case.case_id, status=ReviewStatus.PENDING)
        check = CrossCheckResult(case_id=case.case_id, status=CrossCheckStatus.PASS)
        with self.assertRaises(ValidationError):
            transition_case(case, CaseStatus.PASSED, review_tasks=[task], checks=[check])

    def test_case_can_close_after_review_and_checks(self):
        case = Case(case_number="CASE-001", customer_name="Acme", destination_country="DE", status=CaseStatus.PENDING_REVIEW)
        task = ReviewTask(case_id=case.case_id, status=ReviewStatus.APPROVED)
        check = CrossCheckResult(case_id=case.case_id, status=CrossCheckStatus.PASS)
        transition_case(case, CaseStatus.PASSED, review_tasks=[task], checks=[check])
        transition_case(case, CaseStatus.CLOSED, review_tasks=[task], checks=[check])
        self.assertEqual(case.status, CaseStatus.CLOSED)

    def test_empty_gates_cannot_pass_case(self):
        case = Case(case_number="CASE-001", customer_name="Acme", destination_country="DE", status=CaseStatus.PENDING_REVIEW)
        with self.assertRaises(ValidationError):
            transition_case(case, CaseStatus.PASSED, review_tasks=[], checks=[])

    def test_sqlite_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "crossbot.db")
            case = Case(case_number="CASE-002", customer_name="Acme", destination_country="DE", invoice_total=Decimal("120.50"))
            store.save_case(case)
            loaded = store.get_case(case.case_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.case_number, case.case_number)
            self.assertEqual(loaded.status, CaseStatus.DRAFT)
            self.assertEqual(loaded.invoice_total, Decimal("120.50"))
            self.assertIsInstance(loaded.created_at, datetime)
            store.close()

    def test_nested_document_models_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "crossbot.db")
            document = Document(
                case_id="case_1",
                document_type=DocumentType.PACKING_LIST,
                versions=[DocumentVersion(fields=[ExtractedField(name="packages", value=12, source=FieldSource.MACHINE)])],
            )
            store.save_document(document)
            loaded = store.get_document(document.document_id)
            self.assertEqual(loaded.document_type, DocumentType.PACKING_LIST)
            self.assertEqual(loaded.versions[0].fields[0].source, FieldSource.MACHINE)
            store.close()

if __name__ == "__main__":
    unittest.main()
