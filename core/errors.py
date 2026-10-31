"""Stable error types for UI, APIs, parsers, and workflow services."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

class ErrorCode(str, Enum):
    FORBIDDEN = "forbidden"
    INVALID_INPUT = "invalid_input"
    CONFIGURATION = "configuration_error"
    PROVIDER_AUTH = "provider_auth_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PARSE_FAILED = "parse_failed"
    FILE_TYPE_UNSUPPORTED = "file_type_unsupported"
    FILE_TOO_LARGE = "file_too_large"
    REVIEW_REQUIRED = "review_required"
    CROSS_CHECK_FAILED = "cross_check_failed"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"

@dataclass(slots=True)
class CrossBotError(Exception):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __post_init__(self):
        Exception.__init__(self, self.message)

    def to_dict(self):
        return {"code": self.code.value, "message": self.message, "retryable": self.retryable, "details": self.details or {}}

class ValidationError(CrossBotError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.INVALID_INPUT, message, details=details)

class ParseError(CrossBotError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.PARSE_FAILED, message, details=details)
