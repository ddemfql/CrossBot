"""Synthetic-only tests for the optional legacy BIFF reader."""
from io import BytesIO
import unittest

import xlwt

from core.errors import CrossBotError, ErrorCode, ParseError
from core.models import FieldValueStatus
from documents.xls_parser import MAX_XLS_BYTES, parse_xls


def workbook_bytes(rows, *, sheet="Synthetic"):
    book = xlwt.Workbook()
    page = book.add_sheet(sheet)
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            if value is not None:
                page.write(row, col, value)
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


class LegacyXlsParserTests(unittest.TestCase):
    def test_fixed_key_values_keep_sheet_cell_and_candidate_provenance(self):
        fields = parse_xls(workbook_bytes([
            ("order_no", "SYNTH-001"),
            ("quantity", 12),
            ("description", "Synthetic item"),
        ]))
        self.assertEqual([field.name for field in fields], ["order_no", "quantity", "product_name"])
        self.assertEqual(fields[1].value, "12")
        self.assertEqual(fields[1].normalized_value, "12")
        self.assertEqual(fields[1].sheet_name, "Synthetic")
        self.assertEqual(fields[1].cell_reference, "B2")
        self.assertIs(fields[1].status, FieldValueStatus.CANDIDATE)
        self.assertIsNone(fields[1].confirmed_by)

    def test_blank_candidate_stays_missing(self):
        field, = parse_xls(workbook_bytes([("order_no", None)]))
        self.assertIsNone(field.value)
        self.assertIs(field.status, FieldValueStatus.MISSING)
        self.assertIsNone(field.confidence)

    def test_unsupported_layout_and_duplicate_fields_fail_closed(self):
        with self.assertRaises(ParseError):
            parse_xls(workbook_bytes([("Order Number", "SYNTH-001")]))
        with self.assertRaises(ParseError):
            parse_xls(workbook_bytes([("quantity", 1), ("quantity", 2)]))

    def test_xlsx_or_arbitrary_bytes_are_not_treated_as_legacy_xls(self):
        with self.assertRaises(ParseError):
            parse_xls(b"PK\x03\x04not-an-xls")

    def test_size_limit_is_enforced_before_workbook_parsing(self):
        with self.assertRaises(CrossBotError) as error:
            parse_xls(b"\0" * (MAX_XLS_BYTES + 1))
        self.assertEqual(error.exception.code, ErrorCode.FILE_TOO_LARGE)


if __name__ == "__main__":
    unittest.main()
