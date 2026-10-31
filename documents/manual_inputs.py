"""Explicit human-provided data, separate from custody of original files."""
from copy import deepcopy

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import CaseStatus, DocumentType, FormSourceReference, ManualDataRecord, to_dict, utc_now
from review.policy import audit, invalidate, require
from .catalog import DOC_LABELS, FORM_FIELDS, ROW_FIELDS, SELLER_TYPES
from .forms import (FormService, get_form, effective_rows,
    input_form_fields as form_fields, input_form_row_fields as form_row_fields)
from .source_mapping import map_field

_SOURCE_ONLY_FIELDS = frozenset({"document_no", "document_date", "notes", "issuer",
    "external_reference", "issued_on", "expires_on", "source_party", "evidence_reference",
    "recorded_conclusion", "invoice_no", "invoice_date", "packing_list_no", "packing_date",
    "contract_no", "inspection_date", "inspector", "observed_result", "responsible_person",
    "declaration_text"})


def has_manual_data(form):
    return bool(form is not None and (isinstance(form, ManualDataRecord) or form.manually_provided)
        and (any(str(v).strip() for key, v in form.values.items() if key not in _SOURCE_ONLY_FIELDS)
        or any(any(str(v).strip() for v in row.values()) for row in form.rows)))


def manual_source(workspace, document_type, record_id=""):
    if record_id:
        return next((record for record in workspace.manual_data_records
            if record.record_id == record_id and record.document_type is DocumentType(document_type)), None)
    return get_form(workspace, document_type)


