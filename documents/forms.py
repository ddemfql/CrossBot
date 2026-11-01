"""Human-maintained seller forms and deterministic HTML worksheets."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, DecimalException, InvalidOperation
from hashlib import sha256
from html import escape
import json
import re

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import CaseStatus, DocumentForm, DocumentType, TradeData, to_dict, utc_now
from core.ports import RenderedOutput
from review.policy import audit, invalidate, require
from .catalog import (DOC_LABELS, FORM_FIELDS, REQUIRED_FIELDS, REQUIRED_ROW_FIELDS,
                      ROW_FIELDS, SELLER_TYPES)

FORM_TEMPLATE_VERSION = "w3-seller-forms-1"
TRADE_ROW_TYPES = frozenset({DocumentType.PROFORMA_INVOICE,
    DocumentType.COMMERCIAL_INVOICE, DocumentType.PACKING_LIST})


def get_form(workspace, kind) -> DocumentForm:
    kind = DocumentType(kind)
    existing = next((form for form in workspace.document_forms if form.document_type is kind), None)
    if existing:
        return deepcopy(existing)
    from .catalog import DEFAULT_FIELDS, DEFAULT_ROW_FIELDS, DEFAULT_CONTEXT_FIELDS
    return DocumentForm(document_type=kind, revision=0,
        included_fields=deepcopy(DEFAULT_FIELDS.get(kind)),
        included_row_fields=deepcopy(DEFAULT_ROW_FIELDS.get(kind)),
        included_context_fields=deepcopy(DEFAULT_CONTEXT_FIELDS.get(kind)))


def _required_fields(kind):
    names = set(REQUIRED_FIELDS.get(kind, ()))
    aliases = {DocumentType.PROFORMA_INVOICE: ("invoice_no", "invoice_date"),
        DocumentType.COMMERCIAL_INVOICE: ("invoice_no", "invoice_date"),
        DocumentType.COMBINED_INVOICE_PACKING_LIST: ("invoice_no", "invoice_date"),
        DocumentType.PACKING_LIST: ("packing_list_no", "packing_date"),
        DocumentType.SALES_CONTRACT: ("contract_no",)}
    names.update(aliases.get(kind, ()))
    if kind in {DocumentType.PROFORMA_INVOICE, DocumentType.COMMERCIAL_INVOICE,
                DocumentType.COMBINED_INVOICE_PACKING_LIST}:
        names.add("currency")
    return names


def form_fields(form):
    catalog = FORM_FIELDS[form.document_type]
    selected = set(catalog if form.included_fields is None else form.included_fields)
    selected.update(_required_fields(form.document_type))
    return {name: label for name, label in catalog.items() if name in selected}


def form_row_fields(form):
    catalog = ROW_FIELDS[form.document_type]
    selected = set(catalog if form.included_row_fields is None else form.included_row_fields)
    from .catalog import ROW_REQUIRED_TYPES
    if form.document_type in ROW_REQUIRED_TYPES or selected or form.rows:
        selected.update(REQUIRED_ROW_FIELDS.get(form.document_type, ()))
        if "product_code" in catalog:
            selected.add("product_code")
    return {name: label for name, label in catalog.items() if name in selected}


def form_context_fields(form, workspace=None):
    from .simple_export import TRADE_EXTRA_FIELDS
    allowed = set(TRADE_EXTRA_FIELDS) | {"adjustments", "packages", "line_items"}
    selected = allowed if form.included_context_fields is None else allowed & set(form.included_context_fields)
    return selected


def _mandatory_context_fields(form, workspace=None):
    selected = set()
    if workspace is not None:
        from .requirements import US_TRADE_FIELDS, US_DESTINATIONS, INVOICES
        destination = str(form.values.get("destination_country") or workspace.trade.destination_country or "").strip().casefold()
        if form.document_type in INVOICES and destination in US_DESTINATIONS:
            selected |= US_TRADE_FIELDS | {"line_items"}
            if workspace.trade.packages or workspace.trade.transport_mode or any(
                    str(row.get("packages", "")).strip() not in {"", "0"} for row in effective_rows(workspace.trade, form)):
                selected.add("packages")
            if workspace.trade.adjustments:
                selected.add("adjustments")
    return selected


def input_form_fields(form):
    selected = set(form_fields(form)) | set(form.required_input_fields)
    return {name: label for name, label in FORM_FIELDS[form.document_type].items() if name in selected}


def input_form_row_fields(form):
    selected = set(form_row_fields(form)) | set(form.required_input_row_fields)
    return {name: label for name, label in ROW_FIELDS[form.document_type].items() if name in selected}


def input_context_fields(form, workspace=None):
    return (form_context_fields(form, workspace) | set(form.required_input_context_fields)
            | _mandatory_context_fields(form, workspace))


def _values_for(trade, form, allowed):
    values = {name: str(getattr(trade, name, "") or "") for name in allowed}
    values.update({name: value for name, value in form.values.items()
                   if name in allowed and (value or name in form.manual_fields)})
    return values


def effective_values(trade, form):
    return _values_for(trade, form, form_fields(form))


def input_values(trade, form):
    return _values_for(trade, form, input_form_fields(form))


def _rows_for(trade, form, allowed):
    """Use actual form rows, including deliberate emptiness, before trade rows."""
    if form.rows or form.rows_manually_edited:
        return [{name: value for name, value in row.items() if name in allowed} for row in form.rows]
    from .catalog import ROW_REQUIRED_TYPES
    if form.document_type not in ROW_REQUIRED_TYPES:
        return []
    if not {"description", "quantity"} <= allowed.keys():
        return []
    return [{name: "" if getattr(line, name, "") is None else str(getattr(line, name, ""))
             for name in allowed}
            for line in trade.line_items]


def effective_rows(trade, form):
    return _rows_for(trade, form, form_row_fields(form))


def input_rows(trade, form):
    return _rows_for(trade, form, input_form_row_fields(form))


def missing_form_fields(trade, form):
    values = effective_values(trade, form)
    missing = [name for name in REQUIRED_FIELDS[form.document_type] if not values.get(name, "").strip()]
    has_trade_rows = (form.document_type in TRADE_ROW_TYPES and bool(trade.line_items)
                      and not form.rows_manually_edited)
    if not has_trade_rows and (not form.rows or not any(any(value.strip() for value in row.values()) for row in form.rows)):
        missing.append("rows")
    for number, row in enumerate(form.rows, 1):
        missing.extend(f"rows[{number}].{name}" for name in REQUIRED_ROW_FIELDS[form.document_type]
                       if not row.get(name, "").strip())
        if form.document_type in TRADE_ROW_TYPES and not row.get("product_code", "").strip():
            missing.append(f"rows[{number}].product_code")
    return missing


def form_conflicts(trade, form):
    """Deterministic consistency checks; no inference about compliance or signatures."""
    errors = []
    for name in ("order_no", "seller", "buyer", "currency", "destination_country", "incoterm"):
        value = form.values.get(name, "").strip()
        expected = str(getattr(trade, name, "") or "").strip()
        if value and expected and value.casefold() != expected.casefold():
            errors.append(name + ": 与已确认交易不一致")
    header_numbers = {}
    for name in ("amount_due", "opening_balance", "closing_balance", "total_amount", "volume_cbm"):
        value = form.values.get(name, "").strip()
        if not value:
            continue
        try:
            number = Decimal(value)
            if not number.is_finite() or (name not in {"opening_balance", "closing_balance"} and number < 0):
                raise InvalidOperation
            header_numbers[name] = number
        except DecimalException:
            errors.append(f"{name}: 不是有效十进制数字")
    if "amount_due" in header_numbers:
        # A payment request may cover a deposit or instalment, rather than the whole order.
        try:
            total = sum((Decimal(line.quantity) * Decimal(line.unit_price)
                         for line in trade.line_items), Decimal(0))
            if not total.is_finite() or header_numbers["amount_due"] > total:
                errors.append("amount_due: 不得超过已确认订单总额")
        except DecimalException:
            errors.append("amount_due: 已确认订单总额无法核验")
    dates = {}
    for name, value in form.values.items():
        if not value.strip() or not (name.endswith(("_date", "_until", "_on"))
                                    or name in {"period_start", "period_end"}):
            continue
        try:
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                raise ValueError
            dates[name] = date.fromisoformat(value)
        except (ValueError, TypeError):
            errors.append(f"{name}: 日期须为有效的 YYYY-MM-DD")
    if "period_start" in dates and "period_end" in dates and dates["period_start"] > dates["period_end"]:
        errors.append("period_end: 账期结束不得早于开始")
    numeric = {"quantity", "unit_price", "amount", "packages", "gross_weight_kg",
        "net_weight_kg", "debit", "credit", "balance"}
    for index, row in enumerate(form.rows, 1):
        numbers = {}
        for name, value in row.items():
            if name not in numeric or not value.strip():
                continue
            try:
                number = Decimal(value)
                if not number.is_finite() or (name != "balance" and number < 0):
                    raise InvalidOperation
                numbers[name] = number
            except DecimalException:
                errors.append(f"rows[{index}].{name}: 不是有效十进制数字")
        if {"quantity", "unit_price", "amount"} <= numbers.keys():
            try:
                if numbers["quantity"] * numbers["unit_price"] != numbers["amount"]:
                    errors.append(f"rows[{index}].amount: 与数量乘单价不一致")
            except DecimalException:
                errors.append(f"rows[{index}].amount: 数字超出可计算范围")
        if {"gross_weight_kg", "net_weight_kg"} <= numbers.keys():
            if numbers["gross_weight_kg"] < numbers["net_weight_kg"]:
                errors.append(f"rows[{index}].gross_weight_kg: 毛重小于净重")
    if form.document_type in TRADE_ROW_TYPES and form.rows:
        _compare_trade_rows(trade, form, errors)
    return errors


def _compare_trade_rows(trade, form, errors):
    """The legacy XLSX main sheet uses trade rows; supplementary rows must agree."""
    line_by_code = {line.product_code.strip(): line for line in trade.line_items}
    if len(line_by_code) != len(trade.line_items):
        errors.append("rows: 订单产品编码不唯一，无法核对明细")
        return
    seen = set()
    numeric_fields = {"quantity", "unit_price", "packages", "gross_weight_kg", "net_weight_kg"}
    text_fields = {"description", "unit", "country_of_origin", "marks_and_numbers", "package_contents"}
    for index, row in enumerate(form.rows, 1):
        code = row.get("product_code", "").strip()
        if not code:
            errors.append(f"rows[{index}].product_code: 需要产品编码关联订单明细")
            continue
        if code in seen:
            errors.append(f"rows[{index}].product_code: 产品编码重复")
        seen.add(code)
        line = line_by_code.get(code)
        if line is None:
            errors.append(f"rows[{index}].product_code: 不在已确认订单中")
            continue
        for name in (numeric_fields | text_fields) & ROW_FIELDS[form.document_type].keys():
            value = row.get(name, "").strip()
            # Blank supplementary cells stay blank in output; required blanks are pending.
            if not value:
                continue
            expected = str(getattr(line, name, "") or "").strip()
            if name in numeric_fields:
                try:
                    actual_number, expected_number = Decimal(value), Decimal(expected)
                    matches = actual_number.is_finite() and expected_number.is_finite() and actual_number == expected_number
                except DecimalException:
                    matches = False
            else:
                matches = value.casefold() == expected.casefold()
            if not matches:
                errors.append(f"rows[{index}].{name}: 与已确认订单产品 {code} 不一致")
    if seen != set(line_by_code):
        errors.append("rows: 表单产品编码必须完整对应已确认订单")


class FormService:
    def __init__(self, repository):
        self.repository = repository

    def configure_fields(self, case_id, actor, kind, fields, row_fields, reason,
                         expected_revision=None, context_fields=None, required_input_fields=None,
                         required_input_row_fields=None, required_input_context_fields=None):
        workspace = self.repository.get(case_id)
        require(workspace, actor, "manual_field")
        if expected_revision is not None and workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已被修改，请刷新后调整。")
        try:
            kind = DocumentType(kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的交付文件类型。") from exc
        if kind not in FORM_FIELDS:
            raise ValidationError("此文件类型尚不支持调整字段。")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValidationError("请填写不超过 2000 字的调整说明。")
        from .input_validation import validate_input_text
        validate_input_text(reason)
        for selected, allowed in ((fields, FORM_FIELDS[kind]), (row_fields, ROW_FIELDS[kind])):
            if not isinstance(selected, (list, tuple)) or any(name not in allowed for name in selected):
                raise ValidationError("请选择此文件支持的字段。")
        form = get_form(workspace, kind)
        before = to_dict(form)
        form.included_fields, form.included_row_fields = list(dict.fromkeys(fields)), list(dict.fromkeys(row_fields))
        form.included_fields = list(form_fields(form))
        form.included_row_fields = list(form_row_fields(form))
        if context_fields is not None:
            from .simple_export import TRADE_EXTRA_FIELDS
            if not isinstance(context_fields, (list, tuple)) or any(name not in set(TRADE_EXTRA_FIELDS) |
                    {"adjustments", "packages", "line_items"} for name in context_fields):
                raise ValidationError("请选择支持的业务补充资料。")
            form.included_context_fields = list(dict.fromkeys(context_fields))
        from .simple_export import TRADE_EXTRA_FIELDS
        for attribute, selected, allowed in (
                ("required_input_fields", required_input_fields, FORM_FIELDS[kind]),
                ("required_input_row_fields", required_input_row_fields, ROW_FIELDS[kind]),
                ("required_input_context_fields", required_input_context_fields,
                 set(TRADE_EXTRA_FIELDS) | {"adjustments", "packages", "line_items"})):
            if selected is not None:
                if not isinstance(selected, (list, tuple)) or any(name not in allowed for name in selected):
                    raise ValidationError("请选择此文件支持的必需输入资料。")
                setattr(form, attribute, list(dict.fromkeys(selected)))
        if to_dict(form) == before:
            return workspace
        form.revision += 1
        form.confirmed_by = form.confirmed_at = None
        invalidate(workspace, actor, reason.strip())
        workspace.document_forms = [item for item in workspace.document_forms if item.document_type is not kind]
        workspace.document_forms.append(form)
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "document_fields_configured", "document_form", kind.value,
            before=before, after=to_dict(form), reason=reason.strip())
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

    def save(self, case_id, actor, kind, values, rows, reason, confirm=False,
             manual_fields=None, rows_manually_edited=None, *,
             manually_provided=None, source_form_references=None, expected_workspace_revision=None,
             preserve_inherited_rows=False):
        workspace = self.repository.get(case_id)
        try:
            require(workspace, actor, "manual_field")
        except CrossBotError as exc:
            if exc.code is ErrorCode.FORBIDDEN:
                self.repository.save(workspace, expected_revision=workspace.revision)
            raise
        if expected_workspace_revision is not None and workspace.revision != expected_workspace_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已被修改，请刷新后重新填写。")
        try:
            kind = DocumentType(kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的文件类型。") from exc
        if kind not in FORM_FIELDS:
            raise ValidationError("该类型暂无结构化记录字段。")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValidationError("保存表单必须填写不超过 2000 字的依据。")
        from .input_validation import validate_input_text
        validate_input_text(reason)
        if not isinstance(confirm, bool):
            raise ValidationError("确认标记须为布尔值。")
        if not isinstance(values, dict) or set(values) - set(FORM_FIELDS[kind]):
            raise ValidationError("表单包含不支持的字段。")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValidationError("明细须为列表且最多 1000 行。")

        def clean(mapping, allowed):
            if not isinstance(mapping, dict) or set(mapping) - set(allowed):
                raise ValidationError("明细包含不支持的字段。")
            if any(not isinstance(value, str) or len(value) > 4000 for value in mapping.values()):
                raise ValidationError("表单值须为不超过 4000 字的文本。")
            from .input_validation import validate_input_text
            validate_input_text(mapping)
            return {name: value.strip() for name, value in mapping.items()}

        cleaned = clean(values, FORM_FIELDS[kind])
        cleaned_rows = [clean(row, ROW_FIELDS[kind]) for row in rows]
        previous = get_form(workspace, kind)
        cleaned = {**{name: value for name, value in previous.values.items() if name not in form_fields(previous)}, **cleaned}
        visible_rows = [{name: value for name, value in row.items() if name in input_form_row_fields(previous)} for row in cleaned_rows]
        identity_names = [name for name in ("product_code", "parameter", "check_item", "requirement")
                          if name in form_row_fields(previous)]
        for row in cleaned_rows:
            matches = [old for old in previous.rows if identity_names and all(row.get(name) and row.get(name) == old.get(name)
                for name in identity_names)]
            if len(matches) == 1:
                row.update({name: value for name, value in matches[0].items()
                            if name not in form_row_fields(previous) and name not in row})
        if not isinstance(preserve_inherited_rows, bool):
            raise ValidationError("明细继承标记无效。")
        inherited_rows_preserved = (preserve_inherited_rows and not cleaned_rows
            and not previous.rows and not previous.rows_manually_edited)
        if manually_provided is not None and not isinstance(manually_provided, bool):
            raise ValidationError("资料提供标记须为布尔值。")
        from core.models import FormSourceReference
        if source_form_references is not None and (not isinstance(source_form_references, list)
                or any(not isinstance(ref, FormSourceReference) for ref in source_form_references)):
            raise ValidationError("填写资料来源记录无效。")
        for ref in source_form_references or []:
            from .manual_inputs import has_manual_data, manual_source
            source = manual_source(workspace, ref.document_type, ref.record_id)
            if (source is None or (not ref.record_id and ref.document_type is kind)
                    or source.revision != ref.revision or not has_manual_data(source)
                    or getattr(source, "source_form_references", [])):
                raise ValidationError("填写资料来源已失效，请刷新后核对。")
        if (manual_fields is not None and (not isinstance(manual_fields, (list, tuple))
                or any(name not in FORM_FIELDS[kind] for name in manual_fields))):
            raise ValidationError("人工字段标记无效。")
        if rows_manually_edited is not None and not isinstance(rows_manually_edited, bool):
            raise ValidationError("人工明细标记无效。")
        protected = set(previous.manual_fields) | set(manual_fields or [])
        protected.update(name for name, value in cleaned.items()
                         if value != previous.values.get(name, "")
                         or (name not in previous.auto_values and name not in previous.values))
        protected.update(name for name, value in previous.values.items()
                         if value and name not in previous.auto_values)
        form = DocumentForm(document_type=kind, values=cleaned, rows=cleaned_rows,
            source_document_ids=previous.source_document_ids, revision=previous.revision + 1,
            confirmed_by=actor.user_id if confirm else None,
            confirmed_at=utc_now() if confirm else None,
            field_candidates=previous.field_candidates, row_candidates=previous.row_candidates,
            auto_values={name: value for name, value in previous.auto_values.items() if name not in protected},
            auto_rows=previous.auto_rows,
            manual_fields=sorted(protected),
            rows_manually_edited=(previous.rows_manually_edited
                or (not inherited_rows_preserved and visible_rows != input_rows(workspace.trade, previous))
                or bool(rows_manually_edited)),
            manually_provided=(previous.manually_provided if manually_provided is None else manually_provided)
                and bool(any(cleaned.values()) or any(any(row.values()) for row in cleaned_rows)),
            source_form_references=deepcopy(previous.source_form_references
                if source_form_references is None else source_form_references),
            included_fields=deepcopy(previous.included_fields),
            included_row_fields=deepcopy(previous.included_row_fields),
            included_context_fields=deepcopy(previous.included_context_fields),
            required_input_fields=deepcopy(previous.required_input_fields),
            required_input_row_fields=deepcopy(previous.required_input_row_fields),
            required_input_context_fields=deepcopy(previous.required_input_context_fields))
        invalidate(workspace, actor, reason.strip())
        workspace.document_forms = [item for item in workspace.document_forms if item.document_type is not kind]
        workspace.document_forms.append(form)
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "document_form_confirmed" if confirm else "document_form_saved",
            "document_form", kind.value, before=to_dict(previous) if previous.revision else None,
            after=to_dict(form), reason=reason.strip())
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace


def render_blank(kind) -> RenderedOutput:
    """Return an empty seller template without saving or confirming any data."""
    kind = DocumentType(kind)
    return render_form(kind, TradeData(currency="", revision=0), DocumentForm(document_type=kind, revision=0))


def render_form(kind, trade=None, form=None) -> RenderedOutput:
    # Also accept render_form(kind, form) for UI previews of blank worksheets.
    if isinstance(trade, DocumentForm) and form is None:
        form, trade = trade, TradeData(currency="", revision=0)
    trade = trade or TradeData(currency="", revision=0)
    form = form or DocumentForm(document_type=kind, revision=0)
    if kind not in SELLER_TYPES:
        raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED, "该类型由外部机构或买方提供，仅可托管原件。")
    spec = {"type": kind.value, "version": FORM_TEMPLATE_VERSION,
        "fields": FORM_FIELDS[kind], "rows": ROW_FIELDS[kind], "required": REQUIRED_FIELDS[kind]}
    template_hash = sha256(json.dumps(spec, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    values = effective_values(trade, form)
    def e(value):
        return escape(str(value or ""), quote=True)
    fields = "".join(f"<tr><th>{e(label)}</th><td>{e(values.get(name, ''))}</td></tr>"
        for name, label in FORM_FIELDS[kind].items())
    headers = "".join(f"<th>{e(label)}</th>" for label in ROW_FIELDS[kind].values())
    rows = "".join("<tr>" + "".join(f"<td>{e(row.get(name, ''))}</td>"
        for name in ROW_FIELDS[kind]) + "</tr>" for row in form.rows)
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{e(DOC_LABELS[kind])}</title>
<style>body{{font-family:Arial,sans-serif;margin:30px;color:#203330}}table{{border-collapse:collapse;width:100%;margin:20px 0}}th,td{{border:1px solid #bbc7ce;padding:8px;text-align:left;white-space:pre-wrap}}th{{background:#eef3f6}}.draft{{padding:12px;border:1px solid #9b4a00;color:#9b4a00}}@media print{{body{{margin:15mm}}}}</style></head>
<body><h1>{e(DOC_LABELS[kind])}</h1><p class="draft">DRAFT / 卖方填写稿 · 使用前须人工复核；空白内容待填写。</p>
<table>{fields}</table><table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>
<footer>模板 {e(FORM_TEMPLATE_VERSION)} · 表单修订 {form.revision} · 订单修订 {trade.revision}。
仅整理用户确认的资料；不代表外部签发、实际验货、认证、申报成功或任何人的签名。</footer></body></html>'''
    return RenderedOutput(content=html.encode("utf-8"), media_type="text/html", extension="html",
        template_id="w3-form-" + kind.value, template_version=FORM_TEMPLATE_VERSION,
        template_sha256=template_hash)

