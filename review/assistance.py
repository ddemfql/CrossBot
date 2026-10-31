"""Traceable virtual-sales transcription; all supplied values stay unconfirmed."""
from copy import deepcopy
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (AssistanceRecord, CaseStatus, DocumentForm, DocumentType,
                         ExtractedField, FieldValueStatus, to_dict)
from review.policy import audit, invalidate, require


def apply_transcription(repository, case_id, actor, entries):
    """Apply sourced annotations without impersonating a human reviewer.

    Entries identify an existing source document, source position, and optionally
    a destination form field or row. Master trade edits use CaseService instead.
    """
    from documents.catalog import FORM_FIELDS, ROW_FIELDS
    workspace = repository.get(case_id)
    try:
        require(workspace, actor, "manual_field")
    except CrossBotError as exc:
        if exc.code is ErrorCode.FORBIDDEN:
            repository.save(workspace, expected_revision=workspace.revision)
        raise
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValidationError("辅助整理记录数量无效。")
    changes = []
    for entry in entries:
        document = next((d for d in workspace.documents if d.document_id == entry.get("document_id")
                         and d.deleted_at is None), None)
        if document is None or not entry.get("location") or not entry.get("reason"):
            raise ValidationError("辅助填写必须指定原件、位置和依据。")
        version = next(v for v in document.versions if v.version_id == document.current_version_id)
        name, value = entry.get("field_name", ""), entry.get("value", "")
        if not isinstance(value, str) or len(value) > 20000 or not name:
            raise ValidationError("辅助字段必须为有名称的文本。")
        target = entry.get("target", "source")
        record_name = (entry.get("document_type", "") + "." if target in {"form", "rows"} else "") + name
        latest = next((r for r in reversed(workspace.assistance_records)
                       if r.document_id == document.document_id and r.version_id == version.version_id
                       and r.field_name == record_name and r.location == entry["location"]), None)
        if latest is not None and latest.proposed_value == value:
            continue
        previous = ""
        if target == "form":
            kind = DocumentType(entry["document_type"])
            if name not in FORM_FIELDS[kind]:
                raise ValidationError("辅助表单字段不在允许清单。")
            form = next((f for f in workspace.document_forms if f.document_type is kind), None)
            if form is None:
                form = DocumentForm(kind)
                workspace.document_forms.append(form)
            if form.confirmed_by:
                raise ValidationError("不能用辅助整理覆盖已确认表单。")
            previous = form.values.get(name, "")
            form.values[name] = value
            if document.document_id not in form.source_document_ids:
                form.source_document_ids.append(document.document_id)
            form.revision += 1
        elif target == "rows":
            kind = DocumentType(entry["document_type"])
            rows = json.loads(value)
            if not isinstance(rows, list) or any(not isinstance(row, dict)
                    or set(row) - set(ROW_FIELDS[kind])
                    or any(not isinstance(v, str) for v in row.values()) for row in rows):
                raise ValidationError("辅助表单明细格式无效。")
            form = next((f for f in workspace.document_forms if f.document_type is kind), None)
            if form is None:
                form = DocumentForm(kind)
                workspace.document_forms.append(form)
            if form.confirmed_by:
                raise ValidationError("不能覆盖已确认表单明细。")
            previous = json.dumps(form.rows, ensure_ascii=False)
            form.rows = deepcopy(rows)
            if document.document_id not in form.source_document_ids:
                form.source_document_ids.append(document.document_id)
            form.revision += 1
        elif target == "source":
            field = next((f for f in version.fields if f.name == name), None)
            if field and field.confirmed_by:
                raise ValidationError("不能用辅助整理覆盖人工确认字段。")
            previous = str(field.assisted_value if field.assisted_value is not None else field.value or "") if field else ""
            if field is None:
                field = ExtractedField(name)
                version.fields.append(field)
            field.assisted_value = value
            field.assistance_note = "虚拟业务员辅助抄录（待人工核对）：" + entry["location"] + "；" + entry["reason"]
        else:
            raise ValidationError("未知辅助整理目标。")
        record = AssistanceRecord(method=entry.get("method", "visual_transcription"),
            document_id=document.document_id, version_id=version.version_id,
            location=entry["location"], field_name=record_name,
            previous_value=previous, proposed_value=value, reason=entry["reason"])
        workspace.assistance_records.append(record)
        changes.append(to_dict(record))
    if changes:
        invalidate(workspace, actor, "虚拟业务员按原件辅助整理，待真人核对。")
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "virtual_sales_transcription", "case", case_id,
              after={"actor_kind": "virtual_sales", "record_count": len(changes)},
              reason="用户授权辅助填写；未确认、未批准、未登记发货或收款。")
        repository.save(workspace, expected_revision=workspace.revision)
    return workspace
