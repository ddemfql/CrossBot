"""Field-level input requirements shared by inventory, review and generation.

Requirements describe usable business data, never require an invented original.
All populated optional values are included so exports have no hidden inputs.
"""
from core.models import DataRequirement, DocumentType as D, FormValueOrigin, TradeData, to_dict
from .catalog import FORM_FIELDS, ROW_FIELDS, REQUIRED_FIELDS, REQUIRED_ROW_FIELDS, SELLER_TYPES, ROW_REQUIRED_TYPES
from .forms import (effective_values, effective_rows, get_form, form_fields,
                    form_row_fields, form_context_fields)
from .provenance import form_value_origins

NUMBER_ALIASES = {D.PROFORMA_INVOICE: "invoice_no", D.COMMERCIAL_INVOICE: "invoice_no",
    D.COMBINED_INVOICE_PACKING_LIST: "invoice_no", D.PACKING_LIST: "packing_list_no",
    D.SALES_CONTRACT: "contract_no"}
DATE_ALIASES = {D.PROFORMA_INVOICE: "invoice_date", D.COMMERCIAL_INVOICE: "invoice_date",
    D.COMBINED_INVOICE_PACKING_LIST: "invoice_date", D.PACKING_LIST: "packing_date"}
INVOICES = {D.PROFORMA_INVOICE, D.COMMERCIAL_INVOICE, D.COMBINED_INVOICE_PACKING_LIST}
US_DESTINATIONS = {"us", "usa", "united states", "united states of america"}
US_TRADE_FIELDS = {"transaction_date", "transaction_place", "port_of_entry", "seller_address",
                   "buyer_address", "responsible_exporter_person", "invoice_adjustments_confirmed"}
US_LINE_FIELDS = {"country_of_origin", "grade_quality", "marks_and_numbers", "package_contents"}


def _text(value):
    value = to_dict(value)
    return "" if value is None else str(value)


def _trade_origin(workspace, name, value, *, collection=None, index=None):
    """Find an actual recorded matching input snapshot; never invent a recorder."""
    if collection is None or collection == "line_items":
        from .provenance import _trade_origin as origin_for_trade
        origin = origin_for_trade(workspace, name, _text(value),
            row_number=index + 1 if collection else None)
        if collection is None and workspace.trade == TradeData() and name in {
                "incoterm_version", "invoice_adjustments_confirmed"}:
            origin.origin_kind = "default"
            origin.note = "未补充订单的系统初始值，需明确填写核实。"
        return origin
    for event in reversed(workspace.audit):
        if event.action not in {"case_created", "trade_updated"} or not isinstance(event.after, dict):
            continue
        target = event.after.get("review_edit_target")
        if isinstance(target, dict):
            field, row = target.get("field_name"), target.get("row_number")
            whole_collection = field == collection and row is None
            exact_field = field == f"{collection}[{index + 1}].{name}" and row is None if index is not None else False
            if not (whole_collection or exact_field):
                continue
        snapshot = event.after.get("trade", event.after)
        records = snapshot.get(collection, [])
        if index is None or index >= len(records) or not isinstance(records[index], dict):
            continue
        recorded = records[index].get(name)
        if _text(recorded) != _text(value) or not event.actor_id or not event.occurred_at:
            continue
        return FormValueOrigin(name, _text(value), "trade", actor_id=event.actor_id,
            occurred_at=event.occurred_at, note="订单录入记录：" + (
                f"{collection}[{index + 1}]." if collection else "") + name)
    return FormValueOrigin(name, _text(value), "unknown", note="尚未找到匹配当前内容的订单人工录入记录。")