class ManualInputService:
    def __init__(self, repository):
        self.repository = repository

    def save(self, case_id, actor, kind, values, rows, reason, expected_revision=None):
        # Only explicit entries count; never materialize inherited order defaults.
        return FormService(self.repository).save(case_id, actor, kind, values, rows, reason,
            manually_provided=True, manual_fields=list(values) if isinstance(values, dict) else None,
            rows_manually_edited=True,
            expected_workspace_revision=expected_revision)

    def save_record(self, case_id, actor, kind, values, rows, reason, record_id=None,
                    title="", expected_revision=None):
        workspace = self.repository.get(case_id)
        require(workspace, actor, "manual_field")
        if expected_revision is not None and workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已被修改，请刷新后重新填写。")
        try:
            kind = DocumentType(kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的资料类型。") from exc
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValidationError("保存资料须填写不超过 2000 字的说明。")
        if not isinstance(title, str) or len(title) > 200:
            raise ValidationError("资料名称须为不超过 200 字的文本。")
        from .input_validation import validate_input_text
        validate_input_text(reason)
        validate_input_text(title)
        if not isinstance(values, dict) or set(values) - set(FORM_FIELDS[kind]):
            raise ValidationError("资料包含不支持的字段。")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValidationError("资料明细最多 1000 行。")
        def clean(mapping, allowed):
            if not isinstance(mapping, dict) or set(mapping) - set(allowed):
                raise ValidationError("资料明细包含不支持的字段。")
            if any(not isinstance(v, str) or len(v) > 4000 for v in mapping.values()):
                raise ValidationError("资料值须为不超过 4000 字的文本。")
            validate_input_text(mapping)
            return {name: value.strip() for name, value in mapping.items()}
        cleaned, cleaned_rows = clean(values, FORM_FIELDS[kind]), [clean(row, ROW_FIELDS[kind]) for row in rows]
        previous = manual_source(workspace, kind, record_id) if record_id else None
        if record_id and previous is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "这份填写资料不属于当前交易或分类。")
        if previous and (previous.values == cleaned and previous.rows == cleaned_rows and previous.title == title.strip()):
            return workspace
        record = ManualDataRecord(kind, title=title.strip(), values=cleaned, rows=cleaned_rows,
            revision=previous.revision + 1 if previous else 1, recorded_by=actor.user_id, recorded_at=utc_now())
        if previous:
            record.record_id = previous.record_id
        invalidate(workspace, actor, reason.strip())
        workspace.manual_data_records = [item for item in workspace.manual_data_records if item.record_id != record.record_id]
        workspace.manual_data_records.append(record)
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "manual_data_saved", "manual_data_record", record.record_id,
            before=to_dict(previous) if previous else None, after=to_dict(record), reason=reason.strip())
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

    def detach(self, case_id, actor, target_type, source_type, reason, source_record_id="", expected_revision=None):
        """Explicitly retain current data as an independent human-maintained form.

        Detaching never changes values or rewrites their historical origins. It
        invalidates old outputs and still requires all current export checks.
        """
        workspace = self.repository.get(case_id)
        require(workspace, actor, "manual_field")
        if expected_revision is not None and workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已被修改，请刷新后重新核对。")
        try:
            source_type, target_type = DocumentType(source_type), DocumentType(target_type)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的填写来源和目标文件。") from exc
        if target_type not in SELLER_TYPES or (target_type is source_type and not source_record_id):
            raise ValidationError("只能解除我方制作文件对另一份填写资料的关联。")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValidationError("解除关联须填写不超过 2000 字的核对说明。")
        from .input_validation import validate_input_text
        validate_input_text(reason)
        target = get_form(workspace, target_type)
        removed = [ref for ref in target.source_form_references
                   if ref.document_type is source_type and ref.record_id == source_record_id]
        if not removed:
            return workspace
        before = to_dict(target)
        target.source_form_references = [ref for ref in target.source_form_references if ref not in removed]
        target.revision += 1
        target.confirmed_by = target.confirmed_at = None
        invalidate(workspace, actor, reason.strip())
        workspace.document_forms = [form for form in workspace.document_forms if form.document_type is not target_type]
        workspace.document_forms.append(target)
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "document_form_saved", "document_form", target_type.value,
              before=before, after=to_dict(target), reason=reason.strip())
        audit(workspace, actor, "manual_source_detached", "document_form", target_type.value,
              before={"source_form_references": to_dict(removed)},
              after={"retained_current_values": True, "source_type": source_type.value, "source_record_id": source_record_id},
              reason=reason.strip())
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

    def apply(self, case_id, actor, source_type, target_type, reason, source_record_id="", expected_revision=None):
        workspace = self.repository.get(case_id)
        require(workspace, actor, "manual_field")
        if expected_revision is not None and workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已被修改，请刷新后重新填入。")
        try:
            source_type, target_type = DocumentType(source_type), DocumentType(target_type)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的资料和交付文件类型。") from exc
        if (source_type == target_type and not source_record_id) or target_type not in SELLER_TYPES:
            raise ValidationError("请选择另一份由我方制作的文件。")
        source, target = manual_source(workspace, source_type, source_record_id), get_form(workspace, target_type)
        if not has_manual_data(source) or getattr(source, "source_form_references", []):
            raise ValidationError("来源须为已直接填写的资料，不能使用空资料或转填形成的来源链。")
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("请填写使用这份资料的依据。")
        values = dict(target.values)
        copied = False
        conflicts = []
        # Every protected clear remains a clear. Existing values from files are
        # also preserved rather than silently re-attributing their provenance.
        for name, value in source.values.items():
            name = map_field(source_type, target_type, name)
            if name not in form_fields(target) or not value.strip():
                continue
            if name in target.manual_fields or values.get(name):
                if values.get(name, "") != value:
                    conflicts.append(FORM_FIELDS[target_type][name])
                continue
            values[name] = value
            copied = True
        compatible = {name for name in set(form_row_fields(target)) & set(ROW_FIELDS[source_type])
                      if map_field(source_type, target_type, name) == name}
        rows = [{key: value for key, value in row.items() if key in compatible}
                for row in source.rows]
        rows = [row for row in rows if any(row.values())]
        target_rows = deepcopy(target.rows)
        if rows:
            if target.rows or target.rows_manually_edited:
                if target.rows != rows:
                    conflicts.append("产品明细（含人工清空）")
            else:
                target_rows = rows
                copied = True
        if conflicts:
            raise ValidationError("以下内容已填写或人工清空，请先核对并手动修订，系统未覆盖：" + "、".join(conflicts))
        references = [ref for ref in target.source_form_references
                      if not (ref.document_type is source_type and ref.record_id == source_record_id)]
        reference = FormSourceReference(source_type, source.revision, source_record_id)
        references.append(reference)
        if not copied and reference in target.source_form_references:
            return workspace
        if not copied and not any(map_field(source_type, target_type, name) in form_fields(target) and value.strip()
                for name, value in source.values.items()) and not rows:
            raise ValidationError("这份资料没有可用于目标文件的对应字段。")
        return FormService(self.repository).save(case_id, actor, target_type, values, target_rows,
            f"{reason.strip()}；填写来源：{DOC_LABELS[source_type]}，修订 {source.revision}"
            + (f"；记录 {source.record_id} · {source.title}；录入人 {source.recorded_by}" if source_record_id else ""),
            source_form_references=references, expected_workspace_revision=workspace.revision,
            preserve_inherited_rows=not rows)
