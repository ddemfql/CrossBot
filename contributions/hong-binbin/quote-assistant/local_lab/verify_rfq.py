"""Platform applicability, material ambiguity, history and no-network interaction."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
from importlib import reload

from streamlit.testing.v1 import AppTest
from local_lab.catalog import load_catalog
from local_lab.deepseek_client import Draft, generate_draft
from local_lab.dialogue import ChangeProposal
from local_lab.revisions import append_version, export_history, export_text
from local_lab.rfq_check import build_rfq_report
from local_lab.ui_loader import load_revision_panel


class RFQChecks(unittest.TestCase):
    def setUp(self):
        self.cotton = load_catalog().products[0]
        self.app = str(Path(__file__).with_name("app.py"))

    def test_source_version_applicability_and_exact_materials(self):
        report = build_rfq_report("alibaba_rfq", "涤纶", self.cotton)
        self.assertEqual(report["status"], "mismatch")
        self.assertEqual(report["input_product"]["material"], "棉布")
        self.assertIn("2016", report["reference_version"])
        self.assertIn("待人工核对", report["freshness"])
        self.assertEqual(build_rfq_report("general_email", "涤纶", self.cotton)["status"], "not_applicable")
        self.assertEqual(build_rfq_report("amazon", "涤纶", self.cotton)["status"], "manual")
        for value in ["棉布", " Cotton ", "ＣＯＴＴＯＮ", "cotton fabric"]:
            result = build_rfq_report("alibaba_rfq", value, self.cotton)
            self.assertEqual(result["status"], "material_match")
            self.assertIn("仅材质", result["status_label"])

    def test_ambiguous_missing_canvas_and_alternatives_are_manual(self):
        for value in ["", "帆布", "cotton or polyester", "不要棉布", "50% cotton 50% polyester", "100% cotton", "纯棉且防水"]:
            self.assertEqual(build_rfq_report("alibaba_rfq", value, self.cotton)["status"], "manual", value)
        canvas = load_catalog().products[2]
        self.assertEqual(build_rfq_report("alibaba_rfq", "棉布", canvas)["status"], "manual")
        self.assertEqual(build_rfq_report("alibaba_rfq", "棉布", None)["status"], "manual")

    def test_compare_ui_without_model_call_or_amount_change(self):
        with patch("local_lab.revision_panel.generate_draft") as generate, \
             patch("local_lab.dialogue_panel.interpret_change") as interpret:
            page = AppTest.from_file(self.app, default_timeout=30).run()
            page.number_input(key="quote_quantity").set_value(500).run()
            page.selectbox(key="rfq_scene").select("alibaba_rfq").run()
            page.text_input(key="rfq_required_material").set_value("涤纶").run()
            self.assertTrue(any("材质不一致" in x.value for x in page.warning))
            self.assertEqual([m.value for m in page.metric if m.label == "货品金额（USD）"], ["600.00"])
            page.text_input(key="rfq_required_material").set_value("棉布").run()
            self.assertTrue(any("仅材质字段相符" in x.value for x in page.info))
            self.assertFalse(any("材质不一致" in x.value for x in page.warning))
            page.selectbox(key="rfq_scene").select("general_email").run()
            self.assertTrue(page.text_input(key="rfq_required_material").disabled)
            self.assertTrue(any("不套用 RFQ" in x.value for x in page.info))
            self.assertFalse(page.exception)
            generate.assert_not_called()
            interpret.assert_not_called()

    def test_snapshot_model_input_export_staleness_and_dialogue(self):
        old = json.loads((Path(__file__).parents[1] / "docs/verification/step7-payment-verified.json").read_text(encoding="utf-8-sig"))["versions"]
        page = AppTest.from_file(self.app, default_timeout=30)
        page.session_state["quote_history"] = deepcopy(old)
        page.session_state["quote_quantity"] = 500
        page.session_state["payment_terms"] = "50%预付款，50%发货前支付"
        draft = Draft(english_reply="Simulated draft: our material is cotton, your requirement is polyester. Would you consider an alternative?", chinese_checklist="模拟材质差异")
        with patch("local_lab.revision_panel.generate_draft", return_value=draft) as generate, \
             patch("local_lab.dialogue_panel.interpret_change", return_value=ChangeProposal(quantity=1000, clarification="", unsupported=[])):
            page.run()
            page.selectbox(key="rfq_scene").select("alibaba_rfq").run()
            page.text_input(key="rfq_required_material").set_value("涤纶").run()
            page.button(key="generate_ai").click().run()
            record = deepcopy(page.session_state.quote_history[-1])
            sent = generate.call_args.args[1]
            self.assertEqual(sent["platform_report"], record["platform_report"])
            self.assertEqual(sent["workflow"], "RFQ 报价修订")
            self.assertEqual(record["platform_report"]["status"], "mismatch")
            self.assertEqual(record["context"]["platform_request"]["requested_material"], "涤纶")
            self.assertEqual(record["draft"]["english_reply"], draft.english_reply)
            self.assertEqual(page.session_state.quote_history[:2], old)
            self.assertIn("P01", export_text(record))
            self.assertIn("2016", export_text(record))
            self.assertEqual(json.loads(export_history([record]))["versions"][0]["platform_report"], record["platform_report"])
            # A new material requirement invalidates current output, not historical facts.
            page.text_input(key="rfq_required_material").set_value("棉布").run()
            self.assertNotIn("ai_draft", page.session_state)
            self.assertEqual(page.session_state.quote_history[-1], record)
            page.text_input(key="change_message").set_value("数量改成1000个").run()
            page.button(key="interpret_change").click().run()
            page.button(key="apply_change").click().run()
            page.button(key="generate_ai").click().run()
            last = deepcopy(page.session_state.quote_history[-1])
            self.assertEqual(last["context"]["dialogue"]["user_message"], "数量改成1000个")
            self.assertEqual(last["platform_report"]["status"], "material_match")
            self.assertEqual(last["context"]["goods_total"], "1050.00")
            page.run()  # timestamp changes must not invalidate stable business inputs
            self.assertIn("ai_draft", page.session_state)
            self.assertEqual(page.session_state.quote_history[-1], last)
            page.selectbox(key="revision_view").select(1).run()
            self.assertTrue(any("此旧版本未保存平台" in c.value for c in page.caption))
            self.assertFalse(page.exception)

    def test_snapshot_is_deep_and_prompt_contains_scope(self):
        report = build_rfq_report("alibaba_rfq", "涤纶", self.cotton)
        history = append_version([], {}, {}, platform_report=report)
        report["input_product"]["material"] = "changed"
        self.assertEqual(history[0]["platform_report"]["input_product"]["material"], "棉布")
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": Draft(english_reply="Mock", chinese_checklist="模拟").model_dump_json()}}]}).encode()
        with patch("local_lab.deepseek_client.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            generate_draft("test-only", {"platform_report": history[0]["platform_report"]})
            body = json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertIn("普通邮件不套用 RFQ", body["messages"][0]["content"])
            self.assertIn("不得改写两者", body["messages"][0]["content"])

    def test_stale_client_refreshes_panel_without_resetting_other_state(self):
        module = load_revision_panel()
        import local_lab.deepseek_client as client
        with patch.object(client, "SOURCE_SHA256", "old"), patch("local_lab.ui_loader.reload", wraps=reload) as refresh:
            self.assertIs(load_revision_panel(), module)
            self.assertEqual([c.args[0].__name__ for c in refresh.call_args_list], ["local_lab.deepseek_client", "local_lab.revision_panel"])
            load_revision_panel()
            self.assertEqual(refresh.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