def data_requirements(workspace, kind):
    kind = D(kind)
    if kind not in SELLER_TYPES:
        return []
    from .simple_export import TRADE_EXTRA_FIELDS, LINE_EXTRA_FIELDS, PACKAGE_FIELDS
    from .forms import input_form_fields, input_form_row_fields, input_context_fields, input_values, input_rows
    form = get_form(workspace, kind)
    active_fields, active_row_fields = input_form_fields(form), input_form_row_fields(form)
    contexts = input_context_fields(form, workspace)
    values, rows = input_values(workspace.trade, form), input_rows(workspace.trade, form)
    origins = {(o.row_number, o.field_name): o for o in form_value_origins(workspace, kind, include_inputs=True)}
    required = set(REQUIRED_FIELDS[kind]) | set(form.required_input_fields)
    required_context = set(form.required_input_context_fields)
    if kind in INVOICES:
        required.add("currency")
    result = []
    destination = str(values.get("destination_country") or workspace.trade.destination_country or "").strip().casefold()
    us_invoice = kind in INVOICES and destination in US_DESTINATIONS

    def add(name, label, value, needed, row=None, origin=None, *, satisfied=None, force=False):
        value = _text(value)
        if not needed and not value.strip() and not force:
            return
        state = "provided" if value.strip() else "missing"
        message = ""
        if value.strip() and (origin is None or origin.origin_kind in {"unknown", "default"}):
            state, message = "review_required", "来源或初始值需要明确填写核实。"
        if value.strip() and origin and origin.origin_kind in {"manual", "trade"} and not (
                origin.actor_id and origin.occurred_at):
            state, message = "review_required", "当前内容缺少可核实的人工录入人或时间。"
        if satisfied is False and needed:
            state, message = "missing", "尚未明确核实费用及调整项目；无调整也需明确记录。"
        result.append(DataRequirement(kind, name, label, needed, row, value, state, origin, message))

    for name, label in active_fields.items():
        value, origin = values.get(name, ""), origins.get((None, name))
        if value and form.auto_values.get(name) == value:
            from .source_mapping import map_field
            candidates = [c for c in form.field_candidates.get(name, []) if c.value == value]
            active = [c for c in candidates if origin is None or not origin.document_id
                      or c.document_id == origin.document_id]
            unsafe = any((doc := next((d for d in workspace.documents if d.document_id == c.document_id), None))
                and map_field(doc.document_type, kind, c.field_name or name) != name for c in active)
            if unsafe:
                origin = FormValueOrigin(name, _text(value), "unknown",
                    note="旧版自动填写字段不符合当前来源映射，请重新采集或由人工核实后填写。")
        alias = NUMBER_ALIASES.get(kind) if name == "document_no" else DATE_ALIASES.get(kind) if name == "document_date" else None
        if not str(value).strip() and alias and str(values.get(alias, "")).strip():
            value, origin = values[alias], origins.get((None, alias))
        add(name, label, value, name in required, origin=origin,
            force=form.included_fields is not None)
    row_required = set(REQUIRED_ROW_FIELDS[kind]) | set(form.required_input_row_fields)
    if us_invoice:
        row_required.update(US_LINE_FIELDS & active_row_fields.keys())
    if "product_code" in active_row_fields:
        row_required.add("product_code")
    if not rows and (kind in ROW_REQUIRED_TYPES or form.required_input_row_fields):
        add("rows", "产品/业务明细", "", True)
    for index, row in enumerate(rows, 1):
        for name, label in active_row_fields.items():
            add(name, label, row.get(name, ""), name in row_required, index,
                origins.get((index, name)), force=form.included_row_fields is not None)
    for name, label in TRADE_EXTRA_FIELDS.items():
        if name in active_fields or name not in contexts:
            continue
        value = getattr(workspace.trade, name)
        needed = (us_invoice and name in US_TRADE_FIELDS) or name in required_context
        add(name, label, value, needed,
            origin=_trade_origin(workspace, name, value),
            satisfied=value is True if name == "invoice_adjustments_confirmed" else None,
            force=form.included_context_fields is not None)
    for group, label, records in (("adjustments", "费用与调整明细", workspace.trade.adjustments),
                                  ("packages", "逐包内容明细", workspace.trade.packages),
                                  ("line_items", "订单补充明细", workspace.trade.line_items)):
        if group in required_context and not records:
            add(group, label, "", True)
    # Extra product attributes belong to the actual product, not an arbitrary
    # row position. Missing/duplicate master matches remain unknown.
    for index, row in enumerate(rows, 1):
        matches = [(i, line) for i, line in enumerate(workspace.trade.line_items)
                   if line.product_code and line.product_code == row.get("product_code")]
        for name, label in LINE_EXTRA_FIELDS.items() if "line_items" in contexts else []:
            if name in active_row_fields:
                continue
            value = getattr(matches[0][1], name) if len(matches) == 1 else ""
            add(name, label, value, us_invoice and name in US_LINE_FIELDS, index,
                _trade_origin(workspace, name, value, collection="line_items", index=matches[0][0])
                if len(matches) == 1 else None)
    for index, line in enumerate(workspace.trade.line_items if "line_items" in contexts else [], 1):
        for name, label in LINE_EXTRA_FIELDS.items():
            value = getattr(line, name)
            add(f"line_items[{index}].{name}", f"订单产品第{index}行 · {label}", value, False,
                origin=_trade_origin(workspace, name, value, collection="line_items", index=index - 1))
    # These values are rendered in the export as separate business input sheets.
    for index, item in enumerate(workspace.trade.adjustments if "adjustments" in contexts else [], 1):
        for name in ("kind", "name", "amount", "currency", "included_in_invoice_price"):
            value = getattr(item, name)
            add(f"adjustments[{index}].{name}", f"费用第{index}项 · {name}", value, True,
                origin=_trade_origin(workspace, name, value, collection="adjustments", index=index - 1))
    physical_packages = bool(workspace.trade.packages) or any(
        str(row.get("unit", "")).casefold() not in {"service", "services", "hour", "hours"}
        and (str(row.get("packages", "")).strip() not in {"", "0"} or bool(workspace.trade.transport_mode))
        for row in rows)
    if us_invoice and physical_packages and not workspace.trade.packages and "packages" in contexts:
        add("package_details", "逐包内容明细", "", True)
    for index, item in enumerate(workspace.trade.packages if "packages" in contexts else [], 1):
        for name in PACKAGE_FIELDS:
            value = getattr(item, name)
            add(f"packages[{index}].{name}", f"包件第{index}项 · {name}", value,
                name in {"package_number", "product_code", "quantity", "unit"}
                    or us_invoice and physical_packages,
                origin=_trade_origin(workspace, name, value, collection="packages", index=index - 1))
    return result


def missing_required_data(workspace, kind):
    return [item for item in data_requirements(workspace, kind)
            if item.required and item.status != "provided"]


def delivery_requirements(workspace):
    from .checklist import requirement_groups
    kinds = list(dict.fromkeys([*requirement_groups(workspace)[1],
        *(artifact.document_type for artifact in workspace.artifacts if artifact.document_type in SELLER_TYPES)]))
    return {kind: data_requirements(workspace, kind) for kind in kinds}
