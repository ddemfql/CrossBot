"""User-authorized real transaction showcase regression test.

This test intentionally reads the real fixture created for the requested demo.
It is not a synthetic parser fixture and must not be generalized into defaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest


FIXTURE = Path(os.environ["CROSSBOT_REAL_SHOWCASE"]) if os.environ.get("CROSSBOT_REAL_SHOWCASE") else None


@unittest.skipUnless(FIXTURE and FIXTURE.is_file(), "Real private showcase is an explicit local opt-in")
class RealTransactionShowcaseTests(unittest.TestCase):
    def test_real_case_has_complete_document_inventory_and_explicit_review_gates(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        documents = payload["documents"]
        fields = payload["field_candidates"]
        self.assertEqual(payload["fixture_kind"], "user-authorized-real-transaction")
        self.assertEqual(payload["case"]["workflow_end"], "shipped_waiting_for_payment")
        self.assertEqual(len(documents), 39)
        self.assertGreaterEqual(len(fields), 1)
        self.assertTrue(all(document["original_preserved"] for document in documents))
        self.assertTrue(all(len(document["sha256"]) == 64 for document in documents))
        self.assertTrue(all(field["review"] == "pending human confirmation" for field in fields))
        types = {document["document_type"] for document in documents}
        self.assertIn("purchase_order", types)
        self.assertIn("proforma_invoice", types)
        self.assertIn("bill_of_lading", types)
        self.assertIn("isf_receipt", types)
        statuses = {check["status"] for check in payload["cross_checks"]}
        self.assertIn("PASS", statuses)
        self.assertIn("PENDING_REVIEW", statuses)
        self.assertIn("awaiting_payment", payload["case"]["receivable_status"])


if __name__ == "__main__":
    unittest.main()

