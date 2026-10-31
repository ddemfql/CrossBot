"""Deterministic, source-bound form prefill; every result remains unconfirmed."""
from __future__ import annotations

from copy import deepcopy
from collections import defaultdict
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import DocumentForm, DocumentType, FormFieldCandidate, FormRowCandidate, SourceRequirementLink, CaseStatus, to_dict
from review.policy import audit, invalidate, require
from .catalog import FORM_FIELDS, ROW_FIELDS
from .intake import source_label
from .source_mapping import map_field


def _column(index):
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def row_candidates(version, document_id, allowed, *, include_incomplete_identity=False):
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
            if not (values.get("description") or values.get("product_code")) and not (
                    include_incomplete_identity and any(values.values())):
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


def current_row_review(workspace, candidate, *, cache=None):
    """Resolve only a unique review of the exact current machine row snapshot."""
    document = next((doc for doc in workspace.documents if doc.document_id == candidate.document_id
                     and doc.deleted_at is None and doc.current_version_id == candidate.version_id), None)
    version = next((v for v in document.versions if v.version_id == candidate.version_id), None) if document else None
    if version is None:
        return None
    from validation.cross_checker import get_current_version
    try:
        get_current_version(workspace.case, document)
    except ValidationError:
        return None
    from validation.export_review import raw_row_candidates
    cache = {} if cache is None else cache
    key = (candidate.document_id, candidate.version_id)
    if key not in cache:
        raw_map, review_map = defaultdict(list), defaultdict(list)
        for row in raw_row_candidates(version, candidate.document_id):
            raw_map[(row.table_index, row.row_number)].append(row)
        for row in workspace.reviewed_source_rows:
            if row.document_id == candidate.document_id and row.version_id == candidate.version_id and row.confirmed_by and row.confirmed_at:
                review_map[(row.table_index, row.row_number)].append(row)
        cache[key] = raw_map, review_map
    raw_map, review_map = cache[key]
    raw = raw_map[(candidate.table_index, candidate.row_number)]
    if len(raw) != 1 or any(raw[0].values.get(name) != value for name, value in candidate.values.items()):
        return None
    reviewed = [row for row in review_map[(candidate.table_index, candidate.row_number)]
                if row.original_values == raw[0].values]
    return reviewed[0] if len(reviewed) == 1 else None


def effective_candidate_values(workspace, candidate, target_type, *, cache=None):
    document = next((doc for doc in workspace.documents if doc.document_id == candidate.document_id), None)
    allowed = {name for name in ROW_FIELDS[target_type] if document is not None
               and map_field(document.document_type, target_type, name) == name}
    review = current_row_review(workspace, candidate, cache=cache)
    values = review.values if review is not None else candidate.values
    return {name: value for name, value in values.items() if name in allowed}


def source_row_candidates(workspace, version, document_id, allowed, *, cache=None):
    """Keep immutable readings; reviewed rows may supply a previously absent SKU."""
    result = row_candidates(version, document_id, allowed)
    cache = {} if cache is None else cache
    from validation.export_review import raw_row_candidates
    known = {(row.table_index, row.row_number) for row in result}
    for raw in raw_row_candidates(version, document_id):
        if (raw.table_index, raw.row_number) in known or current_row_review(workspace, raw, cache=cache) is None:
            continue
        projected = deepcopy(raw)
        projected.values = {name: value for name, value in raw.values.items() if name in allowed}
        projected.cell_references = {name: value for name, value in raw.cell_references.items() if name in allowed}
        result.append(projected)
    return sorted(result, key=lambda row: (row.table_index, row.row_number))


