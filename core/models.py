"""Shared domain models for workflow, document, review, and validation layers."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
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
    QUOTATION = "quotation"
    ORDER_CONFIRMATION = "order_confirmation"
    PRODUCTION_ORDER = "production_order"
    INSPECTION_RECORD = "inspection_record"
    PACKAGING_ARTWORK = "packaging_artwork"
    BOOKING_REQUEST = "booking_request"
    CUSTOMS_WORKSHEET = "customs_worksheet"
    PAYMENT_NOTICE = "payment_notice"
    STATEMENT_OF_ACCOUNT = "statement_of_account"
    COMPLIANCE_DRAFT = "compliance_draft"
    ISF_WORKSHEET = "isf_worksheet"
    COMBINED_INVOICE_PACKING_LIST = "combined_invoice_packing_list"
    AIR_WAYBILL = "air_waybill"
    CERTIFICATE_OF_ORIGIN = "certificate_of_origin"
    INSURANCE_POLICY = "insurance_policy"
    BANK_RECEIPT = "bank_receipt"
    TEST_REPORT = "test_report"
    INSPECTION_REPORT = "inspection_report"
    PURCHASE_CONTRACT = "purchase_contract"
    FORWARDER_RECEIPT = "forwarder_receipt"
    PROFORMA_INVOICE = "proforma_invoice"
    PURCHASE_ORDER = "purchase_order"
    PRODUCT_SPEC = "product_spec"
    COMPLIANCE = "compliance"
    SHIPPING_INSTRUCTION = "shipping_instruction"
    ISF_RECEIPT = "isf_receipt"
    PHOTO = "photo"
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
    MANUALLY_ENTERED = "manually_entered"
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
    confirmed_value: Any = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    assisted_value: Any = None
    assistance_note: str | None = None

@dataclass(slots=True)
class SourceTable:
    name: str = ""
    rows: list[list[str]] = field(default_factory=list)
    page_number: int | None = None

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
    media_type: str = "application/octet-stream"
    size_bytes: int = 0
    source_party: str = ""
    expires_on: str | None = None
    original_path: str = ""
    source_text: str = ""
    source_tables: list[SourceTable] = field(default_factory=list)
    extraction_warnings: list[str] = field(default_factory=list)

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
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    deletion_reason: str | None = None
    responsibility: str = ""
    classification_note: str = ""

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
    left_version_id: str | None = None
    right_version_id: str | None = None

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
    invoice_total: float | None = None
    owner_id: str | None = None
    due_at: datetime | None = None
    status: CaseStatus = CaseStatus.DRAFT
    document_ids: list[str] = field(default_factory=list)
    review_task_ids: list[str] = field(default_factory=list)
    cross_check_result_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

class TradeMilestone(str, Enum):
    ORDER = "order"
    PREPARING = "preparing"
    READY_TO_SHIP = "ready_to_ship"
    SHIPPED = "shipped"
    DELIVERED = "delivered"

class TradeAdjustmentKind(str, Enum):
    FREIGHT = "freight"
    INSURANCE = "insurance"
    COMMISSION = "commission"
    PACKING = "packing"
    DISCOUNT = "discount"
    REBATE = "rebate"
    DRAWBACK = "drawback"
    BOUNTY = "bounty"
    ASSIST = "assist"
    OTHER = "other"

class ReceivableStatus(str, Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"

@dataclass(slots=True)
class Actor:
    """Trusted identity supplied by an authenticated application boundary."""
    user_id: str
    roles: list[str] = field(default_factory=list)
    authenticated: bool = False
    kind: str = "human"

@dataclass(slots=True)
class TradeLine:
    product_code: str
    description: str
    quantity: str
    unit_price: str
    unit: str = "pcs"
    packages: int = 0
    gross_weight_kg: str = "0"
    net_weight_kg: str = "0"
    hs_code: str = ""
    country_of_origin: str = ""
    grade_quality: str = ""
    marks_and_numbers: str = ""
    package_contents: str = ""

@dataclass(slots=True)
class TradeAdjustment:
    kind: TradeAdjustmentKind = TradeAdjustmentKind.OTHER
    name: str = ""
    amount: str = "0"
    currency: str = "USD"
    included_in_invoice_price: bool = False

@dataclass(slots=True)
class TradePackage:
    package_number: str = ""
    marks_and_numbers: str = ""
    product_code: str = ""
    contents_description: str = ""
    quantity: str = ""
    unit: str = ""
    gross_weight_kg: str = ""
    net_weight_kg: str = ""

@dataclass(slots=True)
class TradeData:
    """Decimal quantities and money travel as strings; never binary floats."""
    order_no: str = ""
    seller: str = ""
    buyer: str = ""
    destination_country: str = ""
    currency: str = "USD"
    incoterm: str = ""
    incoterm_version: str = "2020"
    payment_terms: str = ""
    transport_mode: str = ""
    product_category: str = ""
    expected_ship_date: str | None = None
    actual_ship_date: str | None = None
    due_date: str | None = None
    transaction_date: str | None = None
    transaction_place: str = ""
    port_of_entry: str = ""
    seller_address: str = ""
    buyer_address: str = ""
    responsible_exporter_person: str = ""
    invoice_adjustments_confirmed: bool = False
    adjustments: list[TradeAdjustment] = field(default_factory=list)
    packages: list[TradePackage] = field(default_factory=list)
    line_items: list[TradeLine] = field(default_factory=list)
    revision: int = 1
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None

@dataclass(slots=True)
class RequirementPolicy:
    policy_id: str
    required_types: list[DocumentType] = field(default_factory=list)
    destination_country: str = ""
    product_category: str = ""
    transport_mode: str = ""
    incoterm: str = ""
    payment_terms: str = ""

@dataclass(slots=True)
class GeneratedArtifact:
    artifact_id: str = field(default_factory=lambda: new_id("artifact"))
    document_type: DocumentType = DocumentType.OTHER
    version_number: int = 1
    input_revision: int = 0
    input_snapshot: dict[str, Any] = field(default_factory=dict)
    source_versions: dict[str, str] = field(default_factory=dict)
    template_id: str = ""
    template_version: str = ""
    template_sha256: str = ""
    rule_version: str = "w3-1"
    storage_path: str = ""
    sha256: str = ""
    media_type: str = "text/html"
    filename: str = ""
    created_by: str = ""
    created_at: datetime = field(default_factory=utc_now)
    status: str = "draft"
    checks: list[CrossCheckResult] = field(default_factory=list)
    form_snapshot: dict[str, Any] = field(default_factory=dict)
    form_revision: int = 0

@dataclass(slots=True)
class ApprovalRecord:
    approval_id: str = field(default_factory=lambda: new_id("approval"))
    actor_id: str = ""
    reason: str = ""
    input_revision: int = 0
    document_versions: dict[str, str] = field(default_factory=dict)
    artifact_hashes: dict[str, str] = field(default_factory=dict)
    checks_sha256: str = ""
    approved_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True)
class PaymentRecord:
    payment_id: str = field(default_factory=lambda: new_id("payment"))
    amount: str = "0"
    currency: str = "USD"
    reference: str = ""
    received_on: str = ""
    recorded_by: str = ""
    recorded_at: datetime = field(default_factory=utc_now)

@dataclass(slots=True)
class WorkItem:
    task_id: str = field(default_factory=lambda: new_id("task"))
    title: str = ""
    role: str = "documents"
    assignee_id: str | None = None
    status: str = "pending"
    note: str = ""

@dataclass(slots=True)
class FormValueOrigin:
    """Read-only explanation of one displayed/exported form value."""
    field_name: str
    value: str
    origin_kind: str
    row_number: int | None = None
    document_id: str = ""
    version_id: str = ""
    filename: str = ""
    version_number: int | None = None
    location: str = ""
    actor_id: str = ""
    occurred_at: datetime | None = None
    note: str = ""

@dataclass(slots=True)
class FormFieldCandidate:
    """Unconfirmed value tied to a specific source file version and position."""
    value: str
    document_id: str
    version_id: str
    field_name: str = ""
    page_number: int | None = None
    sheet_name: str | None = None
    cell_reference: str | None = None

@dataclass(slots=True)
class FormRowCandidate:
    """One source row; identity is its source version/table/row, not its SKU."""
    values: dict[str, str]
    document_id: str
    version_id: str
    table_index: int
    row_number: int
    table_name: str = ""
    page_number: int | None = None
    cell_references: dict[str, str] = field(default_factory=dict)

@dataclass(slots=True)
class DocumentForm:
    document_type: DocumentType
    values: dict[str, str] = field(default_factory=dict)
    rows: list[dict[str, str]] = field(default_factory=list)
    source_document_ids: list[str] = field(default_factory=list)
    revision: int = 1
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    field_candidates: dict[str, list[FormFieldCandidate]] = field(default_factory=dict)
    row_candidates: list[FormRowCandidate] = field(default_factory=list)
    auto_values: dict[str, str] = field(default_factory=dict)
    auto_rows: list[dict[str, str]] = field(default_factory=list)
    manual_fields: list[str] = field(default_factory=list)
    rows_manually_edited: bool = False

@dataclass(slots=True)
class ImportContext:
    archive_name: str = ""
    archive_sha256: str = ""
    imported_by: str = ""
    imported_at: datetime = field(default_factory=utc_now)
    reported_trade_state: str = ""
    reported_receivable_state: str = ""

@dataclass(slots=True)
class AssistanceRecord:
    """Unconfirmed transcription by a virtual salesperson, never human sign-off."""
    method: str = "visual_transcription"
    document_id: str = ""
    version_id: str = ""
    location: str = ""
    field_name: str = ""
    previous_value: str = ""
    proposed_value: str = ""
    reason: str = ""
    created_at: datetime = field(default_factory=utc_now)
    actor_kind: str = "virtual_sales"

@dataclass(slots=True)
class Workspace:
    case: Case
    trade: TradeData = field(default_factory=TradeData)
    revision: int = 0
    required_document_types: list[DocumentType] = field(default_factory=list)
    base_required_document_types: list[DocumentType] = field(default_factory=list)
    requirement_policies: list[RequirementPolicy] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    review_tasks: list[ReviewTask] = field(default_factory=list)
    checks: list[CrossCheckResult] = field(default_factory=list)
    artifacts: list[GeneratedArtifact] = field(default_factory=list)
    approvals: list[ApprovalRecord] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    payments: list[PaymentRecord] = field(default_factory=list)
    tasks: list[WorkItem] = field(default_factory=list)
    milestone: TradeMilestone = TradeMilestone.ORDER
    shipped_on: str | None = None
    shipment_reference: str = ""
    document_forms: list[DocumentForm] = field(default_factory=list)
    import_context: ImportContext | None = None
    assistance_records: list[AssistanceRecord] = field(default_factory=list)

def to_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_dict(item) for item in value]
    return value
