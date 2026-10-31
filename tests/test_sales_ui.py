from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "app.py"


class SalesUI(unittest.TestCase):
    def test_multiline_first_and_two_rounds_are_stable_across_reruns(self):
        from providers.mock_client import MockChatClient
        client = MockChatClient()
        with patch("workflows.sales_ui.MockChatClient", return_value=client), patch.object(client, "complete", wraps=client.complete) as complete:
            page = AppTest.from_file(str(APP), default_timeout=15).run()
            page.text_area(key="sales_inquiry").set_value("Need lamps, 500\nShip DE\n目标语言：不是字段").run()
            page.text_area(key="sales_product").set_value("产品，USB\n色温：3000K").run()
            page.selectbox(key="sales_language").select("德文").run()
            page.button(key="sales_generate").click().run()
            self.assertEqual(len(page.session_state.sales_conversation.turns), 1)
            for feedback in ["请更礼貌\n保留参数", "请追加确认包装的询问"]:
                page.text_area(key="sales_feedback").set_value(feedback).run()
                page.button(key="sales_revise").click().run()
            self.assertFalse(page.exception)
            self.assertEqual(len(page.session_state.sales_conversation.turns), 3)
            request = page.session_state.sales_conversation.request
            self.assertEqual(request.inquiry, "Need lamps, 500\nShip DE\n目标语言：不是字段")
            self.assertEqual(request.target_language, "德文")
            page.run()
            self.assertEqual(complete.call_count, 3)
            self.assertFalse(any("校验通过" in item.value for item in page.success))

    def test_invalid_new_input_and_failed_revision_preserve_successful_history(self):
        page = AppTest.from_file(str(APP), default_timeout=15).run()
        page.button(key="sales_generate").click().run()
        original = page.session_state.sales_conversation
        page.text_area(key="sales_inquiry").set_value(" ").run()
        self.assertTrue(page.button(key="sales_revise").disabled)
        page.button(key="sales_generate").click().run()
        self.assertTrue(page.error)
        self.assertEqual(page.session_state.sales_conversation, original)
        page.text_area(key="sales_inquiry").set_value(original.request.inquiry).run()
        page.text_area(key="sales_feedback").set_value("改一下").run()
        with patch("workflows.sales_ui.MockChatClient.complete", side_effect=RuntimeError("secret marker")):
            page.button(key="sales_revise").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(len(page.session_state.sales_conversation.turns), 1)
        self.assertTrue(page.error)
        self.assertFalse(any("secret marker" in x.value for x in page.error))

    def test_switching_w2_and_back_preserves_w1_without_loading_real_client(self):
        legacy = ModuleType("main")
        legacy.run_workflow_stream = Mock()
        legacy.iterate_workflow_stream = Mock()
        with patch.dict(sys.modules, {"main": legacy}):
            page = AppTest.from_file(str(APP), default_timeout=15).run()
            page.button(key="sales_generate").click().run()
            original = page.session_state.sales_conversation
            page.radio[0].set_value("W2 多平台Listing生成").run()
            self.assertFalse(page.exception)
            self.assertTrue(page.multiselect)
            page.button(key="listing_generate").click().run()
            page.radio[0].set_value("W1 询盘极速转化").run()
            self.assertFalse(page.exception)
            self.assertTrue(page.button(key="sales_generate"))
            self.assertEqual(page.session_state.sales_conversation, original)
            legacy.run_workflow_stream.assert_not_called()
            legacy.iterate_workflow_stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
