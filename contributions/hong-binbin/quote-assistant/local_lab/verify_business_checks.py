"""Verify missing/pending data and stale-feedback removal in the learning UI."""
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from local_lab.business_checks import check_payment_terms


class BusinessChecks(unittest.TestCase):
    def test_blank_and_unconfirmed_terms_stay_flagged(self):
        for value in ["", "   ", "\n\t"]:
            self.assertEqual(check_payment_terms(value).status, "missing")
        for value in ["待确认", " 待双方确认 ", "TBD", "unknown"]:
            self.assertEqual(check_payment_terms(value).status, "pending")

    def test_provided_is_not_approval(self):
        result = check_payment_terms("模拟约定：30% 预付款，70% 发货前支付")
        self.assertEqual(result.status, "provided")
        self.assertIn("仍需人工核对", result.message)

    def test_fill_clear_and_pending_update_page(self):
        page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
        def payment_warnings():
            return [w.value for w in page.warning if "付款条件" in w.value]
        self.assertTrue(payment_warnings())
        page.text_input(key="payment_terms").set_value("模拟约定：30% 预付款，70% 发货前支付").run()
        self.assertEqual(payment_warnings(), [])
        self.assertTrue(any("已填写付款条件" in i.value for i in page.info))
        self.assertEqual([m.value for m in page.metric if m.label == "货品金额（USD）"], ["360.00"])
        page.text_input(key="payment_terms").set_value("  ").run()
        self.assertTrue(payment_warnings())
        self.assertFalse(any("已填写付款条件" in i.value for i in page.info))
        page.text_input(key="payment_terms").set_value("待确认").run()
        self.assertTrue(any("仍未明确" in w for w in payment_warnings()))
        self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
