"""Verify the exact checked input/result travels to model and immutable exports."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from local_lab.check_report import build_check_report
from local_lab.deepseek_client import Draft, GenerationError
from local_lab.revisions import append_version, export_history, export_text


class CheckReportChecks(unittest.TestCase):
    def test_report_statuses_and_no_claim_of_legal_review(self):
        for terms, status, total in [("50%预付款，50%发货前支付", "consistent", "100"),
                                     ("60%预付款，50%发货前支付", "invalid", "110"),
                                     ("全额预付", "manual", None), ("", "missing", None)]:
            report = build_check_report(terms)
            self.assertEqual((report["status"], report["total_percent"]), (status, total))
            self.assertEqual(report["input_payment_terms"], terms)
            self.assertIn("不是平台规定或法律审查", report["limitation"])
            self.assertEqual(report["checked_by"], "local_python_decimal")

    def test_snapshot_does_not_alias_report(self):
        report = build_check_report("50%预付款，50%发货前支付")
        history = append_version([], {}, {}, report)
        report["status"] = "changed"
        self.assertEqual(history[0]["check_report"]["status"], "consistent")

    def test_model_snapshot_exports_and_old_version_are_consistent(self):
        path = Path(__file__).parents[1] / "docs/verification/step7-payment-verified.json"
        old = json.loads(path.read_text(encoding="utf-8-sig"))["versions"]
        page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30)
        page.session_state["quote_history"] = deepcopy(old)
        page.session_state["quote_quantity"] = 500
        page.session_state["payment_terms"] = "50%预付款，50%发货前支付，待双方确认"
        draft = Draft(english_reply="Simulated test output", chinese_checklist="模拟测试输出")
        with patch("local_lab.revision_panel.generate_draft", return_value=draft) as generate:
            page.run()
            self.assertTrue(any("此旧版本未保存" in item.value for item in page.info))
            page.button(key="generate_ai").click().run()
            record = deepcopy(page.session_state.quote_history[-1])
            self.assertEqual(record["check_report"], generate.call_args.args[1]["check_report"])
            self.assertEqual(record["check_report"]["total_percent"], "100")
            self.assertEqual(record["context"]["goods_total"], "600.00")
            self.assertEqual(page.session_state.quote_history[:2], old)
            self.assertIn("C02", export_text(record))
            self.assertIn("项目模拟练习设定", export_text(record))
            self.assertEqual(json.loads(export_history([record]))["versions"][0]["check_report"], record["check_report"])
            page.text_input(key="payment_terms").set_value("全额预付").run()
            self.assertEqual(page.session_state.quote_history[-1], record)
            generate.side_effect = GenerationError("模拟网络失败")
            page.button(key="generate_ai").click().run()
            self.assertEqual(len(page.session_state.quote_history), 3)
            self.assertEqual(page.session_state.quote_history[-1], record)
            self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
