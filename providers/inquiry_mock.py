"""Explicit local candidates for the AI-extraction UI; no AI or network."""
import json
import re


class MockInquiryClient:
    provider = "mock"
    model = "local-inquiry-patterns-v1"

    def complete(self, messages):
        from workflows.inquiry_analysis import PATTERNS
        payload = json.loads(messages[1]["content"])
        fields = []
        for name, pattern in PATTERNS.items():
            for match in re.finditer(pattern, payload["inquiry"], re.I):
                fields.append({"name": name, "value": match.group(), "start": match.start(), "end": match.end()})
        return json.dumps({"fields": fields[:20]}, ensure_ascii=False)
