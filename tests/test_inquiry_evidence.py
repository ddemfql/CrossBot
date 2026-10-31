"""Model quote evidence must be located by code rather than model character counting."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from streamlit.testing.v1 import AppTest

from core.config import AppConfig
from core.errors import CrossBotError
from core.models import FieldValueStatus
from core.schemas import SalesRequest
from workflows.inquiry_analysis import extract_inquiry_candidates


def candidate_response(text, items):
    client = Mock()
    client.provider = "real"
    client.model = "test-double"
    client.complete.return_value = json.dumps({"fields": items}, ensure_ascii=False)
    return extract_inquiry_candidates(SalesRequest(text, "lamp", "en"), client)


class InquiryEvidenceTests(unittest.TestCase):
    def test_unique_quote_without_model_offsets_is_located_in_unicode_original(self):
        text = "您好🙂\nPlease ship to Germany."
        fields = candidate_response(text, [{"name": "customer_country", "value": "Germany"}])
        start = text.index("Germany")
        self.assertIn(f"字符[{start}:{start + 7}]", fields[0].note)
        self.assertIn("代码精确定位原文", fields[0].note)
        self.assertIn("provider=real", fields[0].note)
        self.assertEqual(fields[0].status, FieldValueStatus.CANDIDATE)
        self.assertIsNone(fields[0].normalized_value)

    def test_wrong_positive_model_offsets_replaced_only_for_unique_literal_quote(self):
        text = "Need 500 USB desk lamps for Germany."
        for start, end in [(0, 7), (1000, 1007)]:
            fields = candidate_response(text, [{"name": "customer_country", "value": "Germany", "start": start, "end": end}])
            actual = text.index("Germany")
            self.assertIn(f"字符[{actual}:{actual + 7}]", fields[0].note)

    def test_duplicate_quote_without_exact_valid_span_is_rejected(self):
        text = "Germany or Germany?"
        for extra in [{}, {"start": 1, "end": 8}]:
            with self.subTest(extra=extra), self.assertRaises(CrossBotError) as caught:
                candidate_response(text, [{"name": "customer_country", "value": "Germany", **extra}])
            self.assertIn("出现多次", caught.exception.message)

    def test_exact_legacy_span_can_distinguish_repeated_quote(self):
        fields = candidate_response("Germany or Germany?", [
            {"name": "customer_country", "value": "Germany", "start": 11, "end": 18}])
        self.assertIn("字符[11:18]", fields[0].note)

    def test_fabricated_translated_or_normalized_quotes_are_not_repaired(self):
        for value in ["France", "德国", "germany", "500 units"]:
            with self.subTest(value=value), self.assertRaises(CrossBotError) as caught:
                candidate_response("Germany 500 pcs", [{"name": "customer_country", "value": value}])
            self.assertIn("未在询盘原文中逐字找到", caught.exception.message)

    def test_partial_batch_is_not_returned_and_error_does_not_echo_model_value(self):
        with self.assertRaises(CrossBotError) as caught:
            candidate_response("Germany", [{"name": "customer_country", "value": "Germany"},
                                           {"name": "document_requirement", "value": "private-model-value"}])
        self.assertNotIn("private-model-value", caught.exception.message)

    def test_prompt_requests_quotes_not_character_arithmetic(self):
        client = Mock(); client.complete.return_value = '{"fields":[]}'
        extract_inquiry_candidates(SalesRequest("Hello", "lamp", "en"), client)
        prompt = client.complete.call_args.args[0][0]["content"]
        self.assertNotIn('"start":', prompt)
        self.assertIn("不要输出字符索引", prompt)

    def test_real_mode_ui_accepts_quote_only_and_preserves_previous_candidates_on_failure(self):
        app = Path(__file__).parents[1] / "app.py"
        client = Mock(provider="real", model="test-double")
        client.complete.return_value = '{"fields":[{"name":"customer_country","value":"Germany"}]}'
        with patch("providers.settings_ui.render_provider_settings", return_value=(AppConfig(mock_mode=False), True)), \
             patch("workflows.sales_ui.HttpChatClient", return_value=client):
            page = AppTest.from_file(str(app), default_timeout=15).run()
            page.button(key="sales_demo").click().run()
            self.assertEqual(client.complete.call_count, 0)
            page.button(key="sales_extract").click().run()
            self.assertFalse(page.exception)
            self.assertFalse(page.error)
            before = page.session_state.sales_candidates
            self.assertIn("provider=real", before[1][0].note)
            self.assertEqual(page.number_input(key="sales_quantity").value, 500)
            client.complete.return_value = '{"fields":[{"name":"customer_country","value":"France"}]}'
            page.button(key="sales_extract").click().run()
            self.assertFalse(page.exception)
            self.assertTrue(any("未在询盘原文中逐字找到" in e.value for e in page.error))
            self.assertEqual(page.session_state.sales_candidates, before)
            self.assertEqual(client.complete.call_count, 2)
            self.assertNotIn("sales_conversation", page.session_state)
