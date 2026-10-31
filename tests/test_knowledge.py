from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from core.errors import CrossBotError, ErrorCode
from core.schemas import ListingRequest, SalesRequest
from knowledge.retrieval import FILES, citation_notes, load_entries, retrieve_context
from providers.listing_mock import MockListingClient
from providers.mock_client import MockChatClient
from workflows.conversation import revise_sales, start_sales
from workflows.listing import generate_listings
from workflows.sales import generate_sales


class KnowledgeTests(unittest.TestCase):
    def test_all_entries_have_provenance_and_never_look_official(self):
        records, hashes = load_entries()
        self.assertEqual(len(records), 13)
        self.assertEqual(set(hashes), set(FILES))
        for record in records:
            self.assertEqual(record["data_kind"], "synthetic")
            self.assertFalse(record["source"]["official"])
            self.assertTrue(record["version"])
            self.assertIn("effective_from", record)
            self.assertIn("effective_until", record)

    def test_exact_product_has_citations_and_unknown_commercial_facts(self):
        context = retrieve_context("demo-lamp-001\n产品资料")
        self.assertEqual(context["product_status"], "reference_only")
        product = next(x for x in context["entries"] if x["type"] == "product")
        for field in ["certifications", "price", "lead_time"]:
            self.assertIsNone(product["facts"][field])
        citation = "\n".join(citation_notes(context))
        for text in ["DEMO-LAMP-001", "knowledge/README.md", "demo-1", "生效未知", "适用", "SHA256"]:
            self.assertIn(text, citation)

    def test_animal_lamp_knowledge_is_exactly_scoped_and_marks_unknowns(self):
        context = retrieve_context("DEMO-ANIMAL-LAMP-001", platforms=["Amazon"], country="Germany")
        entries = {entry["id"]: entry for entry in context["entries"]}
        product = entries["DEMO-ANIMAL-LAMP-001"]
        self.assertEqual(product["facts"]["power_w"], 3)
        self.assertEqual(product["facts"]["battery_capacity_mah"], 1200)
        self.assertIsNone(product["facts"]["certifications"])
        self.assertEqual(product["data_kind"], "synthetic")
        self.assertFalse(product["source"]["official"])
        self.assertIn("ANIMAL-LAMP-AMAZON", entries)
        self.assertNotIn("ANIMAL-LAMP-TIKTOK", entries)
        self.assertEqual(context["wording_rules"]["version"], "animal-lamp-demo-1")
        self.assertIn("prohibited_terms.json", context["source_hashes"])

    def test_generic_partial_unknown_and_ambiguous_skus_do_not_establish_identity(self):
        for text in ["USB台灯 白色", "DEMO-LAMP-00", "DEMO-LAMP-001-OLD", "DEMO-UNKNOWN-123",
                     "DEMO-LAMP-001 DEMO-CUP-001", "DEMO-LAMP-001 DEMO-UNKNOWN-123"]:
            with self.subTest(text=text):
                context = retrieve_context(text)
                self.assertEqual(context["product_status"], "unknown")
                self.assertFalse(any(x["type"] == "product" for x in context["entries"]))
                self.assertTrue(any("产品资料未知" in x for x in context["notes"]))

    def test_platform_and_country_scopes_are_explicit(self):
        context = retrieve_context("DEMO-LAMP-001", platforms=["Amazon"], country="德国")
        ids = {x["id"] for x in context["entries"]}
        self.assertIn("DE-DEMO-REVIEW", ids)
        self.assertIn("STYLE-AMAZON", ids)
        self.assertNotIn("STYLE-TIKTOK", ids)
        for country in [None, "US"]:
            context = retrieve_context("DEMO-LAMP-001", country=country)
            self.assertFalse(any(x["type"] == "country" for x in context["entries"]))

    def test_unknown_platform_and_country_are_marked_unknown(self):
        context = retrieve_context("DEMO-CUP-001", platforms=["Unknown"], country="XX")
        self.assertTrue(any("平台资料未知" in x for x in context["notes"]))
        self.assertTrue(any("目标国资料未知" in x for x in context["notes"]))

    def test_expired_and_future_products_are_not_used(self):
        original, hashes = load_entries()
        for start, end in [(None, "2025-12-31"), ("2027-01-01", None)]:
            records = deepcopy(original)
            product = next(x for x in records if x["id"] == "DEMO-LAMP-001")
            product.update(effective_from=start, effective_until=end)
            with patch("knowledge.retrieval.load_entries", return_value=(records, hashes)):
                context = retrieve_context("DEMO-LAMP-001", as_of=date(2026, 10, 31))
            self.assertEqual(context["product_status"], "unknown")

    def test_returned_records_are_snapshots(self):
        context = retrieve_context("DEMO-LAMP-001")
        context["entries"][0]["content"] = "edited"
        self.assertNotEqual(retrieve_context("DEMO-LAMP-001")["entries"][0]["content"], "edited")

    def test_corrupt_or_missing_knowledge_blocks_client_not_unknown_fallback(self):
        with TemporaryDirectory() as folder, patch("knowledge.retrieval.ROOT", Path(folder)), patch("knowledge.wording_rules.RULE_FILE", Path(folder) / "prohibited_terms.json"):
            for content in [None, b'{broken', b'{"schema_version":1,"entries":[{}]}']:
                if content is not None:
                    (Path(folder) / "products.json").write_bytes(content)
                client = Mock()
                with self.assertRaises(CrossBotError) as caught:
                    generate_sales(SalesRequest("q", "DEMO-LAMP-001", "en"), client)
                self.assertEqual(caught.exception.code, ErrorCode.PARSE_FAILED)
                client.complete.assert_not_called()

    def test_missing_or_malformed_wording_rules_block_generation(self):
        with TemporaryDirectory() as folder, patch("knowledge.wording_rules.RULE_FILE", Path(folder) / "prohibited_terms.json"):
            client = Mock()
            with self.assertRaises(CrossBotError):
                generate_sales(SalesRequest("q", "DEMO-ANIMAL-LAMP-001", "en"), client)
            client.complete.assert_not_called()

    def test_metadata_missing_invalid_date_or_official_claim_is_not_silently_used(self):
        original, _ = load_entries()
        for change in [{"version": ""}, {"effective_from": "bad"}, {"data_kind": "real"},
                       {"effective_from": "2027-01-01", "effective_until": "2026-01-01"}]:
            with TemporaryDirectory() as folder:
                for name in FILES:
                    path = Path(__file__).parents[1] / "knowledge" / name
                    data = json.loads(path.read_bytes())
                    if name == "products.json":
                        data["entries"][0].update(change)
                    (Path(folder) / name).write_text(json.dumps(data), encoding="utf-8")
                with patch("knowledge.retrieval.ROOT", Path(folder)), self.assertRaises(CrossBotError):
                    load_entries()

    def test_w1_each_revision_and_w2_client_receive_scoped_context(self):
        client = Mock(wraps=MockChatClient())
        conversation = start_sales(SalesRequest("Ship DE", "DEMO-LAMP-001", "en"), client)
        conversation = revise_sales(conversation, "please", client)
        conversation = revise_sales(conversation, "confirm facts", client)
        for call in client.complete.call_args_list:
            payload = json.loads(call.args[0][1]["content"])
            self.assertEqual(payload["knowledge_context"]["product_status"], "reference_only")
            self.assertFalse(any(x["type"] == "country" for x in payload["knowledge_context"]["entries"]))
        self.assertTrue(all(any("模拟引用 DEMO-LAMP-001" in n for n in t.response.pending_confirmations) for t in conversation.turns))
        listing_client = Mock(wraps=MockListingClient())
        result = generate_listings(ListingRequest("DEMO-CUP-001", ["TikTok"]), listing_client)
        payload = json.loads(listing_client.complete.call_args.args[0][1]["content"])
        ids = {x["id"] for x in payload["knowledge_context"]["entries"]}
        self.assertIn("STYLE-TIKTOK", ids)
        self.assertNotIn("STYLE-AMAZON", ids)
        self.assertTrue(any("DEMO-CUP-001" in x for x in result["TikTok"].pending_confirmations))


if __name__ == "__main__":
    unittest.main()
