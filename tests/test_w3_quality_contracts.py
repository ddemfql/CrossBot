"""Merged shared contracts remain compatible with saved W3 workspaces."""
from decimal import Decimal
import unittest

from core.models import (Case, CrossCheckResult, CrossCheckStatus, DocumentType,
                         ExportReviewReport, Workspace, from_dict, to_dict)
from core.serialization import from_dict as compatibility_decoder


class QualityContractTests(unittest.TestCase):
    def test_shared_decoder_is_canonical_and_old_float_amount_loads_as_decimal(self):
        self.assertIs(compatibility_decoder, from_dict)
        workspace = from_dict(Workspace, {"case": {"invoice_total": 12.5}})
        self.assertEqual(workspace.case.invoice_total, Decimal("12.5"))
        self.assertEqual(to_dict(workspace)["case"]["invoice_total"], "12.5")

    def test_normalization_policy_and_version_evidence_round_trip(self):
        check = CrossCheckResult(rule_code="SYN-WEIGHT", status=CrossCheckStatus.PASS,
            left_version_id="v1", right_version_id="v2", normalized_left="1",
            normalized_right="1", left_unit="g", right_unit="kg",
            comparison_policy={"tolerance": "0", "minimum": "0", "allow_fractional": True},
            scope="document", item_key="SYN-1")
        restored = from_dict(CrossCheckResult, to_dict(check))
        self.assertEqual(to_dict(restored), to_dict(check))

    def test_ready_needs_human_and_every_required_rule_not_case_advisories(self):
        report = ExportReviewReport("synthetic", DocumentType.PROFORMA_INVOICE,
            "digest", "quality-v1", checks=[CrossCheckResult(status=CrossCheckStatus.PASS),
                CrossCheckResult(status=CrossCheckStatus.PENDING_REVIEW, required=False)])
        self.assertFalse(report.ready)
        report.human_confirmed = True
        self.assertTrue(report.ready)
        report.checks.append(CrossCheckResult(status=CrossCheckStatus.PENDING_REVIEW))
        self.assertFalse(report.ready)
        self.assertNotEqual(Case().status.value, "passed")


if __name__ == "__main__":
    unittest.main()

