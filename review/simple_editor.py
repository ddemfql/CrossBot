"""Compact, typed issue correction in the existing review area."""
from copy import deepcopy
from dataclasses import fields
from hashlib import sha256
import json
import re

from core.errors import CrossBotError
from core.models import ReviewEditArea as A, TradeLine, to_dict
from documents.catalog import DOC_LABELS, FORM_FIELDS, ROW_FIELDS
from documents.forms import get_form, input_values, input_rows, input_form_row_fields


def target_label(ws, target):
    kind = target.document_type
    field_labels = {**FORM_FIELDS.get(kind, {}), **ROW_FIELDS.get(kind, {}),
        "rows": "产品/业务明细", "line_items": "订单产品明细", "adjustments": "费用与调整",
        "packages": "逐包内容", "invoice_adjustments_confirmed": "费用及调整确认"}
    from documents.simple_export import TRADE_EXTRA_FIELDS
    field_labels.update(TRADE_EXTRA_FIELDS)
    if target.row_number is not None and target.field_name == "packages":
        field_labels["packages"] = "箱数"
    if target.area is A.SOURCE:
        doc = next((d for d in ws.documents if d.document_id == target.document_id), None)
        place = "外部原件 · " + (doc.title if doc else "待补来源")
    elif target.area is A.TRADE:
        place = "订单资料"
    elif target.area is A.MANUAL_SOURCE:
        record = next((r for r in ws.manual_data_records if r.record_id == target.record_id), None)
        place = "人工资料 · " + (record.title if record else DOC_LABELS.get(kind, "来源资料"))
    elif target.area is A.SOURCE_LINKS:
        place = "来源及版式设置"
    else:
        place = DOC_LABELS.get(kind, "当前文件")
    row = (f" · 明细第 {target.row_number} 行" if target.row_number is not None else "")
    return place + row + " · " + field_labels.get(target.field_name, target.field_name or "核对资料")


def _save(st, action):
    try:
        action()
        st.session_state.w3_notice = "修改已保存，已按最新资料重新审核；请重新核对本次生成内容。"
        st.rerun()
    except CrossBotError as exc:
        st.error(exc.message)
    except (ValueError, TypeError) as exc:
        st.error(str(exc))


