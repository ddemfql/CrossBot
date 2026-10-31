"""Document-scoped review receipts remain distinct from whole-case approval."""
import unittest

from core.models import DocumentType as D
from documents.forms import FormService, get_form
from review.policy import current_artifacts
from tests import test_w3_strict_review_integration as strict_harness


class StrictArtifactPolicyTests(unittest.TestCase):
    setUp = strict_harness.StrictReviewIntegrationTests.setUp
    tearDown = strict_harness.StrictReviewIntegrationTests.tearDown
    save = strict_harness.StrictReviewIntegrationTests.save
    service = strict_harness.StrictReviewIntegrationTests.service

    def test_required_case_advisories_do_not_invalidate_reviewed_standalone_output(self):
        self.save()
        workspace = self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE,
                                             human_confirmed=True)
        self.assertEqual(len(current_artifacts(workspace)), 1)
        self.assertFalse(get_form(workspace, D.PROFORMA_INVOICE).confirmed_by)
        self.assertFalse(workspace.approvals)
        # The exact strict export receipt, rather than a forged form flag, proves
        # the human reviewed this saved document snapshot.
        form = get_form(workspace, D.PROFORMA_INVOICE)
        changed = FormService(self.repo).save(self.case_id, self.actor, D.PROFORMA_INVOICE,
            dict(form.values, notes="Synthetic changed content"), form.rows, "Synthetic content update")
        self.assertEqual(current_artifacts(changed), [])

    def test_no_receipt_or_changed_output_hash_cannot_claim_current_strict_review(self):
        self.save()
        workspace = self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE,
                                             human_confirmed=True)
        artifact = workspace.artifacts[-1]
        original = artifact.sha256
        artifact.sha256 = "0" * 64
        self.assertEqual(current_artifacts(workspace), [])
        artifact.sha256 = original
        workspace.audit = [event for event in workspace.audit if event.action != "simple_export_confirmed"]
        self.assertEqual(current_artifacts(workspace), [])


if __name__ == "__main__":
    unittest.main()

