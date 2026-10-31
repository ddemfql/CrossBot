"""B providers behind real A authorization and temporary SQLite; no external network."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from auth import AuthService
from core.errors import CrossBotError
from core.identity import Role
from core.models import Case, CaseStatus, Document, DocumentVersion, to_dict
from core.schemas import SalesRequest, ListingRequest
from core.v3 import RequestContext, AttachmentRef, AttachmentContent, AttachmentStatus, FieldRevision, EvidenceLocation, ResourceKind, SourceKind, SearchCitation
from documents.access import AttachmentService
from knowledge.governance import KnowledgeService
from providers.mock_client import MockChatClient
from storage.sqlite_store import SQLiteStore
from workflows.v3_service import BWorkflowService, artifact_kind, listing_contents, search_customer


class BIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="crossbot-b-test-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.path)
        self.auth = AuthService(self.store)
        self.auth.bootstrap_admin("admin", "temporary-test-password")
        self.admin = self.auth.login("admin", "temporary-test-password")
        self.tokens = {}
        self.users = {}
        for role in (Role.CUSTOMER_SERVICE, Role.OPERATIONS, Role.REVIEWER):
            user = self.auth.create_user(self.admin, role.value, "temporary-test-password", role.value, [role])
            self.users[role] = user
            self.tokens[role] = self.auth.login(role.value, "temporary-test-password")
        self.case = Case(case_id="B-CASE", case_number="B-CASE", customer_name="Fixture customer", destination_country="Germany")
        self.other = Case(case_id="OTHER", case_number="OTHER", customer_name="Other fixture", destination_country="Germany")
        self.store.create_case(self.case)
        self.store.create_case(self.other)
        for user in self.users.values():
            self.auth.assign_case(self.admin, self.case.case_id, user.user_id)
        self.service = BWorkflowService(self.auth)

    def request(self):
        return SalesRequest("Need 500 lamps", "USB ABS lamp", "English", "Germany", 500,
                            context=RequestContext(self.case.case_id))

    def model(self, text="Please confirm packaging requirements."):
        client = Mock(provider="real", model="offline-double")
        client.complete.return_value = text
        return client

    def test_all_b_model_paths_reject_unauthorized_before_client(self):
        client = self.model()
        cs = self.tokens[Role.CUSTOMER_SERVICE]
        for operation in [lambda: self.service.sales(None, self.request(), client),
                          lambda: self.service.candidates(self.tokens[Role.OPERATIONS], self.request(), client),
                          lambda: self.service.customer(self.tokens[Role.OPERATIONS], self.request(), client),
                          lambda: self.service.listing(cs, ListingRequest("lamp", ["Amazon"], context=RequestContext(self.case.case_id)), client)]:
            with self.assertRaises(CrossBotError): operation()
        bad = self.request(); bad.context.case_id = self.other.case_id
        with self.assertRaises(CrossBotError): self.service.sales(cs, bad, client)
        bad = self.request(); bad.context.actor_id = "forged-user"
        with self.assertRaises(CrossBotError): self.service.sales(cs, bad, client)
        client.complete.assert_not_called()

    def test_sales_two_revisions_survive_reopen_and_no_token_or_demo_json_to_model(self):
        token = self.tokens[Role.CUSTOMER_SERVICE]; request = self.request(); client = self.model()
        with patch("knowledge.retrieval.load_entries", side_effect=AssertionError("demo JSON must not be read")):
            first = self.service.sales(token, request, client)
            second = self.service.sales(token, request, client, feedback="polite", parent=first.artifact_id)
            third = self.service.sales(token, request, client, feedback="concise", parent=second.artifact_id)
        payload = json.loads(client.complete.call_args.args[0][1]["content"])
        self.assertEqual(payload["knowledge_context"]["kind"], "published")
        self.assertEqual(payload["knowledge_context"]["entries"], [])
        self.assertNotIn(token, json.dumps(payload))
        self.assertEqual(payload["context"]["actor_id"], self.users[Role.CUSTOMER_SERVICE].user_id)
        reopened = BWorkflowService(AuthService(SQLiteStore(self.path)))
        self.assertEqual(len(reopened.generations.list_for_case(token, self.case.case_id)), 3)
        messages = self.store.get_conversation(self.case.case_id, "sales").messages
        self.assertEqual(messages[-1]["parent_artifact_id"], second.artifact_id)
        self.assertEqual(messages[-2]["feedback"], "concise")
        exported = reopened.export_case(token, self.case.case_id)
        self.assertEqual(exported["conversation_indexes"]["sales"]["messages"][-2]["feedback"], "concise")

    def test_model_failure_and_changed_revision_input_preserve_history(self):
        token = self.tokens[Role.CUSTOMER_SERVICE]; request = self.request()
        first = self.service.sales(token, request, self.model())
        client = self.model(); client.complete.side_effect = RuntimeError("private response")
        with self.assertRaises(CrossBotError): self.service.sales(token, request, client, feedback="x", parent=first.artifact_id)
        request.product_info = "changed"
        client.reset_mock()
        with self.assertRaises(CrossBotError): self.service.sales(token, request, client, feedback="x", parent=first.artifact_id)
        client.complete.assert_not_called()
        self.assertEqual(len(self.service.generations.list_for_case(token, self.case.case_id)), 1)

    def test_listing_batch_scope_validated_before_persisting(self):
        token = self.tokens[Role.OPERATIONS]
        request = ListingRequest("USB ABS lamp", ["Amazon", "TikTok"], "Germany", context=RequestContext(self.case.case_id))
        client = self.model(json.dumps({"listings": {"Amazon": "Lamp title", "TikTok": "Video opening"}}))
        artifact = self.service.listing(token, request, client)
        self.assertEqual(listing_contents(artifact), {"Amazon": "Lamp title", "TikTok": "Video opening"})
        self.assertFalse(self.service.current_review(token, artifact))
        client.complete.return_value = '{"listings":{"Amazon":"Only one"}}'
        with self.assertRaises(CrossBotError): self.service.listing(token, request, client)
        self.assertEqual(len(self.service.generations.list_for_case(token, self.case.case_id)), 1)

    def test_published_product_only_and_draft_not_used(self):
        knowledge = KnowledgeService(self.auth)
        first = knowledge.create_draft(self.admin, resource_id="verified-product", kind=ResourceKind.PRODUCT,
            content={"name": "USB lamp", "sku": "FIXTURE"}, source="Test-only verified source", source_kind=SourceKind.LOCAL)
        knowledge.submit(self.admin, first.version_id)
        knowledge.review(self.tokens[Role.REVIEWER], first.version_id, approve=True, reason="Temporary fixture checked")
        knowledge.publish(self.admin, first.version_id)
        knowledge.create_draft(self.admin, resource_id="unpublished", kind=ResourceKind.KNOWLEDGE,
            content={"text": "must not leak", "category": "fixture"}, source="fixture")
        request = self.request(); request.context.product_version_id = first.version_id
        client = self.model();self.service.sales(self.tokens[Role.CUSTOMER_SERVICE], request, client)
        context = json.loads(client.complete.call_args.args[0][1]["content"])["knowledge_context"]
        self.assertEqual([v["version_id"] for v in context["entries"]], [first.version_id])
        self.assertNotIn("must not leak", json.dumps(context))
        second = knowledge.create_draft(self.admin, resource_id=first.resource_id, kind=ResourceKind.PRODUCT,
            content={"name": "USB lamp updated", "sku": "FIXTURE"}, source="New fixture version", parent_version_id=first.version_id)
        knowledge.submit(self.admin, second.version_id)
        knowledge.review(self.tokens[Role.REVIEWER], second.version_id, approve=True, reason="Updated fixture checked")
        knowledge.publish(self.admin, second.version_id)
        from knowledge.published import published_context
        after = published_context(self.auth, self.tokens[Role.CUSTOMER_SERVICE], request)
        self.assertEqual(after["source_hashes"][first.version_id], context["source_hashes"][first.version_id])
        from streamlit.testing.v1 import AppTest
        with patch("storage.bootstrap.open_store", return_value=self.store):
            page = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20)
            page.session_state["session_token"] = self.tokens[Role.CUSTOMER_SERVICE]
            page.run()
            page.selectbox(key="v3_product_version").set_value(first.version_id).run()
            self.assertFalse(page.exception)
            self.assertEqual(page.selectbox(key="v3_product_version").value, first.version_id)

    def test_attachment_candidates_not_promoted_and_failed_source_blocks_model(self):
        attachments = AttachmentService(self.auth)
        document = Document(case_id=self.case.case_id)
        version = DocumentVersion(document_id=document.document_id, sha256=hashlib.sha256(b"temporary fixture").hexdigest())
        document.versions = [version]; document.current_version_id = version.version_id
        attachments.save_document(self.admin, document)
        reference = AttachmentRef(document.document_id, version.version_id)
        parsed = AttachmentContent(self.case.case_id, reference, "xlsx", status=AttachmentStatus.READY,
            confidence=0.99, text="Quantity 480", fields=[FieldRevision("quantity", raw_value="480", candidate_value=480,
                location=EvidenceLocation(sheet_name="Packing", cell_reference="B2"))])
        attachments.register(self.admin, parsed)
        request = self.request(); request.context.attachments = [reference]
        client = self.model();self.service.sales(self.tokens[Role.CUSTOMER_SERVICE], request, client)
        payload = json.loads(client.complete.call_args.args[0][1]["content"])
        self.assertEqual(payload["context"]["confirmed_fields"], [])
        self.assertEqual(payload["attachment_context"][0]["fields"][0]["candidate_value"], 480)
        self.assertEqual(payload["attachment_context"][0]["fields"][0]["location"]["cell_reference"], "B2")
        request.context.attachments = [AttachmentRef("forged-document", reference.version_id)]
        client.reset_mock()
        with self.assertRaises(CrossBotError): self.service.sales(self.tokens[Role.CUSTOMER_SERVICE], request, client)
        client.complete.assert_not_called()

    def test_manual_origin_parent_independent_review_and_original_are_preserved(self):
        token = self.tokens[Role.CUSTOMER_SERVICE]
        original = self.service.sales(token, self.request(), self.model())
        manual = self.service.manual_edit(token, original.artifact_id, "Dear Customer. Internal note: review this.")
        self.assertEqual(manual.origin, "manual")
        self.assertEqual(manual.parent_artifact_id, original.artifact_id)
        self.assertIn("internal_note_in_customer_text", {r.code for r in self.service.current_review(token, manual)})
        self.assertEqual(self.service.generations.read(token, original.artifact_id).content, original.content)
        with self.assertRaises(CrossBotError): self.service.review(token, manual.artifact_id, approve=True, reason="self")
        approved = self.service.review(self.tokens[Role.REVIEWER], manual.artifact_id, approve=False, reason="Remove internal note")
        self.assertEqual(approved.review_status, "returned")

    def test_search_is_authorized_and_demo_is_never_external_reference(self):
        adapter = Mock(return_value=[SearchCitation("Fixture", "https://example.com", datetime.now(timezone.utc))])
        with self.assertRaises(CrossBotError): search_customer(self.auth, self.tokens[Role.OPERATIONS], self.case.case_id, "Fixture", "Test", adapter=adapter)
        adapter.assert_not_called()
        token = self.tokens[Role.CUSTOMER_SERVICE]
        with self.assertRaises(CrossBotError): search_customer(self.auth, token, self.case.case_id, "Fixture", "Test")
        report = search_customer(self.auth, token, self.case.case_id, "Fixture", "Demo", demo=True)
        self.assertEqual(report.source_kind, SourceKind.DEMO)
        self.assertIsNone(report.citations[0].url)
        actual = search_customer(self.auth, token, self.case.case_id, "Fixture", "Test adapter", adapter=adapter)
        self.assertEqual(actual.source_kind, SourceKind.EXTERNAL_REFERENCE)
        adapter.assert_called_once_with("Fixture")

    def test_export_is_case_scoped_and_never_contains_session_token(self):
        token = self.tokens[Role.CUSTOMER_SERVICE]
        self.service.sales(token, self.request(), self.model())
        bundle = self.service.export_case(token, self.case.case_id)
        self.assertEqual(bundle["case_id"], self.case.case_id)
        self.assertNotIn(token, json.dumps(bundle))
        self.assertEqual(len(bundle["artifacts"]), 1)
        with self.assertRaises(CrossBotError): self.service.export_case(token, self.other.case_id)

    def test_closed_case_stops_before_model(self):
        self.case.status = CaseStatus.CLOSED; self.store.save_case(self.case)
        client = self.model()
        with self.assertRaises(CrossBotError): self.service.sales(self.tokens[Role.CUSTOMER_SERVICE], self.request(), client)
        client.complete.assert_not_called()

    def test_knowledge_ui_cannot_self_review_or_publish_demo(self):
        from streamlit.testing.v1 import AppTest
        app_path = Path(__file__).parents[1] / "app.py"
        with patch("storage.bootstrap.open_store", return_value=self.store):
            page = AppTest.from_file(str(app_path), default_timeout=20)
            page.session_state["session_token"] = self.admin
            page.run()
            self.assertFalse(page.exception)
            page.text_input(key="v3_resource_id").set_value("ui-demo")
            next(w for w in page.text_input if w.label == "产品名称").set_value("UI fixture lamp")
            next(w for w in page.text_input if w.label == "资料来源与依据").set_value("Demonstration only")
            next(b for b in page.button if b.label == "保存新草稿").click().run()
            self.assertFalse(page.exception)
            version = KnowledgeService(self.auth).list_versions(self.admin)[0]
            self.assertEqual(version.source_kind, SourceKind.DEMO)
            page.button(key="v3_resource_submit").click().run()
            self.assertTrue(page.button(key="v3_resource_review_True").disabled)
            page.session_state["session_token"] = self.tokens[Role.REVIEWER]
            page.run()
            page.text_input(key="v3_resource_reason").set_value("Demo reviewed only").run()
            page.button(key="v3_resource_review_True").click().run()
            self.assertFalse(page.exception)
            page.session_state["session_token"] = self.admin
            page.run()
            self.assertTrue(page.button(key="v3_resource_publish").disabled)
            with self.assertRaises(CrossBotError): KnowledgeService(self.auth).publish(self.admin, version.version_id)

    def test_case_switch_clears_forms_and_revocation_hides_sensitive_ui(self):
        from streamlit.testing.v1 import AppTest
        token = self.tokens[Role.CUSTOMER_SERVICE]
        self.auth.assign_case(self.admin, self.other.case_id, self.users[Role.CUSTOMER_SERVICE].user_id)
        with patch("storage.bootstrap.open_store", return_value=self.store), patch("providers.llm_client.HttpChatClient.complete") as provider:
            page = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20)
            page.session_state["session_token"] = token
            page.run()
            page.text_area(key="v3_inquiry").set_value("Private Case A message").run()
            next(w for w in page.selectbox if w.label == "当前 Case").set_value(self.other.case_id).run()
            self.assertFalse(page.exception)
            self.assertEqual(page.text_area(key="v3_inquiry").value, "")
            self.auth.revoke_case(self.admin, self.case.case_id, self.users[Role.CUSTOMER_SERVICE].user_id)
            self.auth.revoke_case(self.admin, self.other.case_id, self.users[Role.CUSTOMER_SERVICE].user_id)
            page.run()
            self.assertFalse(page.exception)
            self.assertFalse(any(w.label == "客户询盘" for w in page.text_area))
            provider.assert_not_called()
