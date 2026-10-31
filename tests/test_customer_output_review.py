"""Regressions from the saved bilingual customer drafts; all calls are test doubles."""
import json
import unittest
from unittest.mock import Mock

from core.schemas import SalesRequest, ListingRequest
from providers.mock_client import MockChatClient
from workflows.content_review import review_customer_output
from workflows.conversation import start_sales, revise_sales
from workflows.sales import generate_sales
from workflows.listing import generate_listings


class CustomerOutputReviewTests(unittest.TestCase):
    def test_german_draft_marks_chinese_greeting_and_internal_section(self):
        text = "您好，\nVielen Dank.\nHinweis für die interne Prüfung (nicht an den Kunden senden): Entwurf."
        risks = review_customer_output(text, target_language="德文")
        self.assertEqual({r.code for r in risks}, {"target_language_cjk", "internal_note_in_customer_text"})
        self.assertTrue(all(r.evidence and r.source for r in risks))

    def test_clean_text_and_non_latin_target_are_not_mislabeled(self):
        for text, language in [("Guten Tag, vielen Dank.", "German"), ("Dear Customer, thank you.", "English"),
                               ("您好，这是客户回复。", "中文")]:
            self.assertEqual(review_customer_output(text, target_language=language), [])

    def test_every_real_sales_revision_is_rechecked(self):
        client = Mock(provider="real", model="test-double")
        client.complete.side_effect = ["Guten Tag.", "您好，Guten Tag.", "Guten Tag. Internal review: draft only."]
        first = start_sales(SalesRequest("Question", "USB lamp", "德文"), client)
        second = revise_sales(first, "polite", client)
        third = revise_sales(second, "concise", client)
        self.assertEqual(first.turns[-1].response.risks, [])
        self.assertIn("target_language_cjk", {r.code for r in second.turns[-1].response.risks})
        self.assertIn("internal_note_in_customer_text", {r.code for r in third.turns[-1].response.risks})
        self.assertEqual(len(first.turns), 1)

    def test_listing_checks_each_real_platform(self):
        client = Mock(provider="real", model="test-double")
        client.complete.return_value = json.dumps({"listings": {"Amazon": "您好，USB-Lampe", "TikTok": "USB-Lampe"}})
        results = generate_listings(ListingRequest("USB lamp", ["Amazon", "TikTok"], "Germany", "德文"), client)
        self.assertIn("target_language_cjk", {r.code for r in results["Amazon"].risks})
        self.assertEqual(results["TikTok"].risks, [])

    def test_explicit_mock_transport_is_not_treated_as_a_real_translation(self):
        response = generate_sales(SalesRequest("Question", "USB台灯", "德文"), MockChatClient())
        self.assertNotIn("target_language_cjk", {r.code for r in response.risks})

    def test_flagged_original_content_is_never_silently_rewritten(self):
        text = "Dear Customer.\n---\nNote: This is a draft for manual review."
        client = Mock(provider="real", model="test-double"); client.complete.return_value = text
        response = generate_sales(SalesRequest("Question", "USB lamp", "English"), client)
        self.assertEqual(response.content, text)
        self.assertIn("internal_note_in_customer_text", {r.code for r in response.risks})
