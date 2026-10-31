"""Deterministic, source-bound form prefill; every result remains unconfirmed."""
from __future__ import annotations

from copy import deepcopy
from collections import defaultdict
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import DocumentForm, DocumentType, FormFieldCandidate, FormRowCandidate, to_dict
from review.policy import audit, invalidate, require
from .catalog import FORM_FIELDS, ROW_FIELDS
from .intake import source_label


def _column(index):
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def row_candidates(version, document_id, allowed):
    """Read explicit product matrices, never interpret label/value lists as rows."""
    result = []
    for table_index, table in enumerate(version.source_tables):
        headers = None
        for index, row in enumerate(table.rows):
            labels = {column: source_label(str(value)) for column, value in enumerate(row)}
            labels = {column: "description" if name == "product_name" else name
                      for column, name in labels.items()}
            candidate = {column: name for column, name in labels.items() if name in allowed}
            matrix = (len(set(candidate.values())) >= 2
                      and bool({"description", "product_code"} & set(candidate.values()))
                      and bool({"quantity", "unit_price", "packages", "gross_weight_kg", "net_weight_kg"}
                               & set(candidate.values())))
            if matrix:
                headers = candidate
                continue
            if headers is None:
                continue
            if not any(str(value).strip() for value in row):
                headers = None
                continue
            values, cells = {}, {}
            for column, name in headers.items():
                if column >= len(row):
                    continue
                value = str(row[column]).strip()
                if value and not value.startswith("=") and not source_label(value):
                    values[name] = value
                    cells[name] = f"{_column(column + 1)}{index + 1}"
            if not (values.get("description") or values.get("product_code")):
                continue
            # Footer/total rows lack explicitly supplied product identifiers.
            identity = (values.get("description") or values.get("product_code") or "").casefold()
            if identity in {"total", "subtotal", "grand total", "合计", "总计", "小计"}:
                headers = None
                continue
            result.append(FormRowCandidate(values, document_id, version.version_id,
                table_index, index + 1, table.name, table.page_number, cells))
    return result


def _sources(workspace, ids):
    documents = {doc.document_id: doc for doc in workspace.documents if doc.deleted_at is None}
    for document_id in dict.fromkeys(ids):
        document = documents.get(document_id)
        if document is None:
            continue
        version = next((v for v in document.versions if v.version_id == document.current_version_id), None)
        if version is not None:
            yield document, version


def prefill_form(workspace, kind, document_ids):
    """Update candidates and machine-owned slots in one loaded workspace."""
    kind = DocumentType(kind)
    if kind not in FORM_FIELDS:
        raise ValidationError("该类型暂无可预填表单。")
    form = next((f for f in workspace.document_forms if f.document_type is kind), None)
    if form is None:
        form = DocumentForm(kind, revision=0)
        workspace.document_forms.append(form)
    before = to_dict(form)
    fields, rows = defaultdict(list), []
    sources = list(_sources(workspace, document_ids))
    for document, version in sources:
        for field in version.fields:
            name = field.name
            if name not in FORM_FIELDS[kind] and name == "customer_name":
                name = "buyer"
            if name not in FORM_FIELDS[kind]:
                continue
            value = field.confirmed_value if field.confirmed_by else field.value
            if value is None or not str(value).strip() or str(value).lstrip().startswith("="):
                continue
            candidate = FormFieldCandidate(str(value).strip(), document.document_id,
                version.version_id, field.name, field.page_number, field.sheet_name, field.cell_reference)
            if candidate not in fields[name]:
                fields[name].append(candidate)
        rows.extend(row_candidates(version, document.document_id, ROW_FIELDS.get(kind, {})))
    ids = [doc.document_id for doc, _ in sources]
    if (form.confirmed_by and dict(fields) == form.field_candidates
            and rows == form.row_candidates and ids == form.source_document_ids):
        return False
    # Preserve reviewed data when sources change; withdraw its applicability
    # marker so another explicit human confirmation is needed before generation.
    protected = set(form.manual_fields)
    protected.update(name for name, value in form.values.items()
                     if value and (name not in form.auto_values or form.auto_values[name] != value))
    if form.confirmed_by:
        protected.update(form.values)
        if form.rows:
            form.rows_manually_edited = True
    form.manual_fields = sorted(protected)
    form.source_document_ids = ids
    form.field_candidates, form.row_candidates = dict(fields), rows
    next_auto = {}
    for name in set(form.auto_values) | set(fields):
        if name in protected:
            continue
        distinct = {candidate.value for candidate in fields.get(name, [])}
        value = next(iter(distinct)) if len(distinct) == 1 else ""
        form.values[name] = value
        if value:
            next_auto[name] = value
    form.auto_values = next_auto
    # Preserve source identities even for equal rows. Only one agreed product set
    # is copied to editable rows; competing file sets remain candidates.
    by_document = defaultdict(list)
    for row in rows:
        by_document[row.document_id].append(row.values)
    sets = {json.dumps(value, sort_keys=True, ensure_ascii=False)
            for value in by_document.values()}
    selected = deepcopy(next(iter(by_document.values()))) if len(sets) == 1 else []
    if not form.rows_manually_edited:
        if form.rows and form.rows != form.auto_rows:
            form.rows_manually_edited = True  # Legacy human rows.
        else:
            form.rows = selected
    form.auto_rows = selected if not form.rows_manually_edited else []
    if to_dict(form) != before:
        form.revision += 1
        form.confirmed_by = form.confirmed_at = None
        return True
    return False


def refresh_prefills(workspace):
    """Withdraw stale version candidates/auto values after custody mutations."""
    changed = False
    for form in list(workspace.document_forms):
        if form.document_type in FORM_FIELDS:
            changed = prefill_form(workspace, form.document_type, form.source_document_ids) or changed
    return changed


class PrefillService:
    def __init__(self, repository):
        self.repository = repository

    def fill(self, case_id, actor, kind, document_ids):
        workspace = self.repository.get(case_id)
        try:
            require(workspace, actor, "manual_field")
        except CrossBotError as exc:
            if exc.code is ErrorCode.FORBIDDEN:
                self.repository.save(workspace, expected_revision=workspace.revision)
            raise
        try:
            kind = DocumentType(kind)
        except (TypeError, ValueError) as exc:
            raise ValidationError("请选择有效的表单类型。") from exc
        if (not isinstance(document_ids, (list, tuple))
                or any(not isinstance(item, str) for item in document_ids)):
            raise ValidationError("请选择当前交易中的来源文件。")
        valid = {doc.document_id for doc in workspace.documents if doc.deleted_at is None}
        if any(item not in valid for item in document_ids):
            raise CrossBotError(ErrorCode.NOT_FOUND, "来源文件不属于当前交易或已移除。")
        if prefill_form(workspace, kind, document_ids):
            invalidate(workspace, actor, "来源资料预填更新，等待人工核对。")
            audit(workspace, actor, "form_prefilled", "document_form", kind.value,
                  after={"document_ids": list(document_ids)}, reason="按明确标签预填；未进行人工确认。")
            self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace
