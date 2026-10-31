from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "app.py"


class ListingUI(unittest.TestCase):
    def page(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        page.radio[0].set_value("W2 多平台Listing生成").run()
        return page

    def test_single_and_multiple_selections_replace_all_sections(self):
        page = self.page()
        page.text_area(key="listing_description").set_value('咖啡杯, 350ml\n颜色"白"').run()
        page.selectbox(key="listing_language").select("德文").run()
        for platforms in [["Amazon"], ["TikTok"], ["阿里国际站"], ["Amazon", "TikTok"]]:
            page.multiselect(key="listing_platforms").set_value(platforms).run()
            page.button(key="listing_generate").click().run()
            self.assertFalse(page.exception)
            self.assertEqual(list(page.session_state.listing_results), platforms)
            self.assertEqual([x.value for x in page.subheader if "模拟结果" in x.value], [x + " · 模拟结果" for x in platforms])
        self.assertEqual(page.session_state.listing_request.product_description, '咖啡杯, 350ml\n颜色"白"')
        self.assertEqual(page.session_state.listing_request.target_language, "德文")

    def test_empty_selection_disables_generation_and_hides_old_output(self):
        page = self.page()
        page.button(key="listing_generate").click().run()
        with patch("workflows.listing_ui.MockListingClient.complete") as complete:
            page.multiselect(key="listing_platforms").set_value([]).run()
            self.assertTrue(page.button(key="listing_generate").disabled)
            self.assertFalse(any("模拟结果" in x.value for x in page.subheader))
            complete.assert_not_called()

    def test_provider_mismatch_preserves_old_batch_without_showing_wrong_sections(self):
        page = self.page()
        page.multiselect(key="listing_platforms").set_value(["Amazon"]).run()
        page.button(key="listing_generate").click().run()
        original = page.session_state.listing_results
        page.multiselect(key="listing_platforms").set_value(["TikTok"]).run()
        with patch("workflows.listing_ui.MockListingClient.complete", return_value='{"listings":{"Amazon":"wrong"}}'):
            page.button(key="listing_generate").click().run()
        self.assertFalse(page.exception)
        self.assertTrue(page.error)
        self.assertEqual(page.session_state.listing_results, original)
        self.assertFalse(any("模拟结果" in x.value for x in page.subheader))

    def test_blank_description_and_language_market_change_do_not_reuse_results(self):
        page = self.page()
        page.button(key="listing_generate").click().run()
        page.text_input(key="listing_country").set_value("DE").run()
        self.assertFalse(any("模拟结果" in x.value for x in page.subheader))
        page.text_area(key="listing_description").set_value(" \n").run()
        with patch("workflows.listing_ui.MockListingClient.complete") as complete:
            page.button(key="listing_generate").click().run()
            self.assertTrue(page.error)
            complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
