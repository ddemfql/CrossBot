"""Versioned integration contracts for B/C/D; no external providers here."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .models import ExtractedField, new_id, utc_now

CONTRACT_VERSION = "3.0.0"


class LifecycleStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ResourceKind(str, Enum):
    PRODUCT = "product"
    KNOWLEDGE = "knowledge"
    PROHIBITED_TERM = "prohibited_term"


class SourceKind(str, Enum):
    LOCAL = "local"
    AUTHORIZED_API = "authorized_api"
    EXTERNAL_REFERENCE = "external_reference"
    DEMO = "demo"


class AttachmentStatus(str, Enum):
    READY = "ready"
    PENDING_REVIEW = "pending_review"
    FAILED = "failed"


@dataclass(slots=True)
class EvidenceLocation:
    page_number: int | None = None
    image_region: list[float] | None = None
    sheet_name: str | None = None
    cell_reference: str | None = None
    row_number: int | None = None
    column_name: str | None = None
    text_excerpt: str | None = None


@dataclass(slots=True)
class AttachmentRef:
    document_id: str
    version_id: str


@dataclass(slots=True)
class FieldRevision:
    field_name: str
    raw_value: Any = None
    candidate_value: Any = None
    confirmed_value: Any = None
    location: EvidenceLocation = field(default_factory=EvidenceLocation)
    status: str = "candidate"
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AttachmentContent:
    case_id: str
    reference: AttachmentRef
    method: str
    status: AttachmentStatus = AttachmentStatus.PENDING_REVIEW
    text: str = ""
    fields: list[FieldRevision] = field(default_factory=list)
    confidence: float | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class RequestContext:
    case_id: str
    actor_id: str = ""
    product_version_id: str | None = None
    attachments: list[AttachmentRef] = field(default_factory=list)
    confirmed_fields: list[ExtractedField] = field(default_factory=list)
    contract_version: str = CONTRACT_VERSION


@dataclass(slots=True)
class ResourceVersion:
    resource_id: str
    kind: ResourceKind
    content: dict[str, Any]
    source: str
    version_id: str = field(default_factory=lambda: new_id("version"))
    source_url: str | None = None
    source_kind: SourceKind = SourceKind.LOCAL
    version_number: int = 1
    parent_version_id: str | None = None
    status: LifecycleStatus = LifecycleStatus.DRAFT
    effective_date: str | None = None
    market: str | None = None
    platform: str | None = None
    product_scope: str | None = None
    language: str | None = None
    created_by: str = ""
    reviewed_by: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    published_at: datetime | None = None
    archived_at: datetime | None = None


@dataclass(slots=True)
class Publication:
    resource_id: str
    version_id: str
    actor_id: str
    publication_id: str = field(default_factory=lambda: new_id("publication"))
    previous_publication_id: str | None = None
    is_rollback: bool = False
    reason: str = ""
    published_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SearchCitation:
    title: str
    url: str | None
    retrieved_at: datetime
    matched_name: str = ""
    confidence: float | None = None
    excerpt: str = ""
    review_status: str = "pending_review"


@dataclass(slots=True)
class SearchReport:
    case_id: str
    actor_id: str
    query: str
    purpose: str
    source_kind: SourceKind
    citations: list[SearchCitation]
    search_id: str = field(default_factory=lambda: new_id("search"))
    authorization_audit_id: str = ""
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class GenerationArtifact:
    case_id: str
    actor_id: str
    workflow: str
    content: str
    context: RequestContext
    artifact_id: str = field(default_factory=lambda: new_id("generation"))
    parent_artifact_id: str | None = None
    origin: str = "ai"
    review_status: str = "pending_review"
    reviewed_by: str | None = None
    risks: list[dict[str, Any]] = field(default_factory=list)
    pending_confirmations: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ProductData:
    name: str
    sku: str = ""
    specifications: dict[str, Any] = field(default_factory=dict)
    materials: list[str] = field(default_factory=list)
    package_info: dict[str, Any] = field(default_factory=dict)
    allowed_claims: list[str] = field(default_factory=list)
    claims_requiring_evidence: list[str] = field(default_factory=list)
    certificates: list[dict[str, Any]] = field(default_factory=list)
    source_files: list[AttachmentRef] = field(default_factory=list)


@dataclass(slots=True)
class KnowledgeData:
    text: str
    category: str
    applicability: str = ""


@dataclass(slots=True)
class ProhibitedTermData:
    term: str
    pattern_type: str = "exact"
    severity: str = "warning"
    replacement_suggestion: str = ""
    synonyms: list[str] = field(default_factory=list)
