from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import streamlit as st

from auth import AuthService
from auth.browser_session import (
    COOKIE_NAME, browser_cookie, cookie_name, queue_cookie, queue_cookie_clear, session_expiry, validated_cookie,
)
from core.errors import CrossBotError
from core.identity import Role
from storage.sqlite_store import SQLiteStore


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "synthetic.db"
        self.auth = AuthService(SQLiteStore(self.path))
        self.auth.bootstrap_admin("admin", "synthetic-password-123")
        self.token = self.auth.login("admin", "synthetic-password-123")

    def test_cookie_survives_server_restart_with_same_absolute_expiry(self):
        expires = session_expiry(self.auth, self.token)
        fresh = AuthService(SQLiteStore(self.path))
        self.assertEqual(validated_cookie(fresh, self.token).username, "admin")
        self.assertEqual(session_expiry(fresh, self.token), expires)
        with patch("auth.service.time.time", return_value=expires):
            with self.assertRaises(CrossBotError):
                validated_cookie(fresh, self.token)

    def test_cookie_namespace_isolates_database_port_and_app_path(self):
        first = cookie_name(self.auth, "http://localhost:8501")
        self.assertNotEqual(first, cookie_name(self.auth, "http://localhost:8502"))
        self.assertNotEqual(first, cookie_name(self.auth, "http://localhost:8501", "other"))
        other_auth = AuthService(SQLiteStore(self.path.with_name("other.db")))
        self.assertNotEqual(first, cookie_name(other_auth, "http://localhost:8501"))

    def test_invalid_cookie_and_logout_replay_rejected(self):
        for cookie in (None, [], "short", "x" * 300, "x" * 43):
            with self.subTest(cookie=type(cookie)), self.assertRaises(CrossBotError):
                validated_cookie(self.auth, cookie)
        self.auth.logout(self.token)
        with self.assertRaises(CrossBotError):
            validated_cookie(self.auth, self.token)

    def test_disabled_or_role_revoked_cookie_rejected(self):
        user = self.auth.create_user(self.token, "other", "synthetic-password-123", "Other", [Role.OPERATIONS])
        cookie = self.auth.login("other", "synthetic-password-123")
        self.auth.update_user(self.token, user.user_id, roles=[Role.PRODUCT])
        with self.assertRaises(CrossBotError):
            validated_cookie(self.auth, cookie)
        cookie = self.auth.login("other", "synthetic-password-123")
        self.auth.update_user(self.token, user.user_id, status="disabled")
        with self.assertRaises(CrossBotError):
            validated_cookie(self.auth, cookie)

    def test_transport_initial_header_then_live_component(self):
        state = {}
        component_result = SimpleNamespace(result=None)
        with patch.object(st, "session_state", state), patch.object(st, "context", SimpleNamespace(cookies={COOKIE_NAME: self.token})), patch(
                "streamlit.components.v2.component", return_value=lambda **kwargs: component_result):
            self.assertEqual(browser_cookie(), self.token)
            self.assertIsNone(browser_cookie())
            command = state["_browser_cookie_command"]
            component_result.result = {"request_id": command["request_id"], "cookie": self.token, "ok": True}
            self.assertEqual(browser_cookie(), self.token)

    def test_clear_ack_cannot_restore_stale_websocket_header(self):
        state = {}
        component_result = SimpleNamespace(result=None)
        with patch.object(st, "session_state", state), patch.object(st, "context", SimpleNamespace(cookies={COOKIE_NAME: self.token})), patch(
                "streamlit.components.v2.component", return_value=lambda **kwargs: component_result):
            queue_cookie_clear()
            self.assertIsNone(browser_cookie())
            command = state["_browser_cookie_command"]
            component_result.result = {"request_id": command["request_id"], "cookie": "", "ok": True}
            self.assertIsNone(browser_cookie())
            self.assertIsNone(browser_cookie())

    def test_command_ids_reject_stale_component_response(self):
        state = {}
        component_result = SimpleNamespace(result=None)
        with patch.object(st, "session_state", state), patch.object(st, "context", SimpleNamespace(cookies={})), patch(
                "streamlit.components.v2.component", return_value=lambda **kwargs: component_result):
            queue_cookie(self.token, session_expiry(self.auth, self.token))
            old = state["_browser_cookie_command"]
            queue_cookie_clear()
            component_result.result = {"request_id": old["request_id"], "cookie": self.token, "ok": True}
            self.assertIsNone(browser_cookie())
            self.assertEqual(state["_browser_cookie_command"]["action"], "clear")


if __name__ == "__main__":
    unittest.main()
