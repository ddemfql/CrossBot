"""Synthetic regression of existing state gates, not a complete W3 workflow."""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from core.errors import ValidationError
from core.models import (
    Case, CaseStatus, CrossCheckResult, CrossCheckStatus, Document, DocumentStatus,
    DocumentType, DocumentVersion, ExtractedField, FieldValueStatus, ReviewStatus,
    ReviewTask, to_dict,
)
from core.validators import transition_case
from validation.rules import compare_quantity


class WorkflowGateTests(unittest.TestCase):
    def setUp(self):
        network = patch("socket.socket", side_effect=AssertionError("Network must not be used"))
        network.start()
        self.addCleanup(network.stop)

    def _inputs(self, state=CaseStatus.PENDING_REVIEW, right_value=100):
        case = Case(case_id="case-fixture", case_number="SYNTHETIC-001",
                    customer_name="Synthetic Customer", destination_country="DE", status=state)
        documents = []
        for index, kind in enumerate((DocumentType.COMMERCIAL_INVOICE, DocumentType.PACKING_LIST)):
            document_id = f"doc-fixture-{index}"
            field = ExtractedField(
                name="fixture_quantity", value=100 if index == 0 else right_value,
                status=FieldValueStatus.CONFIRMED, page_number=1 if index == 0 else None,
                sheet_name="Synthetic" if index == 1 else None,
                cell_reference="B2" if index == 1 else None,
            )
            version = DocumentVersion(
                version_id=f"version-fixture-{index}", document_id=document_id,
                sha256=str(index + 1) * 64, status=DocumentStatus.PARSED, fields=[field],
            )
            documents.append(Document(
                document_id=document_id, case_id=case.case_id, document_type=kind,
                current_version_id=version.version_id, status=DocumentStatus.PARSED, versions=[version],
            ))
        # Fixture-only selection: no claim about C's final-value convention.
        left, right = [document.versions[0].fields[0] for document in documents]
        status = compare_quantity(
            left.value, right.value, left_status=left.status, right_status=right.status,
            context_confirmed=True, left_unit="pcs", right_unit="pcs",
            minimum=Decimal(0), allow_fractional=False,
        )
        checks = [CrossCheckResult(case_id=case.case_id, rule_code="IP-002", status=status)]
        tasks = [ReviewTask(case_id=case.case_id, status=ReviewStatus.APPROVED)]
        return case, documents, tasks, checks

    def _blocked(self, case, target, documents, tasks, checks):
        before = to_dict(case)
        with self.assertRaises(ValidationError):
            transition_case(case, target, documents=documents, review_tasks=tasks, checks=checks)
        self.assertEqual(to_dict(case), before)

    def test_reviewed_pass_records_pass_and_close_audit_without_network(self):
        case, documents, tasks, checks = self._inputs()
        self.assertIs(checks[0].status, CrossCheckStatus.PASS)
        for target in (CaseStatus.PASSED, CaseStatus.CLOSED):
            _, audit = transition_case(case, target, documents=documents, review_tasks=tasks,
                                       checks=checks, actor_id="synthetic-reviewer")
            self.assertIs(case.status, target)
            self.assertEqual(audit.after["status"], target.value)
            self.assertEqual(audit.actor_id, "synthetic-reviewer")

    def test_fail_and_pending_comparisons_block_pass_and_close(self):
        for target, state in ((CaseStatus.PASSED, CaseStatus.PENDING_REVIEW),
                              (CaseStatus.CLOSED, CaseStatus.PASSED)):
            for right, expected in ((120, CrossCheckStatus.FAIL),
                                     (None, CrossCheckStatus.PENDING_REVIEW)):
                with self.subTest(target=target, result=expected):
                    case, documents, tasks, checks = self._inputs(state, right)
                    self.assertIs(checks[0].status, expected)
                    self._blocked(case, target, documents, tasks, checks)

    def test_mixed_fail_and_pending_results_are_preserved_on_rejection(self):
        case, documents, tasks, checks = self._inputs(CaseStatus.PASSED, 120)
        checks.append(CrossCheckResult(case_id=case.case_id, rule_code="IP-004",
                                        status=CrossCheckStatus.PENDING_REVIEW))
        before = to_dict(checks)
        self._blocked(case, CaseStatus.CLOSED, documents, tasks, checks)
        self.assertEqual(to_dict(checks), before)

    def test_unconfirmed_field_blocks_even_when_supplied_result_says_pass(self):
        for target, state in ((CaseStatus.PASSED, CaseStatus.PENDING_REVIEW),
                              (CaseStatus.CLOSED, CaseStatus.PASSED)):
            for status in (FieldValueStatus.CANDIDATE, FieldValueStatus.REJECTED, FieldValueStatus.MISSING):
                with self.subTest(target=target, field_status=status):
                    case, documents, tasks, checks = self._inputs(state)
                    documents[0].versions[0].fields[0].status = status
                    self._blocked(case, target, documents, tasks, checks)

    def test_missing_required_document_blocks_pass_and_close(self):
        for target, state in ((CaseStatus.PASSED, CaseStatus.PENDING_REVIEW),
                              (CaseStatus.CLOSED, CaseStatus.PASSED)):
            with self.subTest(target=target):
                case, documents, tasks, checks = self._inputs(state)
                self._blocked(case, target, documents[1:], tasks, checks)

    def test_pending_and_returned_required_reviews_block_pass_and_close(self):
        for target, state in ((CaseStatus.PASSED, CaseStatus.PENDING_REVIEW),
                              (CaseStatus.CLOSED, CaseStatus.PASSED)):
            for status in (ReviewStatus.PENDING, ReviewStatus.RETURNED):
                with self.subTest(target=target, review=status):
                    case, documents, tasks, checks = self._inputs(state)
                    tasks[0].status = status
                    self._blocked(case, target, documents, tasks, checks)

    def test_empty_checks_or_reviews_block_pass_and_close(self):
        for target, state in ((CaseStatus.PASSED, CaseStatus.PENDING_REVIEW),
                              (CaseStatus.CLOSED, CaseStatus.PASSED)):
            for missing in ("checks", "reviews"):
                with self.subTest(target=target, missing=missing):
                    case, documents, tasks, checks = self._inputs(state)
                    self._blocked(case, target, documents,
                                  [] if missing == "reviews" else tasks,
                                  [] if missing == "checks" else checks)

    def test_new_unreviewed_version_blocks_previously_supplied_pass(self):
        case, documents, tasks, checks = self._inputs(CaseStatus.PASSED)
        document = documents[0]
        new = deepcopy(document.versions[0])
        new.version_id = "version-fixture-new"
        new.version_number = 2
        new.fields[0].status = FieldValueStatus.CANDIDATE
        document.versions.append(new)
        document.current_version_id = new.version_id
        self._blocked(case, CaseStatus.CLOSED, documents, tasks, checks)


if __name__ == "__main__":
    unittest.main()

