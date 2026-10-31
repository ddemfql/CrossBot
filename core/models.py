"""Shared domain models for workflow, document, review, and validation layers."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Union, get_args, get_origin, get_type_hints
import types
from uuid import uuid4

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"

class CaseStatus(str, Enum):
    DRAFT = "draft"
    MISSING_DOCUMENTS = "missing_documents"
    PARSING = "parsing"
    PENDING_REVIEW = "pending_review"
    REVIEW_RETURNED = "review_returned"
    CROSS_CHECK_FAILED = "cross_check_failed"
    PASSED = "passed"
    CLOSED = "closed"

class DocumentType(str, Enum):
    COMMERCIAL_INVOICE = "commercial_invoice"
    PACKING_LIST = "packing_list"
    SALES_CONTRACT = "sales_contract"
    BILL_OF_LADING = "bill_of_lading"
    OTHER = "other"

class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    PARSE_FAILED = "parse_failed"
    SUPERSEDED = "superseded"

class FieldSource(str, Enum):
    MACHINE = "machine"
    HUMAN = "human"
    SYSTEM = "system"

class FieldValueStatus(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    MISSING = "missing"

class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    RETURNED = "returned"
    SKIPPED = "skipped"

class CrossCheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING_REVIEW = "pending_review"

class RiskSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"

@dataclass(slots=True)
class RiskItem:
    code: str
    message: str
    severity: RiskSeverity = RiskSeverity.WARNING
    source: str = "rule"
    field_name: str | None = None
    evidence: str | None = None
    blocking: bool = False

@dataclass(slots=True)
class ExtractedField:
    name: str
    value: Any = None
    normalized_value: Any = None
    source: FieldSource = FieldSource.MACHINE
    status: FieldValueStatus = FieldValueStatus.CANDIDATE
    confidence: float | None = None
    page_number: int | None = None
    sheet_name: str | None = None
    cell_reference: str | None = None
    note: str | None = None

@dataclass(slots=True)
class DocumentVersion:
    version_id: str = field(default_factory=lambda: new_id("docver"))
    document_id: str = ""
    version_number: int = 1
    sha256: str = ""
    original_filename: str = ""
    storage_path: str = ""
    uploaded_by: str = ""
    uploaded_at: datetime = field(default_factory=utc_now)
    parser_name: str | None = None
    parser_version: str | None = None
    status: DocumentStatus = DocumentStatus.UPLOADED
    fields: list[ExtractedField] = field(default_factory=list)
    parse_error: str | None = None

@dataclass(slots=True)
class Document:
    document_id: str = field(default_factory=lambda: new_id("doc"))
    case_id: str = ""
    document_type: DocumentType = DocumentType.OTHER
    title: str = ""
    current_version_id: str | None = None
    status: DocumentStatus = DocumentStatus.UPLOADED
    required: bool = True
    versions: list[DocumentVersion] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True)
class ReviewDecision:
    decision_id: str = field(default_factory=lambda: new_id("decision"))
    task_id: str = ""
    reviewer_id: str = ""
    status: ReviewStatus = ReviewStatus.PENDING
    reason: str | None = None
    changed_fields: list[str] = field(default_factory=list)
    decided_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True)
class ReviewTask:
    task_id: str = field(default_factory=lambda: new_id("review"))
    case_id: str = ""
    document_id: str | None = None
    assignee_id: str | None = None
    role: str = "reviewer"
    required: bool = True
    status: ReviewStatus = ReviewStatus.PENDING
    field_names: list[str] = field(default_factory=list)
    decisions: list[ReviewDecision] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True)
class CrossCheckResult:
    result_id: str = field(default_factory=lambda: new_id("check"))
    case_id: str = ""
    rule_code: str = ""
    status: CrossCheckStatus = CrossCheckStatus.PENDING_REVIEW
    message: str = ""
    left_document_id: str | None = None
    right_document_id: str | None = None
    left_field: str | None = None
    right_field: str | None = None
    left_value: Any = None
    right_value: Any = None
    left_source: str | None = None
    right_source: str | None = None
    rule_version: str = "1"
    checked_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True)
class AuditEvent:
    audit_id: str = field(default_factory=lambda: new_id("audit"))
    case_id: str = ""
    actor_id: str = "system"
    action: str = ""
    entity_type: str = ""
    entity_id: str = ""
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    reason: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True)
class Case:
    case_id: str = field(default_factory=lambda: new_id("case"))
    case_number: str = ""
    order_no: str | None = None
    customer_name: str = ""
    destination_country: str = ""
    incoterm: str | None = None
    currency: str | None = None
    invoice_total: Decimal | None = None
    owner_id: str | None = None
    due_at: datetime | None = None
    status: CaseStatus = CaseStatus.DRAFT
    document_ids: list[str] = field(default_factory=list)
    review_task_ids: list[str] = field(default_factory=list)
    cross_check_result_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

def to_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_dict(item) for item in value]
    return value


def from_dict(model_type: type, payload: Mapping[str, Any]) -> Any:
    """Restore a model and its nested enums, dates, and dataclasses."""
    hints = get_type_hints(model_type)
    values = {
        name: _restore_value(hints.get(name, Any), value)
        for name, value in payload.items()
        if name in hints
    }
    return model_type(**values)


def _restore_value(value_type: Any, value: Any) -> Any:
    if value is None or value_type is Any:
        return value
    if value_type is datetime:
        return datetime.fromisoformat(value)
    if value_type is Decimal:
        return Decimal(str(value))
    if isinstance(value_type, type) and issubclass(value_type, Enum):
        return value_type(value)
    origin = get_origin(value_type)
    args = get_args(value_type)
    if origin in (Union, types.UnionType):
        candidates = [candidate for candidate in args if candidate is not type(None)]
        for candidate in candidates:
            try:
                return _restore_value(candidate, value)
            except (TypeError, ValueError):
                continue
        return value
    if origin is list:
        item_type = args[0] if args else Any
        return [_restore_value(item_type, item) for item in value]
    if origin is dict:
        key_type, item_type = args if len(args) == 2 else (Any, Any)
        return {
            _restore_value(key_type, key): _restore_value(item_type, item)
            for key, item in value.items()
        }
    if isinstance(value_type, type) and is_dataclass(value_type):
        return from_dict(value_type, value)
    return value
