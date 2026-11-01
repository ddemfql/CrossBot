"""Workflow request and response schemas for W1, W2, and shared UI code."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .models import RiskItem, new_id, to_dict
from .v3 import RequestContext

@dataclass(slots=True)
class SalesRequest:
    inquiry: str
    product_info: str
    target_language: str
    customer_country: str | None = None
    requested_quantity: int | None = None
    correlation_id: str = field(default_factory=lambda: new_id("sales"))
    context: RequestContext | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

@dataclass(slots=True)
class ListingRequest:
    product_description: str
    platforms: list[str]
    target_country: str | None = None
    target_language: str = "English"
    correlation_id: str = field(default_factory=lambda: new_id("listing"))
    context: RequestContext | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

@dataclass(slots=True)
class GenerationResponse:
    content: str
    risks: list[RiskItem] = field(default_factory=list)
    pending_confirmations: list[str] = field(default_factory=list)
    correlation_id: str | None = None
    provider: str | None = None
    model: str | None = None
    context: RequestContext | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)

@dataclass(slots=True)
class WorkflowErrorResponse:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_dict(self)


@dataclass(slots=True)
class WorkflowConversation:
    """One persisted W1/W2 conversation per Case, with optimistic locking."""

    case_id: str
    workflow: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    revision: int = 0
