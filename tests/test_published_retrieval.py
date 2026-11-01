import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from auth import AuthService
from auth.cases import CaseAccessService
from core.errors import CrossBotError, ValidationError
from core.identity import Role
from core.models import Case, RiskItem
from core.schemas import ListingRequest
from core.v3 import RequestContext
from knowledge.governance import KnowledgeService
from knowledge.published_retrieval import retrieve_published, review_published_terms, platform_policy_resources
from storage.sqlite_store import SQLiteStore
from workflows.access import GenerationService
from workflows.listing import generate_listings, revise_listings


class PublishedRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.auth = AuthService(SQLiteStore(Path(self.directory.name) / "synthetic.db"))
        password = "synthetic-password-123"
        self.auth.bootstrap_admin("admin", password)
        self.admin = self.auth.login("admin", password)
        self.tokens = {}
        for role in (Role.PRODUCT, Role.REVIEWER, Role.OPERATIONS, Role.DOCUMENT):
            self.auth.create_user(self.admin, role.value, password, role.value, [role])
            self.tokens[role] = self.auth.login(role.value, password)
        self.service = KnowledgeService(self.auth)

    def publish(self, resource_id, kind, content, *, parent=None, **scope):
        version = self.service.create_draft(
            self.tokens[Role.PRODUCT], resource_id=resource_id, kind=kind, content=content,
            source="Synthetic approved fixture", source_url="https://example.invalid/fixture",
            parent_version_id=parent, **scope)
        self.service.submit(self.tokens[Role.PRODUCT], version.version_id)
        self.service.review(self.tokens[Role.REVIEWER], version.version_id, approve=True, reason="Synthetic fixture check")
        self.service.publish(self.admin, version.version_id)
        return version

    def retrieve(self, query="LAMP-001", **options):
        return retrieve_published(self.auth, self.tokens[Role.OPERATIONS], query, **options)

    def test_only_public_service_and_string_kind_snapshots(self):
        version = self.publish("lamp", "product", {"sku": "LAMP-001", "name": "USB desk lamp"})
        with patch.object(KnowledgeService, "list_published", autospec=True, wraps=None, return_value=[version]) as listing:
            result = self.retrieve()
        listing.assert_called_once()
        self.assertEqual(result[0]["kind"], "product")
        for key in ("resource_id", "version_id", "content", "source", "source_url", "platform", "market", "product_scope"):
            self.assertIn(key, result[0])
        result[0]["content"]["name"] = "changed"
        self.assertEqual(self.retrieve()[0]["content"]["name"], "USB desk lamp")

    def test_draft_latest_publication_and_rollback(self):
        original = self.publish("lamp", "product", {"sku": "LAMP-001", "name": "Original"})
        draft = self.service.create_draft(self.tokens[Role.PRODUCT], resource_id="lamp", kind="product",
                                         content={"sku": "LAMP-001", "name": "Draft"}, source="Synthetic",
                                         parent_version_id=original.version_id)
        self.assertEqual(self.retrieve()[0]["version_id"], original.version_id)
        self.service.submit(self.tokens[Role.PRODUCT], draft.version_id)
        self.service.review(self.tokens[Role.REVIEWER], draft.version_id, approve=True, reason="Checked")
        self.service.publish(self.admin, draft.version_id)
        self.assertEqual(self.retrieve()[0]["version_id"], draft.version_id)
        self.service.rollback(self.admin, "lamp", original.version_id, reason="Synthetic rollback")
        self.assertEqual(self.retrieve()[0]["version_id"], original.version_id)

    def test_unique_keyword_exact_sku_and_ambiguous_products(self):
        self.publish("lamp", "product", {"sku": "LAMP-001", "name": "Desk lamp"})
        self.assertEqual(len(self.retrieve("need a desk lamp", kind="product")), 1)
        self.assertEqual(len(self.retrieve("LAMP-001 USB-C desk lamp", kind="product")), 1)
        for query in ("LAMP-001-OLD", "LAMP-00", "OTHER-001 desk lamp", "LAMP-001 OTHER-001"):
            self.assertEqual(self.retrieve(query, kind="product"), [])
        self.publish("lamp-two", "product", {"sku": "LAMP-002", "name": "Floor lamp"})
        self.assertEqual(self.retrieve("lamp", kind="product"), [])
        self.assertEqual(self.retrieve("LAMP-001 LAMP-002", kind="product"), [])

    def test_product_scoped_knowledge_and_terms_never_cross_skus(self):
        self.publish("lamp", "product", {"sku": "LAMP-001", "name": "Desk lamp"})
        self.publish("lamp-facts", "knowledge", {"text": "Lamp guidance"}, product_scope="LAMP-001")
        self.publish("other-facts", "knowledge", {"text": "Lamp other product"}, product_scope="CUP-002")
        self.publish("scoped-term", "prohibited_term", {"term": "miracle"}, product_scope="LAMP-001")
        self.assertEqual({record["resource_id"] for record in self.retrieve(limit=None)}, {"lamp", "lamp-facts", "scoped-term"})
        self.assertEqual(self.retrieve("unknown", limit=None), [])
        self.assertEqual(self.retrieve("CUP-002 lamp", limit=None), [])

    def test_market_platform_dates_and_unknown_scope(self):
        self.publish("us-term", "prohibited_term", {"term": "miracle"}, platform="TikTok Shop US", market="United States")
        self.publish("future", "prohibited_term", {"term": "miracle"}, effective_date="2999-01-01")
        self.publish("expired", "prohibited_term", {"term": "miracle", "effective_until": "2000-01-01"})
        self.assertEqual(len(self.retrieve(platform="TikTok", country="美国")), 1)
        for platform, country in (("TikTok", None), ("Amazon", "US"), ("TikTok", "DE"), (None, "US")):
            self.assertEqual(self.retrieve(platform=platform, country=country), [])

    def test_unscoped_knowledge_mentioning_another_sku_is_excluded(self):
        self.publish("lamp", "product", {"sku": "LAMP-001", "name": "Desk lamp"})
        self.publish("unscoped-other", "knowledge", {"text": "lamp uses CUP-002 facts"})
        self.publish("explicit-other", "knowledge", {"text": "lamp guidance", "sku": "CUP-002"})
        self.assertEqual({record["resource_id"] for record in self.retrieve("LAMP-001 lamp")}, {"lamp"})
        self.assertEqual(self.retrieve("unknown lamp CUP-002", kind="knowledge"), [])

    def test_unknown_unseparated_sku_does_not_fall_back_to_product_keywords(self):
        self.publish("lamp", "product", {"sku": "LAMP001", "name": "Desk lamp"})
        self.assertEqual(self.retrieve("Send specifications for OTHER999 desk lamp", kind="product"), [])
        self.assertEqual(self.retrieve("LAMP001 OTHER999 desk lamp", kind="product"), [])

    def test_unscoped_knowledge_with_other_unseparated_sku_is_excluded(self):
        self.publish("lamp", "product", {"sku": "LAMP001", "name": "Desk lamp"})
        self.publish("other-knowledge", "knowledge", {"text": "OTHER999 lamp has IP68 certification"})
        records = self.retrieve("LAMP001 lamp certification", limit=None)
        self.assertEqual({record["resource_id"] for record in records}, {"lamp"})

    def test_general_specifications_do_not_block_product_or_knowledge_matches(self):
        self.publish("lamp", "product", {"sku": "LAMP001", "name": "Desk lamp"})
        self.publish("lamp-knowledge", "knowledge", {"text": "LAMP001 lamp: IP68, 1200mAh, USB-C, 5V"})
        for query in ("LAMP001 lamp IP68 1200mAh USB-C 5V", "Desk lamp IP68 1200mAh USB-C 5V",
                      "Desk lamp models with IP68 1200mAh USB-C 5V"):
            with self.subTest(query=query):
                self.assertEqual({record["resource_id"] for record in self.retrieve(query, limit=None)},
                                 {"lamp", "lamp-knowledge"})

    def test_explicit_unknown_models_and_known_plain_skus_do_not_cross_products(self):
        self.publish("lamp", "product", {"sku": "LAMP001", "name": "Desk lamp"})
        self.publish("remote-product", "product", {"sku": "OTHERPLAIN", "name": "Coffee cup"})
        self.publish("remote-knowledge", "knowledge", {"text": "OTHERPLAIN lamp certification"})
        for query in ("SKU: UNKNOWN desk lamp", "model=UNKNOWN desk lamp", "型号为UNKNOWN desk lamp"):
            with self.subTest(query=query):
                self.assertEqual(self.retrieve(query, kind="product"), [])
        self.assertEqual([record["resource_id"] for record in self.retrieve("OTHERPLAIN desk lamp", kind="product")],
                         ["remote-product"])
        self.assertEqual([record["resource_id"] for record in self.retrieve("remote-product", kind="product")],
                         ["remote-product"])
        self.assertEqual(self.retrieve("LAMP001 lamp certification", kind="knowledge"), [])

    def test_all_terms_scan_independently_of_query_and_default_limit(self):
        for index in range(10):
            self.publish(f"term-{index:02}", "prohibited_term", {"term": f"forbidden-{index}"})
        self.assertEqual(len(self.retrieve("unrelated", kind="prohibited_term")), 8)
        records = self.retrieve("unrelated", kind="prohibited_term", limit=None)
        risks = review_published_terms("forbidden-9", records)
        self.assertEqual(len(risks), 1)
        self.assertIsInstance(risks[0], RiskItem)
        self.assertIn("term-09@", risks[0].source)

    def test_term_exact_regex_synonym_and_invalid_pattern(self):
        for resource_id, content in (
            ("exact", {"term": "safe", "severity": "blocking"}),
            ("regex", {"term": r"guaranteed\s+results", "pattern_type": "regex"}),
            ("synonym", {"term": "miracle", "synonyms": ["神效"], "pattern_type": "synonym"})):
            self.publish(resource_id, "prohibited_term", content)
        records = self.retrieve(limit=None)
        self.assertEqual(review_published_terms("unsafe", records), [])
        risks = review_published_terms("safe; guaranteed results; 神效", records)
        self.assertEqual(len(risks), 3)
        self.assertTrue(any(risk.blocking for risk in risks))
        records[0]["content"] = {"term": "[", "pattern_type": "regex"}
        self.assertTrue(any(risk.code == "published_term_invalid" for risk in review_published_terms("text", records)))

    def test_unauthenticated_and_invalid_options_fail(self):
        with self.assertRaises(CrossBotError):
            retrieve_published(self.auth, "invalid", "lamp")
        for changes in ({"limit": -1}, {"limit": True}, {"limit": "8"}, {"kind": "invalid"}):
            with self.assertRaises(ValidationError):
                self.retrieve(**changes)

    def test_authorized_listing_prompt_and_persisted_risks(self):
        self.publish("lamp", "product", {"sku": "LAMP-001", "name": "Desk lamp"})
        self.publish("risk", "prohibited_term", {"term": "special claim", "severity": "blocking"})
        case = Case(case_number="SYNTHETIC", customer_name="Synthetic", destination_country="US")
        CaseAccessService(self.auth).create_case(self.admin, case)
        request = ListingRequest("LAMP-001", ["Amazon"], "US", context=RequestContext(case.case_id))
        generation = GenerationService(self.auth)
        client = Mock(provider="real", model="test-only", complete=Mock(return_value='{"listings":{"Amazon":"special claim"}}'))
        response = generate_listings(request, client, generation_service=generation, auth_token=self.admin)["Amazon"]
        self.assertEqual(response.context.case_id, case.case_id)
        self.assertTrue(any(risk.code == "published_prohibited_term" for risk in response.risks))
        artifacts = generation.list_for_case(self.admin, case.case_id)
        self.assertEqual(len(artifacts), 1)
        self.assertTrue(any(risk["blocking"] for risk in artifacts[0].risks))
        payload = json.loads(client.complete.call_args.args[0][1]["content"])
        self.assertNotIn("context", payload)
        self.assertNotIn(self.admin, json.dumps(payload))
        self.assertEqual(payload["knowledge_context"]["entries_by_platform"]["Amazon"][0]["content"]["sku"], "LAMP-001")
        with self.assertRaises(CrossBotError):
            generate_listings(request, client, generation_service=generation, auth_token=self.tokens[Role.OPERATIONS])
        client.complete.assert_called_once()

    def test_revision_reloads_new_publication_and_keeps_old_response(self):
        old = self.publish("risk", "prohibited_term", {"term": "old phrase"})
        request = ListingRequest("Unknown product", ["Amazon"], "US")
        client = Mock(provider="mock", model="synthetic", complete=Mock(return_value='{"listings":{"Amazon":"new phrase"}}'))
        original = generate_listings(request, client, auth=self.auth, auth_token=self.admin)
        self.assertFalse(any(r.code == "published_prohibited_term" for r in original["Amazon"].risks))
        latest = self.publish("risk", "prohibited_term", {"term": "new phrase"}, parent=old.version_id)
        revised = revise_listings(request, original, "Shorten", client, auth=self.auth, auth_token=self.admin)
        self.assertTrue(any(latest.version_id in risk.source for risk in revised["Amazon"].risks))
        self.assertFalse(original["Amazon"].risks)
        payload = json.loads(client.complete.call_args.args[0][1]["content"])
        self.assertEqual(payload["request_kind"], "revision")
        self.assertEqual(payload["previous_listings"], {"Amazon": "new phrase"})


