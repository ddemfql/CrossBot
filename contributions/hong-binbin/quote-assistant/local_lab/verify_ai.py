"""Offline transport/interaction tests. These do not prove live API availability."""
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from streamlit.testing.v1 import AppTest

from local_lab.catalog import load_catalog
from local_lab.deepseek_client import Draft, GenerationError, build_context, generate_draft


class AIChecks(unittest.TestCase):
    def setUp(self):
        self.context = build_context(load_catalog().products[0], 300, "待确认")

    def test_local_numbers_and_missing_terms(self):
        self.assertEqual(self.context["goods_total"], "360.00")
        self.assertEqual(self.context["payment_status"], "pending")

    def test_no_request_without_key(self):
        with patch("local_lab.deepseek_client.request.build_opener") as opener:
            with self.assertRaises(GenerationError):
                generate_draft("", self.context)
            opener.assert_not_called()

    def test_request_and_structured_response(self):
        draft = {"english_reply": "Simulated draft", "chinese_checklist": "模拟资料待核对"}
        response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}]}
        with patch("local_lab.deepseek_client.request.build_opener") as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            self.assertEqual(generate_draft("test-key", self.context).model_dump(), draft)
            req = factory.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.deepseek.com/chat/completions")
            self.assertNotIn("test-key", req.data.decode())
            self.assertFalse(json.loads(req.data)["stream"])

    def test_errors_do_not_expose_response_or_key(self):
        for failure in [HTTPError("https://api.deepseek.com", 401, "test-secret", {}, None), TimeoutError("test-secret")]:
            with patch("local_lab.deepseek_client.request.build_opener") as factory:
                factory.return_value.open.side_effect = failure
                with self.assertRaises(GenerationError) as caught:
                    generate_draft("test-key", self.context)
                self.assertNotIn("test-secret", str(caught.exception))

    def test_rejects_partial_or_malformed_output(self):
        for raw in [b"{}", b"broken", json.dumps({"choices": [{"finish_reason": "length"}]}).encode(),
                    json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}).encode()]:
            with patch("local_lab.deepseek_client.request.build_opener") as factory:
                factory.return_value.open.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(GenerationError):
                    generate_draft("test-key", self.context)

    def test_page_only_calls_on_click_and_invalidates_draft(self):
        with patch("local_lab.revision_panel.generate_draft", return_value=Draft(english_reply="Simulated test", chinese_checklist="模拟测试")) as call:
            page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            call.assert_not_called()
            page.text_input(key="deepseek_api_key").set_value("test-key").run()
            call.assert_not_called()
            page.button(key="generate_ai").click().run()
            self.assertEqual(call.call_count, 1)
            self.assertIn("ai_draft", page.session_state)
            page.number_input(key="quote_quantity").set_value(1000).run()
            self.assertNotIn("ai_draft", page.session_state)
            self.assertEqual(call.call_count, 1)
            page.button(key="clear_api_key").click().run()
            self.assertEqual(page.session_state["deepseek_api_key"], "")
            page.number_input(key="quote_quantity").set_value(50).run()
            self.assertTrue(page.button(key="generate_ai").disabled)
            self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
