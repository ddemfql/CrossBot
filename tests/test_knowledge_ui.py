from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "app.py"


class KnowledgeUI(unittest.TestCase):
    def test_w1_reference_unknown_and_historical_citations_are_separate(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        page.text_area(key="sales_product").set_value("DEMO-LAMP-001\n产品参数待确认").run()
        self.assertTrue(any("命中模拟参考" in x.value for x in page.info))
        page.button(key="sales_generate").click().run()
        notes = page.session_state.sales_conversation.turns[-1].response.pending_confirmations
        self.assertTrue(any("模拟引用 DEMO-LAMP-001" in x for x in notes))
        page.text_area(key="sales_product").set_value("一款普通台灯").run()
        self.assertTrue(any("产品资料：未知" in x.value for x in page.warning))
        self.assertEqual(page.session_state.sales_conversation.turns[-1].response.pending_confirmations, notes)
        self.assertFalse(page.exception)

    def test_w2_citations_only_include_selected_platform_reference(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        page.radio[0].set_value("W2 多平台Listing生成").run()
        page.text_area(key="listing_description").set_value("DEMO-CUP-001").run()
        page.multiselect(key="listing_platforms").set_value(["Amazon"]).run()
        page.button(key="listing_generate").click().run()
        notes = "\n".join(page.session_state.listing_results["Amazon"].pending_confirmations)
        self.assertIn("STYLE-AMAZON", notes)
        self.assertNotIn("STYLE-TIKTOK", notes)
        self.assertIn("生效未知", notes)
        self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main()
