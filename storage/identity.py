"""Local, provisioned human accounts; public pages cannot assign roles."""
from __future__ import annotations
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import Actor

ROLES = frozenset({"sales", "admin"})
_LEGACY_BUSINESS_ROLES = frozenset({"documents", "reviewer", "logistics", "finance"})
ITERATIONS = 310_000


def _effective_roles(stored_roles):
    """Expose two roles without rewriting old accounts or historical records."""
    roles = set(stored_roles)
    if "admin" in roles:
        return ["admin"]
    return ["sales"] if roles & ({"sales"} | _LEGACY_BUSINESS_ROLES) else []


class SQLiteIdentityStore:
    def __init__(self, db_path):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
        self._lock = RLock()
        self._db.execute("""CREATE TABLE IF NOT EXISTS w3_users (
            user_id TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL,
            roles TEXT NOT NULL, iterations INTEGER NOT NULL)""")
        self._db.commit()

    def add_user(self, user_id: str, password: str, roles: list[str]) -> None:
        if (not isinstance(user_id, str) or not user_id.strip() or user_id != user_id.strip()
                or len(user_id) > 120 or not isinstance(password, str) or len(password) < 10
                or not isinstance(roles, list) or not roles or any(role not in ROLES for role in roles)):
            raise ValidationError("用户、密码或岗位无效；密码至少需要10个字符")
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
        with self._lock:
            try:
                self._db.execute("INSERT INTO w3_users VALUES (?, ?, ?, ?, ?)",
                    (user_id, salt, digest, json.dumps(sorted(set(roles))), ITERATIONS))
                self._db.commit()
            except sqlite3.IntegrityError:
                self._db.rollback()
                raise CrossBotError(ErrorCode.CONFLICT, "用户已存在") from None

    def authenticate(self, user_id: str, password: str) -> Actor | None:
        if not isinstance(user_id, str) or not isinstance(password, str):
            return None
        with self._lock:
            row = self._db.execute("SELECT salt, password_hash, roles, iterations FROM w3_users WHERE user_id=?",
                                   (user_id,)).fetchone()
        salt, expected, roles, rounds = row if row else (b"\0" * 32, b"\0" * 32, "[]", ITERATIONS)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
        if not hmac.compare_digest(digest, expected) or row is None:
            return None
        return Actor(user_id=user_id, roles=_effective_roles(json.loads(roles)), authenticated=True, kind="human")

    def count_users(self) -> int:
        """Return the provisioned account count without exposing credential data."""
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM w3_users").fetchone()[0]

    def list_users(self) -> list[dict[str, object]]:
        """Return identity and role metadata only; password hashes remain private."""
        with self._lock:
            rows = self._db.execute("SELECT user_id, roles FROM w3_users ORDER BY user_id").fetchall()
        return [{"user_id": user_id, "roles": _effective_roles(json.loads(roles))} for user_id, roles in rows]

    def close(self):
        with self._lock:
            self._db.close()
