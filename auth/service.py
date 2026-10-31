"""Authenticate opaque tokens; never accept a caller's role as authority."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.identity import Permission, Principal, Role, UserSummary
from core.models import new_id
from storage.v3_store import V3Repository, encode

COMMON = {Permission.CASE_READ, Permission.ATTACHMENT_READ, Permission.KNOWLEDGE_READ}
ROLE_PERMISSIONS = {
    Role.ADMIN: set(Permission),
    Role.CUSTOMER_SERVICE: COMMON | {Permission.CASE_CREATE, Permission.W1_GENERATE, Permission.CUSTOMER_SIMULATE, Permission.ATTACHMENT_WRITE, Permission.EXTERNAL_SEARCH},
    Role.PRODUCT: COMMON | {Permission.KNOWLEDGE_EDIT, Permission.ATTACHMENT_WRITE},
    Role.OPERATIONS: COMMON | {Permission.W2_GENERATE},
    Role.DOCUMENT: COMMON | {Permission.CASE_CREATE, Permission.CASE_UPDATE, Permission.ATTACHMENT_WRITE, Permission.ATTACHMENT_CONFIRM, Permission.CROSS_CHECK},
    Role.REVIEWER: COMMON | {Permission.ATTACHMENT_CONFIRM, Permission.KNOWLEDGE_REVIEW, Permission.GENERATION_REVIEW, Permission.CASE_REVIEW, Permission.CASE_CLOSE, Permission.CROSS_CHECK},
}


def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000)
    return f"pbkdf2_sha256$310000${salt}${digest.hex()}"


def _verify_password(password, encoded):
    algorithm, iterations, salt, expected = encoded.split("$")
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    return hmac.compare_digest(actual, expected)


class AuthService:
    def __init__(self, store):
        self.store = store
        self.repository = V3Repository(store)

    def needs_bootstrap(self):
        with self.repository.transaction() as conn:
            return conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0

    def bootstrap_admin(self, username, password, display_name="Administrator"):
        with self.repository.transaction() as conn:
            if conn.execute("SELECT count(*) FROM users").fetchone()[0]:
                raise CrossBotError(ErrorCode.PERMISSION_DENIED, "初始管理员已创建")
            user = self._insert_user(conn, username, password, display_name, [Role.ADMIN])
            self.repository.audit(conn, "admin_bootstrap", user.user_id, entity_type="user", entity_id=user.user_id)
            return user

    def _insert_user(self, conn, username, password, display_name, roles):
        username = username.strip()
        if not username or len(password) < 12 or not roles:
            raise ValidationError("账号不能为空、密码至少 12 位，且必须分配角色")
        roles = [Role(role) for role in roles]
        if conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            raise CrossBotError(ErrorCode.CONFLICT, "账号已存在")
        user = UserSummary(new_id("user"), username, display_name.strip() or username, roles)
        conn.execute(
            "INSERT INTO users(id, username, display_name, password_hash, roles) VALUES (?, ?, ?, ?, ?)",
            (user.user_id, username, user.display_name, _hash_password(password), encode(roles)),
        )
        return user

    def create_user(self, token, username, password, display_name, roles):
        with self.repository.transaction() as conn:
            principal = self._require(conn, token, Permission.USER_MANAGE)
            user = self._insert_user(conn, username, password, display_name, roles)
            self.repository.audit(conn, "user_created", principal.user_id, entity_type="user", entity_id=user.user_id, after={"roles": roles})
            return user

    def login(self, username, password):
        error = None
        token = None
        with self.repository.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
            now = time.time()
            valid = user and user["status"] == "active" and user["locked_until"] <= now
            if not valid or not _verify_password(password, user["password_hash"]):
                if user:
                    attempts = user["failed_attempts"] + 1
                    locked = now + 300 if attempts >= 5 else user["locked_until"]
                    conn.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?", (attempts, locked, user["id"]))
                self.repository.audit(conn, "login_failed", user["id"] if user else "anonymous", entity_type="user")
                error = CrossBotError(ErrorCode.UNAUTHENTICATED, "账号或密码错误，或账号暂不可用")
            else:
                token = secrets.token_urlsafe(32)
                role = json.loads(user["roles"])[0]
                conn.execute("INSERT INTO auth_sessions VALUES (?, ?, ?, ?)", (self._token_hash(token), user["id"], role, now + 8 * 3600))
                conn.execute("UPDATE users SET failed_attempts=0, locked_until=0 WHERE id=?", (user["id"],))
                self.repository.audit(conn, "login_succeeded", user["id"], entity_type="user", entity_id=user["id"])
        if error:
            raise error
        return token

    @staticmethod
    def _token_hash(token):
        if not isinstance(token, str) or not token:
            raise CrossBotError(ErrorCode.UNAUTHENTICATED, "请先登录")
        return hashlib.sha256(token.encode()).hexdigest()

    def _principal(self, conn, token):
        row = conn.execute(
            "SELECT u.*, s.active_role, s.expires_at FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
            (self._token_hash(token),),
        ).fetchone()
        if row is None or row["expires_at"] <= time.time():
            raise CrossBotError(ErrorCode.UNAUTHENTICATED, "登录已失效")
        if row["status"] != "active":
            raise CrossBotError(ErrorCode.ACCOUNT_DISABLED, "账号已停用")
        roles = [Role(value) for value in json.loads(row["roles"])]
        active_role = Role(row["active_role"])
        if active_role not in roles:
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "当前角色授权已撤销，请重新登录")
        return Principal(row["id"], row["username"], row["display_name"], roles, active_role)

    def require_session(self, token):
        with self.repository.transaction() as conn:
            return self._principal(conn, token)

    def _require(self, conn, token, action, case_id=None):
        principal = self._principal(conn, token)
        try:
            action = Permission(action)
        except ValueError as error:
            raise ValidationError("未知权限操作") from error
        if action not in ROLE_PERMISSIONS[principal.active_role]:
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "当前角色无此操作权限", details={"action": action.value})
        if case_id is not None:
            if conn.execute("SELECT id FROM cases WHERE id=?", (case_id,)).fetchone() is None:
                raise CrossBotError(ErrorCode.NOT_FOUND, "Case 不存在")
            assigned = conn.execute("SELECT 1 FROM case_assignments WHERE case_id=? AND user_id=?", (case_id, principal.user_id)).fetchone()
            if principal.active_role != Role.ADMIN and not assigned:
                raise CrossBotError(ErrorCode.PERMISSION_DENIED, "该 Case 未分配给当前账号")
        return principal

    def require_permission(self, token, action, case_id=None):
        try:
            with self.repository.transaction() as conn:
                return self._require(conn, token, action, case_id)
        except CrossBotError as error:
            with self.repository.transaction() as conn:
                try:
                    actor_id = self._principal(conn, token).user_id
                except CrossBotError:
                    actor_id = "anonymous"
                self.repository.audit(conn, "permission_denied", actor_id, case_id=case_id or "", after={"action": str(action), "code": error.code.value})
            raise

    def switch_role(self, token, role):
        try:
            role = Role(role)
        except ValueError as error:
            raise ValidationError("未知角色") from error
        with self.repository.transaction() as conn:
            principal = self._principal(conn, token)
            if role not in principal.allowed_roles:
                raise CrossBotError(ErrorCode.PERMISSION_DENIED, "账号没有该角色授权")
            conn.execute("UPDATE auth_sessions SET active_role=? WHERE token_hash=?", (role.value, self._token_hash(token)))
            self.repository.audit(conn, "role_switched", principal.user_id, before={"role": principal.active_role.value}, after={"role": role.value})
        return self.require_session(token)

    def logout(self, token):
        with self.repository.transaction() as conn:
            principal = self._principal(conn, token)
            conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (self._token_hash(token),))
            self.repository.audit(conn, "logout", principal.user_id)

    def list_users(self, token):
        with self.repository.transaction() as conn:
            self._require(conn, token, Permission.USER_MANAGE)
            return [UserSummary(row["id"], row["username"], row["display_name"], [Role(r) for r in json.loads(row["roles"])], row["status"]) for row in conn.execute("SELECT * FROM users ORDER BY username")]

    def update_user(self, token, user_id, *, roles=None, status=None):
        with self.repository.transaction() as conn:
            principal = self._require(conn, token, Permission.USER_MANAGE)
            user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if user is None:
                raise CrossBotError(ErrorCode.NOT_FOUND, "账号不存在")
            new_roles = [Role(r).value for r in roles] if roles is not None else json.loads(user["roles"])
            new_status = status or user["status"]
            if not new_roles or new_status not in {"active", "disabled"}:
                raise ValidationError("无效账号状态或角色")
            if user_id == principal.user_id and (new_status != "active" or Role.ADMIN.value not in new_roles):
                raise ValidationError("管理员不能移除自己的管理权限")
            conn.execute("UPDATE users SET roles=?, status=? WHERE id=?", (encode(new_roles), new_status, user_id))
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            self.repository.audit(conn, "user_permissions_changed", principal.user_id, entity_type="user", entity_id=user_id, after={"roles": new_roles, "status": new_status})

    def assign_case(self, token, case_id, user_id):
        with self.repository.transaction() as conn:
            principal = self._require(conn, token, Permission.CASE_ASSIGN, case_id)
            if conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone() is None:
                raise CrossBotError(ErrorCode.NOT_FOUND, "账号不存在")
            conn.execute("INSERT OR IGNORE INTO case_assignments VALUES (?, ?)", (case_id, user_id))
            self.repository.audit(conn, "case_assigned", principal.user_id, case_id=case_id, entity_type="user", entity_id=user_id)

    def revoke_case(self, token, case_id, user_id):
        with self.repository.transaction() as conn:
            principal = self._require(conn, token, Permission.CASE_ASSIGN, case_id)
            conn.execute("DELETE FROM case_assignments WHERE case_id=? AND user_id=?", (case_id, user_id))
            self.repository.audit(conn, "case_unassigned", principal.user_id, case_id=case_id, entity_type="user", entity_id=user_id)
