"""Verify existing RFQ sessions survive the three-workspace presentation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from local_lab.revisions import export_history, export_text


class WorkspaceUI(unittest.TestCase):
    def test_archived_rfq_state_and_downloads_survive_relayout_and_rerun(self):
        root = Path(__file__).parents[1]
        history = json.loads((root / "docs/verification/rfq-live-history.json").read_text(encoding="utf-8-sig"))["versions"]
        record = history[-1]
        context = record["context"]
        page = AppTest.from_file(str(root / "local_lab/app.py"), default_timeout=30)
        for key, value in {
            "quote_history": deepcopy(history),
            "ai_draft": {"context": deepcopy(context), "draft": deepcopy(record["draft"])},
            "quote_sku": context["product"]["sku"],
            "quote_quantity": context["quantity"],
            "payment_terms": context["payment_terms"],
            "rfq_scene": "alibaba_rfq",
            "rfq_required_material": context["platform_request"]["requested_material"],
            "deepseek_api_key": "test-key-not-a-real-credential",
        }.items():
            page.session_state[key] = value
        with patch("local_lab.revision_panel.generate_draft") as generate, patch("local_lab.dialogue_panel.interpret_change") as interpret:
            page.run()
            self.assertFalse(page.exception)
            self.assertEqual([tab.label for tab in page.tabs], ["1 填写需求", "2 检查并生成", "3 修订与下载"])
            self.assertEqual(page.tabs[0].number_input(key="quote_quantity").value, 500)
            self.assertEqual(page.tabs[1].button(key="generate_ai").label, "生成英文回复与中文清单")
            self.assertEqual(page.tabs[2].selectbox(key="revision_view").value, 1)
            self.assertEqual({item.key for item in page.tabs[2].get("download_button")} & {"download_quote", "download_history"}, {"download_quote", "download_history"})
            page.button(key="reload_catalog").click().run()
            self.assertFalse(page.exception)
            self.assertEqual(page.session_state.quote_history, history)
            self.assertEqual(page.session_state.ai_draft["draft"], record["draft"])
            self.assertEqual(page.session_state.deepseek_api_key, "test-key-not-a-real-credential")
            self.assertEqual(export_text(page.session_state.quote_history[-1]), export_text(record))
            self.assertNotIn("test-key-not-a-real-credential", export_history(page.session_state.quote_history))
            generate.assert_not_called()
            interpret.assert_not_called()


if __name__ == "__main__":
    unittest.main()
