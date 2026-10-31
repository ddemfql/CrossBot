import unittest

from core.errors import ParseError
from core.models import FieldValueStatus
from documents.field_mapping import from_ocr_lines, from_text_lines
from documents.ocr import RecognizedLine


class FieldMappingTests(unittest.TestCase):
    def test_labeled_text_values_remain_candidates_with_source_pages(self):
        fields = from_text_lines([
            ("Purchase Order No: SYN-001", 1),
            ("Product Description    Synthetic widget", 2),
            ("Qty: 25", 2),
        ])
        self.assertEqual({field.name for field in fields}, {"order_no", "product_name", "quantity"})
        self.assertEqual(next(f for f in fields if f.name == "order_no").page_number, 1)
        self.assertEqual(next(f for f in fields if f.name == "quantity").value, "25")
        self.assertTrue(all(field.status is FieldValueStatus.CANDIDATE for field in fields))
        self.assertTrue(all(field.confirmed_by is None for field in fields))

    def test_ocr_neighbor_matching_requires_label_geometry_and_keeps_confidence(self):
        fields = from_ocr_lines([
            RecognizedLine("Order Number", .99, (10, 10, 100, 30), 1),
            RecognizedLine("SYN-002", .92, (120, 11, 185, 30), 1),
            RecognizedLine("Quantity", .95, (10, 60, 75, 80), 1),
            RecognizedLine("4", .90, (120, 61, 130, 80), 1),
        ])
        self.assertEqual({field.name for field in fields}, {"order_no", "quantity"})
        quantity = next(field for field in fields if field.name == "quantity")
        self.assertEqual(quantity.value, "4")
        self.assertEqual(quantity.page_number, 1)
        self.assertAlmostEqual(quantity.confidence, .90)

    def test_conflicting_duplicate_labels_fail_closed(self):
        with self.assertRaises(ParseError):
            from_text_lines([("Order No: SYN-001", 1), ("PO Number: SYN-002", 2)])


if __name__ == "__main__":
    unittest.main()

