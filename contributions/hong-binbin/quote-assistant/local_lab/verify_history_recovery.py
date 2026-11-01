"""Exercise a fresh browser session, intact archives, and failed recovery."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from local_lab.history_recovery import BACKUP_PATH, restore_archived_rfq
from local_lab.revisions import export_text


class HistoryRecovery(unittest.TestCase):
    def test_restore_round1_replaces_detached_session_then_appends_v3(self):
        from local_lab.deepseek_client import Draft
        from local_lab.dialogue import ChangeProposal
        root = Path(__file__).parents[1] / "docs/verification"
        detached = json.loads((root / "demo-round2-detached-history.json").read_text(encoding="utf-8-sig"))["versions"]
        expected = json.loads((root / "demo-round1-history.json").read_text(encoding="utf-8-sig"))["versions"]
        page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30)
        page.session_state["quote_history"] = deepcopy(detached)
        terms = "拟议条款，30%预付款，70%发货前支付，待双方确认"
        with patch("local_lab.revision_panel.generate_draft", return_value=Draft(english_reply="Simulated draft", chinese_checklist="模拟")) as generate, patch("local_lab.dialogue_panel.interpret_change", return_value=ChangeProposal(payment_terms=terms, clarification="", unsupported=[])) as interpret:
            page.run()
            page.button(key="restore_demo_round1").click().run()
            self.assertFalse(page.exception)
            self.assertEqual(page.session_state.quote_history, expected)
            self.assertEqual(page.session_state.history_before_recovery, detached)
            self.assertEqual(page.number_input(key="quote_quantity").value, 1000)
            self.assertEqual(page.selectbox(key="rfq_scene").value, "alibaba_rfq")
            generate.assert_not_called()
            interpret.assert_not_called()
            message = "付款条件改为：" + terms + "。数量保持1000个。"
            page.text_input(key="change_message").set_value(message).run()
            page.button(key="interpret_change").click().run()
            page.button(key="apply_change").click().run()
            page.button(key="generate_ai").click().run()
            result = page.session_state.quote_history
            self.assertEqual(result[:2], expected)
            self.assertEqual(len(result), 3)
            self.assertEqual(result[-1]["context"]["goods_total"], "1050.00")
            self.assertEqual(result[-1]["context"]["dialogue"]["user_message"], message)
            self.assertEqual(result[-1]["check_report"]["total_percent"], "100")
            self.assertEqual(result[-1]["platform_report"]["status"], "mismatch")
            self.assertFalse(page.exception)

    def test_fresh_session_restores_original_record_and_inputs_without_model(self):
        expected = json.loads(BACKUP_PATH.read_text(encoding="utf-8-sig"))["versions"]
        with patch("local_lab.revision_panel.generate_draft") as generate, patch("local_lab.dialogue_panel.interpret_change") as interpret:
            page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            self.assertEqual(page.session_state.quote_history, [])
            page.button(key="restore_archived_rfq").click().run()
            self.assertFalse(page.exception)
            self.assertEqual(page.session_state.quote_history, expected)
            self.assertEqual(page.selectbox(key="revision_view").value, 1)
            self.assertEqual(page.number_input(key="quote_quantity").value, 500)
            self.assertEqual(page.selectbox(key="rfq_scene").value, "alibaba_rfq")
            self.assertEqual(page.text_input(key="rfq_required_material").value, "涤纶")
            self.assertEqual(page.session_state.ai_draft["draft"], expected[-1]["draft"])
            self.assertEqual([m.value for m in page.metric if m.label == "货品金额（USD）"], ["600.00"])
            self.assertEqual(page.text_input(key="deepseek_api_key").value, "")
            self.assertEqual(export_text(page.session_state.quote_history[-1]), BACKUP_PATH.with_name("rfq-live-quote-v1.txt").read_text(encoding="utf-8-sig"))
            page.run()
            self.assertEqual(page.session_state.quote_history, expected)
            self.assertNotIn("restore_archived_rfq", [b.key for b in page.button])
            generate.assert_not_called()
            interpret.assert_not_called()

    def test_no_overwrite_existing_work_or_credentials(self):
        state = {"quote_history": [{"version": 2}], "deepseek_api_key": "test-only"}
        original = deepcopy(state)
        with self.assertRaises(ValueError):
            restore_archived_rfq(state)
        self.assertEqual(state, original)
        state = {"quote_history": [], "deepseek_api_key": "test-only"}
        restore_archived_rfq(state)
        self.assertEqual(state["deepseek_api_key"], "test-only")

    def test_missing_or_changed_backup_does_not_partially_restore(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "archive.json"
            for content in (None, b'{}'):
                if content is not None:
                    path.write_bytes(content)
                state = {"quote_history": [], "quote_quantity": 1000}
                original = deepcopy(state)
                with patch("local_lab.history_recovery.BACKUP_PATH", path), self.assertRaises(ValueError):
                    restore_archived_rfq(state)
                self.assertEqual(state, original)


if __name__ == "__main__":
    unittest.main()
