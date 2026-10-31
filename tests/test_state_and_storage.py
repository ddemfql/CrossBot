import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from core.models import Case, CaseStatus, CrossCheckResult, CrossCheckStatus, Document, DocumentStatus, DocumentType, DocumentVersion, ExtractedField, FieldValueStatus, ReviewTask, ReviewStatus
from core.validators import transition_case
from core.errors import CrossBotError, ValidationError
from storage.sqlite_store import SQLiteStore

class StateAndStorageTests(unittest.TestCase):
    def _parsed_document(self, case_id):
        version = DocumentVersion(sha256="abc", fields=[ExtractedField(name="packages", value=12, status=FieldValueStatus.CONFIRMED)])
        return Document(case_id=case_id, document_type=DocumentType.PACKING_LIST, status=DocumentStatus.PARSED, current_version_id=version.version_id, versions=[version])

    def test_case_cannot_pass_without_review_and_checks(self):
        case = Case(case_number="CASE-001", customer_name="Acme", destination_country="DE", status=CaseStatus.PENDING_REVIEW, required_document_types=[DocumentType.PACKING_LIST])
        with self.assertRaises(ValidationError):
            transition_case(case, CaseStatus.PASSED, documents=[self._parsed_document(case.case_id)], review_tasks=[], checks=[])

    def test_case_can_close_after_all_gates(self):
        case = Case(case_number="CASE-001", customer_name="Acme", destination_country="DE", status=CaseStatus.PENDING_REVIEW, required_document_types=[DocumentType.PACKING_LIST])
        task = ReviewTask(case_id=case.case_id, status=ReviewStatus.APPROVED)
        check = CrossCheckResult(case_id=case.case_id, status=CrossCheckStatus.PASS)
        documents = [self._parsed_document(case.case_id)]
        transition_case(case, CaseStatus.PASSED, documents=documents, review_tasks=[task], checks=[check])
        transition_case(case, CaseStatus.CLOSED, documents=documents, review_tasks=[task], checks=[check])
        self.assertEqual(case.status, CaseStatus.CLOSED)

    def test_unconfirmed_field_cannot_pass(self):
        case = Case(case_number="CASE-001", customer_name="Acme", destination_country="DE", status=CaseStatus.PENDING_REVIEW, required_document_types=[DocumentType.PACKING_LIST])
        with self.assertRaises(ValidationError):
            document = Document(case_id=case.case_id, document_type=DocumentType.PACKING_LIST, status=DocumentStatus.PARSED)
            version = DocumentVersion(sha256="abc", fields=[ExtractedField(name="packages")])
            document.current_version_id = version.version_id
            document.versions = [version]
            transition_case(case, CaseStatus.PASSED, documents=[document], review_tasks=[ReviewTask(case_id=case.case_id, status=ReviewStatus.APPROVED)], checks=[CrossCheckResult(case_id=case.case_id, status=CrossCheckStatus.PASS)])

    def test_sqlite_queries_and_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "crossbot.db")
            case = Case(case_number="CASE-002", customer_name="Acme", destination_country="DE", invoice_total=Decimal("120.50"))
            document = self._parsed_document(case.case_id)
            task = ReviewTask(case_id=case.case_id)
            check = CrossCheckResult(case_id=case.case_id, status=CrossCheckStatus.PASS)
            store.save_case(case); store.save_document(document); store.save_review_task(task); store.save_cross_check(check)
            self.assertEqual(store.get_case(case.case_id).invoice_total, Decimal("120.50"))
            self.assertEqual(len(store.list_documents(case.case_id)), 1)
            self.assertEqual(len(store.list_review_tasks(case.case_id)), 1)
            self.assertEqual(len(store.list_cross_checks(case.case_id)), 1)
            self.assertIsNotNone(store.find_document_by_sha256(case.case_id, "abc"))
            store.close()

    def test_duplicate_document_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "crossbot.db")
            case_id = "case-dup"
            store.save_document(self._parsed_document(case_id))
            with self.assertRaises(CrossBotError):
                store.save_document(self._parsed_document(case_id))
            store.close()

if __name__ == "__main__": unittest.main()
