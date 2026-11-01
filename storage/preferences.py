"""Append-only per-user document defaults with optimistic concurrency."""
import json
import sqlite3
from pathlib import Path
from threading import RLock

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import UserWorkbenchDefaults, from_dict, to_dict


class SQLiteUserDefaultsStore:
    def __init__(self, db_path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
        self._lock = RLock()
        self._db.execute("PRAGMA busy_timeout=15000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS w3_user_defaults (
                user_id TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
                PRIMARY KEY(user_id, revision));
            CREATE TRIGGER IF NOT EXISTS w3_user_defaults_no_update
                BEFORE UPDATE ON w3_user_defaults BEGIN
                SELECT RAISE(ABORT, 'personal defaults history is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS w3_user_defaults_no_delete
                BEFORE DELETE ON w3_user_defaults BEGIN
                SELECT RAISE(ABORT, 'personal defaults history is immutable'); END;
        """)

    def get(self, user_id):
        with self._lock:
            row = self._db.execute("SELECT snapshot FROM w3_user_defaults WHERE user_id=? "
                "ORDER BY revision DESC LIMIT 1", (user_id,)).fetchone()
        return from_dict(UserWorkbenchDefaults, json.loads(row[0])) if row else None

    def save(self, defaults, expected_revision):
        if (not isinstance(defaults, UserWorkbenchDefaults) or not defaults.user_id.strip()
                or isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
                or expected_revision < 0 or defaults.revision != expected_revision):
            raise ValidationError("个人默认配置的版本参数无效。")
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                row = self._db.execute("SELECT MAX(revision) FROM w3_user_defaults WHERE user_id=?",
                    (defaults.user_id,)).fetchone()
                current = row[0] or 0
                if current != expected_revision:
                    raise CrossBotError(ErrorCode.CONFLICT, "个人默认配置已更新，请刷新后重试。")
                snapshot = to_dict(defaults)
                snapshot["revision"] = current + 1
                self._db.execute("INSERT INTO w3_user_defaults VALUES (?, ?, ?)",
                    (defaults.user_id, current + 1, json.dumps(snapshot, ensure_ascii=False,
                     allow_nan=False, sort_keys=True)))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        defaults.revision = current + 1

    def history(self, user_id):
        with self._lock:
            rows = self._db.execute("SELECT snapshot FROM w3_user_defaults WHERE user_id=? ORDER BY revision",
                (user_id,)).fetchall()
        return [from_dict(UserWorkbenchDefaults, json.loads(row[0])) for row in rows]

    def close(self):
        with self._lock:
            self._db.close()

