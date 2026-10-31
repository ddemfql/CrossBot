"""Backward-compatible, typed provenance for form-prefill module boundaries."""
import unittest

from core.models import DocumentForm, DocumentType, FormFieldCandidate, FormRowCandidate, to_dict
from core.serialization import from_dict


class PrefillContractTests(unittest.TestCase):
    def test_old_snapshot_loads_without_prefill_metadata(self):
        form = from_dict(DocumentForm, {"document_type": "commercial_invoice",
            "values": {"buyer": "Synthetic buyer"}, "rows": [], "revision": 2})
        self.assertEqual(form.values["buyer"], "Synthetic buyer")
        self.assertEqual(form.field_candidates, {})
        self.assertFalse(form.rows_manually_edited)

    def test_provenance_round_trip_preserves_types_and_manual_clears(self):
        source = FormFieldCandidate("Synthetic buyer", "doc-synthetic", "v1",
            "customer_name", sheet_name="Order", cell_reference="B2")
        row = FormRowCandidate({"description": "Synthetic item", "quantity": "0"},
            "doc-synthetic", "v1", 0, 4, "Order", cell_references={"quantity": "C4"})
        form = DocumentForm(DocumentType.COMMERCIAL_INVOICE,
            values={"buyer": ""}, field_candidates={"buyer": [source]},
            row_candidates=[row], auto_values={"buyer": "Synthetic buyer"},
            manual_fields=["buyer"], rows_manually_edited=True)
        restored = from_dict(DocumentForm, to_dict(form))
        self.assertIsInstance(restored.field_candidates["buyer"][0], FormFieldCandidate)
        self.assertIsInstance(restored.row_candidates[0], FormRowCandidate)
        self.assertEqual(to_dict(restored), to_dict(form))
        self.assertEqual(restored.values["buyer"], "")


if __name__ == "__main__":
    unittest.main()
