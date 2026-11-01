"""Authenticated encrypted preferences in a private repository table."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import json
import sys
from urllib.parse import urlsplit

from core.errors import CrossBotError, ErrorCode, ValidationError
from providers.llm_client import resolve_api_endpoint


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class WindowsDPAPI:
    """Current Windows account scope, with application-user-specific entropy."""

    def _crypt(self, value, entropy, *, decrypt=False):
        if sys.platform != "win32":
            raise CrossBotError(ErrorCode.CONFIGURATION, "安全保存需要 Windows DPAPI")
        source_buffer = ctypes.create_string_buffer(value)
        entropy_buffer = ctypes.create_string_buffer(entropy)
        source = _Blob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        extra = _Blob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = _Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        function.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
            winerror = ctypes.get_last_error()
            raise CrossBotError(ErrorCode.CONFIGURATION,
                                f"Windows DPAPI 不可用（WinError {winerror}）；请以已加载用户配置文件的 Windows 账号运行",
                                details={"dpapi_winerror": winerror})
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel32.LocalFree(output.data)

    def encrypt(self, value, entropy):
        return self._crypt(value, entropy)

    def decrypt(self, value, entropy):
        return self._crypt(value, entropy, decrypt=True)


@dataclass(frozen=True)
class ProviderSettings:
    url: str = "https://api.tokenhubcloud.net"
    model: str = "gpt-6.1-sol"
    protocol: str = "auto"
    api_key: str = field(default="", repr=False)
    remember: bool = False

    def validate(self):
        if (not all(isinstance(value, str) for value in (self.url, self.model, self.protocol, self.api_key))
                or not isinstance(self.remember, bool) or not self.model.strip()
                or max(map(len, (self.url, self.model, self.api_key))) > 16384
                or self.protocol not in {"auto", "responses", "chat_completions"}):
            raise ValidationError("供应商配置无效")
        try:
            parsed = urlsplit(self.url)
            if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
                raise ValueError()
            resolve_api_endpoint(self.url, self.protocol)
        except (CrossBotError, ValueError):
            raise ValidationError("供应商服务地址无效，不能包含凭据、查询参数或片段") from None


class UserSettingsRepository:
    def __init__(self, auth, *, cipher=None):
        self.auth = auth
        self.cipher = cipher if cipher is not None else WindowsDPAPI()

    @staticmethod
    def _initialize(connection):
        connection.execute("""CREATE TABLE IF NOT EXISTS user_provider_settings (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            encrypted_payload BLOB NOT NULL
        )""")

    def _authorize(self, connection, token, principal):
        current = self.auth._principal(connection, token)
        if current.user_id != principal.user_id:
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "配置不属于当前登录账号")
        self._initialize(connection)
        return current.user_id

    @staticmethod
    def _entropy(user_id):
        return ("CrossBot/provider-settings/v1/" + user_id).encode("utf-8")

    def load(self, token, principal):
        with self.auth.repository.transaction() as connection:
            user_id = self._authorize(connection, token, principal)
            row = connection.execute("SELECT encrypted_payload FROM user_provider_settings WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                return ProviderSettings()
            try:
                payload = json.loads(self.cipher.decrypt(bytes(row[0]), self._entropy(user_id)))
                if payload.pop("version") != 1:
                    raise ValueError()
                settings = ProviderSettings(**payload)
                settings.validate()
                if not settings.remember and settings.api_key:
                    raise ValueError()
                return settings
            except Exception:
                raise CrossBotError(ErrorCode.CONFIGURATION,
                                    "已保存的配置无法解密，请检查 Windows 运行账号及用户配置文件；原配置未改动") from None

    def save(self, token, principal, settings):
        with self.auth.repository.transaction() as connection:
            user_id = self._authorize(connection, token, principal)
            settings.validate()
            payload = json.dumps({"version": 1, "url": settings.url, "model": settings.model,
                                  "protocol": settings.protocol, "remember": settings.remember,
                                  "api_key": settings.api_key if settings.remember else ""}).encode("utf-8")
            try:
                encrypted = self.cipher.encrypt(payload, self._entropy(user_id))
                if not isinstance(encrypted, bytes) or not encrypted:
                    raise ValueError()
            except Exception as error:
                if isinstance(self.cipher, WindowsDPAPI) and isinstance(error, CrossBotError):
                    raise CrossBotError(ErrorCode.CONFIGURATION, "配置未保存：" + error.message) from None
                raise CrossBotError(ErrorCode.CONFIGURATION, "配置未保存：安全加密失败") from None
            connection.execute("""INSERT INTO user_provider_settings VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET encrypted_payload=excluded.encrypted_payload""", (user_id, encrypted))

    def delete(self, token, principal):
        with self.auth.repository.transaction() as connection:
            user_id = self._authorize(connection, token, principal)
            connection.execute("DELETE FROM user_provider_settings WHERE user_id=?", (user_id,))
