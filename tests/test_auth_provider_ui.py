from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from auth import AuthService
from core.errors import CrossBotError
from core.identity import Role
from storage.sqlite_store import SQLiteStore


class AuthProviderUITests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "synthetic.db"
        self.auth = AuthService(SQLiteStore(self.path))
        self.auth.bootstrap_admin("admin", "synthetic-password-123")
        self.token = self.auth.login("admin", "synthetic-password-123")
        self.principal = self.auth.require_session(self.token)
        self.auth.create_user(self.token, "other", "synthetic-password-123", "Other", [Role.OPERATIONS])
        self.other_token = self.auth.login("other", "synthetic-password-123")
        self.script = f'''
from storage.sqlite_store import SQLiteStore
from ui.auth_panel import render_auth_panel
from providers.settings_ui import initialize_provider_session, render_provider_settings
auth, token, principal = render_auth_panel(SQLiteStore({str(self.path)!r}))
initialize_provider_session(auth, token, principal)
render_provider_settings(mode="真实", show_test=False)
'''

    def page(self, token=None):
        page = AppTest.from_string(self.script, default_timeout=15)
        if token:
            page.session_state["session_token"] = token
        return page.run()

    def test_unmodified_component_api_renders_login_and_logout(self):
        page = self.page()
        self.assertFalse(page.exception)
        page.text_input[0].set_value("admin")
        page.text_input[1].set_value("synthetic-password-123")
        next(button for button in page.button if button.label == "登录").click().run()
        self.assertFalse(page.exception)
        self.assertTrue(page.session_state["session_token"])
        page.text_input(key="provider_key").set_value("synthetic-key").run()
        cookie = page.session_state["session_token"]
        next(button for button in page.button if button.label == "退出登录").click().run()
        self.assertFalse(page.exception)
        self.assertNotIn("provider_key", page.session_state)
        self.assertNotIn("session_token", page.session_state)
        self.assertEqual(page.session_state["_browser_cookie_command"]["action"], "clear")
        with self.assertRaises(CrossBotError):
            self.auth.require_session(cookie)

    def test_cookie_restore_and_invalid_cookie_fail_closed(self):
        with patch("ui.auth_panel.browser_cookie", side_effect=[self.token, None]):
            page = self.page()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state["session_token"], self.token)
        with patch("ui.auth_panel.browser_cookie", side_effect=["invalid-cookie-synthetic-123456", None]):
            rejected = self.page()
        self.assertFalse(rejected.exception)
        self.assertNotIn("session_token", rejected.session_state)
        self.assertNotIn("provider_key", rejected.session_state)

    def test_new_browser_session_keeps_login_but_resets_model_settings(self):
        page = self.page(self.token)
        page.text_input(key="provider_url").set_value("https://provider.invalid").run()
        page.text_input(key="provider_model").set_value("synthetic-model").run()
        page.text_input(key="provider_key").set_value("synthetic-key").run()
        page.checkbox(key="provider_confirm").check().run()
        page.run()
        self.assertFalse(page.exception)
        self.assertEqual(page.text_input(key="provider_key").value, "synthetic-key")
        self.assertTrue(page.checkbox(key="provider_confirm").value)
        fresh = self.page(self.token)
        self.assertFalse(fresh.exception)
        self.assertEqual(fresh.session_state["session_token"], self.token)
        self.assertEqual(fresh.text_input(key="provider_url").value, "https://api.tokenhubcloud.net")
        self.assertEqual(fresh.text_input(key="provider_model").value, "gpt-6.1-sol")
        self.assertEqual(fresh.text_input(key="provider_key").value, "")
        self.assertFalse(fresh.checkbox(key="provider_confirm").value)

    def test_save_controls_removed_and_settings_table_not_created(self):
        page = self.page(self.token)
        self.assertFalse(page.exception)
        self.assertNotIn("记住此账号的 API Key", [item.label for item in page.checkbox])
        self.assertTrue({"保存设置", "删除已保存配置"}.isdisjoint(item.label for item in page.button))
        page.text_input(key="provider_key").set_value("synthetic-key").run()
        page.checkbox(key="provider_confirm").check().run()
        page.button(key="provider_clear").click().run()
        with self.auth.repository.transaction() as connection:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_provider_settings'"
            ).fetchone())

    def test_legacy_saved_configuration_is_ignored_and_preserved(self):
        with self.auth.repository.transaction() as connection:
            connection.execute("CREATE TABLE user_provider_settings (user_id TEXT PRIMARY KEY, encrypted_payload BLOB NOT NULL)")
            connection.execute("INSERT INTO user_provider_settings VALUES (?, ?)",
                               (self.principal.user_id, b"synthetic-legacy-encrypted-settings"))
        page = self.page(self.token)
        self.assertFalse(page.exception)
        self.assertFalse(page.error)
        self.assertFalse(any("DPAPI" in item.value or "无法解密" in item.value for item in page.warning))
        self.assertEqual(page.text_input(key="provider_key").value, "")
        page.button(key="provider_clear").click().run()
        with self.auth.repository.transaction() as connection:
            row = connection.execute("SELECT encrypted_payload FROM user_provider_settings WHERE user_id=?",
                                     (self.principal.user_id,)).fetchone()
            self.assertEqual(bytes(row[0]), b"synthetic-legacy-encrypted-settings")

    def test_user_switch_replaces_all_provider_state(self):
        page = self.page(self.token)
        page.text_input(key="provider_key").set_value("admin-synthetic-key").run()
        page.checkbox(key="provider_confirm").check().run()
        self.assertEqual(page.text_input(key="provider_key").value, "admin-synthetic-key")
        page.session_state["session_token"] = self.other_token
        page.run()
        self.assertFalse(page.exception)
        self.assertEqual(page.text_input(key="provider_key").value, "")
        self.assertFalse(page.checkbox(key="provider_confirm").value)

    def test_clear_key_revokes_current_session_confirmation(self):
        page = self.page(self.token)
        page.text_input(key="provider_key").set_value("synthetic-key").run()
        page.checkbox(key="provider_confirm").check().run()
        page.button(key="provider_clear").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.text_input(key="provider_key").value, "")
        self.assertFalse(page.checkbox(key="provider_confirm").value)

    def test_same_user_rerun_does_not_overwrite_unsaved_input(self):
        page = self.page(self.token)
        page.text_input(key="provider_model").set_value("unsaved-model").run()
        page.run()
        self.assertEqual(page.text_input(key="provider_model").value, "unsaved-model")

    def test_revoked_login_clears_current_session_settings(self):
        page = self.page(self.token)
        page.text_input(key="provider_key").set_value("synthetic-key").run()
        self.auth.logout(self.token)
        page.run()
        self.assertFalse(page.exception)
        self.assertNotIn("session_token", page.session_state)
        self.assertNotIn("provider_key", page.session_state)

    def test_exports_exclude_in_memory_model_settings_and_tokens(self):
        from core.schemas import GenerationResponse, ListingRequest
        from workflows.exports import business_snapshot, export_bundle, json_bytes, text_bytes
        state = {"provider_key": "synthetic-private-key", "session_token": self.token,
                 "_provider_owner": ("memory-only-v1", "synthetic-owner"),
                 "_browser_cookie_command": {"token": self.token},
                 "listing_request": ListingRequest("synthetic product", ["Amazon"]),
                 "listing_results": {"Amazon": GenerationResponse("synthetic listing")}}
        bundle = export_bundle(business_snapshot(state))
        exported = json_bytes(bundle) + text_bytes(bundle, "w2")
        self.assertNotIn(b"synthetic-private-key", exported)
        self.assertNotIn(self.token.encode(), exported)
        self.assertNotIn(b"_provider_owner", exported)


if __name__ == "__main__":
    unittest.main()
