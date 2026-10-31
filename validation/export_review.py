"""Read-only, versioned assessment of one exact seller export snapshot.

The human flag reviews effective selected/related saved seller form values in
this digest. It never promotes raw source fields, changes a Case, or confirms
unknown provenance. Required-document inventory is advisory for standalone files.
Comparison policy: exact literal text, unique SKU pairing, explicit units,
zero monetary/weight tolerance, no rounding, currency conversion or aliases.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, DecimalException, localcontext
from hashlib import sha256
import json
import re

from core.errors import ValidationError
from core.models import (CaseStatus, CrossCheckResult, CrossCheckStatus as S,
    DocumentType as T, ExportReviewReport, FieldValueStatus as F, TradeData, to_dict)
from documents.catalog import FORM_FIELDS, ROW_FIELDS, SELLER_TYPES
from documents.forms import effective_rows, effective_values, get_form, missing_form_fields
from documents.provenance import form_value_origins
from .cross_checker import get_current_version
from .normalizers import (convert_weight, normalize_currency, normalize_date,
                          normalize_decimal, normalize_text)
from .rules import compare_currency, compare_money, compare_quantity, compare_text, compare_weight

RULE_VERSION = "w3-export-review-1"
VERSION = RULE_VERSION
POLICY = {"version": RULE_VERSION, "tolerance": "0", "rounding": "none",
          "text": "trim-only-case-sensitive", "pairing": "unique-product_code",
          "human_scope": "exact-effective-selected-and-related-saved-seller-forms",
          "raw_sources": "confirmed-fields-only"}
INVOICES = {T.COMMERCIAL_INVOICE, T.PROFORMA_INVOICE, T.COMBINED_INVOICE_PACKING_LIST}
PRODUCT_KINDS = INVOICES | {T.PACKING_LIST, T.SALES_CONTRACT, T.ORDER_CONFIRMATION, T.QUOTATION}
DATE_ALIASES = {T.COMMERCIAL_INVOICE: "invoice_date", T.PROFORMA_INVOICE: "invoice_date",
    T.COMBINED_INVOICE_PACKING_LIST: "invoice_date", T.PACKING_LIST: "packing_date"}
NUMBER_ALIASES = {kind: "invoice_no" for kind in INVOICES} | {T.PACKING_LIST: "packing_list_no"}
NUMBERS = {"quantity", "unit_price", "amount", "packages", "gross_weight_kg", "net_weight_kg",
           "total_amount", "amount_due", "debit", "credit", "balance", "volume_cbm",
           "opening_balance", "closing_balance"}
COUNT_UNITS = frozenset({"pcs", "piece", "pieces", "box", "boxes", "carton", "cartons", "set", "sets"})


def review_digest(workspace, kind):
    """Bind inputs, provenance evidence and current sources; ignore download noise."""
    kind = T(kind)
    documents = []
    for document in workspace.documents:
        current = [to_dict(version) for version in document.versions
                   if version.version_id == document.current_version_id]
        for version in current:
            tables = version.pop("source_tables", [])
            version["source_tables_sha256"] = sha256(json.dumps(tables, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        documents.append({"document_id": document.document_id, "case_id": document.case_id,
            "type": document.document_type.value, "status": document.status.value,
            "current_version_id": document.current_version_id, "current": current,
            "deleted": document.deleted_at is not None})
    evidence = [{"actor": event.actor_id, "action": event.action, "entity": event.entity_id,
                 "before": event.before, "after": event.after}
        for event in workspace.audit if event.action in {
            "case_created", "trade_updated", "document_form_saved", "document_form_confirmed"}]
    payload = {"rule_version": RULE_VERSION, "selected": kind.value,
        "case_id": workspace.case.case_id, "closed": workspace.case.status is CaseStatus.CLOSED,
        "case_identity": {name: getattr(workspace.case, name) for name in (
            "order_no", "customer_name", "destination_country", "currency", "incoterm")},
        "trade": to_dict(workspace.trade), "forms": to_dict(workspace.document_forms),
        "documents": documents, "required": to_dict(workspace.required_document_types),
        "base_required": to_dict(workspace.base_required_document_types),
        "policies": to_dict(workspace.requirement_policies), "human_evidence": evidence}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def _nonempty(value):
    return value is not None and bool(str(value).strip())


def _multiply(left, right):
    with localcontext() as context:
        context.prec = max(28, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2)
        return left * right


def _sum(values):
    if not values:
        return None
    with localcontext() as context:
        context.prec = max(28, max(value.adjusted() for value in values)
            - min(value.as_tuple().exponent for value in values) + len(str(len(values))) + 3)
        return sum(values, Decimal(0))


def _view(workspace, kind):
    form = get_form(workspace, kind)
    return {"kind": kind, "form": form, "values": effective_values(workspace.trade, form),
            "rows": effective_rows(workspace.trade, form),
            "origins": {(origin.row_number, origin.field_name): origin
                        for origin in form_value_origins(workspace, kind)}}


def _source(view, field, row=None):
    origin = view["origins"].get((row, field))
    prefix = (f"trade:r{view['trade_revision']}" if "trade_revision" in view
              else f"form:{view['kind'].value}:r{view['form'].revision}")
    if row is not None:
        prefix += f":rows[{row}].{field}"
    else:
        prefix += f":{field}"
    return (prefix + (f";{origin.origin_kind};{origin.filename};{origin.location}" if origin else ""),
            origin.document_id or None if origin else None,
            origin.version_id or None if origin else None)


def review_export(workspace, kind, *, human_confirmed=False):
    kind = T(kind)
    human = human_confirmed is True
    report = ExportReviewReport(workspace.case.case_id, kind, review_digest(workspace, kind),
                                RULE_VERSION, human_confirmed=human)

    def add(code, status, message, *, left=None, right=None, field=None, row=None,
            lv=None, rv=None, lu=None, ru=None, nl=None, nr=None, required=True,
            scope="document", item="", right_field=None, right_row=None, raw=None, policy=None):
        ls, ld, lver = _source(left, field, row) if left and field else (None, None, None)
        rs, rd, rver = (_source(right, right_field or field, right_row if right_row is not None else row)
                       if right and field else (None, None, None))
        if raw:
            rd, rver, rs = raw
        report.checks.append(CrossCheckResult(case_id=workspace.case.case_id,
            rule_code=code, status=status, message=message, rule_version=RULE_VERSION,
            left_document_id=ld, right_document_id=rd, left_version_id=lver,
            right_version_id=rver, left_field=field, right_field=right_field or field,
            left_value=lv, right_value=rv, left_source=ls, right_source=rs,
            normalized_left=nl, normalized_right=nr, left_unit=lu, right_unit=ru,
            comparison_policy=dict(POLICY, **(policy or {})), scope=scope, item_key=item, required=required))

    def state(ok, *, invalid=False):
        return S.FAIL if not ok and invalid else S.PASS if ok else S.PENDING_REVIEW

    def confirmed_status(reviewed):
        return F.CONFIRMED if reviewed else F.CANDIDATE

    def cmp(left, right, field, lv, rv, *, category="text", row=None, right_row=None,
            lu=None, ru=None, reviewed=None, raw=None, code="CROSS_DOCUMENT", raw_field_name=None):
        ready = human if reviewed is None else human and reviewed
        args = {"left_status": confirmed_status(ready), "right_status": confirmed_status(ready),
                "context_confirmed": ready}
        nl, nr = None, None
        comparison_policy = {"minimum": "0", "tolerance": "0"}
        try:
            if category == "quantity":
                fractional = field != "packages" and lu not in COUNT_UNITS and ru not in COUNT_UNITS
                comparison_policy.update(allow_fractional=fractional,
                    count_units=sorted(COUNT_UNITS), unit_policy="exact-explicit-no-conversion")
                result = compare_quantity(lv, rv, **args, left_unit=lu, right_unit=ru,
                    minimum=Decimal(0), allow_fractional=fractional)
                nl, nr = str(normalize_decimal(lv)), str(normalize_decimal(rv))
            elif category == "weight":
                result = compare_weight(lv, rv, **args, left_unit=lu, right_unit=ru,
                    minimum=Decimal(0), tolerance=Decimal(0))
                nl, nr = str(convert_weight(lv, lu)), str(convert_weight(rv, ru))
            elif category == "money":
                result = compare_money(lv, rv, **args, left_currency=lu, right_currency=ru,
                    minimum=Decimal(0), tolerance=Decimal(0))
                nl, nr = str(normalize_decimal(lv)), str(normalize_decimal(rv))
            elif category == "currency":
                result = compare_currency(lv, rv, **args)
                nl, nr = normalize_currency(lv), normalize_currency(rv)
            else:
                result = compare_text(lv, rv, **args)
                nl, nr = normalize_text(lv), normalize_text(rv)
        except ValidationError:
            result = S.PENDING_REVIEW
        add(code, result, f"{left['kind'].value} 与 {right['kind'].value if right else '外部原件'}：{field}",
            left=left, right=right, field=field, row=row, right_row=right_row,
            lv=lv, rv=rv, lu=lu, ru=ru, nl=nl, nr=nr, scope="cross_document",
            item=str(left["rows"][row - 1].get("product_code", "")) if row else "", raw=raw)
        report.checks[-1].comparison_policy.update(comparison_policy)
        if raw_field_name is not None:
            report.checks[-1].right_field = raw_field_name

    selected = _view(workspace, kind) if kind in FORM_FIELDS else None
    add("EXPORT_HUMAN_CONFIRMATION", state(human), "已核对本次精确输入。" if human else "尚未由真人核对本次精确输入。")
    add("EXPORT_SUPPORTED_TYPE", state(kind in SELLER_TYPES, invalid=True),
        "仅生成我方填写文件；外部签发文件须保存真实原件。")
    add("EXPORT_OPEN_CASE", state(workspace.case.status is not CaseStatus.CLOSED, invalid=True),
        "已关闭交易不可新增生成版本。" if workspace.case.status is CaseStatus.CLOSED else "交易允许生成新版本。")
    if selected is None or kind not in SELLER_TYPES:
        return report

    def source_checks(view):
        form = view["form"]
        for document_id in form.source_document_ids:
            document = next((doc for doc in workspace.documents if doc.document_id == document_id
                             and doc.deleted_at is None), None)
            try:
                if document is None:
                    raise ValidationError("来源文件已移除或缺失")
                version = get_current_version(workspace.case, document)
                if not re.fullmatch(r"[0-9a-f]{64}", version.sha256):
                    raise ValidationError("来源哈希无效")
                add("SOURCE_CURRENT", S.PASS, "来源归属、当前解析版本与哈希元数据有效。",
                    left=view, field="source_document_ids", lv=document_id,
                    rv=version.version_id, scope="source")
            except ValidationError as exc:
                add("SOURCE_CURRENT", S.PENDING_REVIEW, str(exc), left=view,
                    field="source_document_ids", lv=document_id, scope="source")
        for origin in view["origins"].values():
            if not _nonempty(origin.value):
                continue
            known = (origin.origin_kind in {"file", "default", "derived"}
                     or origin.origin_kind in {"manual", "trade"}
                     and bool(origin.actor_id and origin.occurred_at))
            if origin.origin_kind == "file":
                document = next((doc for doc in workspace.documents
                                 if doc.document_id == origin.document_id and doc.deleted_at is None), None)
                try:
                    version = get_current_version(workspace.case, document) if document else None
                    known = bool(version and version.version_id == origin.version_id
                                 and re.fullmatch(r"[0-9a-f]{64}", version.sha256))
                except ValidationError:
                    known = False
            add("VALUE_PROVENANCE", state(known), origin.note or "字段来源核对。",
                left=view, field=origin.field_name, row=origin.row_number,
                lv=origin.value, scope="source")
        for field, candidates in form.field_candidates.items():
            distinct = {candidate.value for candidate in candidates}
            if len(distinct) <= 1:
                continue
            origin = view["origins"].get((None, field))
            resolved = bool(origin and origin.origin_kind == "manual" and origin.actor_id)
            add("SOURCE_FIELD_CONFLICT", state(resolved),
                "多来源字段冲突已由可追溯人工改值解决。" if resolved else "多来源字段存在不同候选，须明确人工修改或选择来源。",
                left=view, field=field, lv=view["values"].get(field), rv=sorted(distinct), scope="source")
        sets = defaultdict(list)
        for candidate in form.row_candidates:
            sets[candidate.document_id].append(candidate.values)
        if len({json.dumps(rows, sort_keys=True, ensure_ascii=False) for rows in sets.values()}) > 1:
            audited = any(event.entity_id == view["kind"].value
                and event.action in {"document_form_saved", "document_form_confirmed"}
                and isinstance(event.after, dict) and event.after.get("rows") == form.rows
                and (event.before or {}).get("rows") != form.rows for event in workspace.audit)
            add("SOURCE_ROW_CONFLICT", state(bool(form.rows_manually_edited and audited)),
                "多来源明细存在不同产品集；须选定来源或明确人工维护明细。", left=view,
                field="rows", lv=form.rows, rv=dict(sets), scope="source")

    source_checks(selected)
    effective_form = deepcopy(selected["form"])
    effective_form.values = {name: "" if value is None else str(value)
                             for name, value in selected["values"].items()}
    effective_form.rows = [{name: "" if value is None else str(value) for name, value in row.items()}
                           for row in selected["rows"]]
    for target, aliases in (("document_no", NUMBER_ALIASES), ("document_date", DATE_ALIASES)):
        alias = aliases.get(kind)
        if alias and _nonempty(effective_form.values.get(alias)):
            if not _nonempty(effective_form.values.get(target)):
                effective_form.values[target] = effective_form.values[alias]
            else:
                cmp(selected, selected, target, effective_form.values[target], effective_form.values[alias],
                    code="DOCUMENT_IDENTITY_ALIAS")
    missing = missing_form_fields(TradeData(currency=""), effective_form)
    if kind in INVOICES and not _nonempty(selected["values"].get("currency")):
        missing.append("currency")
    add("DOCUMENT_COMPLETENESS", state(not missing), "所选文件字段齐备。" if not missing else "待补：" + "、".join(missing),
        left=selected, field="document_form", lv=missing)

    def numeric(view, name, value, row=None):
        if not _nonempty(value):
            return None
        try:
            number = normalize_decimal(value)
            valid = number > 0 if name == "quantity" else number >= 0 or name in {"balance", "opening_balance", "closing_balance"}
            if name == "packages":
                valid = valid and number == number.to_integral_value()
            count_unit = (view["rows"][row - 1].get("unit") if row and name == "quantity" else None)
            if count_unit in COUNT_UNITS:
                valid = valid and number == number.to_integral_value()
            add("NUMBER_VALID", state(valid, invalid=True), f"{name} 必须是有效{'正' if name == 'quantity' else '非负'}十进制数" +
                ("且箱数必须为整数。" if name == "packages" else "。"), left=view, field=name,
                row=row, lv=value, nl=str(number), lu=count_unit,
                policy={"minimum": "0", "strictly_positive": name == "quantity",
                    "allow_fractional": name != "packages" and count_unit not in COUNT_UNITS})
            return number if valid else None
        except ValidationError:
            add("NUMBER_VALID", S.FAIL, f"{name} 不接受浮点、指数、非数字或非有限值。", left=view, field=name, row=row, lv=value)
            return None

    for name, value in effective_form.values.items():
        if not _nonempty(value):
            continue
        if name == "currency":
            try:
                normalized = normalize_currency(value)
                add("CURRENCY_VALID", S.PASS, "三位币种代码。", left=selected, field=name, lv=value, nl=normalized)
            except ValidationError:
                add("CURRENCY_VALID", S.FAIL, "币种必须为明确的三位代码。", left=selected, field=name, lv=value)
        elif name in NUMBERS:
            numeric(selected, name, value)
        elif name.endswith(("_date", "_until", "_on")) or name in {"period_start", "period_end"}:
            try:
                normalized = normalize_date(value).isoformat()
                add("DATE_VALID", S.PASS, "明确的 YYYY-MM-DD 日期。", left=selected, field=name, lv=value, nl=normalized)
            except ValidationError:
                add("DATE_VALID", S.FAIL, "日期须为有效 YYYY-MM-DD。", left=selected, field=name, lv=value)

    for index, row in enumerate(selected["rows"], 1):
        numbers = {name: numeric(selected, name, value, index)
                   for name, value in row.items() if name in NUMBERS and _nonempty(value)}
        if {"gross_weight_kg", "net_weight_kg"} <= numbers.keys() and all(
                numbers[name] is not None for name in ("gross_weight_kg", "net_weight_kg")):
            add("GROSS_NOT_BELOW_NET", state(numbers["gross_weight_kg"] >= numbers["net_weight_kg"], invalid=True),
                "同一产品行毛重不得小于净重。", left=selected, field="gross_weight_kg", row=index,
                lv=row["gross_weight_kg"], rv=row["net_weight_kg"], lu="kg", ru="kg")
        if numbers.get("quantity") is not None and numbers.get("unit_price") is not None:
            expected = _multiply(numbers["quantity"], numbers["unit_price"])
            if _nonempty(row.get("amount")):
                cmp(selected, selected, "amount", row["amount"], str(expected), category="money", row=index,
                    lu=selected["values"].get("currency"), ru=selected["values"].get("currency"), code="LINE_AMOUNT")

    def index_rows(view):
        result = {}
        codes = [row.get("product_code", "") for row in view["rows"]]
        try:
            normalized_codes = [normalize_text(code) for code in codes]
            valid = bool(codes) and len(set(normalized_codes)) == len(codes)
        except ValidationError:
            normalized_codes, valid = [], False
        add("ROW_IDENTITY", state(valid), "产品行必须具有唯一且大小写精确的产品编码。", left=view,
            field="product_code", lv=codes, nl=normalized_codes)
        if valid:
            result = {code: (index, row) for index, (code, row) in enumerate(zip(normalized_codes, view["rows"]), 1)}
        return result

    selected_index = index_rows(selected) if kind in PRODUCT_KINDS else {}
    master = {"kind": kind, "form": selected["form"], "trade_revision": workspace.trade.revision,
              "origins": {}, "rows": [], "values": {}}
    for field in ("order_no", "seller", "buyer", "destination_country", "currency", "incoterm"):
        expected = getattr(workspace.trade, field)
        if field in FORM_FIELDS[kind] and _nonempty(expected) and _nonempty(selected["values"].get(field)):
            cmp(selected, master, field, selected["values"].get(field), expected,
                category="currency" if field == "currency" else "text", code="ORDER_IDENTITY")
    for field, expected in (("order_no", workspace.case.order_no),
            ("buyer", workspace.case.customer_name), ("destination_country", workspace.case.destination_country),
            ("currency", workspace.case.currency), ("incoterm", workspace.case.incoterm)):
        if (field in FORM_FIELDS[kind] and _nonempty(expected) and _nonempty(selected["values"].get(field))
                and expected != getattr(workspace.trade, field, None)):
            cmp(selected, master, field, selected["values"].get(field), expected,
                category="currency" if field == "currency" else "text", code="CASE_IDENTITY")

    def total(view):
        values = []
        for row in view["rows"]:
            try:
                amount = normalize_decimal(row["amount"]) if _nonempty(row.get("amount")) else _multiply(
                    normalize_decimal(row.get("quantity")), normalize_decimal(row.get("unit_price")))
                values.append(amount)
            except (ValidationError, DecimalException, KeyError):
                return None
        return _sum(values)

    related = []
    if kind in INVOICES:
        related = [T.PACKING_LIST, T.SALES_CONTRACT, T.PURCHASE_CONTRACT]
    elif kind is T.PACKING_LIST:
        related = [T.COMMERCIAL_INVOICE, T.COMBINED_INVOICE_PACKING_LIST, T.BILL_OF_LADING]
    saved = {}
    for other_kind in related:
        form = next((form for form in workspace.document_forms if form.document_type is other_kind), None)
        # Empty catalog slots and master fallback alone are not another document.
        if form and (any(_nonempty(value) for value in form.values.values()) or bool(form.rows)):
            saved[other_kind] = _view(workspace, other_kind)
    for other_kind, other in saved.items():
        if other_kind not in SELLER_TYPES:
            continue  # External annotations do not substitute for issuer evidence.
        source_checks(other)
        for field in ("order_no", "buyer", "destination_country"):
            if _nonempty(selected["values"].get(field)) or _nonempty(other["values"].get(field)):
                cmp(selected, other, field, selected["values"].get(field), other["values"].get(field))
        if other_kind is T.SALES_CONTRACT:
            for field in ("currency", "incoterm"):
                cmp(selected, other, field, selected["values"].get(field), other["values"].get(field),
                    category="currency" if field == "currency" else "text")
            cmp(selected, other, "total_amount", total(selected), total(other), category="money",
                lu=selected["values"].get("currency"), ru=other["values"].get("currency"))
        other_index = index_rows(other)
        if not selected_index or not other_index:
            continue
        add("CROSS_ROW_SET", state(set(selected_index) == set(other_index), invalid=True),
            "相关文件必须逐产品对应，不能用相同合计掩盖不同明细。", left=selected, right=other,
            field="product_code", lv=sorted(selected_index), rv=sorted(other_index), scope="cross_document")
        for code in sorted(set(selected_index) & set(other_index)):
            li, lrow = selected_index[code]
            ri, rrow = other_index[code]
            for field in ("description", "quantity", "packages", "gross_weight_kg", "net_weight_kg"):
                if field not in ROW_FIELDS[kind] or field not in ROW_FIELDS[other_kind]:
                    continue
                if field in {"packages", "gross_weight_kg", "net_weight_kg"} and not (
                        _nonempty(lrow.get(field)) or _nonempty(rrow.get(field))):
                    continue
                category = "weight" if field.endswith("_kg") else "quantity" if field in {"quantity", "packages"} else "text"
                lu = "kg" if category == "weight" else "box" if field == "packages" else lrow.get("unit")
                ru = "kg" if category == "weight" else "box" if field == "packages" else rrow.get("unit")
                cmp(selected, other, field, lrow.get(field), rrow.get(field), category=category,
                    row=li, right_row=ri, lu=lu, ru=ru)

    # Compare relevant supplied raw counterparts only where no reviewed seller
    # form represents them. Current source originals of the selected form itself
    # are checked above, not blanket-promoted into a second unreviewed form.
    for document in workspace.documents:
        other_kind = document.document_type
        if (document.deleted_at is not None or other_kind not in related
                or other_kind in saved and other_kind in SELLER_TYPES):
            continue
        try:
            version = get_current_version(workspace.case, document)
            if not re.fullmatch(r"[0-9a-f]{64}", version.sha256):
                raise ValidationError("相关原件哈希元数据无效。")
        except ValidationError as exc:
            add("RELATED_SOURCE_CURRENT", S.PENDING_REVIEW, str(exc), lv=document.document_id, scope="cross_document")
            continue
        def raw_field(name):
            fields = [field for field in version.fields if field.name == name]
            if len(fields) != 1:
                return None, False, "missing/ambiguous"
            field = fields[0]
            confirmed = bool(field.status is F.CONFIRMED and field.confirmed_by and field.confirmed_at)
            value = field.confirmed_value if confirmed else field.value
            location = f"{version.original_filename} v{version.version_number};page:{field.page_number};sheet:{field.sheet_name};cell:{field.cell_reference}"
            return value, confirmed, location
        from documents.prefill import row_candidates
        source_rows = row_candidates(version, document.document_id,
            {"product_code", "description", "quantity", "unit", "unit_price", "packages",
             "gross_weight_kg", "net_weight_kg"})
        fields = ("buyer", "currency", "incoterm", "total_amount") if other_kind in {T.SALES_CONTRACT, T.PURCHASE_CONTRACT} else (
            "shipper", "consignee", "packages", "gross_weight_kg", "net_weight_kg") if other_kind is T.BILL_OF_LADING else ("quantity", "packages", "gross_weight_kg", "net_weight_kg")
        for field in fields:
            # Exact contract mapping; customer_name is an explicit core source
            # synonym for buyer, not a seller/shipper or buyer/consignee alias.
            raw_name = "customer_name" if field == "buyer" and not any(f.name == "buyer" for f in version.fields) else field
            rv, reviewed, location = raw_field(raw_name)
            row = None
            lu = ru = None
            category = "text"
            if field in {"quantity", "packages", "gross_weight_kg", "net_weight_kg"}:
                fixed_single = (version.parser_name or "").startswith("fixed-template")
                explicit_single = (len(source_rows) == 1
                    and source_rows[0].values.get("product_code") == selected["rows"][0].get("product_code")) if len(selected["rows"]) == 1 else False
                if len(selected["rows"]) != 1 or not (explicit_single or fixed_single and not source_rows):
                    add("RAW_ROW_PAIRING", S.PENDING_REVIEW, "原件未提供可唯一对应的逐产品行，不能仅比较合计。",
                        left=selected, field=field, rv=rv, raw=(document.document_id, version.version_id, location), scope="cross_document")
                    continue
                row = 1
                lv = selected["rows"][0].get(field)
                code, code_confirmed, _ = raw_field("product_code")
                reviewed = reviewed and code_confirmed and code == selected["rows"][0].get("product_code")
                category = "weight" if field.endswith("_kg") else "quantity"
                lu = ru = "kg" if category == "weight" else "box" if field == "packages" else None
                if field == "quantity":
                    lu = selected["rows"][0].get("unit")
                    ru, unit_confirmed, _ = raw_field("unit")
                    reviewed = reviewed and unit_confirmed
            else:
                lv = total(selected) if field == "total_amount" else selected["values"].get(field)
                category = "money" if field == "total_amount" else "currency" if field == "currency" else "text"
                if category == "money":
                    lu = selected["values"].get("currency")
                    ru, currency_confirmed, _ = raw_field("currency")
                    reviewed = reviewed and currency_confirmed
            # Only applicable schema fields are compared; optional absent weights
            # on both sides do not become invented mandatory CI fields.
            if field not in FORM_FIELDS[kind] and field not in ROW_FIELDS[kind] and field != "total_amount":
                continue
            if not _nonempty(lv) and not _nonempty(rv) and field in {"packages", "gross_weight_kg", "net_weight_kg"}:
                continue
            cmp(selected, None, field, lv, rv, category=category, row=row, lu=lu, ru=ru,
                reviewed=reviewed, raw=(document.document_id, version.version_id, location), code="RAW_CROSS_DOCUMENT",
                raw_field_name=raw_name)

    # Whole-case inventory remains visible without blocking a first standalone
    # seller file. The selected completed form counts as available in this view.
    present = {doc.document_type for doc in workspace.documents
               if doc.deleted_at is None and doc.current_version_id}
    from review.policy import current_artifacts
    present.update(artifact.document_type for artifact in current_artifacts(workspace))
    present.add(kind)
    for required_kind in workspace.required_document_types:
        add("CASE_REQUIRED_DOCUMENT", state(required_kind in present),
            f"整单所需文件：{required_kind.value}", lv=required_kind.value,
            required=False, scope="case")
    return report
