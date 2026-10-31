from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from core.errors import CrossBotError
from core.schemas import GenerationResponse, ListingRequest, SalesRequest
from workflows.conversation import SalesConversation, SalesTurn
from workflows.exports import business_snapshot, export_bundle, json_bytes, text_bytes


def saved_records():
    request = SalesRequest("询盘\nNeed lamps", "DEMO-LAMP-001\n产品", "英文")
    turns = [SalesTurn("initial" if i == 1 else "revision", None if i == 1 else f"修改第{i-1}轮\n保留参数",
                       GenerationResponse(f"原始草稿 V{i}\nPlease confirm.", pending_confirmations=["模拟引用 DEMO-LAMP-001 | demo-1"],
                                          provider="real", model="test-model", correlation_id=request.correlation_id))
             for i in range(1, 4)]
    return {"sales_conversation": SalesConversation(request, turns),
            "listing_request": ListingRequest("DEMO-CUP-001", ["Amazon"]),
            "listing_results": {"Amazon": GenerationResponse("Exact listing\n350ml", provider="real", model="test-model")},
            "provider_key": "PRIVATE_TEST_SENTINEL", "provider_url": "https://private-test.example", "provider_confirm": True,
            "sales_product": "未生成的新表单资料"}


class ExportTests(unittest.TestCase):
    def test_three_versions_feedback_and_w2_are_preserved_without_config(self):
        state = saved_records()
        before = deepcopy(state)
        snapshot = business_snapshot(state)
        bundle = export_bundle(snapshot)
        data = json.loads(json_bytes(bundle))
        self.assertEqual([x["version"] for x in data["w1"]["versions"]], [1, 2, 3])
        for original, exported in zip(state["sales_conversation"].turns, data["w1"]["versions"]):
            self.assertEqual(exported["feedback"], original.feedback)
            self.assertEqual(exported["response"]["content"], original.response.content)
            self.assertEqual(exported["response"]["pending_confirmations"], original.response.pending_confirmations)
            self.assertEqual(exported["response"]["provider"], "real")
        self.assertEqual(list(data["w2"]["results"]), ["Amazon"])
        combined = json_bytes(bundle).decode() + text_bytes(bundle, "w1").decode("utf-8-sig") + text_bytes(bundle, "w2").decode("utf-8-sig")
        for private in ["PRIVATE_TEST_SENTINEL", "private-test.example", "provider_key", "provider_confirm", "未生成的新表单资料"]:
            self.assertNotIn(private, combined)
        self.assertEqual(state, before)
        canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(data["business_sha256"], hashlib.sha256(canonical.encode()).hexdigest())

    def test_only_three_business_keys_are_read(self):
        class GuardedState(dict):
            def get(self, key):
                if key not in {"sales_conversation", "listing_request", "listing_results"}:
                    raise AssertionError("Settings must not be read")
                return super().get(key)
        self.assertIsNotNone(business_snapshot(GuardedState(saved_records()))["w1"])

    def test_snapshot_is_detached_and_does_not_invent_generation_timestamps(self):
        state = saved_records()
        snapshot = business_snapshot(state)
        state["sales_conversation"].turns[0].response.content = "changed afterwards"
        bundle = export_bundle(snapshot)
        self.assertNotIn("changed afterwards", json_bytes(bundle).decode())
        self.assertNotIn("generated_at", json_bytes(bundle).decode())
        self.assertIn("exported_at_utc", bundle)

    def test_txt_contains_all_versions_and_both_feedbacks_without_rewriting_text(self):
        bundle = export_bundle(business_snapshot(saved_records()))
        raw = text_bytes(bundle, "w1")
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        for expected in ["W1 V1", "W1 V2", "W1 V3", "修改第1轮", "修改第2轮", "原始草稿 V3", "来源：real", "demo-1"]:
            self.assertIn(expected, text)

    def test_empty_or_partial_workflow_not_fabricated(self):
        self.assertEqual(business_snapshot({}), {"w1": None, "w2": None})
        with self.assertRaises(CrossBotError):
            export_bundle(business_snapshot({}))
        state = saved_records()
        for key in ["listing_request", "listing_results"]:
            state.pop(key)
        bundle = export_bundle(business_snapshot(state))
        self.assertIsNone(bundle["w2"])
        with self.assertRaises(CrossBotError):
            text_bytes(bundle, "w2")

    def test_inconsistent_listing_not_silently_exported(self):
        state = saved_records()
        state["listing_results"]["TikTok"] = GenerationResponse("unexpected")
        with self.assertRaises(CrossBotError):
            business_snapshot(state)

    def test_existing_live_like_session_can_add_downloads_without_new_generation(self):
        # Tests a prepared session, never the user's live browser session or real key.
        state = saved_records()
        page = AppTest.from_file(str(Path(__file__).parent / "fixtures" / "b_panels_app.py"), default_timeout=15)
        page.session_state["sales_conversation"] = state["sales_conversation"]
        page.session_state["listing_request"] = state["listing_request"]
        page.session_state["listing_results"] = state["listing_results"]
        page.session_state["provider_mode"] = "真实"
        page.session_state["provider_key"] = "PRIVATE_TEST_SENTINEL"
        with patch("providers.llm_client.HttpChatClient.complete", side_effect=AssertionError("No generation")) as real:
            page.run()
            self.assertFalse(page.exception)
            self.assertEqual({x.key for x in page.get("download_button")}, {"download_business_json", "download_w1_txt", "download_w2_txt"})
            self.assertEqual(page.session_state.sales_conversation, state["sales_conversation"])
            self.assertEqual(page.text_input(key="provider_key").value, "PRIVATE_TEST_SENTINEL")
            page.run()
            self.assertEqual(len(page.session_state.sales_conversation.turns), 3)
            real.assert_not_called()


if __name__ == "__main__":
    unittest.main()
