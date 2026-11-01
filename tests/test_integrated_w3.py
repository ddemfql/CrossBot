"""Shared-account W3 integration, isolation and atomic persistence checks."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auth import AuthService
from auth.cases import CaseAccessService
from case_workflow.service import CaseService
from core.errors import CrossBotError, ErrorCode
from core.identity import Role
from core.models import Actor, AuditEvent, Case, DocumentType
from review.access import shared_w3_session
from review.policy import require
from storage.integrated_workspace import IntegratedWorkspaceRepository
from storage.sqlite_store import SQLiteStore
from validation.rules import DeterministicRules


class IntegratedW3Tests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SQLiteStore(Path(folder.name) / "crossbot.db")
        self.auth = AuthService(self.store)
        self.auth.bootstrap_admin("integration-admin", "synthetic-password-123")
        self.token = self.auth.login("integration-admin", "synthetic-password-123")
        self.case = Case(case_number="W3-INTEGRATION", customer_name="Synthetic buyer",
            destination_country="DE", metadata={"keep": "W1/W2 metadata"})
        CaseAccessService(self.auth).create_case(self.token, self.case)
        self.repo = IntegratedWorkspaceRepository(self.auth, self.token, self.case.case_id)
        self.addCleanup(self.repo.close)

    def member(self, role, *, assigned=True):
        user = self.auth.create_user(self.token, role.value, "synthetic-password-123", role.value, [role])
        if assigned:
            self.auth.assign_case(self.token, self.case.case_id, user.user_id)
        return self.auth.login(role.value, "synthetic-password-123")

    def initialize(self):
        with shared_w3_session(self.auth, self.token, self.case.case_id) as actor:
            return self.repo.initialize(actor)

    def test_same_case_metadata_documents_and_conversation_survive_reopen(self):
        conversation = self.store.get_conversation(self.case.case_id, "sales")
        conversation.messages = [{"role": "assistant", "content": "Preserve W1 draft"}]
        self.store.save_conversation(conversation)
        workspace = self.initialize()
        with shared_w3_session(self.auth, self.token, self.case.case_id) as actor:
            trade = deepcopy(workspace.trade)
            trade.order_no = "ORDER-SYNTHETIC"
            updated = CaseService(self.repo, DeterministicRules()).update_trade(
                self.case.case_id, actor, trade, "Synthetic integration update")
        reopened = IntegratedWorkspaceRepository(self.auth, self.token, self.case.case_id)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get(self.case.case_id).case, self.store.get_case(self.case.case_id))
        self.assertEqual(reopened.get(self.case.case_id).trade.order_no, "ORDER-SYNTHETIC")
        self.assertEqual(updated.case.metadata["keep"], "W1/W2 metadata")
        self.assertEqual(self.store.get_conversation(self.case.case_id, "sales").messages, conversation.messages)
        self.assertEqual(len(reopened.history(self.case.case_id)), 2)
        self.assertTrue(self.store.list_audit_events(self.case.case_id))

    def test_active_role_permissions_are_not_collapsed_to_sales(self):
        self.initialize()
        document_token = self.member(Role.DOCUMENT)
        with shared_w3_session(self.auth, document_token, self.case.case_id) as actor:
            require(None, actor, "upload")
            require(None, actor, "confirm_field")
            require(None, actor, "check")
            with self.assertRaises(CrossBotError):
                require(None, actor, "approve")
        reviewer_token = self.member(Role.REVIEWER)
        with shared_w3_session(self.auth, reviewer_token, self.case.case_id) as actor:
            require(None, actor, "approve")
            with self.assertRaises(CrossBotError):
                require(None, actor, "manual_field")

    def test_customer_cannot_edit_trade_or_initialize_w3(self):
        customer = self.member(Role.CUSTOMER_SERVICE)
        repository = IntegratedWorkspaceRepository(self.auth, customer, self.case.case_id)
        self.addCleanup(repository.close)
        with shared_w3_session(self.auth, customer, self.case.case_id) as actor:
            require(None, actor, "view")
            with self.assertRaises(CrossBotError):
                repository.initialize(actor)
            with self.assertRaises(CrossBotError):
                require(None, actor, "generate")

    def test_unassigned_case_forged_actor_and_revoked_session_are_blocked(self):
        token = self.member(Role.DOCUMENT, assigned=False)
        with self.assertRaises(CrossBotError):
            IntegratedWorkspaceRepository(self.auth, token, self.case.case_id)
        with shared_w3_session(self.auth, self.token, self.case.case_id):
            with self.assertRaises(CrossBotError):
                require(None, Actor("forged", ["admin"], authenticated=True), "upload")
        token = self.member(Role.REVIEWER)
        with shared_w3_session(self.auth, token, self.case.case_id) as actor:
            self.auth.logout(token)
            with self.assertRaises(CrossBotError):
                require(None, actor, "approve")

    def test_repository_rejects_another_selected_case(self):
        other = Case(case_number="OTHER-W3", customer_name="Other", destination_country="DE")
        CaseAccessService(self.auth).create_case(self.token, other)
        with self.assertRaises(CrossBotError):
            self.repo.get(other.case_id)

    def test_shared_projection_failure_rolls_back_snapshot_and_case(self):
        workspace = self.initialize()
        before = self.store.get_case(self.case.case_id)
        workspace.case.customer_name = "Must roll back"
        workspace.audit.append(AuditEvent(case_id=self.case.case_id, action="rolled_back"))
        original = self.repo._project

        def fail(connection, value):
            original(connection, value)
            raise RuntimeError("Synthetic projection failure")

        with patch.object(self.repo, "_project", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.repo.save(workspace, workspace.revision)
        self.assertEqual(self.store.get_case(self.case.case_id), before)
        self.assertEqual(len(self.repo.history(self.case.case_id)), 1)
        self.assertFalse(any(event.action == "rolled_back" for event in self.store.list_audit_events(self.case.case_id)))

    def test_stale_workspace_cannot_overwrite_current_case(self):
        workspace = self.initialize()
        stale = deepcopy(workspace)
        self.repo.save(workspace, workspace.revision)
        stale.case.customer_name = "Stale"
        with self.assertRaises(CrossBotError) as failure:
            self.repo.save(stale, stale.revision)
        self.assertEqual(failure.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(self.store.get_case(self.case.case_id).customer_name, "Synthetic buyer")

    def test_main_app_exposes_w3_without_second_login_and_persists(self):
        from streamlit.testing.v1 import AppTest
        with patch("storage.bootstrap.open_store", return_value=self.store), \
             patch("ui.auth_panel.browser_cookie", return_value=None), \
             patch("providers.llm_client.HttpChatClient.complete") as provider:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=25)
            app.session_state["session_token"] = self.token
            app.session_state["selected_case_id"] = self.case.case_id
            app.session_state["workbench_page"] = "cases"
            app.run()
            app.button(key="nav_documents").click().run()
            self.assertFalse(app.exception, [item.message for item in app.exception])
            self.assertFalse(any(widget.label == "账号" for widget in app.text_input))
            next(widget for widget in app.button if widget.label == "启用 W3 单证工作台").click().run()
            self.assertFalse(app.exception, [item.message for item in app.exception])
            self.assertEqual([tab.label for tab in app.tabs], ["文件分类与查看", "生成与导出"])
            self.assertTrue(self.repo.get(self.case.case_id).case.metadata["w3_workspace"])
            reloaded = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=25)
            reloaded.session_state["session_token"] = self.token
            reloaded.session_state["selected_case_id"] = self.case.case_id
            reloaded.session_state["workbench_page"] = "documents"
            reloaded.run()
            self.assertFalse(reloaded.exception, [item.message for item in reloaded.exception])
            self.assertEqual([tab.label for tab in reloaded.tabs], ["文件分类与查看", "生成与导出"])
            self.assertEqual(len(self.repo.history(self.case.case_id)), 1)
            provider.assert_not_called()

    def test_shared_case_transition_cannot_bypass_w3_review(self):
        self.initialize()
        with self.assertRaises(CrossBotError) as failure:
            CaseAccessService(self.auth).transition(self.token, self.case.case_id, "passed", reason="bypass")
        self.assertEqual(failure.exception.code, ErrorCode.REVIEW_REQUIRED)

    def test_uploaded_documents_project_and_duplicates_are_rejected(self):
        from documents.service import DocumentService
        from storage.blobs import LocalBlobStore
        self.initialize()
        documents = DocumentService(self.repo, LocalBlobStore(Path(self.store.path).parent / "files"))
        with shared_w3_session(self.auth, self.token, self.case.case_id) as actor:
            workspace = documents.upload(self.case.case_id, actor, "synthetic.txt", b"Quantity: 500",
                DocumentType.COMMERCIAL_INVOICE)
            self.assertEqual(len(self.store.list_documents(self.case.case_id)), 1)
            self.assertEqual(self.store.list_documents(self.case.case_id)[0], workspace.documents[0])
            with self.assertRaises(CrossBotError):
                documents.upload(self.case.case_id, actor, "duplicate.txt", b"Quantity: 500",
                    DocumentType.PACKING_LIST)
        self.assertEqual(len(self.repo.get(self.case.case_id).documents), 1)


if __name__ == "__main__":
    unittest.main()
