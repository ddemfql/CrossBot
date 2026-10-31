"""Validation and guarded state transitions for shared domain objects."""
from __future__ import annotations

from .errors import ErrorCode, ValidationError
from .models import Case, CaseStatus, CrossCheckResult, CrossCheckStatus, ReviewTask, ReviewStatus

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
    required = {
        "case_number": case.case_number,
        "customer_name": case.customer_name,
        "destination_country": case.destination_country,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValidationError("Case 缺少必填字段", details={"fields": missing})
    if case.invoice_total is not None and case.invoice_total < 0:
        raise ValidationError("发票总金额不能为负数", details={"field": "invoice_total"})


def validate_case_transition(case: Case, target: CaseStatus, *, review_tasks: list[ReviewTask] | None = None, checks: list[CrossCheckResult] | None = None) -> None:
    validate_case(case)
    if target not in _ALLOWED_TRANSITIONS.get(case.status, set()):
        raise ValidationError("非法 Case 状态转换", details={"from": case.status.value, "to": target.value})
    if target in {CaseStatus.PASSED, CaseStatus.CLOSED}:
        if not review_tasks:
            raise ValidationError("Case 至少需要一个人工复核任务", details={"code": ErrorCode.REVIEW_REQUIRED.value})
        if not checks:
            raise ValidationError("Case 至少需要一项交叉比对结果", details={"code": ErrorCode.CROSS_CHECK_FAILED.value})
        tasks = review_tasks or []
        results = checks or []
        if any(task.required and task.status != ReviewStatus.APPROVED for task in tasks):
            raise ValidationError("仍有必需的人工复核任务未通过", details={"code": ErrorCode.REVIEW_REQUIRED.value})
        if any(result.status != CrossCheckStatus.PASS for result in results):
            raise ValidationError("仍有交叉比对未通过或待复核", details={"code": ErrorCode.CROSS_CHECK_FAILED.value})


def transition_case(case: Case, target: CaseStatus, *, review_tasks: list[ReviewTask] | None = None, checks: list[CrossCheckResult] | None = None) -> Case:
    validate_case_transition(case, target, review_tasks=review_tasks, checks=checks)
    case.status = target
    from .models import utc_now
    case.updated_at = utc_now()
    return case
