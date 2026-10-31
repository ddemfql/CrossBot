"""Synthetic filename hints preserve distinct originals and conservative types."""
import unittest

from core.models import DocumentType as D
from documents.intake import classify_source


class PurchaseOrderClassificationTests(unittest.TestCase):
    def test_explicit_po_aliases_and_unclassified_children(self):
        for path in ("PO123.xlsx", "PO_123.xlsx", "P.O.123.xlsx", "P.O._123.pdf",
                     "Purchase_Order_123.xlsx", "purchase-order-123.pdf",
                     "客户订单/001.xlsx", "PO/001.xlsx", "客户PO/001.xlsx",
                     r"买方订单\001.xlsx"):
            with self.subTest(path=path):
                self.assertEqual(classify_source(path)[:2], (D.PURCHASE_ORDER, "external"))

    def test_directory_hint_does_not_override_specific_leaf(self):
        cases = {
            "客户订单/Commercial Invoice.xlsx": D.COMMERCIAL_INVOICE,
            "PO/Packing List.xlsx": D.PACKING_LIST,
            "PO/inspection report.pdf": D.INSPECTION_REPORT,
            "PO/artwork.pdf": D.PACKAGING_ARTWORK,
            "工厂/PO/Commercial Invoice.xlsx": D.COMMERCIAL_INVOICE,
            "PO/出货照片/IMG-1.jpg": D.PHOTO,
            "工厂/P.O.123.xlsx": D.PRODUCTION_ORDER,
            "工厂/PO/001.xlsx": D.PRODUCTION_ORDER,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify_source(path)[0], expected)

    def test_embedded_letters_do_not_create_po_hint(self):
        for path in ("report.xlsx", "portfolio.pdf", "support/001.xlsx", "shipping.xlsx"):
            with self.subTest(path=path):
                self.assertIs(classify_source(path)[0], D.OTHER)


if __name__ == "__main__":
    unittest.main()
