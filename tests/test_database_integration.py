import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.errors import CrossBotError
from core.models import Case
from core.identity import Role
from core.schemas import WorkflowConversation
from storage.bootstrap import DEMO_ID, import_animal_lamp_case, import_demo_case
from storage.sqlite_store import SQLiteStore
from auth import AuthService
from workflows.access import GenerationService
from workflows.inquiry import InquiryService


class DatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "crossbot.db"
        self.store = SQLiteStore(self.path)
        for target in ("ui.auth_panel.browser_cookie", "providers.llm_client.HttpChatClient.complete"):
            patcher = patch(target, return_value=None)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if target.endswith("complete"):
                self.provider = mocked

    def configure_provider(self, app):
        app.button(key="nav_model").click().run()
        app.text_input(key="provider_url").input("https://provider.invalid").run()
        app.text_input(key="provider_key").input("synthetic-not-a-real-key").run()
        app.checkbox(key="provider_confirm").check().run()
        self.assertFalse(app.exception)

    def test_demo_import_is_idempotent_and_preserves_edits(self):
        case = import_demo_case(self.store)
        case.customer_name = "Edited demo"
        self.store.save_case(case)
        import_demo_case(self.store)
        self.assertEqual(len(self.store.list_cases()), 1)
        self.assertEqual(self.store.get_case(DEMO_ID).customer_name, "Edited demo")
        self.assertEqual(len(self.store.list_audit_events(DEMO_ID)), 1)
        self.assertTrue(case.metadata["is_demo"])

    def test_animal_lamp_import_is_idempotent_and_preserves_edits(self):
        case = import_animal_lamp_case(self.store)
        case.customer_name = "Edited synthetic demo"
        self.store.save_case(case)
        imported_again = import_animal_lamp_case(self.store)
        self.assertEqual(imported_again.case_id, "CASE-DE-ANIMAL-LAMP-001")
        self.assertEqual(imported_again.customer_name, "Edited synthetic demo")
        self.assertEqual(len(self.store.list_cases()), 1)
        self.assertEqual(import_demo_case(self.store).case_id, DEMO_ID)
        self.assertEqual(len(self.store.list_cases()), 2)

    def test_conversations_survive_reopen_and_are_isolated(self):
        case = import_demo_case(self.store)
        other = Case(case_number="OTHER", customer_name="Other", destination_country="DE")
        self.store.create_case(other)
        conversation = self.store.get_conversation(case.case_id, "sales")
        conversation.messages = [{"role": "user", "content": "Line 1\nLine 2"}]
        self.store.save_conversation(conversation)
        case.customer_name = "Updated after conversation"
        self.store.save_case(case)
        reopened = SQLiteStore(self.path)
        self.assertEqual(reopened.get_case(case.case_id).customer_name, "Updated after conversation")
        self.assertEqual(reopened.get_conversation(case.case_id, "sales").messages, conversation.messages)
        self.assertEqual(reopened.get_conversation(case.case_id, "listing").messages, [])
        self.assertEqual(reopened.get_conversation(other.case_id, "sales").messages, [])

    def test_stale_save_is_rejected_without_overwrite(self):
        case = import_demo_case(self.store)
        first = self.store.get_conversation(case.case_id, "sales")
        stale = self.store.get_conversation(case.case_id, "sales")
        first.messages = [{"role": "user", "content": "New"}]
        self.store.save_conversation(first)
        with self.assertRaises(CrossBotError):
            self.store.save_conversation(stale)
        self.assertEqual(self.store.get_conversation(case.case_id, "sales").messages, first.messages)

    def test_missing_case_and_duplicate_number_rejected(self):
        with self.assertRaises(CrossBotError):
            self.store.save_conversation(WorkflowConversation("missing", "sales"))
        case = import_demo_case(self.store)
        duplicate = Case(case_number=case.case_number, customer_name="Other", destination_country="DE")
        with self.assertRaises(CrossBotError):
            self.store.create_case(duplicate)

    def test_old_database_is_upgraded_without_data_loss(self):
        old_path = Path(self.folder.name) / "old.db"
        with sqlite3.connect(old_path) as conn:
            conn.execute("CREATE TABLE cases (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            conn.execute("INSERT INTO cases VALUES (?, ?)", ("legacy", json.dumps({
                "case_id": "legacy", "case_number": "LEGACY", "customer_name": "Old", "destination_country": "DE",
            })))
        conn.close()
        upgraded = SQLiteStore(old_path)
        self.assertEqual(upgraded.get_case("legacy").case_number, "LEGACY")
        self.assertEqual(upgraded.get_conversation("legacy", "sales").revision, 0)

    def test_web_flow_persists_generation_feedback_and_new_session(self):
        from streamlit.testing.v1 import AppTest

        auth = AuthService(self.store)
        auth.bootstrap_admin("merge-admin", "test-password-123")
        token = auth.login("merge-admin", "test-password-123")
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        self.provider.return_value = '{"reply":"Synthetic reply", "fields":{}}'

        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(app_path), default_timeout=20)
            app.session_state["session_token"] = token
            app.run()
            self.configure_provider(app)
            app.button(key="nav_sales").click().run()
            app.text_area(key="inquiry_customer_text").input("Hi\nNeed 500 lamps")
            next(button for button in app.button if button.label == "保存并生成回复").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            inquiry_id = app.session_state["active_inquiry"]
            stored = InquiryService(auth).get(token, inquiry_id)
            self.assertEqual(stored.messages[0]["content"], "Hi\nNeed 500 lamps")
            self.assertEqual(stored.messages[1]["content"], "Synthetic reply")
            app.text_area(key="inquiry_revision_text").input("Make it shorter")
            self.provider.return_value = '{"reply":"Short reply", "fields":{}}'
            next(button for button in app.button if button.label == "生成修改稿").click().run()
            self.assertFalse(app.exception)
            reopened_auth = AuthService(SQLiteStore(self.path))
            stored = InquiryService(reopened_auth).get(token, inquiry_id)
            self.assertEqual(len(stored.messages), 3)
            self.assertEqual(stored.messages[-1]["revision_instruction"], "Make it shorter")
            self.assertEqual(stored.messages[-1]["content"], "Short reply")
            reloaded = AppTest.from_file(str(app_path), default_timeout=20)
            reloaded.session_state["session_token"] = token
            reloaded.run()
            reloaded.selectbox(key="active_inquiry").set_value(inquiry_id).run()
            self.assertFalse(reloaded.exception)
            self.assertIn("Short reply", [item.value for item in reloaded.markdown])
            self.assertTrue(reloaded.get("download_button"))
            reloaded.button(key="nav_products").click().run()
            reloaded.button(key="nav_sales").click().run()
            reloaded.selectbox(key="active_inquiry").set_value(inquiry_id).run()
            self.assertIn("Short reply", [item.value for item in reloaded.markdown])
            self.assertEqual(self.provider.call_count, 2)
            self.assertFalse(self.store.list_cases())

    def test_web_listing_saves_only_selected_platform_under_case(self):
        from streamlit.testing.v1 import AppTest

        auth = AuthService(self.store)
        auth.bootstrap_admin("merge-admin", "test-password-123")
        token = auth.login("merge-admin", "test-password-123")
        case = Case(case_number="REAL-LISTING", customer_name="Synthetic buyer", destination_country="DE")
        from auth.cases import CaseAccessService
        CaseAccessService(auth).create_case(token, case)
        other = Case(case_number="OTHER-LISTING", customer_name="Other synthetic buyer", destination_country="DE")
        CaseAccessService(auth).create_case(token, other)
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        self.provider.return_value = '{"listings":{"Amazon":"Lamp listing"}}'

        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(app_path), default_timeout=20)
            app.session_state["session_token"] = token
            app.session_state["selected_case_id"] = case.case_id
            app.run()
            self.configure_provider(app)
            app.button(key="nav_listing").click().run()
            app.text_area(key="listing_description").input("Synthetic lamp, SKU SYN-001")
            app.multiselect(key="listing_platforms").set_value(["Amazon"])
            app.button(key="listing_next").click().run()
            app.button(key="listing_generate").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            stored = self.store.get_conversation(case.case_id, "listing")
            self.assertEqual(stored.messages[0]["listing_request"]["platforms"], ["Amazon"])
            artifacts = GenerationService(auth).list_for_case(token, case.case_id, workflow="listing")
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].content, "Lamp listing")
            self.assertEqual(artifacts[0].context.case_id, case.case_id)
            selector = next(widget for widget in app.selectbox if widget.label == "当前 Case")
            selector.set_value(other.case_id).run()
            self.assertFalse(app.exception)
            self.assertNotIn("listing_results", app.session_state)
            other_state = self.store.get_conversation(other.case_id, "listing").messages[0]
            self.assertNotIn("listing_results", other_state)
            self.assertNotIn("listing_request", other_state)
            self.assertFalse(GenerationService(auth).list_for_case(token, other.case_id, workflow="listing"))
            next(widget for widget in app.selectbox if widget.label == "当前 Case").set_value(case.case_id).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["listing_results"]["Amazon"].content, "Lamp listing")
            self.assertEqual(self.provider.call_count, 1)

    def test_web_creates_and_selects_real_cases_without_demo_import(self):
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "app.py"
        auth = AuthService(self.store)
        auth.bootstrap_admin("merge-admin", "test-password-123")
        token = auth.login("merge-admin", "test-password-123")
        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(app_path), default_timeout=20)
            app.session_state["session_token"] = token
            app.run()
            self.assertFalse(app.exception)
            app.button(key="nav_cases").click().run()
            inputs = {widget.label: widget for widget in app.text_input}
            inputs["Case 编号"].input("WEB-001")
            inputs["客户名称"].input("Web customer")
            inputs["目的国"].input("Germany")
            inputs["总金额（选填）"].input("100.20")
            next(button for button in app.button if button.label == "保存 Case").click().run()
            self.assertFalse(app.exception)
            created = self.store.list_cases()[0]
            self.assertEqual(created.case_number, "WEB-001")
            self.assertEqual(str(created.invoice_total), "100.20")
            self.assertFalse(created.metadata.get("is_demo"))
            self.assertNotIn("import_demo", [button.key for button in app.button])
            from auth.cases import CaseAccessService
            other = Case(case_number="WEB-002", customer_name="Other synthetic buyer", destination_country="DE")
            CaseAccessService(auth).create_case(token, other)
            app.run()
            next(select for select in app.selectbox if select.label == "当前 Case").set_value(other.case_id).run()
            self.assertEqual(app.session_state["selected_case_id"], other.case_id)
            next(select for select in app.selectbox if select.label == "当前 Case").set_value(created.case_id).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_case_id"], created.case_id)

    def test_web_initial_admin_and_login_required(self):
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "app.py"
        import_demo_case(self.store)
        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(app_path), default_timeout=20)
            app.session_state["session_token"] = ""
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any(widget.key == "inquiry_customer_text" for widget in app.text_area))
            app.text_input[0].input("first-admin")
            app.text_input[1].input("first-password-123")
            next(button for button in app.button if button.label == "创建管理员并登录").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state["session_token"])
            next(button for button in app.button if button.label == "退出登录").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any(widget.key == "inquiry_customer_text" for widget in app.text_area))

    def test_web_exposes_only_the_active_role_workflow(self):
        from streamlit.testing.v1 import AppTest
        from auth.cases import CaseAccessService

        auth = AuthService(self.store)
        auth.bootstrap_admin("role-admin", "test-password-123")
        admin_token = auth.login("role-admin", "test-password-123")
        case = Case(case_number="REAL-ROLE", customer_name="Synthetic role buyer", destination_country="DE")
        CaseAccessService(auth).create_case(admin_token, case)
        user = auth.create_user(admin_token, "role-cs", "test-password-123", "客服", [Role.CUSTOMER_SERVICE, Role.OPERATIONS])
        token = auth.login("role-cs", "test-password-123")
        auth.assign_case(admin_token, case.case_id, user.user_id)
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(app_path), default_timeout=20)
            app.session_state["session_token"] = token
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.button(key="nav_sales").disabled)
            self.assertTrue(app.button(key="nav_listing").disabled)
            self.assertFalse(app.button(key="nav_documents").disabled)
            self.assertTrue(app.button(key="role_product").disabled)
            app.button(key="role_operations").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key="nav_sales").disabled)
            self.assertFalse(app.button(key="nav_listing").disabled)
            self.assertFalse(app.button(key="nav_documents").disabled)
            self.assertEqual(app.session_state["selected_case_id"], case.case_id)
            self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
