"""Arithmetic and UI checks; no real model calls."""
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch
from importlib import reload

from streamlit.testing.v1 import AppTest
from local_lab.payment_ratio import check_payment_ratio
from local_lab.ui_loader import load_revision_panel


class PaymentRatioChecks(unittest.TestCase):
    def test_stale_ui_is_reloaded_once_without_touching_session(self):
        module = load_revision_panel()
        with patch.object(module, "SOURCE_SHA256", "old-code"), \
             patch("local_lab.ui_loader.reload", wraps=reload) as refresh:
            self.assertIs(load_revision_panel(), module)
            self.assertIs(load_revision_panel(), module)
            refresh.assert_called_once_with(module)

    def test_supported_terms_and_decimal_precision(self):
        for text in ["模拟拟议条款，50% 预付款，50% 发货前支付，待双方确认",
                     "模拟约定：30% 预付款，70% 发货前支付",
                     "３３．３３％ 预付款，６６．６７％ 发货前支付。"]:
            result = check_payment_ratio(text)
            self.assertEqual(result.status, "consistent")
            self.assertEqual(result.total, Decimal("100"))
            self.assertIn("仍需双方确认", result.message)

    def test_over_under_and_invalid_individual_ratio(self):
        for first, second, total in [(60, 50, "110"), (40, 50, "90"),
                                     (0, 100, "100"), (-10, 110, "100")]:
            result = check_payment_ratio(f"{first}% 预付款，{second}% 发货前支付")
            self.assertEqual(result.status, "invalid")
            self.assertEqual(result.total, Decimal(total))

    def test_unsupported_is_not_approved(self):
        for text in ["100% 棉布", "20%折扣", "50%预付款，剩余发货前支付", "全额预付",
                     "不是50%预付款，50%发货前支付", "50%预付款，50%发货前支付或60%预付",
                     "50%预付款，50%发货前支付，优惠10%", "30% advance, 70% before shipment"]:
            result = check_payment_ratio(text)
            self.assertEqual(result.status, "manual")
            self.assertIsNone(result.total)
        self.assertEqual(check_payment_ratio("  ").status, "missing")

    def test_ui_invalid_then_correct_and_no_automatic_model_call(self):
        with patch("local_lab.revision_panel.generate_draft") as generate, \
             patch("local_lab.dialogue_panel.interpret_change") as interpret:
            page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            page.number_input(key="quote_quantity").set_value(500).run()
            page.text_input(key="payment_terms").set_value("模拟拟议条款，60% 预付款，50% 发货前支付，待双方确认").run()
            self.assertTrue(any("110%" in entry.value for entry in page.error))
            self.assertTrue(page.button(key="generate_ai").disabled)
            self.assertEqual([m.value for m in page.metric if m.label == "货品金额（USD）"], ["600.00"])
            page.text_input(key="payment_terms").set_value("模拟拟议条款，50% 预付款，50% 发货前支付，待双方确认").run()
            self.assertTrue(any("合计为 100%" in entry.value for entry in page.success))
            self.assertFalse(page.button(key="generate_ai").disabled)
            self.assertFalse(page.error)
            page.text_input(key="payment_terms").set_value("全额预付").run()
            self.assertFalse(page.success)
            self.assertTrue(any("不等于校验通过" in entry.value for entry in page.info))
            generate.assert_not_called()
            interpret.assert_not_called()
            self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
