import json
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from core.errors import ValidationError
from core.models import CrossCheckStatus, FieldValueStatus
from validation.rules import (
    compare_currency,
    compare_money,
    compare_quantity,
    compare_text,
    compare_weight,
)


class ValueRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "fixtures" / "cross_check_cases.json"
        cls.fixtures = json.loads(path.read_text(encoding="utf-8"))
        cls.functions = {
            "quantity": compare_quantity, "weight": compare_weight,
            "money": compare_money, "currency": compare_currency, "text": compare_text,
        }

    def _arguments(self, kind, overrides=None):
        arguments = dict(self.fixtures["profiles"][kind])
        arguments.update(overrides or {})
        for key in ("left_status", "right_status"):
            arguments[key] = FieldValueStatus(arguments[key])
        for key in ("minimum", "tolerance"):
            if key in arguments and arguments[key] is not None:
                arguments[key] = Decimal(arguments[key])
        return arguments

    def test_hand_authored_cases_offline(self):
        self.assertTrue(self.fixtures["synthetic"])
        ids = [case["id"] for case in self.fixtures["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        with patch("socket.socket", side_effect=AssertionError("Network must not be used")):
            for case in self.fixtures["cases"]:
                with self.subTest(case=case["id"]):
                    arguments = self._arguments(case["kind"], case.get("overrides"))
                    before = dict(arguments)
                    function = self.functions[case["kind"]]
                    expected = CrossCheckStatus(case["expected"])
                    self.assertTrue(case["reason"])
                    self.assertIs(function(case["left"], case["right"], **arguments), expected)
                    self.assertIs(function(case["left"], case["right"], **arguments), expected)
                    self.assertEqual(arguments, before)

    def test_review_gate_applies_to_both_sides_and_every_comparison(self):
        for kind, function in self.functions.items():
            value = "USD" if kind == "currency" else "1"
            for side in ("left_status", "right_status"):
                for status in (FieldValueStatus.CANDIDATE, FieldValueStatus.REJECTED, FieldValueStatus.MISSING):
                    with self.subTest(kind=kind, side=side, status=status):
                        arguments = self._arguments(kind)
                        arguments[side] = status
                        self.assertIs(function(value, value, **arguments), CrossCheckStatus.PENDING_REVIEW)

    def test_omitted_confirmation_and_policy_never_pass(self):
        for kind, function in self.functions.items():
            with self.subTest(kind=kind):
                self.assertIs(function("1", "1"), CrossCheckStatus.PENDING_REVIEW)

    def test_context_must_be_explicitly_confirmed(self):
        for kind, function in self.functions.items():
            for context in (False, None, "confirmed", 1):
                with self.subTest(kind=kind, context=context):
                    arguments = self._arguments(kind)
                    arguments["context_confirmed"] = context
                    self.assertIs(function("1", "1", **arguments), CrossCheckStatus.PENDING_REVIEW)

    def test_tolerance_boundary_uses_inclusive_exact_decimal_comparison(self):
        for kind in ("weight", "money"):
            for difference, expected in [("0.009", CrossCheckStatus.PASS),
                                         ("0.01", CrossCheckStatus.PASS),
                                         ("0.011", CrossCheckStatus.FAIL)]:
                for left, right in [("0", difference), (difference, "0")]:
                    with self.subTest(kind=kind, left=left, right=right):
                        arguments = self._arguments(kind, {"tolerance": "0.01"})
                        self.assertIs(self.functions[kind](left, right, **arguments), expected)

    def test_comparison_preserves_digits_beyond_current_decimal_precision(self):
        with localcontext() as context:
            context.prec = 2
            left = "123456789012345678901234567890.001"
            right = "123456789012345678901234567890.002"
            for kind in ("weight", "money"):
                with self.subTest(kind=kind):
                    self.assertIs(self.functions[kind](left, right, **self._arguments(kind)), CrossCheckStatus.FAIL)
                    arguments = self._arguments(kind, {"tolerance": "0.001"})
                    self.assertIs(self.functions[kind](left, right, **arguments), CrossCheckStatus.PASS)
            self.assertEqual(context.prec, 2)

    def test_invalid_tolerance_raises_shared_configuration_validation_error(self):
        for kind in ("weight", "money"):
            for tolerance in (Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity"), 0.01, True):
                with self.subTest(kind=kind, tolerance=tolerance):
                    arguments = self._arguments(kind)
                    arguments["tolerance"] = tolerance
                    with self.assertRaises(ValidationError):
                        self.functions[kind]("1", "1", **arguments)

    def test_unknown_legality_policy_never_passes(self):
        for kind in ("quantity", "weight", "money"):
            with self.subTest(kind=kind):
                arguments = self._arguments(kind, {"minimum": None})
                self.assertIs(self.functions[kind]("0", "0", **arguments), CrossCheckStatus.PENDING_REVIEW)
        arguments = self._arguments("quantity", {"allow_fractional": None})
        self.assertIs(compare_quantity(1, 1, **arguments), CrossCheckStatus.PENDING_REVIEW)

    def test_money_cannot_relax_existing_negative_amount_constraint(self):
        arguments = self._arguments("money", {"minimum": "-1"})
        with self.assertRaises(ValidationError):
            compare_money("-1", "-1", **arguments)


if __name__ == "__main__":
    unittest.main()