def _source_editor(st, ws, actor, kind, target, repository, digest, key):
    from review.quality_inputs import (pending_source_fields, confirm_source_fields,
        pending_source_rows, confirm_source_rows)
    from core.models import SourceFieldSelection, SourceRowSelection
    from documents.drafts import DraftStore
    import pandas as pd
    store = DraftStore(st.session_state)
    target_id = kind.value + ":" + target.document_id + ":" + target.version_id
    st.caption("修改的是核对记录，原件及机器采集值保留。")
    if target.row_number is None and target.field_name != "rows":
        matching = [f for f in pending_source_fields(ws, kind) if f.document_id == target.document_id
            and f.version_id == target.version_id and f.field_name == target.field_name]
        if len(matching) == 1:
            scope = (ws.case.case_id, actor.user_id + ":source-field", target_id + ":" + target.field_name)
            initial = {"value": matching[0].value}
            draft = store.load(*scope, ws.revision, initial, concurrency_token=digest)
            if draft.stale:
                st.warning("原件核对依据已有新版本，暂存修改已保留，请重新载入后保存。")
            value_key = store.widget_key(*scope, "value")
            st.text_input("原件核对值", draft.values["value"], key=value_key,
                on_change=store.capture_widget, args=(*scope, value_key, "value"))
            fingerprint = sha256(draft.values["value"].encode()).hexdigest()[:12]
            ack = st.checkbox("我已对照原件核实此修改", key=key + "-ack-" + fingerprint)
            save = st.button("保存原件核对值并重新审核", key=key + "-save", disabled=draft.stale)
            if draft.dirty and st.button("放弃原件字段暂存，载入最新内容", key=key + "-discard"):
                store.discard(*scope, ws.revision, initial, concurrency_token=digest)
                st.rerun()
            if save:
                def save_field():
                    result = confirm_source_fields(repository, ws.case.case_id, actor, kind,
                        [SourceFieldSelection(target.document_id, target.version_id, target.field_name, draft.values["value"])],
                        expected_review_digest=digest, human_confirmed=ack)
                    store.clear(*scope)
                    return result
                _save(st, save_field)
            return
    source = [r for r in pending_source_rows(ws, kind) if r.document_id == target.document_id
        and r.version_id == target.version_id]
    if source:
        st.caption("已定位此原件；请核对整份产品明细后保存，不能遗漏行。" +
            (f" 本次问题位于工作表 {target.table_index + 1}、原行 {target.row_number}。"
                if target.table_index is not None and target.row_number is not None else ""))
        columns = list(dict.fromkeys(k for r in source for k in r.values))
        data = [{"工作表": r.table_index + 1, "原行": r.row_number, **r.values} for r in source]
        scope = (ws.case.case_id, actor.user_id + ":source-rows", target_id)
        initial = {"rows": data}
        draft = store.load(*scope, ws.revision, initial, concurrency_token=digest)
        if draft.stale:
            st.warning("原件明细依据已有新版本，暂存内容已保留；请重新载入后保存。")
        rows_key = store.widget_key(*scope, "rows")
        displayed = draft.values["rows"]
        st.data_editor(pd.DataFrame(displayed).fillna(""), hide_index=True,
            disabled=["工作表", "原行"], column_config={k: st.column_config.TextColumn(
                ROW_FIELDS.get(kind, {}).get(k, k)) for k in columns}, use_container_width=True,
            key=rows_key, on_change=store.capture_editor, args=(*scope, rows_key, "rows", displayed))
        fingerprint = sha256(json.dumps(displayed, sort_keys=True).encode()).hexdigest()[:12]
        ack = st.checkbox("我已对照原件核实此修改及全部产品行", key=key + "-ack-" + fingerprint)
        save = st.button("保存原件明细并重新审核", key=key + "-save", disabled=draft.stale)
        if draft.dirty and st.button("放弃原件明细暂存，载入最新内容", key=key + "-discard"):
            store.discard(*scope, ws.revision, initial, concurrency_token=digest)
            st.rerun()
        if save:
            selections = [SourceRowSelection(target.document_id, target.version_id,
                int(row["工作表"]) - 1, int(row["原行"]), {k: str(row.get(k, "")) for k in columns}) for row in displayed]
            def save_rows():
                result = confirm_source_rows(repository, ws.case.case_id, actor, kind, selections,
                    expected_review_digest=digest, human_confirmed=ack)
                store.clear(*scope)
                return result
            _save(st, save_rows)
    else:
        st.info("此来源当前无法直接核对，请在下方展开的来源处理区重新采集或更换原件。")


def render_issue_editor(st, ws, actor, kind, checks, repository, cases, digest, message):
    """Display one correction at a time; return selected location for related panels."""
    import pandas as pd
    options = [i for i, check in enumerate(checks) if check.edit_targets]
    if not options:
        return None
    chosen = st.selectbox("选择要处理的审核问题", [None, *options],
        format_func=lambda i: "选择一个问题，直接补充或修改" if i is None else
            f"{i + 1}. {target_label(ws, checks[i].edit_targets[0])} · {message(checks[i], kind)}", key=f"review-problem-{ws.case.case_id}-{kind.value}-{digest}")
    if chosen is None:
        return None
    check = checks[chosen]
    targets = []
    for target in check.edit_targets:
        if target not in targets:
            targets.append(target)
    index = st.selectbox("要修改的位置", range(len(targets)),
        format_func=lambda i: target_label(ws, targets[i]),
        key=f"review-location-{ws.case.case_id}-{kind.value}-{digest}-{chosen}")
    target = targets[index]
    return render_target_editor(st, ws, actor, kind, target, repository, cases, digest)


