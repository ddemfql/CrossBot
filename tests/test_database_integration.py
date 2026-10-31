import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.errors import CrossBotError
from core.models import Case
from core.schemas import WorkflowConversation
from storage.bootstrap import DEMO_ID, import_demo_case
from storage.sqlite_store import SQLiteStore
from auth import AuthService


class DatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "crossbot.db"
        self.store = SQLiteStore(self.path)

    def test_demo_import_is_idempotent_and_preserves_edits(self):
        case = import_demo_case(self.store)
        case.customer_name = "Edited demo"
        self.store.save_case(case)
        import_demo_case(self.store)
        self.assertEqual(len(self.store.list_cases()), 1)
        self.assertEqual(self.store.get_case(DEMO_ID).customer_name, "Edited demo")
        self.assertEqual(len(self.store.list_audit_events(DEMO_ID)), 1)
        self.assertTrue(case.metadata["is_demo"])

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

        import_demo_case(self.store)
        auth = AuthService(self.store)
        auth.bootstrap_admin("admin", "test-password-123")
        token = auth.login("admin", "test-password-123")
        app_path = Path(__file__).resolve().parents[1] / "app.py"

        from workflows.access import GenerationService
        with patch("storage.bootstrap.open_store", return_value=self.store), \
             patch("providers.mock_client.MockChatClient.complete", return_value="Demo reply"):
            app = AppTest.from_file(str(app_path), default_timeout=20)
            app.session_state["session_token"] = token
            app.run()
            self.assertFalse(app.exception)
            app.text_area(key="v3_inquiry").set_value("Hi\nNeed 500 lamps").run()
            app.text_area(key="v3_product").set_value("USB lamp ABS").run()
            app.button(key="v3_generate_sales").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(self.store.get_conversation(DEMO_ID, "sales").messages), 2)
            app.text_area(key="v3_feedback").set_value("Make it shorter").run()
            app.button(key="v3_revise").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(self.store.get_conversation(DEMO_ID, "sales").messages), 4)
            reloaded = AppTest.from_file(str(app_path), default_timeout=20)
            reloaded.session_state["session_token"] = token
            reloaded.run()
            self.assertFalse(reloaded.exception)
            self.assertEqual(len(GenerationService(auth).list_for_case(token, DEMO_ID, workflow="sales")), 2)
            reloaded.radio[0].set_value("W2 多平台Listing生成").run()
            self.assertFalse(reloaded.exception)
            self.assertFalse(any(w.label == "客户询盘" for w in reloaded.text_area))
            self.assertEqual(GenerationService(auth).list_for_case(token, DEMO_ID, workflow="listing"), [])

    def test_web_creates_case_and_imports_demo(self):
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "app.py"
        auth = AuthService(self.store)
        auth.bootstrap_admin("admin", "test-password-123")
        token = auth.login("admin", "test-password-123")
        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(app_path), default_timeout=20)
            app.session_state["session_token"] = token
            app.run()
            self.assertFalse(app.exception)
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
            next(button for button in app.button if button.label == "导入台灯演示案例").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(self.store.list_cases()), 2)
            self.assertEqual(app.session_state["selected_case_id"], DEMO_ID)
            next(select for select in app.selectbox if select.label == "当前 Case").set_value(created.case_id).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_case_id"], created.case_id)

    def test_web_initial_admin_and_login_required(self):
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "app.py"
        import_demo_case(self.store)
        with patch("storage.bootstrap.open_store", return_value=self.store):
            app = AppTest.from_file(str(app_path), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertFalse(any(widget.label == "客户询盘" for widget in app.text_area))
            app.text_input[0].input("first-admin")
            app.text_input[1].input("first-password-123")
            next(button for button in app.button if button.label == "创建管理员并登录").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state["session_token"])
            next(button for button in app.button if button.label == "退出登录").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any(widget.label == "客户询盘" for widget in app.text_area))


if __name__ == "__main__":
    unittest.main()
