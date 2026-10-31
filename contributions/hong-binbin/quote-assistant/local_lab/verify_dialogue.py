"""Offline bounded dialogue tests; never call the real model."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest
from local_lab.catalog import load_catalog
from local_lab.deepseek_client import Draft, GenerationError, build_context
from local_lab.dialogue import ChangeProposal, proposed_context, interpret_change
from local_lab.revisions import export_history


def proposal(**values):
    return ChangeProposal(clarification="", unsupported=[], **values)


class DialogueChecks(unittest.TestCase):
    def test_explicit_keep_quantity_conflict_stops_before_network(self):
        with patch("local_lab.dialogue.request.build_opener") as opener:
            with self.assertRaisesRegex(GenerationError, "当前填写需求为300"):
                interpret_change("test-only", "付款改30%/70%。数量保持1000个。", {"quantity": 300})
            opener.assert_not_called()

    def test_bounds_and_preservation(self):
        product = load_catalog().products[0]
        updated = proposed_context(product, 1000, "模拟付款", proposal(quantity=500))
        self.assertEqual((updated["unit_price"], updated["goods_total"]), ("1.20", "600.00"))
        self.assertEqual(updated["payment_terms"], "模拟付款")
        for value in [True, "500", 0, 1_000_001]:
            with self.assertRaises(ValidationError):
                proposal(quantity=value)
        for item in [proposal(quantity=1), proposal(),
                     ChangeProposal(quantity=500, clarification="请确认数量", unsupported=[]),
                     ChangeProposal(quantity=500, clarification="", unsupported=["改单价"])]:
            with self.assertRaises(ValueError):
                proposed_context(product, 1000, "模拟付款", item)

    def test_transport_and_malformed_output(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": proposal(quantity=500).model_dump_json()}}]}).encode()
        with patch("local_lab.dialogue.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            result = interpret_change("test-only", "改为500个", {})
            self.assertEqual(result.quantity, 500)
            sent = opener.return_value.open.call_args.args[0]
            self.assertEqual(sent.full_url, "https://api.deepseek.com/chat/completions")
            self.assertFalse(json.loads(sent.data)["stream"])
            response.read.return_value = b"invalid json"
            with self.assertRaises(GenerationError):
                interpret_change("test-only", "改为500个", {})
            calls = opener.call_count
            with self.assertRaises(GenerationError):
                interpret_change("", "改为500个", {})
            self.assertEqual(opener.call_count, calls)

    def test_two_turns_application_history_and_stale_preview(self):
        draft = Draft(english_reply="Simulated", chinese_checklist="模拟")
        with patch("local_lab.dialogue_panel.interpret_change", return_value=proposal(quantity=500)) as understand, \
             patch("local_lab.revision_panel.generate_draft", return_value=draft) as generate:
            page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            page.text_input(key="deepseek_api_key").set_value("test-never-export").run()
            page.button(key="generate_ai").click().run()
            page.text_input(key="change_message").set_value("数量改成500个").run()
            page.button(key="interpret_change").click().run()
            self.assertEqual(page.number_input(key="quote_quantity").value, 300)
            page.button(key="apply_change").click().run()
            self.assertEqual(page.number_input(key="quote_quantity").value, 500)
            page.button(key="generate_ai").click().run()
            self.assertEqual(page.session_state.quote_history[-1]["context"]["goods_total"], "600.00")
            understand.return_value = proposal(payment_terms="模拟：全额预付，待双方确认")
            page.text_input(key="change_message").set_value("付款改为全额预付，待双方确认").run()
            page.button(key="interpret_change").click().run()
            page.button(key="apply_change").click().run()
            page.button(key="generate_ai").click().run()
            history = page.session_state.quote_history
            self.assertEqual(len(history), 3)
            self.assertEqual(history[1]["context"]["dialogue"]["user_message"], "数量改成500个")
            self.assertEqual(history[2]["context"]["quantity"], 500)
            self.assertEqual(history[2]["context"]["payment_status"], "provided")
            self.assertNotIn("test-never-export", export_history(history))
            self.assertEqual((understand.call_count, generate.call_count), (2, 3))
            understand.return_value = proposal(quantity=800)
            page.text_input(key="change_message").set_value("改成800个").run()
            page.button(key="interpret_change").click().run()
            page.text_input(key="change_message").set_value("改成900个").run()
            self.assertNotIn("pending_change", page.session_state)
            self.assertNotIn("apply_change", [button.key for button in page.button])
            understand.side_effect = GenerationError("模拟网络失败")
            page.button(key="interpret_change").click().run()
            self.assertEqual(page.session_state.quote_history, history)
            self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
