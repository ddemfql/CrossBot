import unittest
from datetime import date
from decimal import Decimal, localcontext

from core.errors import ErrorCode, ValidationError
from validation.normalizers import (
    convert_weight,
    normalize_currency,
    normalize_date,
    normalize_decimal,
    normalize_text,
)


class NormalizerTests(unittest.TestCase):
    def test_decimal_preserves_zero_sign_fraction_and_large_values(self):
        cases = [
            (0, Decimal("0")),
            (" +100.00 ", Decimal("100.00")),
            ("-10.5", Decimal("-10.5")),
            (Decimal("0.125"), Decimal("0.125")),
            ("123456789012345678901234567890.123",
             Decimal("123456789012345678901234567890.123")),
        ]
        with localcontext() as context:
            context.prec = 2
            for value, expected in cases:
                with self.subTest(value=value):
                    self.assertEqual(normalize_decimal(value), expected)

    def test_missing_empty_and_invalid_have_distinct_reasons(self):
        for value, reason in [(None, "missing"), ("  ", "empty"), ("abc", "invalid_format")]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as raised:
                    normalize_decimal(value)
                self.assertEqual(raised.exception.code, ErrorCode.INVALID_INPUT)
                self.assertEqual(raised.exception.details["reason"], reason)

    def test_decimal_rejects_ambiguous_text_and_placeholders(self):
        for value in ["1,000", "1,5", "1 000", "1e3", "1_000", "10 kg",
                      "NA", "N/A", "—", "１２", "NaN", "Infinity"]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_decimal(value)

    def test_decimal_rejects_float_bool_containers_and_nonfinite(self):
        for value in [True, False, 1.0, [], {}, Decimal("NaN"), Decimal("sNaN"),
                      Decimal("Infinity"), Decimal("-Infinity")]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_decimal(value)

    def test_weight_converts_both_directions_without_rounding(self):
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(convert_weight("1", "kg", "g"), Decimal("1000"))
            self.assertEqual(convert_weight("1000", "g"), Decimal("1"))
            self.assertEqual(convert_weight("0.001", "kg", "g"), Decimal("1"))
            self.assertEqual(
                convert_weight("123456789012345678901234567890.123", "kg", "g"),
                Decimal("123456789012345678901234567890123"),
            )
            self.assertEqual(convert_weight(0, "g"), Decimal(0))

    def test_weight_does_not_guess_units(self):
        for unit in [None, "", "lb", "KG", "公斤"]:
            with self.subTest(unit=unit):
                with self.assertRaises(ValidationError):
                    convert_weight(1, unit)
        with self.assertRaises(ValidationError):
            convert_weight(1, "kg", "lb")

    def test_text_preserves_internal_characters_and_leading_zeroes(self):
        self.assertEqual(normalize_text("  PO-001  "), "PO-001")
        self.assertEqual(normalize_text(" Acme  Ltd. "), "Acme  Ltd.")
        self.assertEqual(normalize_text("001"), "001")
        for value in [None, "", "  ", 0]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_text(value)

    def test_currency_accepts_code_syntax_without_guessing_symbols(self):
        self.assertEqual(normalize_currency(" usd "), "USD")
        for value in [None, "$", "US$", "US", "１２３", "123"]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_currency(value)

    def test_date_requires_valid_iso_calendar_date(self):
        self.assertEqual(normalize_date(" 2024-02-29 "), date(2024, 2, 29))
        for value in [None, "2026-02-29", "2026-13-01", "01/02/2026", "20260102", "2026-1-2"]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_date(value)


if __name__ == "__main__":
    unittest.main()

