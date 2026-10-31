"""Fixed buyer-question templates, explicitly synthetic, without network calls."""
import json


class MockCustomerClient:
    provider = "mock"
    model = "fixed-customer-template-v1"

    def complete(self, messages):
        payload = json.loads(messages[1]["content"])
        if payload["target_language"] in {"德文", "German", "de"}:
            return "Könnten Sie bitte die Produktspezifikationen sowie die verfügbaren Zertifizierungsunterlagen bestätigen? Welche Verpackungsoptionen und Lieferzeiten sind möglich?"
        return "Could you confirm the product specifications and available certification documents? What packaging options and delivery times can you offer?"
