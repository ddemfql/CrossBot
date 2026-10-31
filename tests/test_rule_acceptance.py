"""Synthetic value-level acceptance; no parser or formal rule registration."""
import json
import re
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from core.models import CrossCheckStatus, FieldValueStatus
from validation.rules import (
    compare_currency, compare_money, compare_quantity, compare_text, compare_weight,
)


class RuleAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = Path(__file__).parent / "fixtures"
        cls.profiles = json.loads((fixtures / "cross_check_cases.json").read_text(encoding="utf-8"))["profiles"]
        cls.acceptance = json.loads((fixtures / "rule_acceptance_cases.json").read_text(encoding="utf-8"))
        cls.functions = {
            "text": compare_text, "quantity": compare_quantity, "weight": compare_weight,
            "money": compare_money, "currency": compare_currency,
        }

    def test_every_documented_rule_has_five_hand_authored_scenarios(self):
        document = Path(__file__).resolve().parents[1] / "docs" / "cross_check_rules.md"
        documented = re.findall(r"^\| ((?:IP|CASE|IC|PB)-[0-9]{3}) \|", document.read_text(encoding="utf-8"), re.M)
        codes = [rule["rule_code"] for rule in self.acceptance["rules"]]
        self.assertTrue(self.acceptance["synthetic"])
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(set(codes), set(documented))
        self.assertEqual(len(codes), 16)
        expected = {"PASS": "pass", "FAIL": "fail", "MISSING": "pending_review",
                    "INVALID": "pending_review", "UNREVIEWED": "pending_review"}
        for rule in self.acceptance["rules"]:
            with self.subTest(rule=rule["rule_code"]):
                self.assertTrue(rule["scope"])
                self.assertEqual(len(rule["cases"]), 5)
                self.assertEqual({case["id"]: case["expected"] for case in rule["cases"]}, expected)

    def test_all_rule_scenarios_without_network(self):
        with patch("socket.socket", side_effect=AssertionError("Network must not be used")):
            for rule in self.acceptance["rules"]:
                for case in rule["cases"]:
                    with self.subTest(rule=rule["rule_code"], scenario=case["id"]):
                        arguments = dict(self.profiles[rule["kind"]])
                        arguments.update(rule.get("overrides", {}))
                        arguments.update(case.get("overrides", {}))
                        for name in ("left_status", "right_status"):
                            arguments[name] = FieldValueStatus(arguments[name])
                        for name in ("minimum", "tolerance"):
                            if arguments.get(name) is not None:
                                arguments[name] = Decimal(arguments[name])
                        self.assertTrue(case["reason"])
                        self.assertIs(
                            self.functions[rule["kind"]](case["left"], case["right"], **arguments),
                            CrossCheckStatus(case["expected"]),
                        )


if __name__ == "__main__":
    unittest.main()

