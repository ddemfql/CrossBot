import unittest
from datetime import timezone
from core.config import load_config
from core.errors import CrossBotError, ErrorCode
from core.events import EventType, WorkflowEvent
from core.models import Case, CaseStatus, CrossCheckStatus, RiskItem, RiskSeverity, to_dict
from core.schemas import ListingRequest, SalesRequest

class CoreContractTests(unittest.TestCase):
    def test_case_defaults_are_serializable(self):
        data = to_dict(Case(case_number="CASE-001", customer_name="Acme", destination_country="DE"))
        self.assertEqual(data["status"], CaseStatus.DRAFT.value)
        self.assertIn("case_id", data)
        self.assertTrue(data["created_at"].endswith("+00:00"))

    def test_event_factories_keep_a_stable_shape(self):
        event = WorkflowEvent.error("provider_timeout", "Request timed out", retryable=True)
        self.assertEqual(event.type, EventType.ERROR)
        self.assertEqual(event.payload["code"], "provider_timeout")
        self.assertTrue(event.payload["retryable"])
        self.assertEqual(event.occurred_at.tzinfo, timezone.utc)

    def test_requests_keep_structured_fields(self):
        sales = SalesRequest("Need 500 pcs", "USB lamp", "German")
        listing = ListingRequest("316 stainless cup", ["Amazon"])
        self.assertEqual(sales.target_language, "German")
        self.assertEqual(listing.platforms, ["Amazon"])
        self.assertNotIn("\\n", sales.inquiry)

    def test_config_does_not_validate_key_by_prefix(self):
        config = load_config({"OPENAI_API_KEY": "vendor-token", "MOCK_MODE": "false"})
        self.assertTrue(config.has_provider_credentials)
        self.assertFalse(config.mock_mode)

    def test_error_and_risk_contracts_are_explicit(self):
        error = CrossBotError(ErrorCode.PARSE_FAILED, "Could not parse document")
        risk = RiskItem("missing_certificate", "Certificate requires confirmation", RiskSeverity.BLOCKING, blocking=True)
        self.assertEqual(error.to_dict()["code"], "parse_failed")
        self.assertTrue(risk.blocking)
        self.assertEqual(CrossCheckStatus.PENDING_REVIEW.value, "pending_review")

if __name__ == "__main__":
    unittest.main()
