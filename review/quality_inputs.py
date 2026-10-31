"""One grouped human action to review external fields used by strict checks."""
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (CaseStatus, FieldValueStatus, ReviewDecision, ReviewStatus,
                         SourceFieldSelection, to_dict, utc_now)
from documents.prefill import refresh_prefills
from review.policy import audit, invalidate, require
from validation.cross_checker import get_current_version
from validation.export_review import review_digest, review_export


def pending_source_fields(workspace, kind):
    """Expose current fields the report uses, including reviewed values to correct."""
    report = review_export(workspace, kind)
    ids = {check.right_document_id for check in report.checks
           if check.rule_code in {"RAW_CROSS_DOCUMENT", "RAW_ROW_PAIRING"}}
    result = []
    for document in workspace.documents:
        if document.document_id not in ids or document.deleted_at is not None:
            continue
        try:
            version = get_current_version(workspace.case, document)
        except ValidationError:
            continue
        names = [field.name for field in version.fields]
        for field in version.fields:
            if names.count(field.name) != 1:
                continue
            value = (field.confirmed_value if field.status is FieldValueStatus.CONFIRMED
                     and field.confirmed_by and field.confirmed_at else field.value)
            if value is None or not str(value).strip():
                continue
            result.append(SourceFieldSelection(document.document_id, version.version_id,
                                                field.name, str(value)))
    return result


def confirm_source_fields(repository, case_id, actor, kind, selections, *,
                          expected_review_digest, human_confirmed):
    workspace = repository.get(case_id)
    require(workspace, actor, "confirm_field")
    if human_confirmed is not True:
        raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请明确核对这些外部字段后再确认。")
    if expected_review_digest != review_digest(workspace, kind):
        raise CrossBotError(ErrorCode.CONFLICT, "资料版本已变化，请重新查看外部字段。")
    if not isinstance(selections, list) or not selections or len(selections) > 1000:
        raise ValidationError("请选择需要核对的字段。")
    allowed = {(item.document_id, item.version_id, item.field_name)
               for item in pending_source_fields(workspace, kind)}
    staged, seen = [], set()
    for selection in selections:
        if not isinstance(selection, SourceFieldSelection):
            raise ValidationError("外部字段核对格式无效。")
        key = (selection.document_id, selection.version_id, selection.field_name)
        if key not in allowed or key in seen:
            raise ValidationError("字段不属于当前参与比对的资料或存在重复。")
        if not isinstance(selection.value, str) or not selection.value.strip() or len(selection.value) > 4000:
            raise ValidationError("核对值必须是不超过 4000 字的非空文本。")
        seen.add(key)
        document = next(doc for doc in workspace.documents if doc.document_id == selection.document_id)
        version = get_current_version(workspace.case, document)
        field = next(field for field in version.fields if field.name == selection.field_name)
        staged.append((selection, field, to_dict(field)))
    now = utc_now()
    changes = []
    for selection, field, before in staged:
        field.confirmed_value = selection.value.strip()
        field.confirmed_by, field.confirmed_at = actor.user_id, now
        field.status = FieldValueStatus.CONFIRMED
        changes.append({"selection": to_dict(selection), "before": before, "after": to_dict(field)})
    for task in workspace.review_tasks:
        current = next((version for doc in workspace.documents if doc.document_id == task.document_id
                        for version in doc.versions if version.version_id == doc.current_version_id), None)
        touched = [selection.field_name for selection, _, _ in staged if selection.document_id == task.document_id]
        if current and touched:
            task.decisions.append(ReviewDecision(task_id=task.task_id, reviewer_id=actor.user_id,
                status=ReviewStatus.APPROVED, reason="在核验区逐项核对外部字段", changed_fields=touched))
            if all(any(field.name == name and field.status is FieldValueStatus.CONFIRMED
                       and field.confirmed_by and field.confirmed_at for field in current.fields)
                   for name in task.field_names):
                task.status = ReviewStatus.APPROVED
    refresh_prefills(workspace)
    invalidate(workspace, actor, "外部字段经真人核对，需重新运行当前规则。")
    workspace.case.status = CaseStatus.PENDING_REVIEW
    audit(workspace, actor, "external_fields_reviewed", "document_form", kind.value,
        after={"fields": changes, "reviewed_input_digest": expected_review_digest},
        reason="真人明确核对所列当前原件字段；不批准交易，也不跳过确定性规则。")
    repository.save(workspace, expected_revision=workspace.revision)
    return workspace