def render_target_editor(st, ws, actor, kind, target, repository, cases, digest,
                         save_action=None, key_prefix="review-correction",
                         save_label="保存修改并重新审核", value_label="修改后的内容",
                         reason_default="核对审核问题后补充或修订资料"):
    """The same focused input controls serve review corrections and direct intake."""
    import pandas as pd
    identity = sha256(json.dumps(to_dict(target), sort_keys=True).encode()).hexdigest()[:12]
    key = f"{key_prefix}-{ws.case.case_id}-{kind.value}-{digest}-{identity}"
    st.caption("已定位：" + target_label(ws, target))
    if target.area is A.SOURCE:
        _source_editor(st, ws, actor, kind, target, repository, digest, key)
        return target
    if target.area is A.SOURCE_LINKS:
        if target.field_name == "source_form_references":
            from documents.manual_inputs import ManualInputService, manual_source
            source = manual_source(ws, target.document_type, target.record_id)
            if source is not None:
                st.caption("将重新读取这份人工资料的当前修订；已有冲突或人工清空不会被覆盖。")
                if st.button("重新使用这份人工资料", key=key + "-apply"):
                    _save(st, lambda: ManualInputService(repository).apply(ws.case.case_id, actor,
                        target.document_type, kind, "在审核区重新读取当前人工资料",
                        source_record_id=target.record_id, expected_revision=ws.revision))
        st.info("已在下方展开来源处理入口，可重新填入当前来源或调整关联；修改后会重新审核。")
        return target
    form = get_form(ws, target.document_type or kind)
    if target.area is A.TRADE:
        values = to_dict(ws.trade)
        rows = to_dict(ws.trade.line_items)
        row_fields = {f.name: f.name for f in fields(TradeLine)}
        row_fields.pop("supplied_fields", None)
    elif target.area is A.MANUAL_SOURCE:
        from documents.manual_inputs import manual_source
        source = manual_source(ws, target.document_type, target.record_id)
        if source is None:
            st.warning("来源已变化，请重新选择审核问题。")
            return target
        values, rows = source.values, deepcopy(source.rows)
        row_fields = ROW_FIELDS[target.document_type]
    else:
        values, rows = input_values(ws.trade, form), input_rows(ws.trade, form)
        row_fields = input_form_row_fields(form)
    field = target.field_name
    path = re.fullmatch(r"(line_items|adjustments|packages)\[([1-9][0-9]*)\]\.([a-z_]+)", field)
    path_value, path_name = None, None
    if path and target.area is A.TRADE:
        group, number, path_name = path.groups()
        collection = getattr(ws.trade, group)
        if int(number) <= len(collection):
            path_value = getattr(collection[int(number) - 1], path_name, "")
    if field == "package_details":
        field = "packages"
    is_collection = field in {"rows", "line_items", "adjustments", "packages"} and target.row_number is None
    if field in {"adjustments", "packages"} and target.area is A.TRADE and target.row_number is None:
        rows = to_dict(getattr(ws.trade, field))
        from core.models import TradeAdjustment, TradePackage
        row_fields = {f.name: f.name for f in fields(TradeAdjustment if field == "adjustments" else TradePackage)}
    if field in row_fields and target.row_number is None and field not in FORM_FIELDS.get(target.document_type, {}):
        is_collection = True
    from documents.drafts import DraftStore
    store = DraftStore(st.session_state)
    editor_id, target_id = actor.user_id + ":" + key_prefix, kind.value + ":" + identity
    current = (rows[target.row_number - 1].get(field, "") if target.row_number is not None
        and 0 < target.row_number <= len(rows) else values.get(field, ""))
    if path:
        current = path_value
    boolean = field == "invoice_adjustments_confirmed" or path_name == "included_in_invoice_price"
    if boolean:
        current = bool(ws.trade.invoice_adjustments_confirmed if field == "invoice_adjustments_confirmed" else path_value)
    else:
        current = "" if current is None else str(current)
    initial_rows = [{k: bool(row.get(k, False)) if k == "included_in_invoice_price" else
        "" if row.get(k) is None else str(row.get(k, "")) for k in row_fields} for row in rows]
    initial = {"value": current, "rows": initial_rows, "reason": reason_default}
    draft = store.load(ws.case.case_id, editor_id, target_id, ws.revision, initial, concurrency_token=digest)
    scope = (ws.case.case_id, editor_id, target_id)
    if draft.stale:
        st.warning("这份暂存填写对应较早版本，已保留你的输入；请先查看最新资料并重新载入，再保存。")
    elif draft.dirty:
        st.caption("未保存的填写已暂存于当前会话，切换项目后可继续填写。")
    with st.container(border=True):
        if is_collection:
            labels = {key: label for catalog in ROW_FIELDS.values() for key, label in catalog.items()}
            labels.update({"kind": "费用类别", "name": "项目说明", "amount": "金额", "currency": "币种",
                "included_in_invoice_price": "已包含在货价内", "package_number": "包件编号",
                "contents_description": "包内内容", "hs_code": "HS/HTS 编码", "grade_quality": "品级/质量"})
            displayed_rows = draft.values["rows"]
            frame = pd.DataFrame(displayed_rows, columns=list(row_fields)).fillna("")
            config = {k: st.column_config.TextColumn(labels.get(k, k)) for k in row_fields}
            if field == "adjustments" and target.area is A.TRADE:
                from core.models import TradeAdjustmentKind
                config["kind"] = st.column_config.SelectboxColumn("费用类别", options=[v.value for v in TradeAdjustmentKind])
                config["included_in_invoice_price"] = st.column_config.CheckboxColumn("已包含在货价内", default=False)
            rows_key = store.widget_key(*scope, "rows")
            changed = st.data_editor(frame, num_rows="dynamic", hide_index=True,
                column_config=config, key=rows_key, use_container_width=True,
                on_change=store.capture_editor, args=(*scope, rows_key, "rows", displayed_rows))
        else:
            value_key = store.widget_key(*scope, "value")
            if boolean:
                st.checkbox("费用及调整项目已核实（无调整也需明确）" if field == "invoice_adjustments_confirmed"
                    else "已包含在货价内", value=draft.values["value"], key=value_key,
                    on_change=store.capture_widget, args=(*scope, value_key, "value"))
            else:
                st.text_input(value_label, draft.values["value"], key=value_key,
                    on_change=store.capture_widget, args=(*scope, value_key, "value"))
        reason_key = store.widget_key(*scope, "reason")
        st.text_input("修改说明", value=draft.values["reason"], key=reason_key,
            on_change=store.capture_widget, args=(*scope, reason_key, "reason"))
        save = st.button(save_label, key=key + "-save", disabled=draft.stale)
        if draft.dirty and st.button("放弃暂存修改，载入最新内容", key=key + "-discard"):
            store.discard(*scope, ws.revision, initial)
            st.rerun()
    if save:
        if is_collection:
            rendered_rows = [{name: bool(row.get(name, False)) if name == "included_in_invoice_price"
                else "" if pd.isna(row.get(name)) else str(row[name]) for name in row_fields}
                for _, row in changed.iterrows()]
            if rendered_rows != draft.values["rows"]:
                store.patch(*scope, {"rows": rendered_rows})
        def save_draft():
            active = store.load(*scope, ws.revision, initial, concurrency_token=digest)
            if active.stale:
                from core.errors import ValidationError
                raise ValidationError("资料已变化，请先重新载入最新内容。")
            value = active.values["value"]
            changed_rows = [row for row in active.values["rows"] if any(row.values())] if is_collection else None
            reason = active.values["reason"]
            if save_action is not None:
                result = save_action(value, changed_rows, reason)
            else:
                from review.editing import ReviewEditingService
                result = ReviewEditingService(repository, cases).save(ws.case.case_id, actor, kind, target,
                    value=value, rows=changed_rows, reason=reason, expected_revision=active.base_revision,
                    expected_review_digest=digest)
            store.clear(*scope)
            return result
        _save(st, save_draft)
    return target

