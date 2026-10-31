"""Offline revision/history tests; simulated model responses are test-only."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from local_lab.catalog import load_catalog
from local_lab.deepseek_client import Draft, GenerationError, build_context
from local_lab.revisions import append_version, compare_contexts, export_history, export_text
from local_lab.revisions import attach_current_dialogue


class RevisionChecks(unittest.TestCase):
    def test_legacy_attachment_preserves_evidence_and_rejects_mismatch(self):
        product = load_catalog().products[0]
        before = build_context(product, 500, "")
        after = build_context(product, 500, "拟议50%/50%，待双方确认")
        draft = {"english_reply": "Original draft", "chinese_checklist": "原草稿"}
        history = append_version(append_version([], before, draft), after, draft)
        original = deepcopy(history)
        active = {"context": after, "user_message": "付款改为50%/50%",
                  "feedback": compare_contexts(before, after)}
        result = attach_current_dialogue(history, active)
        self.assertEqual(history, original)
        self.assertEqual(result[0], original[0])
        for field in ["version", "created_at", "context", "draft"]:
            self.assertEqual(result[-1][field], original[-1][field])
        self.assertEqual(result[-1]["dialogue_attachment"]["source"], "current_session_post_generation")
        self.assertIn("事后补充", export_text(result[-1]))
        self.assertIs(attach_current_dialogue(result, active), result)
        self.assertIs(attach_current_dialogue(history, {**active, "feedback": []}), history)
        self.assertIs(attach_current_dialogue(history, {**active, "context": before}), history)
        single = history[:1]
        self.assertIs(attach_current_dialogue(single, active), single)

    def test_legacy_attachment_ui_without_network_or_new_version(self):
        records = json.loads((Path(__file__).parents[1] / "docs/verification/step7-payment-verified.json").read_text(encoding="utf-8-sig"))["versions"]
        original = deepcopy(records)
        page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30)
        page.session_state["quote_history"] = records
        page.session_state["quote_quantity"] = 500
        page.session_state["payment_terms"] = records[-1]["context"]["payment_terms"]
        page.session_state["applied_dialogue"] = {
            "context": deepcopy(records[-1]["context"]), "user_message": "模拟付款修改测试",
            "feedback": compare_contexts(records[0]["context"], records[1]["context"])}
        with patch("local_lab.revision_panel.generate_draft") as call:
            page.run()
            page.button(key="attach_dialogue").click().run()
            self.assertEqual(len(page.session_state.quote_history), 2)
            self.assertEqual(page.session_state.quote_history[-1]["draft"], original[-1]["draft"])
            self.assertTrue(any("事后补充关联" in item.value for item in page.markdown))
            self.assertFalse(any("尚未为当前输入生成新版" in item.value for item in page.info))
            call.assert_not_called()
            self.assertFalse(page.exception)

    def test_immutable_snapshots_diffs_and_export(self):
        product = load_catalog().products[0]
        first = build_context(product, 300, "模拟付款条件")
        draft = {"english_reply": "Simulated draft", "chinese_checklist": "模拟清单"}
        history = append_version([], first, draft)
        first["product"]["name"] = "后来修改"
        draft["english_reply"] = "changed"
        self.assertNotEqual(history[0]["context"]["product"]["name"], "后来修改")
        self.assertEqual(history[0]["draft"]["english_reply"], "Simulated draft")
        second = build_context(product, 1000, "模拟付款条件")
        history = append_version(history, second, history[0]["draft"])
        changes = compare_contexts(history[0]["context"], second)
        self.assertEqual([c["字段"] for c in changes], ["采购数量", "货品单价", "货品金额"])
        self.assertEqual(changes[-1]["新版"], "1050.00")
        exported = json.loads(export_history(history))
        self.assertEqual(len(exported["versions"]), 2)
        self.assertIn("360.00", export_text(history[0]))
        self.assertIn("1050.00", export_text(history[1]))

    def test_two_generations_failure_history_selection_and_no_extra_calls(self):
        draft = Draft(english_reply="Simulated test", chinese_checklist="模拟测试")
        with patch("local_lab.revision_panel.generate_draft", return_value=draft) as call:
            page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
            page.text_input(key="deepseek_api_key").set_value("test-key-do-not-export").run()
            page.button(key="generate_ai").click().run()
            old = deepcopy(page.session_state.quote_history[0])
            page.number_input(key="quote_quantity").set_value(1000).run()
            self.assertEqual(page.session_state.quote_history, [old])
            page.button(key="generate_ai").click().run()
            self.assertEqual(page.session_state.quote_history[0], old)
            self.assertEqual(len(page.session_state.quote_history), 2)
            self.assertEqual(page.selectbox(key="revision_view").value, 2)
            sent = call.call_args.args[1]
            self.assertEqual(sent["workflow"], "邮件报价修订")
            self.assertEqual(sent["revision"]["previous_facts"]["quantity"], 300)
            page.selectbox(key="revision_view").select(1).run()
            self.assertEqual(call.call_count, 2)
            self.assertNotIn("test-key-do-not-export", export_history(page.session_state.quote_history))
            call.side_effect = GenerationError("模拟超时")
            page.button(key="generate_ai").click().run()
            self.assertEqual(len(page.session_state.quote_history), 2)
            self.assertNotIn("ai_draft", page.session_state)
            self.assertFalse(page.exception)

    def test_migrate_step_five_draft_without_call(self):
        page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30)
        context = build_context(load_catalog().products[0], 300, "")
        page.session_state["ai_draft"] = {"context": context,
                                          "draft": {"english_reply": "old", "chinese_checklist": "旧版"}}
        with patch("local_lab.revision_panel.generate_draft") as call:
            page.run()
            self.assertEqual(len(page.session_state.quote_history), 1)
            self.assertEqual(page.session_state.quote_history[0]["draft"]["english_reply"], "old")
            page.run()
            self.assertEqual(len(page.session_state.quote_history), 1)
            call.assert_not_called()
            self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
