import json
import unittest
from unittest.mock import patch

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.schemas import SalesRequest
from workflows.conversation import SalesConversation, revise_sales, start_sales
from workflows.sales import generate_sales


class Client:
    def __init__(self):
        self.calls = []

    def complete(self, messages):
        self.calls.append(json.loads(messages[1]["content"]))
        return "CE certified" if len(self.calls) == 2 else "Please confirm product details."


class ConversationTests(unittest.TestCase):
    def test_two_revisions_keep_facts_previous_text_feedback_and_recheck_claims(self):
        client = Client()
        request = SalesRequest("Line one\nLine two, special", "产品\n规格", "德文")
        first = start_sales(request, client)
        second = revise_sales(first, "更礼貌\n保留信息", client)
        third = revise_sales(second, "删去未经证实的认证", client)
        self.assertEqual([len(x.turns) for x in [first, second, third]], [1, 2, 3])
        self.assertNotIn("revision", client.calls[0])
        for call in client.calls:
            self.assertEqual(call["inquiry"], request.inquiry)
            self.assertEqual(call["product_info"], request.product_info)
            self.assertEqual(call["target_language"], "德文")
        self.assertEqual(client.calls[1]["revision"]["previous_content"], first.turns[-1].response.content)
        self.assertEqual(client.calls[2]["revision"]["previous_content"], second.turns[-1].response.content)
        self.assertEqual(client.calls[1]["revision"]["feedback"], "更礼貌\n保留信息")
        self.assertEqual([len(x.turns[-1].response.risks) for x in [first, second, third]], [0, 1, 0])
        self.assertEqual([x.kind for x in third.turns], ["initial", "revision", "revision"])
        self.assertTrue(all(x.response.pending_confirmations for x in third.turns))

    def test_failed_revision_does_not_append_or_change_old_version(self):
        original = start_sales(SalesRequest("Inquiry", "Product", "English"), Client())
        before = original.turns[0].response.to_dict()
        with patch.object(Client, "complete", side_effect=RuntimeError("private")):
            with self.assertRaises(CrossBotError):
                revise_sales(original, "change", Client())
        self.assertEqual(len(original.turns), 1)
        self.assertEqual(original.turns[0].response.to_dict(), before)

    def test_missing_previous_or_blank_feedback_never_call_provider(self):
        for previous, feedback in [(None, "change"), ("previous", " \n"), ("", "change")]:
            client = Client()
            with self.assertRaises(ValidationError):
                generate_sales(SalesRequest("a", "b", "en"), client, feedback=feedback, previous_content=previous)
            self.assertFalse(client.calls)

    def test_revision_before_first_draft_is_blocked(self):
        with self.assertRaises(ValidationError):
            revise_sales(SalesConversation(SalesRequest("a", "b", "en")), "change", Client())

    def test_snapshot_is_not_changed_by_later_input_edits(self):
        request = SalesRequest("original", "product", "en")
        conversation = start_sales(request, Client())
        request.inquiry = "changed"
        self.assertEqual(conversation.request.inquiry, "original")


if __name__ == "__main__":
    unittest.main()