class LocalPlatformPolicyTests(unittest.TestCase):
    def test_market_filter_and_pending_tiktok_verification(self):
        amazon = platform_policy_resources("Amazon", "US")
        self.assertTrue(amazon)
        pending = [record for record in amazon if record["resource_id"].startswith("amazon-us-")]
        self.assertTrue(pending)
        self.assertTrue(all(record["verification_status"] == "pending_primary_text_verification" for record in pending))
        self.assertTrue(all("unverified" in record["source"] for record in pending))
        guides = [record for record in amazon if record["resource_id"] == "amazon-listing-guide"]
        self.assertTrue(guides)
        self.assertEqual(guides[0]["verification_status"], "primary_text_verified")
        self.assertEqual(platform_policy_resources("Amazon", "DE"), [])
        self.assertEqual(platform_policy_resources("TikTok", None), [])
        self.assertTrue(platform_policy_resources("阿里国际站", None))
        tiktok = platform_policy_resources("TikTok", "US")
        self.assertTrue(all(record["verification_status"] == "pending_latest_verification" for record in tiktok))
        self.assertTrue(all(record["effective_date"] is None for record in tiktok))
        self.assertTrue(all(not record["is_product_fact"] and not record["official_word_list"] for record in tiktok))

    def test_no_database_local_rules_still_scan(self):
        client = Mock(provider="mock", model="synthetic")
        examples = [("Amazon", "Contact us at https://example.invalid and leave a review"),
                    ("TikTok", "A miracle with guaranteed results, certified"),
                    ("阿里国际站", "world's best replica")]
        for platform, text in examples:
            with self.subTest(platform=platform):
                client.complete.return_value = json.dumps({"listings": {platform: text}})
                response = generate_listings(ListingRequest("Unknown", [platform], "US"), client)[platform]
                self.assertTrue(any("https://" in risk.source for risk in response.risks))
                self.assertTrue(any("not_connected" in note for note in response.pending_confirmations))

    def test_policy_failure_prevents_model_call(self):
        client = Mock()
        with patch("knowledge.published_retrieval.POLICY_FILE", Path("missing-policy.json")):
            with self.assertRaises(CrossBotError):
                generate_listings(ListingRequest("Unknown", ["Amazon"], "US"), client)
        client.complete.assert_not_called()

    def test_case_context_without_service_and_bad_revision_prevent_call(self):
        client = Mock()
        with self.assertRaises(ValidationError):
            generate_listings(ListingRequest("Product", ["Amazon"], context=RequestContext("case")), client)
        for feedback, previous in (("", {"Amazon": "old"}), ("Change", {}), (None, {"Amazon": "old"})):
            with self.assertRaises(ValidationError):
                generate_listings(ListingRequest("Product", ["Amazon"]), client,
                                  feedback=feedback, previous_listings=previous)
        client.complete.assert_not_called()
