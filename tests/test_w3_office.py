"""Synthetic fixtures for bounded Office label/value candidate extraction."""
from io import BytesIO
import unittest

from docx import Document as WordDocument
from openpyxl import Workbook

from core.errors import CrossBotError, ParseError
from core.models import FieldValueStatus
from documents.office_parser import parse_docx, parse_xlsx


def xlsx_bytes(sheets):
    workbook = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = workbook.active if first else workbook.create_sheet()
        first = False
        sheet.title = name
        for row in rows:
            sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def docx_bytes(paragraphs=(), tables=()):
    document = WordDocument()
    for text in paragraphs:
        document.add_paragraph(text)
    for rows in tables:
        table = document.add_table(rows=0, cols=max(map(len, rows)))
        for row in rows:
            cells = table.add_row().cells
            for cell, value in zip(cells, row):
                cell.text = value
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class OfficeParserTests(unittest.TestCase):
    def test_xlsx_finds_allowlisted_adjacent_fields_and_preserves_source(self):
        content = xlsx_bytes({"Order": [["Reference", "Value"], ["Purchase Order No", "SYN-100"],
            ["Buyer Name", "Synthetic Buyer"], ["Incoterms", "FCA Shanghai"]],
            "Items": [["Product Name", "Synthetic lamp"], ["Qty", 4], ["Unit Price", 12.5]]})
        fields = parse_xlsx(content)
        self.assertEqual({f.name for f in fields}, {"order_no", "customer_name", "incoterm",
            "product_name", "quantity", "unit_price"})
        quantity = next(f for f in fields if f.name == "quantity")
        self.assertEqual(quantity.value, "4")
        self.assertEqual((quantity.sheet_name, quantity.cell_reference), ("Items", "B2"))
        self.assertIs(quantity.status, FieldValueStatus.CANDIDATE)
        self.assertIsNone(quantity.confirmed_by)

    def test_xlsx_missing_values_and_formula_values_do_not_become_candidates(self):
        content = xlsx_bytes({"Data": [["Order No", ""], ["Quantity", "=1+1"]]})
        fields = parse_xlsx(content)
        self.assertEqual(len(fields), 2)
        self.assertTrue(all(f.status is FieldValueStatus.MISSING and f.value is None for f in fields))

    def test_xlsx_headers_are_not_mistaken_for_values(self):
        fields = parse_xlsx(xlsx_bytes({"Table": [["Product", "Quantity", "Unit Price"]]}))
        self.assertTrue(fields)
        self.assertTrue(all(f.status is FieldValueStatus.MISSING and f.value is None for f in fields))

    def test_duplicate_business_labels_fail_closed(self):
        with self.assertRaises(ParseError):
            parse_xlsx(xlsx_bytes({"Order": [["Order No", "SYN-1"], ["PO Number", "SYN-2"]]}))

    def test_docx_paragraph_and_table_locations_remain_candidates(self):
        content = docx_bytes(paragraphs=["Order Number: SYN-100"],
            tables=[[ ["Product Name", "Synthetic gear"], ["Quantity", "3"] ]])
        fields = parse_docx(content)
        self.assertEqual({f.name for f in fields}, {"order_no", "product_name", "quantity"})
        order = next(f for f in fields if f.name == "order_no")
        quantity = next(f for f in fields if f.name == "quantity")
        self.assertEqual((order.sheet_name, order.cell_reference), ("document", "paragraph:1"))
        self.assertEqual(quantity.cell_reference, "table:1,row:2,col:2")
        self.assertTrue(all(f.status is FieldValueStatus.CANDIDATE for f in fields))
        self.assertTrue(all(f.confirmed_by is None for f in fields))

    def test_docx_unlabeled_body_text_is_not_parsed(self):
        with self.assertRaises(ParseError):
            parse_docx(docx_bytes(paragraphs=["Synthetic declaration content without fields."]))

    def test_invalid_and_oversize_files_fail_closed(self):
        for parser in (parse_xlsx, parse_docx):
            with self.assertRaises(ParseError):
                parser(b"not an office document")
            with self.assertRaises(CrossBotError):
                parser(b"x" * (20 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
