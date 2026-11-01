import dataclasses
import ctypes
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

from auth import AuthService
from core.errors import CrossBotError
from core.identity import Role
from providers.user_settings import ProviderSettings, UserSettingsRepository, WindowsDPAPI
from storage.sqlite_store import SQLiteStore

PASSWORD = "synthetic-password-123"
KEY = "synthetic-api-key-never-real"


class MemoryCipher:
    def __init__(self):
        self.values = {}

    def encrypt(self, value, entropy):
        encrypted = secrets.token_bytes(48)
        self.values[encrypted] = (value, entropy)
        return encrypted

    def decrypt(self, value, entropy):
        payload, expected = self.values[value]
        if entropy != expected:
            raise ValueError("wrong owner")
        return payload


class UserProviderSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "synthetic.db"
        self.store = SQLiteStore(self.path)
        self.auth = AuthService(self.store)
        self.auth.bootstrap_admin("admin", PASSWORD)
        self.token = self.auth.login("admin", PASSWORD)
        self.principal = self.auth.require_session(self.token)
        self.auth.create_user(self.token, "other", PASSWORD, "Other", [Role.OPERATIONS])
        self.other_token = self.auth.login("other", PASSWORD)
        self.other = self.auth.require_session(self.other_token)
        self.cipher = MemoryCipher()
        self.repository = UserSettingsRepository(self.auth, cipher=self.cipher)
        self.settings = ProviderSettings(api_key=KEY, remember=True)

    def test_roundtrip_restart_and_owner_isolation(self):
        self.repository.save(self.token, self.principal, self.settings)
        fresh_auth = AuthService(SQLiteStore(self.path))
        fresh = UserSettingsRepository(fresh_auth, cipher=self.cipher)
        self.assertEqual(fresh.load(self.token, self.principal), self.settings)
        self.assertEqual(fresh.load(self.other_token, self.other), ProviderSettings())
        with self.assertRaises(CrossBotError):
            fresh.load(self.token, self.other)
        with self.assertRaises(CrossBotError):
            fresh.save(self.other_token, self.principal, self.settings)
        with self.assertRaises(CrossBotError):
            fresh.delete(self.other_token, self.principal)

    def test_remember_false_never_sends_key_to_cipher(self):
        settings = dataclasses.replace(self.settings, remember=False, model="synthetic-model")
        self.repository.save(self.token, self.principal, settings)
        self.assertFalse(any(KEY.encode() in payload for payload, _ in self.cipher.values.values()))
        restored = self.repository.load(self.token, self.principal)
        self.assertEqual(restored.api_key, "")
        self.assertEqual(restored.model, settings.model)
        self.assertFalse(restored.remember)

    def test_no_plaintext_or_session_token_in_database_or_repr(self):
        self.repository.save(self.token, self.principal, self.settings)
        self.assertNotIn(KEY, repr(self.settings))
        for secret in (KEY, self.token, self.other_token):
            self.assertNotIn(secret.encode(), self.path.read_bytes())
        with self.auth.repository.transaction() as connection:
            audits = str([tuple(row) for row in connection.execute("SELECT * FROM audit_events")])
            self.assertNotIn(KEY, audits)
            self.assertNotIn(self.token, audits)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_copying_ciphertext_to_other_user_is_rejected(self):
        self.repository.save(self.token, self.principal, self.settings)
        with self.auth.repository.transaction() as connection:
            connection.execute("INSERT INTO user_provider_settings SELECT ?, encrypted_payload FROM user_provider_settings WHERE user_id=?",
                               (self.other.user_id, self.principal.user_id))
        with self.assertRaises(CrossBotError) as caught:
            self.repository.load(self.other_token, self.other)
        self.assertNotIn(KEY, str(caught.exception))

    def test_revoked_and_expired_sessions_cannot_read_write_delete(self):
        self.repository.save(self.token, self.principal, self.settings)
        self.auth.logout(self.token)
        for operation in (lambda: self.repository.load(self.token, self.principal),
                          lambda: self.repository.save(self.token, self.principal, self.settings),
                          lambda: self.repository.delete(self.token, self.principal)):
            with self.assertRaises(CrossBotError):
                operation()
        with patch("auth.service.time.time", return_value=10**12):
            with self.assertRaises(CrossBotError):
                self.repository.load(self.other_token, self.other)

    def test_disabled_account_rejected(self):
        self.auth.update_user(self.token, self.other.user_id, status="disabled")
        with self.assertRaises(CrossBotError):
            self.repository.load(self.other_token, self.other)

    def test_corrupt_blob_sanitized_and_deletable(self):
        self.repository.save(self.token, self.principal, self.settings)
        with self.auth.repository.transaction() as connection:
            connection.execute("UPDATE user_provider_settings SET encrypted_payload=?", (b"broken",))
        with self.assertRaises(CrossBotError):
            self.repository.load(self.token, self.principal)
        self.repository.delete(self.token, self.principal)
        self.assertEqual(self.repository.load(self.token, self.principal), ProviderSettings())

    def test_encryption_failure_does_not_replace_previous_row(self):
        self.repository.save(self.token, self.principal, self.settings)
        with patch.object(self.cipher, "encrypt", side_effect=RuntimeError(KEY)):
            with self.assertRaises(CrossBotError) as caught:
                self.repository.save(self.token, self.principal, dataclasses.replace(self.settings, model="changed"))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(self.repository.load(self.token, self.principal), self.settings)

    def test_credentials_in_address_rejected(self):
        for url in ("https://user:secret@example.test", "https://example.test?key=secret", "https://example.test#secret"):
            with self.subTest(url=url), self.assertRaises(CrossBotError):
                self.repository.save(self.token, self.principal, dataclasses.replace(self.settings, url=url))

    def test_export_ignores_new_private_settings_context(self):
        from core.schemas import GenerationResponse, ListingRequest
        from workflows.exports import business_snapshot, export_bundle, json_bytes, text_bytes
        state = {"provider_key": KEY, "session_token": self.token, "_provider_context": self.repository,
                 "_browser_cookie_command": {"token": self.token},
                 "listing_request": ListingRequest("synthetic product", ["Amazon"]),
                 "listing_results": {"Amazon": GenerationResponse("synthetic listing")}}
        bundle = export_bundle(business_snapshot(state))
        exported = json_bytes(bundle) + text_bytes(bundle, "w2")
        self.assertNotIn(KEY.encode(), exported)
        self.assertNotIn(self.token.encode(), exported)
        self.assertNotIn(b"_provider_context", exported)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI only")
    def test_real_dpapi_with_synthetic_values(self):
        cipher = WindowsDPAPI()
        try:
            encrypted = cipher.encrypt(KEY.encode(), b"synthetic-user-a")
        except CrossBotError:
            if ctypes.get_last_error() in (2, 5):
                self.skipTest("Sandbox Windows profile cannot access DPAPI; verify under the deployment account")
            raise
        self.assertNotIn(KEY.encode(), encrypted)
        self.assertEqual(cipher.decrypt(encrypted, b"synthetic-user-a"), KEY.encode())
        with self.assertRaises(CrossBotError):
            cipher.decrypt(encrypted, b"synthetic-user-b")


if __name__ == "__main__":
    unittest.main()
