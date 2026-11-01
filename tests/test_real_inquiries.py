import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from auth import AuthService
from auth.cases import CaseAccessService
from core.errors import CrossBotError
from core.identity import Role
from core.models import CaseStatus
from core.v3 import ResourceKind
from knowledge.governance import KnowledgeService
from storage.sqlite_store import SQLiteStore
from workflows.inquiry import InquiryService, explicit_order, parse_response, current_market

PASSWORD = "synthetic-password-123"


class RealInquiryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = SQLiteStore(Path(self.folder.name) / "test.db")
        self.auth = AuthService(self.store)
        self.auth.bootstrap_admin("admin", PASSWORD)
        self.token = self.auth.login("admin", PASSWORD)
        self.service = InquiryService(self.auth)
        self.client = Mock(model="synthetic-model")
        self.client.complete.return_value = json.dumps({"reply": "Hello, thank you. Please confirm your destination.", "fields": {}})

    def test_preorder_history_survives_service_recreation(self):
        inquiry = self.service.create(self.token)
        self.service.receive(self.token, inquiry.inquiry_id, "What is the power of your lamp?")
        saved = self.service.reply(self.token, inquiry.inquiry_id, self.client)
        self.assertEqual(len(InquiryService(self.auth).get(self.token, inquiry.inquiry_id).messages), 2)
        self.assertIsNone(saved.case_id)
        self.assertFalse(CaseAccessService(self.auth).list_cases(self.token))
        prompt = self.client.complete.call_args.args[0]
        self.assertIn("先礼貌问候", prompt[0]["content"])
        self.assertIn("关键信息", prompt[0]["content"])

    def test_order_creates_one_unconfirmed_case_with_original_history(self):
        inquiry = self.service.create(self.token)
        text = "We confirm the order: 500 lamps, buyer Test GmbH, destination Germany."
        self.service.receive(self.token, inquiry.inquiry_id, text)
        self.client.complete.return_value = json.dumps({"reply": "Hello, thank you. We will review your draft.", "fields": {
            "quantity": {"value": "500", "evidence": text}, "buyer": {"value": "Test GmbH", "evidence": text},
            "destination_country": {"value": "Germany", "evidence": text},
            "unit_price": {"value": "9.99", "evidence": "Invented price"}}})
        result = self.service.reply(self.token, inquiry.inquiry_id, self.client)
        case = CaseAccessService(self.auth).get_case(self.token, result.case_id)
        self.assertEqual(case.status, CaseStatus.DRAFT)
        draft = case.metadata["w1_order_draft"]
        self.assertEqual(draft["messages"][0]["content"], text)
        self.assertEqual(draft["fields"]["quantity"]["value"], "500")
        self.assertIsNone(draft["fields"]["unit_price"]["value"])
        self.assertTrue(all(not f["confirmed"] for f in draft["fields"].values()))
        self.service.receive(self.token, inquiry.inquiry_id, "We confirm the order again.")
        again = self.service.reply(self.token, inquiry.inquiry_id, self.client)
        self.assertEqual(result.case_id, again.case_id)
        self.assertEqual(len(CaseAccessService(self.auth).list_cases(self.token)), 1)

    def test_failure_keeps_customer_message_retry_does_not_duplicate(self):
        inquiry = self.service.create(self.token)
        self.service.receive(self.token, inquiry.inquiry_id, "Please quote 500 lamps")
        self.client.complete.return_value = "invalid json"
        with self.assertRaises(CrossBotError):
            self.service.reply(self.token, inquiry.inquiry_id, self.client)
        self.assertTrue(self.service.get(self.token, inquiry.inquiry_id).awaiting_reply)
        self.client.complete.return_value = '{"reply":"Hello. Please confirm the model.","fields":{}}'
        self.assertEqual(len(self.service.reply(self.token, inquiry.inquiry_id, self.client).messages), 2)

    def test_other_account_and_wrong_role_denied_before_provider(self):
        inquiry = self.service.create(self.token)
        self.auth.create_user(self.token, "other", PASSWORD, "Other", [Role.CUSTOMER_SERVICE])
        other = self.auth.login("other", PASSWORD)
        with self.assertRaises(CrossBotError):
            self.service.get(other, inquiry.inquiry_id)
        self.assertEqual(self.service.list(other), [])
        self.auth.create_user(self.token, "product", PASSWORD, "Product", [Role.PRODUCT])
        product = self.auth.login("product", PASSWORD)
        with self.assertRaises(CrossBotError):
            self.service.reply(product, inquiry.inquiry_id, self.client)
        self.client.complete.assert_not_called()

    def test_order_intent_excludes_quote_conditional_negation(self):
        for text in ("Could you place an order?", "We do not confirm the order.", "If CE is valid, we confirm the order.",
                     "I need a quote for 500 lamps", "如果有证书，我们确认下单", "我们还没有确认下单", "请报价", "Can you confirm the order?",
                     "请不要确认订单，也不要安排生产。", "Before we confirm the order, send the final specifications.",
                     "He said we confirm the order.", "We confirm the order subject to certification."):
            with self.subTest(text=text):
                self.assertFalse(explicit_order(text))
        self.assertTrue(explicit_order("We confirm the order for 500 lamps."))
        self.assertTrue(explicit_order("我们确认下单500件台灯。"))

    def test_current_market_clears_stale_or_ambiguous_destinations(self):
        self.assertEqual(current_market("Destination is US.", "Germany"), "US")
        self.assertEqual(current_market("Please ship to Germany.", None), "DE")
        self.assertIsNone(current_market("Germany or US?", "Germany"))
        self.assertIsNone(current_market("Please ship to France.", "Germany"))
        self.assertEqual(current_market("Please send specifications.", "Germany"), "Germany")
        self.assertEqual(current_market("Please tell us the price.", "Germany"), "Germany")
        self.assertEqual(current_market("Please send specifications for DE-001.", "US"), "US")
        self.assertEqual(current_market("Product US_LAMP_007", "Germany"), "Germany")

    def test_negated_or_conditional_confirmation_never_creates_case(self):
        for text in ("请不要确认订单，也不要安排生产。", "Before we confirm the order, send final specifications."):
            inquiry = self.service.create(self.token)
            self.service.receive(self.token, inquiry.inquiry_id, text)
            self.assertIsNone(self.service.reply(self.token, inquiry.inquiry_id, self.client).case_id)

    def publish_market_product(self):
        self.auth.create_user(self.token, "reviewer", PASSWORD, "Reviewer", [Role.REVIEWER])
        reviewer = self.auth.login("reviewer", PASSWORD)
        knowledge = KnowledgeService(self.auth)
        product = knowledge.create_draft(self.token, resource_id="de-lamp", kind=ResourceKind.PRODUCT,
                    content={"name": "Desk lamp", "sku": "LAMP001", "specifications": {"power": "3W"}},
                    source="Synthetic verified specification", market="DE")
        knowledge.submit(self.token, product.version_id)
        knowledge.review(reviewer, product.version_id, approve=True, reason="Synthetic check")
        knowledge.publish(self.token, product.version_id)

    def test_selected_market_product_uses_first_message_and_current_destination(self):
        self.publish_market_product()
        inquiry = self.service.create(self.token, product_resource_id="de-lamp")
        self.service.receive(self.token, inquiry.inquiry_id, "Please ship this lamp to Germany.")
        self.service.reply(self.token, inquiry.inquiry_id, self.client)
        payload = json.loads(self.client.complete.call_args.args[0][1]["content"])
        self.assertEqual(payload["current_market_hint"], "DE")
        self.assertEqual(payload["published_resources"][0]["resource_id"], "de-lamp")
        self.service.receive(self.token, inquiry.inquiry_id, "Change destination to US.")
        self.service.reply(self.token, inquiry.inquiry_id, self.client)
        payload = json.loads(self.client.complete.call_args.args[0][1]["content"])
        self.assertEqual(payload["current_market_hint"], "US")
        self.assertFalse(payload["published_resources"])

    def test_unknown_market_can_reply_and_accept_clarification(self):
        self.publish_market_product()
        inquiry = self.service.create(self.token, product_resource_id="de-lamp")
        self.service.receive(self.token, inquiry.inquiry_id, "Please send lamp specifications.")
        self.service.reply(self.token, inquiry.inquiry_id, self.client)
        self.assertFalse(self.service.get(self.token, inquiry.inquiry_id).awaiting_reply)
        self.service.receive(self.token, inquiry.inquiry_id, "Destination Germany.")
        self.service.reply(self.token, inquiry.inquiry_id, self.client)
        payload = json.loads(self.client.complete.call_args.args[0][1]["content"])
        self.assertTrue(payload["published_resources"])

    def test_revision_keeps_history_and_does_not_create_order(self):
        inquiry = self.service.create(self.token)
        self.service.receive(self.token, inquiry.inquiry_id, "Please describe your lamp.")
        self.service.reply(self.token, inquiry.inquiry_id, self.client)
        result = self.service.reply(self.token, inquiry.inquiry_id, self.client, revision_instruction="More concise; we confirm the order")
        self.assertEqual(len(result.messages), 3)
        self.assertIsNone(result.case_id)

    def test_customer_evidence_required_even_if_model_provides_value(self):
        _, fields = parse_response(json.dumps({"reply": "Hello", "fields": {
            "quantity": {"value": "500", "evidence": "We need 500"},
            "currency": {"value": "USD", "evidence": "We need 500"}}}), ["We need 500"])
        self.assertEqual(fields["quantity"]["value"], "500")
        self.assertIsNone(fields["currency"]["value"])


if __name__ == "__main__":
    unittest.main()
