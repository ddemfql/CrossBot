from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from core.errors import CrossBotError, ErrorCode

APP = Path(__file__).with_name("w1_w2_ui_harness.py")


class ProviderUI(unittest.TestCase):
    def real_settings(self, page):
        page.radio(key="provider_mode").set_value("真实").run()
        self.assertTrue(page.button(key="sales_generate").disabled)
        page.text_input(key="provider_key").set_value("dummy-test-token").run()
        page.checkbox(key="provider_confirm").check().run()
        return page

    def test_default_mock_does_not_access_network(self):
        with patch("providers.llm_client.request.build_opener") as build:
            page = AppTest.from_file(str(APP), default_timeout=15).run()
            self.assertEqual(page.radio(key="provider_mode").value, "模拟")
            page.button(key="sales_generate").click().run()
            self.assertEqual(page.session_state.sales_conversation.turns[0].response.provider, "mock")
            build.assert_not_called()

    def test_real_success_is_labeled_and_failed_revision_keeps_history(self):
        page = self.real_settings(AppTest.from_file(str(APP), default_timeout=15).run())
        with patch("providers.llm_client.HttpChatClient.complete", return_value="Dear customer, please confirm specifications.") as complete:
            page.button(key="sales_generate").click().run()
            response = page.session_state.sales_conversation.turns[-1].response
            self.assertEqual(response.provider, "real")
            self.assertEqual(response.model, "deepseek-flash")
            self.assertNotIn("dummy-test-token", str(response.to_dict()))
            page.run()
            complete.assert_called_once()
        page.text_area(key="sales_feedback").set_value("Please shorten").run()
        with patch("providers.llm_client.HttpChatClient.complete", side_effect=CrossBotError(ErrorCode.PROVIDER_TIMEOUT, "请求超时")):
            page.button(key="sales_revise").click().run()
        self.assertFalse(page.exception)
        self.assertTrue(page.error)
        self.assertEqual(len(page.session_state.sales_conversation.turns), 1)

    def test_key_clear_and_address_change_revoke_ready_state(self):
        page = self.real_settings(AppTest.from_file(str(APP), default_timeout=15).run())
        self.assertFalse(page.button(key="sales_generate").disabled)
        page.button(key="provider_clear").click().run()
        self.assertEqual(page.text_input(key="provider_key").value, "")
        self.assertTrue(page.button(key="sales_generate").disabled)
        page.text_input(key="provider_key").set_value("dummy-test-token").run()
        page.checkbox(key="provider_confirm").check().run()
        page.text_input(key="provider_url").set_value("https://example.test/v1").run()
        self.assertEqual(page.text_input(key="provider_key").value, "")
        self.assertFalse(page.checkbox(key="provider_confirm").value)

    def test_w2_success_and_wrong_platform_response_do_not_replace_good_result(self):
        page = self.real_settings(AppTest.from_file(str(APP), default_timeout=15).run())
        page.radio[0].set_value("W2 多平台Listing生成").run()
        page.multiselect(key="listing_platforms").set_value(["Amazon"]).run()
        with patch("providers.llm_client.HttpChatClient.complete", return_value='{"listings":{"Amazon":"A factual draft"}}'):
            page.button(key="listing_generate").click().run()
        self.assertEqual(page.session_state.listing_results["Amazon"].provider, "real")
        self.assertTrue(any("真实模型结果" in x.value for x in page.subheader))
        original = page.session_state.listing_results
        with patch("providers.llm_client.HttpChatClient.complete", return_value='{"listings":{"TikTok":"wrong"}}'):
            page.button(key="listing_generate").click().run()
        self.assertTrue(page.error)
        self.assertEqual(page.session_state.listing_results, original)


if __name__ == "__main__":
    unittest.main()
