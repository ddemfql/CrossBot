"""Read-only attribution of effective form values.

Stable origin kinds: file (current source evidence), manual (human edit), trade
(inherited order data), default (untouched known initial value), unknown (legacy
or unverifiable attribution), derived (explicit deterministic arithmetic).
Candidates are evidence, not automatically the origin of a displayed value.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, DecimalException
import re

from core.models import FormValueOrigin, TradeData, from_dict
from .catalog import FORM_FIELDS, ROW_FIELDS
from .forms import (effective_rows, effective_values, get_form, form_fields, form_row_fields,
    input_form_fields, input_form_row_fields, input_values, input_rows)

ORIGIN_LABELS = {"file": "来源文件", "manual": "人工填写", "trade": "继承订单资料",
                 "default": "系统初始值", "unknown": "来源未核实", "derived": "计算所得"}


def _text(value):
    return "" if value is None else str(value)


def _excel_cell(value):
    match = re.fullmatch(r"R(\d+)C(\d+)", value or "", re.IGNORECASE)
    if not match:
        return value or ""
    index, column = int(match[2]), ""
    while index:
        index, digit = divmod(index - 1, 26)
        column = chr(65 + digit) + column
    return f"{column}{match[1]}"


def source_location(candidate, field_name=None):
    """Human-readable page/sheet/cell without inventing a missing position."""
    parts = []
    if candidate.page_number is not None:
        parts.append(f"第 {candidate.page_number} 页")
    sheet = getattr(candidate, "sheet_name", None) or getattr(candidate, "table_name", "")
    if sheet:
        parts.append(f"工作表 {sheet}")
    cell = (candidate.cell_references.get(field_name, "") if field_name is not None
            else getattr(candidate, "cell_reference", ""))
    if cell:
        parts.append(_excel_cell(cell))
    elif hasattr(candidate, "row_number"):
        parts.append(f"原表第 {candidate.row_number} 行")
    return " · ".join(parts) or "来源位置未记录"


def _source(workspace, candidate):
    document = next((doc for doc in workspace.documents
                     if doc.document_id == candidate.document_id), None)
    if document is None:
        return None, False
    version = next((v for v in document.versions if v.version_id == candidate.version_id), None)
    current = bool(version and document.deleted_at is None
                   and document.current_version_id == candidate.version_id)
    return version, current


def _file_origin(workspace, name, value, candidate, row_number=None, *, row_review_cache=None):
    version, current = _source(workspace, candidate)
    if not current:
        return None
    origin = FormValueOrigin(name, value, "file", row_number=row_number,
        document_id=candidate.document_id, version_id=candidate.version_id,
        filename=version.original_filename or "来源文件（名称未记录）", version_number=version.version_number,
        location=source_location(candidate, name if row_number is not None else None),
        note="当前原件的明确标签/表格候选；人工确认另行记录。")
    if row_number is None:
        fields = [field for field in version.fields if field.name == candidate.field_name]
        if len(fields) == 1:
            field = fields[0]
            if (field.confirmed_by and field.confirmed_at
                    and _text(field.confirmed_value).strip() == value
                    and _text(field.value).strip() != value):
                origin.origin_kind = "manual"
                origin.actor_id, origin.occurred_at = field.confirmed_by, field.confirmed_at
                origin.note = (f"来源字段原采集值：{_text(field.value)}；真人核对后修正为：{value}。"
                    "当前内容继承该人工修正；文件及位置用于追溯，不表示原单元格含有修正值。")
    else:
        from .prefill import current_row_review
        review = current_row_review(workspace, candidate, cache=row_review_cache)
        if review is not None and review.values.get(name) == value and review.original_values.get(name, "") != value:
            origin.origin_kind = "manual"
            origin.actor_id, origin.occurred_at = review.confirmed_by, review.confirmed_at
            origin.note = (f"来源明细原采集值：{review.original_values.get(name, '')}；真人核对后修正为：{value}。"
                "当前内容继承该人工修正；原件、工作表及行位置保留，不表示原单元格含有修正值。")
    return origin


def _form_event(workspace, kind, name, value, row_number=None):
    for event in reversed(workspace.audit):
        if (event.action == "review_value_verified" and event.entity_id == kind.value
                and isinstance(event.after, dict)):
            target = event.after
            if row_number is None:
                matches = target.get("row_number") is None and target.get("field_name") == name
                if matches and _text(target.get("values", {}).get(name)) == value:
                    return event
            elif (target.get("field_name") == "rows" and target.get("row_number") is None
                    or target.get("field_name") == name and target.get("row_number") == row_number):
                rows = target.get("rows", [])
                if row_number <= len(rows) and _text(rows[row_number - 1].get(name)) == value:
                    return event
        if (event.action not in {"document_form_saved", "document_form_confirmed"}
                or event.entity_id != kind.value or not isinstance(event.after, dict)):
            continue
        after = event.after
        target = after.get("review_edit_target")
        if isinstance(target, dict) and not (target.get("field_name") == name and target.get("row_number") == row_number
                or target.get("field_name") == "rows" and target.get("row_number") is None and row_number is not None):
            continue
        before = event.before if isinstance(event.before, dict) else {}
        if row_number is None:
            if _text(after.get("values", {}).get(name)) != value:
                continue
            prior = before.get("values", {})
            changed = (name not in prior or _text(prior[name]) != value
                or bool(after.get("manually_provided") and not before.get("manually_provided")))
        else:
            rows = after.get("rows", [])
            prior = before.get("rows", [])
            index = row_number - 1
            if index >= len(rows) or _text(rows[index].get(name)) != value:
                continue
            changed = (index >= len(prior) or _text(prior[index].get(name)) != value
                or bool(after.get("manually_provided") and not before.get("manually_provided")))
        if changed:
            return event
    return None


def _trade_origin(workspace, name, value, row_number=None):
    for event in reversed(workspace.audit):
        if event.action not in {"case_created", "trade_updated"} or not isinstance(event.after, dict):
            continue
        target = event.after.get("review_edit_target")
        if isinstance(target, dict):
            field, number = target.get("field_name"), target.get("row_number")
            path = re.fullmatch(r"line_items\[([1-9][0-9]*)\]\.([a-z_]+)", field or "")
            matches_target = (field == name and number == row_number or
                row_number is not None and field in {"rows", "line_items"} and number is None or
                path is not None and int(path[1]) == row_number and path[2] == name)
            if not matches_target:
                continue
        trade = event.after.get("trade", event.after)
        if row_number is None:
            matches = _text(trade.get(name)) == value
        else:
            rows = trade.get("line_items", [])
            matches = row_number <= len(rows) and _text(rows[row_number - 1].get(name)) == value
        if matches:
            defaults = ({"currency": "USD"} if row_number is None else
                {"unit": "pcs", "packages": "0", "gross_weight_kg": "0", "net_weight_kg": "0"})
            supplied = (trade.get("supplied_fields", []) if row_number is None else
                rows[row_number - 1].get("supplied_fields", []))
            initial_value = name in defaults and value.strip() == defaults[name]
            if name in {"packages", "gross_weight_kg", "net_weight_kg"}:
                try:
                    initial_value = Decimal(value) == 0
                except (DecimalException, ValueError, TypeError):
                    initial_value = False
            if initial_value and name not in supplied:
                return FormValueOrigin(name, value, "default", row_number=row_number,
                    note="当前内容沿用系统初始值；尚无此字段已明确提供的记录。")
            return FormValueOrigin(name, value, "trade", row_number=row_number,
                actor_id=event.actor_id, occurred_at=event.occurred_at,
                note=f"继承订单{'第 ' + str(row_number) + ' 行 ' if row_number else ''}{name}；可核对订单录入记录。")
    if row_number is None and name == "currency" and value == "USD" and workspace.trade == TradeData():
        return FormValueOrigin(name, value, "default", note="未填写订单的初始币种 USD；尚无原件或人工录入依据。")
    return FormValueOrigin(name, value, "trade", row_number=row_number,
        note="继承当前订单资料；未找到可核实的录入人/时间，不代表来自原件。")


def _manual_origin(workspace, kind, name, value, row_number=None):
    event = _form_event(workspace, kind, name, value, row_number)
    if event is None:
        return FormValueOrigin(name, value, "unknown", row_number=row_number,
            note="存在内容保护标记，但未找到可核实的人工填写记录；历史来源待补，不能据此认定为人工填写。")
    return FormValueOrigin(name, value, "manual", row_number=row_number,
        actor_id=event.actor_id, occurred_at=event.occurred_at,
        note=("人工明确清空该字段。" if not value else "人工填写/修订；不冒充原件内容。")
            + (f"填写依据：{event.reason}" if event.reason else ""))


def _inherited_origin(workspace, name, value, row_number=None, adoption=None):
    origin = _trade_origin(workspace, name, value, row_number)
    if origin.origin_kind == "default" and adoption is not None:
        origin.origin_kind = "trade"
        origin.actor_id, origin.occurred_at = adoption.actor_id, adoption.occurred_at
        origin.note = "人工保存表单时明确采用此订单初始值；不表示来自原件。"
    return origin


def _preserved_row_origin(workspace, kind, name, value, row_number):
    for event in reversed(workspace.audit):
        if event.action != "document_form_saved" or event.entity_id != kind.value or not isinstance(event.after, dict):
            continue
        for payload in event.after.get("preserved_row_origins", []):
            if (payload.get("field_name") == name and payload.get("row_number") == row_number
                    and _text(payload.get("value")) == value):
                origin = from_dict(FormValueOrigin, payload)
                if origin.origin_kind == "file" and not _source(workspace, origin)[1]:
                    origin.origin_kind = "unknown"
                    origin.note = "保存时保留的原件来源现已失效，仍需核对。"
                return origin
    return None


def _targeted_row_origin(workspace, kind, row, row_number, name, value, *, row_review_cache=None):
    from .prefill import source_row_candidates, effective_candidate_values
    from .source_mapping import map_field
    for link in reversed(workspace.source_requirement_links):
        if link.document_type is not kind or not (link.field_name == "rows" or (
                link.field_name == name and link.row_number == row_number)):
            continue
        doc = next((doc for doc in workspace.documents if doc.document_id == link.document_id
                    and doc.deleted_at is None and doc.current_version_id == link.version_id), None)
        version = next((v for v in doc.versions if v.version_id == link.version_id), None) if doc else None
        if version is None or map_field(doc.document_type, kind, name) != name:
            continue
        candidates = source_row_candidates(workspace, version, doc.document_id, ROW_FIELDS[kind], cache=row_review_cache)
        matches = [candidate for candidate in candidates if row.get("product_code")
            and effective_candidate_values(workspace, candidate, kind, cache=row_review_cache).get("product_code") == row["product_code"]
            and effective_candidate_values(workspace, candidate, kind, cache=row_review_cache).get(name) == value]
        if len(matches) == 1:
            return _file_origin(workspace, name, value, matches[0], row_number, row_review_cache=row_review_cache)
    return None


def form_value_origins(workspace, kind, *, include_inputs=False):
    """Attribute every nonempty effective field/cell; include explicit clears."""
    form = get_form(workspace, kind)
    kind = form.document_type
    result = []
    row_review_cache = {}
    active_fields = input_form_fields(form) if include_inputs else form_fields(form)
    active_rows = input_form_row_fields(form) if include_inputs else form_row_fields(form)
    values = input_values(workspace.trade, form) if include_inputs else effective_values(workspace.trade, form)
    for name in active_fields:
        value = _text(values.get(name))
        if not value and name not in form.manual_fields:
            continue
        trade_value = _text(getattr(workspace.trade, name, ""))
        candidates = form.field_candidates.get(name, [])
        # An unchanged order/default field copied by the editor remains inherited.
        event = _form_event(workspace, kind, name, value)
        inherited_copy = (bool(value) and value == trade_value and event is not None
                          and event.action != "review_value_verified"
                          and not (event.before or {}).get("values", {}).get(name)
                          and name not in form.auto_values and not candidates)
        if name in form.manual_fields and not inherited_copy:
            result.append(_manual_origin(workspace, kind, name, value))
        elif form.auto_values.get(name) == value and value:
            matches = [candidate for candidate in candidates if candidate.value == value]
            origin = next((origin for candidate in matches
                           if (origin := _file_origin(workspace, name, value, candidate))), None)
            result.append(origin or FormValueOrigin(name, value, "unknown",
                note="机器预填缺少匹配的当前来源；原候选可能已失效，请核对原件。"))
        elif (not form.values.get(name) and name not in form.manual_fields) or inherited_copy:
            result.append(_inherited_origin(workspace, name, value, adoption=event if inherited_copy else None))
        else:
            result.append(FormValueOrigin(name, value, "unknown",
                note="历史表单值缺少原件来源及人工操作依据；请核实后重新填写。"))
    rows = input_rows(workspace.trade, form) if include_inputs else effective_rows(workspace.trade, form)
    fallback = not form.rows and not form.rows_manually_edited
    for row_number, row in enumerate(rows, 1):
        row_origins = {}
        index = row_number - 1
        for name in active_rows:
            value = _text(row.get(name))
            if not value:
                continue
            event = _form_event(workspace, kind, name, value, row_number)
            auto_basis = form.auto_rows
            automatic = next((item for item in auto_basis if item == row), None)
            if automatic is None and index < len(auto_basis):
                automatic = auto_basis[index]
            if automatic is None:
                # Refresh may clear machine ownership after a partial human row
                # edit. Immutable save audits retain the original auto row basis.
                for recorded in reversed(workspace.audit):
                    if recorded.entity_id != kind.value or recorded.action not in {
                            "document_form_saved", "document_form_confirmed"}:
                        continue
                    snapshot = recorded.before if isinstance(recorded.before, dict) else {}
                    old_auto = snapshot.get("auto_rows", [])
                    matching = next((item for item in old_auto if item == row), None)
                    if matching is not None or index < len(old_auto):
                        auto_basis = old_auto
                        automatic = matching if matching is not None else old_auto[index]
                        break
            candidate = None
            exact_indices = [position for position, item in enumerate(auto_basis) if item == row]
            basis_index = None
            if len(exact_indices) == 1:
                basis_index = exact_indices[0]
            elif (not form.rows_manually_edited and index < len(auto_basis)
                    and auto_basis[index] == row):
                basis_index = index
            elif automatic:
                signature = (row.get("product_code", ""), row.get("description", ""))
                identities = [position for position, item in enumerate(auto_basis)
                              if (item.get("product_code", ""), item.get("description", "")) == signature]
                if any(signature) and len(identities) == 1:
                    basis_index = identities[0]
            if basis_index is not None:
                automatic = auto_basis[basis_index]
            identity_matches = bool(automatic and (
                (automatic.get("product_code") and automatic.get("product_code") == row.get("product_code"))
                or (not automatic.get("product_code") and automatic.get("description")
                    and automatic.get("description") == row.get("description"))))
            if basis_index is not None and identity_matches and automatic.get(name) == value:
                # Preserve row identity even when duplicate source rows have
                # identical values. A matching product code alone is not enough.
                by_document = {}
                for item in form.row_candidates:
                    if _source(workspace, item)[1]:
                        by_document.setdefault(item.document_id, []).append(item)
                from .prefill import effective_candidate_values
                candidate = next((items[basis_index] for items in by_document.values()
                    if [effective_candidate_values(workspace, item, kind, cache=row_review_cache) for item in items] == auto_basis), None)
            if candidate:
                origin = _file_origin(workspace, name, value, candidate, row_number, row_review_cache=row_review_cache)
            elif event is None and (targeted_origin := _targeted_row_origin(workspace, kind, row, row_number, name, value, row_review_cache=row_review_cache)):
                origin = targeted_origin
            elif event is None and (preserved_origin := _preserved_row_origin(workspace, kind, name, value, row_number)):
                origin = preserved_origin
            elif fallback:
                origin = _trade_origin(workspace, name, value, row_number)
            elif auto_basis and any(item.get(name) == value for item in auto_basis) and event is None:
                origin = FormValueOrigin(name, value, "unknown", row_number=row_number,
                    note="此单元格仍是旧机器预填值，但匹配原件已失效或缺失；没有人工改写依据。")
            elif form.rows_manually_edited:
                # An unchanged trade row copied by the editor still inherits the
                # order. A changed file row cell has its explicit human audit.
                trade_rows = workspace.trade.line_items
                inherited = (index < len(trade_rows)
                    and _text(getattr(trade_rows[index], name, "")) == value
                    and event is not None and event.action != "review_value_verified"
                    and not (event.before or {}).get("rows"))
                origin = (_inherited_origin(workspace, name, value, row_number, event) if inherited
                          else _manual_origin(workspace, kind, name, value, row_number))
            elif any(link.document_type is kind and link.row_number is not None
                    for link in workspace.source_requirement_links) and index < len(workspace.trade.line_items) and (
                    _text(getattr(workspace.trade.line_items[index], name, "")) == value):
                origin = _trade_origin(workspace, name, value, row_number)
            else:
                origin = FormValueOrigin(name, value, "unknown", row_number=row_number,
                    note="历史明细或预填明细缺少匹配的当前来源，不能确认录入依据。")
            row_origins[name] = origin
            result.append(origin)
        if "amount" in active_rows and not row.get("amount"):
            try:
                quantity, price = Decimal(row.get("quantity", "")), Decimal(row.get("unit_price", ""))
                amount = quantity * price
                if not amount.is_finite():
                    continue
            except (InvalidOperation, DecimalException, TypeError, ValueError):
                continue
            def describe(name):
                origin = row_origins[name]
                source = (f"{origin.filename} v{origin.version_number} {origin.location}"
                          if origin.origin_kind == "file" else
                          f"{ORIGIN_LABELS[origin.origin_kind]} {origin.actor_id or origin.note}")
                return f"{name}={origin.value}（{source}）"
            result.append(FormValueOrigin("amount", str(amount), "derived", row_number=row_number,
                note=f"quantity × unit_price；{describe('quantity')}；{describe('unit_price')}。"))
    return result
