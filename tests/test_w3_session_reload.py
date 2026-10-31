"""Hot reload may rebind trusted server sessions, never assign supplied roles."""
from dataclasses import make_dataclass
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.models import Actor


PreviousActor = make_dataclass("Actor", ["user_id", "roles", "authenticated", "kind"],
                              namespace={"__module__": "core.models"})


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class SessionReloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"CROSSBOT_W3_DATA": str(Path(self.temp.name))})
        self.env.start()
        self.previous_module = sys.modules.pop("w3_app", None)
        self.app = importlib.import_module("w3_app")
        self.app.identity.add_user("synthetic-sales", "synthetic-password", ["sales"])
        self.app.identity.add_user("synthetic-admin", "synthetic-password", ["admin"])

    def tearDown(self):
        self.app.repository.close()
        self.app.identity.close()
        sys.modules.pop("w3_app", None)
        if self.previous_module:
            sys.modules["w3_app"] = self.previous_module
        self.env.stop()
        self.temp.cleanup()

    def resolve(self, actor):
        session = SessionState(w3_actor=actor)
        with patch.object(self.app.st, "session_state", session):
            result = self.app._session_actor()
        return result, session

    def test_old_human_session_rebinds_with_provisioned_roles(self):
        original = PreviousActor("synthetic-sales", ["admin"], True, "human")
        rebound, session = self.resolve(original)
        self.assertIsInstance(rebound, Actor)
        self.assertIsNot(rebound, original)
        self.assertEqual(rebound.user_id, "synthetic-sales")
        self.assertEqual(rebound.roles, ["sales"])
        self.assertTrue(rebound.authenticated)
        self.assertEqual(rebound.kind, "human")
        self.assertIs(session["w3_actor"], rebound)

    def test_old_admin_session_uses_current_directory_roles(self):
        rebound, _ = self.resolve(PreviousActor("synthetic-admin", ["sales"], True, "human"))
        self.assertIsInstance(rebound, Actor)
        self.assertEqual(rebound.roles, ["admin"])

    def test_missing_account_cannot_turn_an_old_object_into_authenticated_actor(self):
        original = PreviousActor("synthetic-missing", ["admin"], True, "human")
        rebound, session = self.resolve(original)
        self.assertNotIsInstance(rebound, Actor)
        self.assertIs(session["w3_actor"], original)

    def test_unauthenticated_and_ai_old_objects_are_not_rebound(self):
        for authenticated, kind in ((False, "human"), (True, "ai"), (True, "machine"), (1, "human")):
            with self.subTest(authenticated=authenticated, kind=kind):
                original = PreviousActor("synthetic-sales", ["admin"], authenticated, kind)
                rebound, _ = self.resolve(original)
                self.assertNotIsInstance(rebound, Actor)
                self.assertIs(rebound, original)

    def test_dicts_and_unrelated_objects_cannot_rebind_by_username(self):
        payload = {"user_id": "synthetic-admin", "roles": ["admin"],
                   "authenticated": True, "kind": "human"}
        for original in (payload, SimpleNamespace(**payload), None):
            with self.subTest(kind=type(original).__name__):
                rebound, _ = self.resolve(original)
                self.assertNotIsInstance(rebound, Actor)
                self.assertIs(rebound, original)

    def test_current_actor_remains_compatible_with_existing_sessions(self):
        original = Actor("synthetic-sales", ["sales"], authenticated=True)
        rebound, session = self.resolve(original)
        self.assertIs(rebound, original)
        self.assertIs(session["w3_actor"], original)


if __name__ == "__main__":
    unittest.main()