def prefill_form(workspace, kind, document_ids):
    """Update candidates and machine-owned slots in one loaded workspace."""
    kind = DocumentType(kind)
    if kind not in FORM_FIELDS:
        raise ValidationError("该类型暂无可预填表单。")
    form = next((f for f in workspace.document_forms if f.document_type is kind), None)
    if form is None:
        from .forms import get_form
        form = get_form(workspace, kind)
        workspace.document_forms.append(form)
    before = to_dict(form)
    fields, rows = defaultdict(list), []
    sources = list(_sources(workspace, document_ids))
    row_review_cache = {}
    for document, version in sources:
        for field in version.fields:
            name = map_field(document.document_type, kind, field.name)
            if name not in FORM_FIELDS[kind]:
                continue
            value = field.confirmed_value if field.confirmed_by else field.value
            if value is None or not str(value).strip() or str(value).lstrip().startswith("="):
                continue
            candidate = FormFieldCandidate(str(value).strip(), document.document_id,
                version.version_id, field.name, field.page_number, field.sheet_name, field.cell_reference)
            if candidate not in fields[name]:
                fields[name].append(candidate)
        allowed_rows = {name: label for name, label in ROW_FIELDS.get(kind, {}).items()
                        if map_field(document.document_type, kind, name) == name}
        rows.extend(source_row_candidates(workspace, version, document.document_id, allowed_rows, cache=row_review_cache))
    targeted = [link for link in workspace.source_requirement_links if link.document_type is kind]
    current_versions = {doc.document_id: doc.current_version_id for doc in workspace.documents if doc.deleted_at is None}
    for link in targeted:
        if link.row_number is None and link.field_name in FORM_FIELDS[kind] and current_versions.get(link.document_id) == link.version_id:
            doc = next(doc for doc in workspace.documents if doc.document_id == link.document_id)
            version = next((v for v in doc.versions if v.version_id == link.version_id), None)
            for field in version.fields if version else []:
                value = field.confirmed_value if field.confirmed_by else field.value
                if (map_field(doc.document_type, kind, field.name) != link.field_name or value is None
                        or not str(value).strip() or str(value).lstrip().startswith("=")):
                    continue
                candidate = FormFieldCandidate(str(value).strip(), doc.document_id, version.version_id,
                    field.name, field.page_number, field.sheet_name, field.cell_reference)
                if candidate not in fields[link.field_name]:
                    fields[link.field_name].append(candidate)
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
        by_document[row.document_id].append(effective_candidate_values(workspace, row, kind, cache=row_review_cache))
    sets = {json.dumps(value, sort_keys=True, ensure_ascii=False)
            for value in by_document.values()}
    selected = deepcopy(next(iter(by_document.values()))) if len(sets) == 1 else []
    has_targeted_rows = any(link.field_name == "rows" or link.row_number is not None for link in targeted)
    update_general_rows = not has_targeted_rows or bool(selected and sources)
    if not form.rows_manually_edited and update_general_rows:
        if form.rows and form.rows != form.auto_rows:
            form.rows_manually_edited = True  # Legacy human rows.
        else:
            form.rows = selected
    if update_general_rows:
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

    def fill_requirement(self, case_id, actor, kind, field_name, source_document_id,
                         row_number=None, expected_revision=None):
        """Bind one supplied file to one input slot; never fill unrelated slots."""
        from .forms import get_form, input_form_fields as form_fields, input_form_row_fields as form_row_fields, input_rows as effective_rows
        from .requirements import data_requirements
        from validation.cross_checker import get_current_version
        workspace = self.repository.get(case_id)
        require(workspace, actor, "manual_field")
        if expected_revision is not None and workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已被修改，请刷新后重新填入。")
        try:
            kind = DocumentType(kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的目标文件。") from exc
        form = get_form(workspace, kind)
        if (not isinstance(field_name, str) or not field_name
                or row_number is not None and (isinstance(row_number, bool)
                    or not isinstance(row_number, int) or row_number < 1)):
            raise ValidationError("请选择有效的数据项及明细行。")
        allowed = (row_number is None and (field_name in form_fields(form) or field_name == "rows")) or (
            row_number is not None and field_name in form_row_fields(form)) or any(
                item.field_name == field_name and item.row_number == row_number for item in data_requirements(workspace, kind))
        if not allowed:
            raise ValidationError("此数据项不在当前文件的生成需求中。")
        document = next((doc for doc in workspace.documents if doc.document_id == source_document_id
                         and doc.deleted_at is None), None)
        if document is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "来源文件不属于本交易或已移除。")
        # Unparsed files still have an exact retained requirement link. Parsing
        # failure is a pending input, never a false usable field.
        version = next((v for v in document.versions if v.version_id == document.current_version_id), None)
        if version is None:
            raise ValidationError("来源文件缺少当前版本。")
        before = to_dict(form)
        prior_links = list(workspace.source_requirement_links)
        link = SourceRequirementLink(document.document_id, version.version_id, kind, field_name, row_number)
        workspace.source_requirement_links = [old for old in workspace.source_requirement_links if not (
            old.document_type is kind and old.field_name == field_name and old.row_number == row_number
            and old.document_id == document.document_id)]
        workspace.source_requirement_links.append(link)
        try:
            get_current_version(workspace.case, document)
            parsed = True
        except ValidationError:
            parsed = False
        if parsed and row_number is None and field_name in form_fields(form):
            candidates = []
            for field in version.fields:
                if map_field(document.document_type, kind, field.name) != field_name:
                    continue
                value = field.confirmed_value if field.confirmed_by else field.value
                if value is None or not str(value).strip() or str(value).lstrip().startswith("="):
                    continue
                candidates.append(FormFieldCandidate(str(value).strip(), document.document_id,
                    version.version_id, field.name, field.page_number, field.sheet_name, field.cell_reference))
            old = form.field_candidates.get(field_name, [])
            form.field_candidates[field_name] = [c for c in old if c.document_id != document.document_id] + candidates
            distinct = {c.value for c in form.field_candidates[field_name]}
            protected = field_name in form.manual_fields or (form.values.get(field_name) and
                form.auto_values.get(field_name) != form.values[field_name])
            if len(distinct) == 1 and not protected:
                form.values[field_name] = next(iter(distinct))
                form.auto_values[field_name] = form.values[field_name]
        elif parsed and (field_name == "rows" or row_number is not None and field_name in form_row_fields(form)):
            allowed_rows = {name: label for name, label in form_row_fields(form).items()
                            if map_field(document.document_type, kind, name) == name}
            row_review_cache = {}
            candidates = source_row_candidates(workspace, version, document.document_id, allowed_rows, cache=row_review_cache)
            form.row_candidates = [c for c in form.row_candidates if c.document_id != document.document_id] + candidates
            if field_name == "rows":
                if not form.rows_manually_edited and (not form.rows or form.rows == form.auto_rows):
                    form.rows = [effective_candidate_values(workspace, candidate, kind, cache=row_review_cache) for candidate in candidates]
                    form.auto_rows = deepcopy(form.rows)
            else:
                rows = effective_rows(workspace.trade, form)
                if row_number <= len(rows):
                    code = rows[row_number - 1].get("product_code", "")
                    matches = [c for c in candidates if code and effective_candidate_values(workspace, c, kind, cache=row_review_cache).get("product_code") == code
                               and effective_candidate_values(workspace, c, kind, cache=row_review_cache).get(field_name)]
                    current = rows[row_number - 1].get(field_name, "")
                    deliberate_clear = any(event.action in {"document_form_saved", "document_form_confirmed"}
                        and event.entity_id == kind.value and isinstance(event.before, dict)
                        and row_number <= len(event.before.get("rows", []))
                        and event.before["rows"][row_number - 1].get(field_name)
                        and isinstance(event.after, dict) and row_number <= len(event.after.get("rows", []))
                        and not event.after["rows"][row_number - 1].get(field_name)
                        for event in workspace.audit)
                    from .provenance import _form_event
                    prior_target_fill = any(old.document_type is kind and old.field_name == field_name
                        and old.row_number == row_number and old.document_id == document.document_id
                        for old in prior_links)
                    machine_owned = bool(prior_target_fill and current and
                        _form_event(workspace, kind, field_name, current, row_number) is None)
                    if len(matches) == 1 and (not current or machine_owned) and not deliberate_clear:
                        stored_rows = deepcopy(form.rows) if form.rows else rows
                        stored_rows[row_number - 1][field_name] = effective_candidate_values(workspace, matches[0], kind, cache=row_review_cache)[field_name]
                        form.rows = stored_rows
        form.revision += 1
        form.confirmed_by = form.confirmed_at = None
        workspace.document_forms = [item for item in workspace.document_forms if item.document_type is not kind]
        workspace.document_forms.append(form)
        invalidate(workspace, actor, "指定资料关联更新，仍待核对。")
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "requirement_source_linked", "document_form", kind.value,
            before=before, after={"form": to_dict(form), "link": to_dict(link)},
            reason="仅将当前文件用于指定数据项；未填入内容仍待补充。")
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

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

