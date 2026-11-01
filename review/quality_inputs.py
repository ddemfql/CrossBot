"""Grouped human review of original scalar fields and exact source rows."""
from copy import deepcopy
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (CaseStatus, FieldValueStatus, ReviewDecision, ReviewStatus,
                         SourceFieldSelection, SourceRowSelection, ReviewedSourceRow, to_dict, utc_now)
from documents.prefill import refresh_prefills
from review.policy import audit, invalidate, require
from validation.cross_checker import get_current_version
from validation.export_review import review_digest, review_export, raw_row_candidates


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


def pending_source_rows(workspace, kind):
    """Return editable current rows without treating extracted values as reviewed."""
    report = review_export(workspace, kind)
    ids = {check.right_document_id for check in report.checks if check.rule_code in {
        "RAW_ROW_SOURCE", "RAW_ROW_PAIRING", "RAW_CROSS_DOCUMENT", "RAW_CROSS_DOCUMENT_SET"}}
    selections = []
    for document in workspace.documents:
        if document.deleted_at is not None or document.document_id not in ids:
            continue
        try:
            version = get_current_version(workspace.case, document)
        except ValidationError:
            continue
        for row in raw_row_candidates(version, document.document_id):
            prior = [item for item in workspace.reviewed_source_rows
                if item.document_id == document.document_id and item.version_id == version.version_id
                and item.table_index == row.table_index and item.row_number == row.row_number
                and item.original_values == row.values and item.confirmed_by and item.confirmed_at]
            values = prior[0].values if len(prior) == 1 else row.values
            selections.append(SourceRowSelection(document.document_id, version.version_id,
                row.table_index, row.row_number, deepcopy(values)))
    return selections


def confirm_source_rows(repository, case_id, actor, kind, selections, *,
                        expected_review_digest, human_confirmed):
    """Review complete document row sets; never mutate original tables/candidates."""
    workspace = repository.get(case_id)
    require(workspace, actor, "confirm_field")
    if human_confirmed is not True:
        raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请明确核对原件全部产品行后再确认。")
    if expected_review_digest != review_digest(workspace, kind):
        raise CrossBotError(ErrorCode.CONFLICT, "资料版本已变化，请重新查看原件明细。")
    if not isinstance(selections, list) or not selections or len(selections) > 1000:
        raise ValidationError("请提交完整的原件产品行清单，最多1000行。")
    allowed = {(row.document_id, row.version_id, row.table_index, row.row_number)
               for row in pending_source_rows(workspace, kind)}
    chosen, staged = set(), []
    from documents.provenance import source_location
    columns = {"product_code", "description", "quantity", "unit", "unit_price", "amount",
               "packages", "gross_weight_kg", "net_weight_kg"}
    for selection in selections:
        if not isinstance(selection, SourceRowSelection):
            raise ValidationError("原件明细核对格式无效。")
        if (type(selection.table_index) is not int or selection.table_index < 0
                or type(selection.row_number) is not int or selection.row_number < 1):
            raise ValidationError("原件工作表及行号须为有效整数。")
        key = (selection.document_id, selection.version_id, selection.table_index, selection.row_number)
        if key not in allowed or key in chosen:
            raise ValidationError("原件行不属于当前参与核对的版本或存在重复。")
        chosen.add(key)
        document = next(doc for doc in workspace.documents if doc.document_id == selection.document_id)
        version = get_current_version(workspace.case, document)
        original = next(row for row in raw_row_candidates(version, document.document_id)
            if row.table_index == selection.table_index and row.row_number == selection.row_number)
        values = selection.values
        if (not isinstance(values, dict) or set(values) - columns or set(original.values) - set(values)
                or any(not isinstance(value, str) or len(value) > 4000 for value in values.values())):
            raise ValidationError("明细字段无效或丢失原件字段；不得删除原件明细来通过审核。")
        cleaned = {name: value.strip() for name, value in values.items()}
        if (any(not cleaned.get(name) for name in ("product_code", "description", "quantity", "unit"))
                or any(value.strip() and not cleaned.get(name) for name, value in original.values.items())):
            raise ValidationError("请补齐产品编码、品名、数量、单位，已有原件值不能清空绕过核对。")
        staged.append((key, original, cleaned, source_location(original)))
    selected_documents = {(key[0], key[1]) for key in chosen}
    complete = {key for key in allowed if (key[0], key[1]) in selected_documents}
    if chosen != complete:
        raise ValidationError("每份原件须一次核对全部产品行，不能遗漏或删除行。")
    for document_id, version_id in selected_documents:
        codes = [cleaned["product_code"] for key, _, cleaned, _ in staged
                 if key[:2] == (document_id, version_id)]
        if len(set(codes)) != len(codes):
            raise ValidationError("原件产品编码须唯一，不能合并重复产品来通过审核。")
    now = utc_now()
    before = to_dict(workspace.reviewed_source_rows)
    workspace.reviewed_source_rows = [row for row in workspace.reviewed_source_rows
        if (row.document_id, row.version_id, row.table_index, row.row_number) not in chosen]
    workspace.reviewed_source_rows.extend(ReviewedSourceRow(key[0], key[1], key[2], key[3],
        deepcopy(original.values), cleaned, location, actor.user_id, now)
        for key, original, cleaned, location in staged)
    invalidate(workspace, actor, "原件逐产品明细经真人核对，需重新运行规则。")
    workspace.case.status = CaseStatus.PENDING_REVIEW
    audit(workspace, actor, "external_rows_reviewed", "document_form", kind.value,
        before=before, after={"rows": to_dict(workspace.reviewed_source_rows),
                             "reviewed_input_digest": expected_review_digest},
        reason="真人核对当前原件完整产品行；原始表格和机器候选保持不变，不批准交易。")
    repository.save(workspace, expected_revision=workspace.revision)
    return workspace

