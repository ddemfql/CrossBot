import hashlib
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import Case, Workspace
from storage.blobs import LocalBlobStore
from storage.identity import SQLiteIdentityStore
from storage.sqlite import SQLiteWorkspaceRepository


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_snapshots_survive_reopen_and_preserve_history_and_cas(self):
        path = self.root / "workspace.db"
        first, other = SQLiteWorkspaceRepository(path), SQLiteWorkspaceRepository(path)
        self.addCleanup(first.close)
        self.addCleanup(other.close)
        workspace = Workspace(Case(case_number="SYNTHETIC-001"))
        first.save(workspace, 0)
        stale = other.get(workspace.case.case_id)
        workspace.trade.buyer = "Synthetic Buyer"
        first.save(workspace, 1)
        with self.assertRaises(CrossBotError) as error:
            other.save(stale, 1)
        self.assertEqual(error.exception.code, ErrorCode.CONFLICT)
        self.assertEqual(stale.revision, 1)
        self.assertEqual(other.get(workspace.case.case_id).trade.buyer, "Synthetic Buyer")
        self.assertEqual([w.revision for w in first.history(workspace.case.case_id)], [1, 2])
        self.assertEqual(first.history(workspace.case.case_id)[0].trade.buyer, "")
        self.assertEqual(len(first.list()), 1)
        with sqlite3.connect(path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE w3_snapshots SET snapshot='{}'")

    def test_simultaneous_edits_have_one_winner(self):
        repositories = [SQLiteWorkspaceRepository(self.root / "workspace.db") for _ in range(2)]
        for repository in repositories:
            self.addCleanup(repository.close)
        workspace = Workspace(Case())
        repositories[0].save(workspace, 0)
        edits = [repository.get(workspace.case.case_id) for repository in repositories]
        def save(index):
            try:
                repositories[index].save(edits[index], 1)
                return "saved"
            except CrossBotError as error:
                return error.code
        with ThreadPoolExecutor(2) as executor:
            outcomes = list(executor.map(save, range(2)))
        self.assertCountEqual(outcomes, ["saved", ErrorCode.CONFLICT])

    def test_blob_integrity_path_traversal_and_concurrent_duplicate_upload(self):
        store = LocalBlobStore(self.root / "blobs")
        payload = b"synthetic original binary content"
        with ThreadPoolExecutor(4) as executor:
            keys = list(executor.map(store.put, [payload] * 8))
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(store.get(keys[0]), payload)
        self.assertEqual(keys[0], hashlib.sha256(payload).hexdigest())
        for path in ("../../secret", "/tmp/file", "a" * 63, "G" * 64):
            with self.assertRaises(ValidationError):
                store.get(path)
        (store.root / keys[0]).write_bytes(b"tampered")
        with self.assertRaises(CrossBotError):
            store.get(keys[0])

    def test_blob_symlink_is_rejected(self):
        store = LocalBlobStore(self.root / "blobs")
        target = self.root / "outside"
        target.write_bytes(b"not a blob")
        key = "a" * 64
        (store.root / key).symlink_to(target)
        with self.assertRaises(ValidationError):
            store.get(key)

    def test_human_accounts_are_provisioned_hashed_and_authenticated(self):
        path = self.root / "identity.db"
        store = SQLiteIdentityStore(path)
        self.addCleanup(store.close)
        store.add_user("synthetic-sales", "synthetic password", ["sales"])
        self.assertEqual(store.count_users(), 1)
        self.assertEqual(store.list_users(), [{"user_id": "synthetic-sales", "roles": ["sales"]}])
        actor = store.authenticate("synthetic-sales", "synthetic password")
        self.assertTrue(actor.authenticated)
        self.assertEqual(actor.kind, "human")
        self.assertEqual(actor.roles, ["sales"])
        self.assertIsNone(store.authenticate("synthetic-sales", "wrong password"))
        self.assertIsNone(store.authenticate("missing", "synthetic password"))
        with self.assertRaises(CrossBotError):
            store.add_user("synthetic-sales", "other password", ["admin"])
        for user, password, roles in (("", "password long", ["sales"]),
                                      ("person", "short", ["sales"]),
                                      ("person", "password long", ["invented"])):
            with self.assertRaises(ValidationError):
                store.add_user(user, password, roles)
        with sqlite3.connect(path) as db:
            row = db.execute("SELECT salt,password_hash FROM w3_users").fetchone()
        self.assertNotIn(b"synthetic password", row)
        self.assertNotEqual(row[0], row[1])

if __name__ == "__main__":
    unittest.main()
