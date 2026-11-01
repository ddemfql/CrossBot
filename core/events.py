"""Structured events emitted by workflow and document pipelines."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from .models import new_id, to_dict, utc_now

class EventType(str, Enum):
    TEXT_DELTA = "text_delta"
    STATUS = "status"
    WARNING = "warning"
    ERROR = "error"
    RESULT = "result"

@dataclass(slots=True)
class WorkflowEvent:
    type: EventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("event"))
    correlation_id: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

    @classmethod
    def text_delta(cls, text: str, correlation_id: str | None = None):
        return cls(EventType.TEXT_DELTA, {"text": text}, correlation_id=correlation_id)

    @classmethod
    def status(cls, name: str, message: str = "", correlation_id: str | None = None):
        return cls(EventType.STATUS, {"name": name, "message": message}, correlation_id=correlation_id)

    @classmethod
    def warning(cls, message: str, code: str | None = None, correlation_id: str | None = None):
        payload = {"message": message}
        if code:
            payload["code"] = code
        return cls(EventType.WARNING, payload, correlation_id=correlation_id)

    @classmethod
    def error(cls, code: str, message: str, retryable: bool = False, correlation_id: str | None = None):
        return cls(EventType.ERROR, {"code": code, "message": message, "retryable": retryable}, correlation_id=correlation_id)

    @classmethod
    def result(cls, data: Mapping[str, Any], correlation_id: str | None = None):
        return cls(EventType.RESULT, data, correlation_id=correlation_id)
