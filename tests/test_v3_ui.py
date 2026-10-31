from pathlib import Path
import json
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

APP = Path(__file__).parent / "fixtures" / "b_panels_app.py"


class V3UI(unittest.TestCase):
    def test_demo_load_only_changes_form_preserves_history_and_does_not_call_api(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        page.button(key="sales_generate").click().run()
        old = page.session_state.sales_conversation
        with patch("providers.llm_client.HttpChatClient.complete") as api:
            page.button(key="sales_demo").click().run()
            api.assert_not_called()
        self.assertFalse(page.exception)
        self.assertEqual(page.number_input(key="sales_quantity").value, 500)
        self.assertEqual(page.text_input(key="sales_country").value, "Germany")
        self.assertEqual(page.session_state.sales_conversation, old)
        self.assertTrue(page.button(key="sales_revise").disabled)

    def test_mock_buyer_requires_explicit_apply_and_leaves_sales_history_alone(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        original = page.text_area(key="sales_inquiry").value
        page.button(key="customer_generate").click().run()
        self.assertEqual(page.text_area(key="sales_inquiry").value, original)
        simulated = page.session_state.customer_simulation
        page.button(key="customer_apply").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.text_area(key="sales_inquiry").value, simulated.content)
        self.assertNotIn("sales_conversation", page.session_state)

    def test_candidate_identification_does_not_fill_quantity_or_create_history(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        page.button(key="sales_extract").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.number_input(key="sales_quantity").value, 0)
        self.assertNotIn("sales_conversation", page.session_state)
        self.assertTrue(page.session_state.sales_candidates[1])
        old = page.session_state.sales_candidates
        page.text_area(key="sales_inquiry").set_value("different inquiry").run()
        self.assertEqual(page.session_state.sales_candidates, old)
        self.assertTrue(any("旧识别候选不适用" in caption.value for caption in page.caption))

    def test_demo_w2_multi_platform_and_unsupported_claim_visible(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        page.radio[0].set_value("W2 多平台Listing生成").run()
        page.button(key="listing_demo").click().run()
        self.assertEqual(page.text_input(key="listing_country").value, "Germany")
        self.assertEqual(page.multiselect(key="listing_platforms").value, ["Amazon", "TikTok"])
        with patch("providers.listing_mock.MockListingClient.complete", return_value=json.dumps({"listings": {
                "Amazon": "Compact design, 5 W", "TikTok": "Compact design, 5 W"}})):
            page.button(key="listing_generate").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(set(page.session_state.listing_results), {"Amazon", "TikTok"})
        self.assertTrue(any("设计或性能" in warning.value for warning in page.warning))
