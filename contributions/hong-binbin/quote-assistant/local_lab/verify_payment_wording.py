"""Meaningful false-positive, report provenance and UI checks; no real API calls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from local_lab.deepseek_client import Draft
from local_lab.payment_wording import check_payment_wording, RISK_EXAMPLE, PENDING_EXAMPLE
from local_lab.revisions import export_history, export_text


class PaymentWordingChecks(unittest.TestCase):
    def test_positive_claim_and_multiple_sentences(self):
        for text in [RISK_EXAMPLE, "Both parties have accepted the payment terms.",
                     "Payment terms are confirmed.", "We have agreed on a 50% deposit.",
                     "Shipment is not confirmed. Payment terms are agreed."]:
            report = check_payment_wording(text, "待双方确认")
            self.assertEqual(report["status"], "risk", text)
            self.assertIn("付款", report["findings"][-1]["reason"])
            self.assertEqual(report["input_english_reply"], text)
            self.assertIn("第18条", report["reference_note"])

    def test_negations_questions_conditions_and_unrelated_claims_are_not_approved(self):
        for text in [PENDING_EXAMPLE, "The payment terms have not been agreed.",
                     "Payment terms aren't confirmed.", "Payment terms haven’t been accepted.",
                     "Have both parties agreed to the payment terms?",
                     "Please confirm whether the payment terms are accepted.",
                     "If payment terms are agreed, we can proceed.",
                     "Shipment has been confirmed.", "", "双方已同意付款条件。",
                     "The deal is sealed."]:
            report = check_payment_wording(text, "50%预付款，50%发货前支付")
            self.assertEqual(report["status"], "manual", text)
            self.assertIn("人工审核", report["status_label"])
        # The negation in a separate sentence must not suppress a later affirmative claim.
        report = check_payment_wording("Payment is not agreed. Payment terms are accepted.", "待确认")
        self.assertEqual(report["status"], "risk")
        self.assertEqual([f["severity"] for f in report["findings"]], ["manual", "risk"])

    def test_filled_in_terms_never_become_evidence_of_agreement(self):
        for terms in ["", "待确认", "模拟拟议条款，50%预付款，50%发货前支付", "双方同意付款"]:
            report = check_payment_wording(RISK_EXAMPLE, terms)
            self.assertEqual(report["status"], "risk")
            self.assertEqual(report["agreement_evidence"], "not_recorded_by_application")

    def test_practice_without_history_is_offline_and_invalidates_on_edit(self):
        with patch("local_lab.revision_panel.generate_draft") as generate, \
             patch("local_lab.dialogue_panel.interpret_change") as interpret:
            page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            page.button(key="check_wording").click().run()
            self.assertEqual(page.session_state.wording_practice_report["status"], "risk")
            self.assertTrue(any("发现可能误写" in w.value for w in page.warning))
            page.text_area(key="wording_practice").set_value(PENDING_EXAMPLE).run()
            self.assertTrue(any("旧结果不代表" in w.value for w in page.info))
            page.button(key="check_wording").click().run()
            self.assertEqual(page.session_state.wording_practice_report["status"], "manual")
            self.assertFalse(any("发现可能误写" in w.value for w in page.warning))
            self.assertEqual(page.session_state.quote_history, [])
            self.assertFalse(page.exception)
            generate.assert_not_called()
            interpret.assert_not_called()

    def test_generated_report_snapshot_and_downloads_keep_old_records(self):
        old = json.loads((Path(__file__).parents[1] / "docs/verification/step7-payment-verified.json").read_text(encoding="utf-8-sig"))["versions"]
        page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30)
        page.session_state["quote_history"] = deepcopy(old)
        page.session_state["quote_quantity"] = 500
        page.session_state["payment_terms"] = "50%预付款，50%发货前支付，待双方确认"
        with patch("local_lab.revision_panel.generate_draft", return_value=Draft(english_reply=RISK_EXAMPLE, chinese_checklist="模拟测试")) as generate:
            page.run()
            page.button(key="generate_ai").click().run()
            generate.assert_called_once()
            self.assertNotIn("wording_report", generate.call_args.args[1])  # check happens after generation
            record = deepcopy(page.session_state.quote_history[-1])
            self.assertEqual(record["wording_report"]["input_english_reply"], record["draft"]["english_reply"])
            self.assertEqual(record["wording_report"]["status"], "risk")
            self.assertEqual(record["context"]["goods_total"], "600.00")
            self.assertEqual(page.session_state.quote_history[:2], old)
            txt = export_text(record)
            self.assertIn("C03", txt)
            self.assertIn("建议：", txt)
            self.assertIn(RISK_EXAMPLE, txt)
            self.assertEqual(json.loads(export_history([record]))["versions"][0]["wording_report"], record["wording_report"])
            page.button(key="wording_pending_example").click().run()
            page.button(key="check_wording").click().run()
            self.assertEqual(page.session_state.quote_history[-1], record)
            page.selectbox(key="revision_view").select(1).run()
            self.assertTrue(any("此旧版本未保存英文" in c.value for c in page.caption))
            self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
