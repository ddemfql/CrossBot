"""Validation and guarded state transitions for shared domain objects."""
from __future__ import annotations

from decimal import Decimal
from .errors import ErrorCode, ValidationError
from .models import AuditEvent, Case, CaseStatus, CrossCheckResult, CrossCheckStatus, Document, DocumentStatus, DocumentType, FieldValueStatus, ReviewTask, ReviewStatus, to_dict

_ALLOWED_TRANSITIONS = {
    CaseStatus.DRAFT: {CaseStatus.MISSING_DOCUMENTS, CaseStatus.PARSING, CaseStatus.PENDING_REVIEW},
    CaseStatus.MISSING_DOCUMENTS: {CaseStatus.DRAFT, CaseStatus.PARSING},
    CaseStatus.PARSING: {CaseStatus.PENDING_REVIEW, CaseStatus.MISSING_DOCUMENTS},
    CaseStatus.PENDING_REVIEW: {CaseStatus.REVIEW_RETURNED, CaseStatus.CROSS_CHECK_FAILED, CaseStatus.PASSED},
    CaseStatus.REVIEW_RETURNED: {CaseStatus.PENDING_REVIEW, CaseStatus.PARSING},
    CaseStatus.CROSS_CHECK_FAILED: {CaseStatus.PENDING_REVIEW, CaseStatus.REVIEW_RETURNED},
    CaseStatus.PASSED: {CaseStatus.CLOSED, CaseStatus.PENDING_REVIEW},
    CaseStatus.CLOSED: set(),
}


def validate_case(case: Case) -> None:
    required = {"case_number": case.case_number, "customer_name": case.customer_name, "destination_country": case.destination_country}
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValidationError("Case 缺少必填字段", details={"fields": missing})
    if case.invoice_total is not None and Decimal(str(case.invoice_total)) < 0:
        raise ValidationError("发票总金额不能为负数", details={"field": "invoice_total"})


def validate_document(document: Document) -> None:
    if not document.case_id:
        raise ValidationError("文档必须关联 Case", details={"field": "case_id"})
    if not document.versions:
        raise ValidationError("文档至少需要一个版本", details={"field": "versions"})
    for version in document.versions:
        if not version.sha256:
            raise ValidationError("文档版本缺少 SHA-256", details={"version_id": version.version_id})
        if version.version_number < 1:
            raise ValidationError("文档版本号必须从 1 开始", details={"version_id": version.version_id})


def validate_case_documents(case: Case, documents: list[Document]) -> None:
    if not documents:
        raise ValidationError("Case 至少需要一个文档", details={"code": ErrorCode.REVIEW_REQUIRED.value})
    by_type = {document.document_type for document in documents}
    missing_types = [document_type.value for document_type in case.required_document_types if document_type not in by_type]
    if missing_types:
        raise ValidationError("Case 缺少必需单据", details={"document_types": missing_types})
    for document in documents:
        validate_document(document)
        if document.status != DocumentStatus.PARSED:
            raise ValidationError("所有文档必须完成解析", details={"document_id": document.document_id})
        if not document.current_version_id:
            raise ValidationError("文档缺少当前版本", details={"document_id": document.document_id})
        current_versions = [version for version in document.versions if version.version_id == document.current_version_id]
        if len(current_versions) != 1:
            raise ValidationError("文档当前版本无效", details={"document_id": document.document_id})
        if any(field.status != FieldValueStatus.CONFIRMED for field in current_versions[0].fields):
            raise ValidationError("仍有文档字段未完成人工确认", details={"document_id": document.document_id})


def validate_case_transition(case: Case, target: CaseStatus, *, documents: list[Document] | None = None, review_tasks: list[ReviewTask] | None = None, checks: list[CrossCheckResult] | None = None) -> None:
    validate_case(case)
    if target not in _ALLOWED_TRANSITIONS.get(case.status, set()):
        raise ValidationError("非法 Case 状态转换", details={"from": case.status.value, "to": target.value})
    docs = documents or []
    tasks = review_tasks or []
    results = checks or []
    if target in {CaseStatus.PENDING_REVIEW, CaseStatus.PASSED, CaseStatus.CLOSED}:
        validate_case_documents(case, docs)
    if target in {CaseStatus.PASSED, CaseStatus.CLOSED}:
        if not tasks:
            raise ValidationError("Case 至少需要一个人工复核任务", details={"code": ErrorCode.REVIEW_REQUIRED.value})
        if not results:
            raise ValidationError("Case 至少需要一项交叉比对结果", details={"code": ErrorCode.CROSS_CHECK_FAILED.value})
        if any(task.required and task.status != ReviewStatus.APPROVED for task in tasks):
            raise ValidationError("仍有必需的人工复核任务未通过", details={"code": ErrorCode.REVIEW_REQUIRED.value})
        if any(result.status != CrossCheckStatus.PASS for result in results):
            raise ValidationError("仍有交叉比对未通过或待复核", details={"code": ErrorCode.CROSS_CHECK_FAILED.value})


def transition_case(case: Case, target: CaseStatus, *, documents: list[Document] | None = None, review_tasks: list[ReviewTask] | None = None, checks: list[CrossCheckResult] | None = None, actor_id: str = "system", reason: str | None = None) -> tuple[Case, AuditEvent]:
    validate_case_transition(case, target, documents=documents, review_tasks=review_tasks, checks=checks)
    from .models import utc_now
    before = {"status": case.status.value, "updated_at": case.updated_at.isoformat()}
    case.status = target
    case.updated_at = utc_now()
    audit = AuditEvent(
        case_id=case.case_id,
        actor_id=actor_id,
        action="case_status_changed",
        entity_type="case",
        entity_id=case.case_id,
        before=before,
        after={"status": case.status.value, "updated_at": case.updated_at.isoformat()},
        reason=reason,
    )
    return case, audit
