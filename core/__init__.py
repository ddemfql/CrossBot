"""Shared domain contracts for CrossBot."""
from .config import AppConfig, load_config
from .errors import CrossBotError, ErrorCode, ParseError, ValidationError
from .events import EventType, WorkflowEvent
from .models import (
    AuditEvent, Case, CaseStatus, CrossCheckResult, CrossCheckStatus, Document,
    DocumentStatus, DocumentType, DocumentVersion, ExtractedField, FieldSource,
    FieldValueStatus, ReviewDecision, ReviewStatus, ReviewTask, RiskItem,
    RiskSeverity, from_dict, new_id, to_dict, utc_now,
)
from .schemas import GenerationResponse, ListingRequest, SalesRequest, WorkflowErrorResponse
from .identity import Permission, Principal, Role, UserSummary
from .v3 import CONTRACT_VERSION, RequestContext

__all__ = [
    "AppConfig", "load_config", "CrossBotError", "ErrorCode", "ParseError", "ValidationError",
    "EventType", "WorkflowEvent", "GenerationResponse", "ListingRequest", "SalesRequest",
    "WorkflowErrorResponse", "AuditEvent", "Case", "CaseStatus", "CrossCheckResult",
    "CrossCheckStatus", "Document", "DocumentStatus", "DocumentType", "DocumentVersion",
    "ExtractedField", "FieldSource", "FieldValueStatus", "ReviewDecision", "ReviewStatus",
    "ReviewTask", "RiskItem", "RiskSeverity", "from_dict", "to_dict", "utc_now", "new_id",
    "Permission", "Principal", "Role", "UserSummary", "CONTRACT_VERSION", "RequestContext",
]
