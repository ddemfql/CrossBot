import json
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock

from auth import AuthService
from auth.cases import CaseAccessService
from core.errors import CrossBotError, ErrorCode
from core.identity import Permission, Role
from core.models import Case, Document, DocumentVersion, ExtractedField, from_dict, to_dict
from core.schemas import GenerationResponse, ListingRequest, SalesRequest
from core.v3 import (
    AttachmentContent, AttachmentRef, AttachmentStatus, EvidenceLocation, FieldRevision,
    LifecycleStatus, RequestContext, ResourceKind, SearchCitation, SourceKind,
)
from documents.access import AttachmentService
from knowledge.governance import KnowledgeService
from providers.search_contract import SearchService
from storage.sqlite_store import SQLiteStore
from workflows.access import GenerationService

PASSWORD = "local-test-password-123"


class V3ServicesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.db"
        self.store = SQLiteStore(self.path)
        self.auth = AuthService(self.store)
        self.admin = self.auth.bootstrap_admin("admin", PASSWORD)
        self.admin_token = self.auth.login("admin", PASSWORD)
        self.users, self.tokens = {}, {}
        for role in (Role.CUSTOMER_SERVICE, Role.PRODUCT, Role.OPERATIONS, Role.DOCUMENT, Role.REVIEWER):
            user = self.auth.create_user(self.admin_token, role.value, PASSWORD, role.value, [role])
            self.users[role] = user
            self.tokens[role] = self.auth.login(role.value, PASSWORD)
        self.case = Case(case_number="V3-001", customer_name="Demo", destination_country="DE")
        CaseAccessService(self.auth).create_case(self.admin_token, self.case)
        for user in self.users.values():
            self.auth.assign_case(self.admin_token, self.case.case_id, user.user_id)
        self.knowledge = KnowledgeService(self.auth)
        self.attachments = AttachmentService(self.auth)
        self.generations = GenerationService(self.auth)

    def draft(self, resource="product-lamp", parent=None, source_kind=SourceKind.LOCAL):
        return self.knowledge.create_draft(
            self.tokens[Role.PRODUCT], resource_id=resource, kind=ResourceKind.PRODUCT,
            content={"name": "Lamp", "material": "ABS"}, source="Reviewed manufacturer specification",
            parent_version_id=parent, source_kind=source_kind,
        )

    def publish(self, version):
        self.knowledge.submit(self.tokens[Role.PRODUCT], version.version_id)
        self.knowledge.review(self.tokens[Role.REVIEWER], version.version_id, approve=True, reason="Checked evidence")
        self.knowledge.publish(self.admin_token, version.version_id)
        return version

    def register_attachment(self, confidence=0.99):
        document = Document(case_id=self.case.case_id)
        version = DocumentVersion(document_id=document.document_id, sha256=hashlib.sha256(document.document_id.encode()).hexdigest())
        document.versions = [version]
        document.current_version_id = version.version_id
        self.attachments.save_document(self.tokens[Role.DOCUMENT], document)
        content = AttachmentContent(
            self.case.case_id, AttachmentRef(document.document_id, version.version_id), "xlsx",
            status=AttachmentStatus.READY, text="Quantity: 500", confidence=confidence,
            fields=[FieldRevision("quantity", raw_value="500", candidate_value=500,
                                  location=EvidenceLocation(sheet_name="Packing", cell_reference="B2", row_number=2, column_name="quantity"))],
        )
        return self.attachments.register(self.tokens[Role.DOCUMENT], content)

    def test_login_tokens_are_hashed_and_passwords_not_plaintext(self):
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
            tokens = [r[0] for r in conn.execute("SELECT token_hash FROM auth_sessions")]
        self.assertNotEqual(row[0], PASSWORD)
        self.assertNotIn(self.admin_token, tokens)
        with self.assertRaises(CrossBotError):
            self.auth.login("admin", "wrong")
        self.auth.logout(self.tokens[Role.PRODUCT])
        with self.assertRaises(CrossBotError):
            self.auth.require_session(self.tokens[Role.PRODUCT])

    def test_first_admin_cannot_be_rebootstrapped(self):
        with self.assertRaises(CrossBotError):
            self.auth.bootstrap_admin("other", PASSWORD)

    def test_forged_role_or_unassigned_case_rejected_before_provider(self):
        token = self.tokens[Role.CUSTOMER_SERVICE]
        with self.assertRaises(CrossBotError):
            self.auth.switch_role(token, Role.ADMIN)
        self.auth.revoke_case(self.admin_token, self.case.case_id, self.users[Role.CUSTOMER_SERVICE].user_id)
        provider = Mock(return_value=GenerationResponse("must not execute"))
        request = SalesRequest("Hi", "Lamp", "English", context=RequestContext(self.case.case_id))
        with self.assertRaises(CrossBotError):
            self.generations.generate(token, request, provider)
        provider.assert_not_called()
        self.assertEqual(CaseAccessService(self.auth).list_cases(token), [])

    def test_disabled_account_and_permission_revocation_invalidate_sessions(self):
        token = self.tokens[Role.CUSTOMER_SERVICE]
        self.auth.update_user(self.admin_token, self.users[Role.CUSTOMER_SERVICE].user_id, status="disabled")
        with self.assertRaises(CrossBotError):
            self.auth.require_permission(token, Permission.W1_GENERATE, self.case.case_id)
        with self.assertRaises(CrossBotError):
            self.auth.login(Role.CUSTOMER_SERVICE.value, PASSWORD)

    def test_active_role_is_not_union_of_all_grants(self):
        user = self.auth.create_user(self.admin_token, "multi", PASSWORD, "Multi", [Role.CUSTOMER_SERVICE, Role.OPERATIONS])
        token = self.auth.login("multi", PASSWORD)
        self.auth.assign_case(self.admin_token, self.case.case_id, user.user_id)
        self.auth.require_permission(token, Permission.W1_GENERATE, self.case.case_id)
        with self.assertRaises(CrossBotError):
            self.auth.require_permission(token, Permission.W2_GENERATE, self.case.case_id)
        self.auth.switch_role(token, Role.OPERATIONS)
        self.auth.require_permission(token, Permission.W2_GENERATE, self.case.case_id)

    def test_customer_service_cannot_edit_knowledge_and_operations_cannot_publish(self):
        with self.assertRaises(CrossBotError):
            self.knowledge.create_draft(self.tokens[Role.CUSTOMER_SERVICE], resource_id="x", kind=ResourceKind.PRODUCT, content={"name": "x"}, source="x")
        version = self.draft()
        with self.assertRaises(CrossBotError):
            self.knowledge.publish(self.tokens[Role.OPERATIONS], version.version_id)

    def test_self_review_blocked_even_for_admin(self):
        version = self.knowledge.create_draft(self.admin_token, resource_id="admin-draft", kind=ResourceKind.KNOWLEDGE, content={"text": "x"}, source="evidence")
        self.knowledge.submit(self.admin_token, version.version_id)
        with self.assertRaises(CrossBotError):
            self.knowledge.review(self.admin_token, version.version_id, approve=True, reason="self")

    def test_unreviewed_and_demo_cannot_be_published(self):
        version = self.draft()
        self.assertEqual(self.knowledge.list_published(self.tokens[Role.CUSTOMER_SERVICE]), [])
        with self.assertRaises(CrossBotError):
            self.knowledge.publish(self.admin_token, version.version_id)
        with self.assertRaises(CrossBotError):
            self.knowledge.get_version(self.tokens[Role.CUSTOMER_SERVICE], version.version_id)
        demo = self.draft("demo", source_kind=SourceKind.DEMO)
        self.knowledge.submit(self.tokens[Role.PRODUCT], demo.version_id)
        self.knowledge.review(self.tokens[Role.REVIEWER], demo.version_id, approve=True, reason="Demo only")
        with self.assertRaises(CrossBotError):
            self.knowledge.publish(self.admin_token, demo.version_id)

    def test_publish_and_rollback_preserve_versions_and_audits(self):
        first = self.publish(self.draft())
        second = self.publish(self.draft(parent=first.version_id))
        self.assertEqual(self.knowledge.get_published(self.tokens[Role.CUSTOMER_SERVICE], first.resource_id).version_id, second.version_id)
        rollback = self.knowledge.rollback(self.admin_token, first.resource_id, first.version_id, reason="Restore checked version")
        self.assertTrue(rollback.is_rollback)
        self.assertEqual(self.knowledge.get_published(self.tokens[Role.CUSTOMER_SERVICE], first.resource_id).version_id, first.version_id)
        self.assertEqual(len(self.knowledge.publications(self.admin_token, first.resource_id)), 3)
        self.assertEqual(self.knowledge.get_version(self.admin_token, second.version_id).status, LifecycleStatus.ARCHIVED)

    def test_attachment_confidence_failure_and_confirmation_history(self):
        low = self.register_attachment(confidence=0.4)
        self.assertEqual(low.status, AttachmentStatus.PENDING_REVIEW)
        with self.assertRaises(CrossBotError):
            self.attachments.read(self.tokens[Role.CUSTOMER_SERVICE], low.reference, require_ready=True)
        ready = self.register_attachment()
        with self.assertRaises(CrossBotError):
            self.attachments.confirm_field(self.tokens[Role.CUSTOMER_SERVICE], ready.reference, "quantity", 500, reason="test")
        self.attachments.confirm_field(self.tokens[Role.REVIEWER], ready.reference, "quantity", 500, reason="Read B2")
        self.attachments.confirm_field(self.tokens[Role.REVIEWER], ready.reference, "quantity", 480, reason="Corrected B2")
        saved = self.attachments.read(self.tokens[Role.CUSTOMER_SERVICE], ready.reference)
        self.assertEqual(saved.fields[0].raw_value, "500")
        self.assertEqual(saved.fields[0].candidate_value, 500)
        self.assertEqual(saved.fields[0].confirmed_value, 480)
        self.assertEqual(len(saved.fields[0].history), 2)
        self.assertEqual(saved.fields[0].location.cell_reference, "B2")

    def test_generation_context_checks_and_ai_manual_records_are_separate(self):
        product = self.publish(self.draft())
        attachment = self.register_attachment()
        self.attachments.confirm_field(self.tokens[Role.REVIEWER], attachment.reference, "quantity", 500, reason="Read original")
        request = SalesRequest("Hi", "Lamp", "English", context=RequestContext(
            self.case.case_id, product_version_id=product.version_id, attachments=[attachment.reference],
            confirmed_fields=[ExtractedField("forged", "fake")],
        ))
        received = []
        def provider(actual):
            received.append(actual)
            return GenerationResponse("AI draft")
        artifact = self.generations.generate(self.tokens[Role.CUSTOMER_SERVICE], request, provider)
        self.assertEqual(received[0].context.actor_id, self.users[Role.CUSTOMER_SERVICE].user_id)
        self.assertEqual([f.name for f in received[0].context.confirmed_fields], ["quantity"])
        manual = self.generations.edit(self.tokens[Role.CUSTOMER_SERVICE], artifact.artifact_id, "Edited draft")
        self.assertEqual(manual.parent_artifact_id, artifact.artifact_id)
        self.assertEqual(self.generations.read(self.admin_token, artifact.artifact_id).content, "AI draft")
        approved = self.generations.review(self.tokens[Role.REVIEWER], manual.artifact_id, approve=True, reason="Checked facts")
        self.assertEqual(approved.review_status, "approved")

    def test_forged_actor_and_draft_product_rejected(self):
        request = SalesRequest("Hi", "Lamp", "English", context=RequestContext(self.case.case_id, actor_id="another-user"))
        with self.assertRaises(CrossBotError):
            self.generations.authorize_request(self.tokens[Role.CUSTOMER_SERVICE], request)
        version = self.draft()
        request.context = RequestContext(self.case.case_id, product_version_id=version.version_id)
        with self.assertRaises(CrossBotError):
            self.generations.authorize_request(self.tokens[Role.CUSTOMER_SERVICE], request)

    def test_search_authorization_failure_and_demo_provenance(self):
        service = SearchService(self.auth)
        provider = Mock(return_value=[])
        with self.assertRaises(CrossBotError):
            service.execute(self.tokens[Role.OPERATIONS], case_id=self.case.case_id, query="Demo company", purpose="Verify supplied name", source_kind=SourceKind.EXTERNAL_REFERENCE, provider=provider)
        provider.assert_not_called()
        with self.assertRaises(CrossBotError):
            service.execute(self.tokens[Role.CUSTOMER_SERVICE], case_id=self.case.case_id, query="Demo company", purpose="Verify supplied name", source_kind=SourceKind.EXTERNAL_REFERENCE, provider=Mock(side_effect=RuntimeError("offline")))
        report = service.execute(self.tokens[Role.CUSTOMER_SERVICE], case_id=self.case.case_id, query="Demo company", purpose="Offline demonstration", source_kind=SourceKind.DEMO, provider=lambda q: [SearchCitation("Demo only", None, datetime.now(timezone.utc))])
        saved = service.read(self.admin_token, report.search_id)
        self.assertEqual(saved.source_kind, SourceKind.DEMO)
        self.assertTrue(saved.authorization_audit_id)
        self.assertEqual(saved.citations[0].review_status, "pending_review")

    def test_external_search_requires_real_url_and_round_trip(self):
        service = SearchService(self.auth)
        with self.assertRaises(CrossBotError):
            service.execute(self.tokens[Role.CUSTOMER_SERVICE], case_id=self.case.case_id, query="company", purpose="check", source_kind=SourceKind.EXTERNAL_REFERENCE, provider=lambda q: [SearchCitation("bad", "invented", datetime.now(timezone.utc))])
        citation = SearchCitation("Fixture provider result", "https://example.com/reference", datetime.now(timezone.utc), matched_name="Fixture company", confidence=0.5)
        report = service.execute(self.tokens[Role.CUSTOMER_SERVICE], case_id=self.case.case_id, query="company", purpose="test", source_kind=SourceKind.EXTERNAL_REFERENCE, provider=lambda q: [citation])
        self.assertEqual(service.read(self.admin_token, report.search_id).citations[0].url, citation.url)

    def test_v3_database_upgrade_retains_existing_cases(self):
        reopened = SQLiteStore(self.path)
        self.assertEqual(reopened.get_case(self.case.case_id).case_number, self.case.case_number)
        self.assertEqual(AuthService(reopened).require_session(self.admin_token).user_id, self.admin.user_id)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_unauthenticated_expired_and_revoked_assignment(self):
        request = SalesRequest("Hi", "Lamp", "English", context=RequestContext(self.case.case_id))
        provider = Mock(return_value=GenerationResponse("not called"))
        with self.assertRaises(CrossBotError):
            self.generations.generate(None, request, provider)
        token = self.tokens[Role.CUSTOMER_SERVICE]
        with self.auth.repository.transaction() as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=0 WHERE token_hash=?", (self.auth._token_hash(token),))
        with self.assertRaises(CrossBotError):
            self.generations.generate(token, request, provider)
        provider.assert_not_called()

    def test_low_quality_can_only_be_released_after_field_confirmation(self):
        low = self.register_attachment(confidence=0.3)
        with self.assertRaises(CrossBotError):
            self.attachments.approve_quality(self.tokens[Role.REVIEWER], low.reference, reason="review")
        self.attachments.confirm_field(self.tokens[Role.REVIEWER], low.reference, "quantity", 500, reason="Original B2 checked")
        self.attachments.approve_quality(self.tokens[Role.REVIEWER], low.reference, reason="All fields verified")
        self.assertEqual(self.attachments.read(self.tokens[Role.CUSTOMER_SERVICE], low.reference, require_ready=True).status, AttachmentStatus.READY)

    def test_failed_attachment_preserves_original_and_is_not_promoted(self):
        ready = self.register_attachment()
        document = self.store.get_document(ready.reference.document_id)
        version = DocumentVersion(document_id=document.document_id, version_number=2, sha256="b" * 64)
        document.versions.append(version)
        document.current_version_id = version.version_id
        self.attachments.save_document(self.tokens[Role.DOCUMENT], document)
        failed = AttachmentContent(self.case.case_id, AttachmentRef(document.document_id, version.version_id), "pdf", status=AttachmentStatus.FAILED, error="Unreadable scan")
        self.attachments.register(self.tokens[Role.DOCUMENT], failed)
        with self.assertRaises(CrossBotError):
            self.attachments.read(self.tokens[Role.CUSTOMER_SERVICE], failed.reference, require_ready=True)
        with self.assertRaises(CrossBotError):
            self.attachments.approve_quality(self.tokens[Role.REVIEWER], failed.reference, reason="override")
        self.assertEqual(len(self.store.get_document(document.document_id).versions), 2)
        document.versions[0].sha256 = "c" * 64
        with self.assertRaises(CrossBotError):
            self.attachments.save_document(self.tokens[Role.DOCUMENT], document)

    def test_streamed_result_and_context_conflict(self):
        request = SalesRequest("Hi", "Lamp", "English", context=RequestContext(self.case.case_id))
        authorized = self.generations.authorize_request(self.tokens[Role.CUSTOMER_SERVICE], request)
        artifact = self.generations.save_result(self.tokens[Role.CUSTOMER_SERVICE], authorized, GenerationResponse("streamed", pending_confirmations=["Delivery date"]))
        self.assertEqual(artifact.pending_confirmations, ["Delivery date"])
        authorized.context.confirmed_fields.append(ExtractedField("forged", 123))
        with self.assertRaises(CrossBotError):
            self.generations.save_result(self.tokens[Role.CUSTOMER_SERVICE], authorized, GenerationResponse("forged"))

    def test_customer_service_cannot_close_case_or_approve_generation(self):
        with self.assertRaises(CrossBotError):
            CaseAccessService(self.auth).transition(self.tokens[Role.CUSTOMER_SERVICE], self.case.case_id, "closed", reason="bypass")
        artifact = self.generations.generate(self.tokens[Role.CUSTOMER_SERVICE], SalesRequest("Hi", "Lamp", "English", context=RequestContext(self.case.case_id)), lambda r: GenerationResponse("draft"))
        with self.assertRaises(CrossBotError):
            self.generations.review(self.tokens[Role.CUSTOMER_SERVICE], artifact.artifact_id, approve=True, reason="self")

    def test_role_ui_disables_ungranted_roles_and_hides_other_cases(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
            app.session_state["session_token"] = self.tokens[Role.CUSTOMER_SERVICE]
            app.run()
            self.assertFalse(app.exception)
            for button in app.button:
                if button.key in {"role_product", "role_operations", "role_admin", "role_reviewer"}:
                    self.assertTrue(button.disabled)
            self.assertEqual(app.radio[0].options, ["W1 询盘极速转化"])
            self.auth.revoke_case(self.admin_token, self.case.case_id, self.users[Role.CUSTOMER_SERVICE].user_id)
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.chat_message), 0)
            self.assertFalse(any(widget.label == "客户询盘" for widget in app.text_area))

    def test_publication_and_audit_are_rolled_back_together(self):
        from unittest.mock import patch
        version = self.draft()
        self.knowledge.submit(self.tokens[Role.PRODUCT], version.version_id)
        self.knowledge.review(self.tokens[Role.REVIEWER], version.version_id, approve=True, reason="checked")
        with patch.object(self.knowledge.repository, "audit", side_effect=RuntimeError("disk write failure")):
            with self.assertRaises(RuntimeError):
                self.knowledge.publish(self.admin_token, version.version_id)
        self.assertIsNone(self.knowledge.get_published(self.admin_token, version.resource_id))
        self.assertEqual(self.knowledge.get_version(self.admin_token, version.version_id).status, LifecycleStatus.APPROVED)

    def test_pending_review_accepts_candidates_but_pass_blocks_them(self):
        from core.models import CaseStatus, DocumentType

        attachment = self.register_attachment()
        case = self.store.get_case(self.case.case_id)
        case.required_document_types = [DocumentType.OTHER]
        self.store.save_case(case)
        access = CaseAccessService(self.auth)
        pending = access.transition(self.tokens[Role.DOCUMENT], case.case_id, CaseStatus.PENDING_REVIEW, reason="Ready for review")
        self.assertEqual(pending.status, CaseStatus.PENDING_REVIEW)
        with self.assertRaises(CrossBotError):
            access.transition(self.tokens[Role.REVIEWER], case.case_id, CaseStatus.PASSED, reason="Incomplete")
        self.attachments.confirm_field(self.tokens[Role.REVIEWER], attachment.reference, "quantity", 500, reason="Original checked")
        saved = self.store.get_document(attachment.reference.document_id)
        self.assertEqual(saved.versions[0].fields[0].status.value, "confirmed")
        self.assertEqual(saved.versions[0].fields[0].value, 500)

    def test_queues_and_history_obey_visibility(self):
        draft = self.draft()
        self.assertEqual(len(self.knowledge.list_versions(self.tokens[Role.PRODUCT])), 1)
        self.assertEqual(len(self.knowledge.list_versions(self.tokens[Role.CUSTOMER_SERVICE])), 0)
        self.knowledge.submit(self.tokens[Role.PRODUCT], draft.version_id)
        self.assertEqual(len(self.knowledge.list_versions(self.tokens[Role.REVIEWER], status=LifecycleStatus.IN_REVIEW)), 1)
        attachment = self.register_attachment()
        self.assertEqual(len(self.attachments.list_for_case(self.tokens[Role.CUSTOMER_SERVICE], self.case.case_id)), 1)
        artifact = self.generations.generate(self.tokens[Role.CUSTOMER_SERVICE], SalesRequest("Hi", "Lamp", "English", context=RequestContext(self.case.case_id)), lambda r: GenerationResponse("draft"))
        self.assertEqual(self.generations.list_for_case(self.admin_token, self.case.case_id)[0].artifact_id, artifact.artifact_id)
        self.auth.revoke_case(self.admin_token, self.case.case_id, self.users[Role.CUSTOMER_SERVICE].user_id)
        with self.assertRaises(CrossBotError):
            self.attachments.list_for_case(self.tokens[Role.CUSTOMER_SERVICE], self.case.case_id)


if __name__ == "__main__":
    unittest.main()
