"""Read-only preflight helpers using existing contracts.

No field aliases, final-value selection, rule orchestration or result reports
are defined here. Those still require the A/C integration decisions.
"""
from __future__ import annotations

from core.errors import ErrorCode, ValidationError
from core.models import (
    Case, Document, DocumentStatus, DocumentVersion, ExtractedField, FieldValueStatus,
)


def get_current_version(case: Case, document: Document) -> DocumentVersion:
    """Resolve the unique parsed current version belonging to this Case/document."""
    if not case.case_id or not document.document_id or document.case_id != case.case_id:
        raise ValidationError("Document identity or Case ownership is invalid",
                              details={"code": ErrorCode.CONFLICT.value})
    if not document.current_version_id:
        raise ValidationError("Current document version is missing",
                              details={"code": ErrorCode.REVIEW_REQUIRED.value})
    versions = [version for version in document.versions
                if version.version_id == document.current_version_id]
    if len(versions) != 1:
        raise ValidationError("Current document version must exist exactly once",
                              details={"code": ErrorCode.CONFLICT.value})
    version = versions[0]
    if version.document_id != document.document_id:
        raise ValidationError("Version belongs to a different document",
                              details={"code": ErrorCode.CONFLICT.value})
    if (document.status is not DocumentStatus.PARSED
            or version.status is not DocumentStatus.PARSED
            or not version.sha256 or version.version_number < 1):
        raise ValidationError("Document/version is not ready for comparison",
                              details={"code": ErrorCode.REVIEW_REQUIRED.value})
    return version


def get_confirmed_field(version: DocumentVersion, field_name: str) -> ExtractedField:
    """Return one confirmed field by an explicitly supplied exact name.

    Return the object without choosing value vs normalized_value. Presence of
    the field is not proof that its final value or source evidence is complete.
    """
    if not isinstance(field_name, str) or not field_name.strip():
        raise ValidationError("An explicit field name is required")
    if version.status is not DocumentStatus.PARSED:
        raise ValidationError("Version has not completed parsing",
                              details={"code": ErrorCode.REVIEW_REQUIRED.value})
    fields = [field for field in version.fields if field.name == field_name]
    if len(fields) != 1 or fields[0].status is not FieldValueStatus.CONFIRMED:
        raise ValidationError("Field must exist exactly once and be confirmed",
                              details={"code": ErrorCode.REVIEW_REQUIRED.value,
                                       "field": field_name})
    return fields[0]

