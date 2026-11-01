from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from auth import AuthService
from core.errors import CrossBotError
from core.identity import Role
from providers.user_settings import ProviderSettings, UserSettingsRepository
from storage.sqlite_store import SQLiteStore
from tests.test_user_provider_settings import MemoryCipher


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
        self.cipher = MemoryCipher()
        self.repository = UserSettingsRepository(self.auth, cipher=self.cipher)
        binding = patch("providers.settings_ui.UserSettingsRepository",
                        side_effect=lambda auth, **kwargs: UserSettingsRepository(auth, cipher=self.cipher))
        binding.start()
        self.addCleanup(binding.stop)
        self.script = f'''
from storage.sqlite_store import SQLiteStore
from ui.auth_panel import render_auth_panel
from providers.settings_ui import load_user_provider_settings, render_provider_settings
auth, token, principal = render_auth_panel(SQLiteStore({str(self.path)!r}))
load_user_provider_settings(auth, token, principal)
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

    def test_remember_save_refresh_and_confirmation_reset(self):
        page = self.page(self.token)
        page.text_input(key="provider_key").set_value("synthetic-key").run()
        page.checkbox(key="provider_remember").check().run()
        page.checkbox(key="provider_confirm").check().run()
        page.button(key="provider_save").click().run()
        self.assertFalse(page.exception)
        fresh = self.page(self.token)
        self.assertEqual(fresh.text_input(key="provider_key").value, "synthetic-key")
        self.assertFalse(fresh.checkbox(key="provider_confirm").value)
        self.assertTrue(fresh.checkbox(key="provider_remember").value)

    def test_user_switch_replaces_all_provider_state(self):
        self.repository.save(self.token, self.principal, ProviderSettings(api_key="admin-synthetic-key", remember=True))
        page = self.page(self.token)
        self.assertEqual(page.text_input(key="provider_key").value, "admin-synthetic-key")
        page.session_state["session_token"] = self.other_token
        page.run()
        self.assertFalse(page.exception)
        self.assertEqual(page.text_input(key="provider_key").value, "")
        self.assertFalse(page.checkbox(key="provider_confirm").value)

    def test_uncheck_and_clear_remove_persisted_key_immediately(self):
        self.repository.save(self.token, self.principal, ProviderSettings(api_key="synthetic-key", remember=True))
        page = self.page(self.token)
        page.checkbox(key="provider_remember").uncheck().run()
        self.assertEqual(self.repository.load(self.token, self.principal).api_key, "")
        page.checkbox(key="provider_remember").check().run()
        page.button(key="provider_save").click().run()
        page.button(key="provider_clear").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.text_input(key="provider_key").value, "")
        self.assertEqual(self.repository.load(self.token, self.principal).api_key, "")

    def test_same_user_rerun_does_not_overwrite_unsaved_input(self):
        page = self.page(self.token)
        page.text_input(key="provider_model").set_value("unsaved-model").run()
        page.run()
        self.assertEqual(page.text_input(key="provider_model").value, "unsaved-model")


if __name__ == "__main__":
    unittest.main()
