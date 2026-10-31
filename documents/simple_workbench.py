"""Compact file inventory, editable data and human-confirmed Excel export."""
from copy import deepcopy
from io import BytesIO
from pathlib import PurePath
from zipfile import ZipFile, ZIP_DEFLATED
from hashlib import sha256
import json
import re

from core.errors import CrossBotError
from core.models import CaseStatus, CrossCheckStatus, DocumentType as D, TradeLine
from documents.catalog import DOC_LABELS, SELLER_TYPES, FORM_FIELDS, ROW_FIELDS
from documents.forms import FormService, effective_values, get_form
from documents.intake import classify_source
from documents.workbench import reclassify, collect_document
from documents.checklist import requirement_groups


def _review_value(value):
    if value is None:
        return ""
    if isinstance(value, list):
        if any(isinstance(item, dict) for item in value):
            return f"{len(value)} 项，选择下方问题逐项处理"
        return "、".join(str(item) for item in value)[:200]
    if isinstance(value, dict):
        return "有关联明细，请选择下方问题处理"
    return str(value)[:200]


def _review_message(check, kind):
    labels = {**FORM_FIELDS[kind], **ROW_FIELDS[kind],
        **{item.value: label for item, label in DOC_LABELS.items()},
        "rows": "明细", "document_form": "表单", "source_document_ids": "来源文件",
        "customer_name": "客户", "product_code": "产品编码"}
    message = check.message
    for name in sorted(labels, key=len, reverse=True):
        message = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
                         lambda _: labels[name], message)
    return message


def _download(st, documents, ws, actor, *, document_id=None, version_id=None, artifact_id=None):
    key = (actor.user_id, ws.case.case_id, document_id, version_id, artifact_id)
    cache = st.session_state.setdefault("w3_download_cache", {})
    if key not in cache:
        cache[key] = documents.download(ws.case.case_id, actor, document_id=document_id,
            version_id=version_id, artifact_id=artifact_id)
    return cache[key]


def _source_preview(st, ws, doc, documents, actor, context):
    versions = list(reversed(doc.versions))
    selected = st.selectbox("文件版本", [v.version_id for v in versions],
        format_func=lambda key: next(f"v{v.version_number} · {v.original_filename}" for v in versions if v.version_id == key),
        key=f"simple-version-{context}-{doc.document_id}")
    version = next(v for v in versions if v.version_id == selected)
    try:
        content = _download(st, documents, ws, actor, document_id=doc.document_id, version_id=selected)
    except CrossBotError as exc:
        st.error("此原件暂不可读取：" + exc.message)
        st.caption("原件记录和历史仍保留，可上传修订版；其他资料及生成操作仍可使用。")
        return
    st.download_button("下载原件", content, version.original_filename, mime=version.media_type,
        key=f"simple-source-{context}-{selected}")
    if version.media_type.startswith("image/"):
        st.image(content, caption=version.original_filename)
    if version.parse_error:
        st.warning(version.parse_error)
    for warning in version.extraction_warnings:
        st.caption(warning)
    if version.original_path:
        st.caption(version.original_path)
    if version.source_text:
        st.text_area("文件原文", version.source_text, disabled=True, key=f"simple-text-{context}-{selected}")
    if version.source_tables:
        import pandas as pd
        index = st.selectbox("工作表", range(len(version.source_tables)),
            format_func=lambda n: version.source_tables[n].name or f"表 {n + 1}",
            key=f"simple-sheet-{context}-{selected}")
        st.dataframe(pd.DataFrame(version.source_tables[index].rows), use_container_width=True)
    st.caption("原始文件和历次版本均保留。")


def file_inventory(workspace):
    """Show only required or present categories, never empty template slots."""
    source_types, delivery_types = requirement_groups(workspace)
    kinds = list(dict.fromkeys([*workspace.required_document_types, *source_types, *delivery_types,
        *(d.document_type for d in workspace.documents if d.deleted_at is None),
        *(a.document_type for a in workspace.artifacts),
        *(f.document_type for f in workspace.document_forms if f.manually_provided)]))
    from review.policy import current_artifacts
    current_ids = {item.artifact_id for item in current_artifacts(workspace)}
    rows = []
    for kind in kinds:
        originals = [d for d in workspace.documents if d.deleted_at is None
                     and d.current_version_id and d.document_type is kind]
        generated = [a for a in workspace.artifacts if a.document_type is kind and a.artifact_id in current_ids]
        historical = [a for a in workspace.artifacts if a.document_type is kind and a.artifact_id not in current_ids]
        form = get_form(workspace, kind)
        from .manual_inputs import has_manual_data
        manual = has_manual_data(form) or any(has_manual_data(record)
            for record in workspace.manual_data_records if record.document_type is kind)
        has_data = bool(form.rows or any(value for name, value in
                        effective_values(workspace.trade, form).items() if name != "currency")
                        or workspace.trade.line_items)
        rows.append({"文件分类": DOC_LABELS[kind], "需要": kind in workspace.required_document_types,
            "原件": len(originals), "生成文件": len(generated), "历史生成文件": len(historical),
            "需要提供资料": kind in source_types, "需要制作交付": kind in delivery_types,
            "手动填写": manual,
            "资料情况": "已上传" if originals else "已填写资料（非原件）" if manual else "待上传或填写",
            "交付情况": "可交付（当前已核对）" if generated else "需重新核对/生成（有历史文件）" if historical else "已有上传文件，待核对" if originals else
                "待生成" if has_data else "待填写并生成",
            "情况": ("已有原件" if originals else "已生成" if generated else
                ("待生成" if has_data else "待填写") if kind in SELLER_TYPES else "待获取")})
    return rows


def provided_inventory(workspace):
    """Include every retained source and every direct-data source, not only required types."""
    from .manual_inputs import has_manual_data
    from .requirements import delivery_requirements
    rows = []
    used = {kind: {o.document_id for item in items if (o := item.origin) and o.document_id}
            for kind, items in delivery_requirements(workspace).items()}
    for item in source_inventory(workspace):
        links = [link for link in workspace.source_requirement_links if link.document_id == item["文件编号"]]
        targets = {k for k, ids in used.items() if item["文件编号"] in ids} | {link.document_type for link in links}
        rows.append({"资料": item["文件名"], "分类": item["分类"], "方式": "上传原件",
            "状态": "已留存，内容需核对", "用于生成": "、".join(DOC_LABELS[k]
                for k in sorted(targets, key=lambda k: k.value)) or "留存/待指定用途",
            "关联资料项": "、".join((FORM_FIELDS[link.document_type].get(link.field_name)
                or ROW_FIELDS[link.document_type].get(link.field_name) or link.field_name)
                + (f"（第{link.row_number}行）" if link.row_number else "") for link in links),
            "位置或版本": item["原始路径"] + " · " + item["当前版本"]})
    for record in workspace.manual_data_records:
        rows.append({"资料": record.title or DOC_LABELS[record.document_type],
            "分类": DOC_LABELS[record.document_type], "方式": "人工填写",
            "状态": "已有业务数据" if has_manual_data(record) else "待补充",
            "用于生成": "、".join(DOC_LABELS[f.document_type] for f in workspace.document_forms
                if any(ref.record_id == record.record_id for ref in f.source_form_references)) or "待指定用途",
            "位置或版本": f"{record.record_id} · 修订 {record.revision}"})
    for form in workspace.document_forms:
        if form.manually_provided:
            rows.append({"资料": DOC_LABELS[form.document_type], "分类": DOC_LABELS[form.document_type],
                "方式": "人工填写", "状态": "已有业务数据" if has_manual_data(form) else "待补充",
                "用于生成": "、".join(DOC_LABELS[f.document_type] for f in workspace.document_forms
                    if any(ref.document_type is form.document_type and not ref.record_id
                           for ref in f.source_form_references)) or "待指定用途",
                "位置或版本": f"修订 {form.revision}"})
    if any(str(getattr(workspace.trade, k, "") or "").strip() for k in
           ("order_no", "buyer", "seller")) or workspace.trade.line_items or workspace.trade.supplied_fields \
           or workspace.trade.adjustments or workspace.trade.packages:
        rows.append({"资料": "订单基础资料及明细", "分类": "业务数据", "方式": "订单填写",
            "状态": "已有填写，按生成需求核对", "用于生成": "适用的交付文件",
            "位置或版本": f"订单修订 {workspace.trade.revision}"})
    mapped = delivery_requirements(workspace)
    for form in workspace.document_forms:
        if (form.document_type not in mapped and not form.manually_provided
                and (any(str(v).strip() for v in form.values.values()) or form.rows)):
            rows.append({"资料": DOC_LABELS[form.document_type] + "已有填写", "分类": DOC_LABELS[form.document_type],
                "方式": "表单填写/采集", "状态": "已保存，未设为交付目标", "用于生成": "待指定用途",
                "位置或版本": f"表单修订 {form.revision}"})
    for kind, items in mapped.items():
        if any(item.value.strip() for item in items):
            rows.append({"资料": DOC_LABELS[kind] + "制作资料", "分类": DOC_LABELS[kind],
                "方式": "直接填表/来源预填", "状态": "待补" if any(i.required and i.status != "provided"
                    for i in items) else "数据已齐备，待核对", "用于生成": DOC_LABELS[kind],
                "位置或版本": f"表单修订 {get_form(workspace, kind).revision}"})
    for template in workspace.document_templates:
        selected = workspace.selected_template_ids.get(template.document_type.value) == template.template_id
        rows.append({"资料": template.filename, "分类": "导出模板", "方式": "模板上传",
            "状态": "当前选用" if selected else "已留存版本", "用于生成": DOC_LABELS[template.document_type],
            "位置或版本": f"v{template.version_number} · {template.sha256[:12]}"})
    return rows


def source_inventory(workspace, kind=None):
    """One visible row per active document, never collapse files by category/name."""
    rows = []
    for doc in workspace.documents:
        if doc.deleted_at is not None or (kind is not None and doc.document_type is not kind):
            continue
        current = next((v for v in doc.versions if v.version_id == doc.current_version_id), None)
        if current is None:
            continue
        rows.append({"文件名": current.original_filename or doc.title,
            "分类": DOC_LABELS[doc.document_type], "原始路径": current.original_path or "单独上传",
            "当前版本": f"v{current.version_number}", "保存版本数": len(doc.versions),
            "文件编号": doc.document_id})
    return rows


def selected_bundle(workspace, service, actor, document_ids, artifact_ids):
    """Package only selected current originals and selected artifact versions."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        manifest = []
        for doc in workspace.documents:
            if doc.document_id not in document_ids or doc.deleted_at is not None:
                continue
            version = next(v for v in doc.versions if v.version_id == doc.current_version_id)
            name = PurePath(version.original_filename.replace("\\", "/")).name
            archive.writestr(f"原件/{DOC_LABELS[doc.document_type]}/{doc.document_id}/{name}",
                service.download(workspace.case.case_id, actor, document_id=doc.document_id))
            manifest.append({"文件": name, "类型": "原件", "版本": version.version_number,
                "sha256": version.sha256, "状态": "当前上传版本，签发/内容需按记录核对"})
        for item in workspace.artifacts:
            if item.artifact_id in artifact_ids:
                archive.writestr(f"生成文件/{item.artifact_id}/{PurePath(item.filename).name}",
                    service.download(workspace.case.case_id, actor, artifact_id=item.artifact_id))
                from review.policy import current_artifacts
                current = item.artifact_id in {a.artifact_id for a in current_artifacts(workspace)}
                manifest.append({"文件": item.filename, "类型": "生成文件", "版本": item.version_number,
                    "状态": "当前已核对版本" if current else "历史/需重新核对，勿作为当前交付",
                    "规则版本": item.rule_version, "sha256": item.sha256})
        archive.writestr("导出清单.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output.getvalue()


def _run(st, action, message):
    try:
        action()
        st.session_state.w3_notice = message
        st.rerun()
    except CrossBotError as exc:
        st.error(exc.message)
    except (ValueError, TypeError) as exc:
        st.error(str(exc))


def _edit_trade(st, ws, actor, cases):
    import pandas as pd
    with st.expander("编辑订单资料"):
        from .drafts import DraftStore
        from core.models import to_dict
        from .provenance import _trade_origin
        from validation.export_review import review_digest
        initial = to_dict(ws.trade)
        for name in ("currency",):
            if _trade_origin(ws, name, str(initial[name])).origin_kind == "default":
                initial[name] = ""
        for index, line in enumerate(initial["line_items"], 1):
            line["_line_id"] = str(index - 1)
            for name in ("unit", "packages", "gross_weight_kg", "net_weight_kg"):
                if _trade_origin(ws, name, str(line[name]), index).origin_kind == "default":
                    line[name] = ""
                else:
                    line[name] = str(line[name])
        store = DraftStore(st.session_state)
        scope = (ws.case.case_id, actor.user_id + ":trade", "all")
        digest = review_digest(ws, D.COMMERCIAL_INVOICE)
        draft = store.load(*scope, ws.revision, initial, concurrency_token=digest)
        if draft.stale:
            st.warning("订单已有较新版本，暂存内容已保留；请重新载入后再保存。")
        elif draft.dirty:
            st.caption("订单未保存修改已暂存，切换页面后可继续填写。")
        with st.container():
            edited = deepcopy(ws.trade)
            cols = st.columns(2)
            fields = {"order_no": "订单号", "buyer": "客户", "seller": "卖方",
                "destination_country": "目的国家/地区", "currency": "币种", "incoterm": "贸易术语",
                "payment_terms": "付款条件", "transport_mode": "运输方式",
                "product_category": "产品类别", "incoterm_version": "贸易术语版本",
                "transaction_date": "交易日期", "transaction_place": "交易地点",
                "port_of_entry": "进口口岸", "seller_address": "卖方地址", "buyer_address": "买方地址",
                "responsible_exporter_person": "出口方知情负责人", "expected_ship_date": "预计出运日期",
                "actual_ship_date": "实际出运日期（人工记录）", "due_date": "付款到期日期"}
            for index, (key, label) in enumerate(fields.items()):
                widget_key = store.widget_key(*scope, key)
                setattr(edited, key, cols[index % 2].text_input(label, draft.values.get(key) or "", key=widget_key,
                    on_change=store.capture_widget, args=(*scope, widget_key, key)))
            edited.supplied_fields = list(dict.fromkeys([*ws.trade.supplied_fields,
                *[name for name in fields if str(getattr(edited, name) or "").strip()]]))
            row_fields = {"product_code": "产品编码", "description": "品名", "quantity": "数量",
                "unit_price": "单价", "unit": "单位", "packages": "箱数",
                "gross_weight_kg": "毛重 kg", "net_weight_kg": "净重 kg",
                "country_of_origin": "原产国", "hs_code": "HS/HTS 编码", "grade_quality": "品级/质量",
                "marks_and_numbers": "唛头/件号", "package_contents": "包装内容"}
            shown_lines = draft.values["line_items"]
            data = pd.DataFrame(shown_lines, columns=[*row_fields, "_line_id"])
            line_key = store.widget_key(*scope, "line_items")
            updated = st.data_editor(data, num_rows="dynamic", hide_index=True,
                column_config={**{k: st.column_config.TextColumn(v) for k, v in row_fields.items()},
                               "_line_id": None}, disabled=["_line_id"], key=line_key,
                on_change=store.capture_editor, args=(*scope, line_key, "line_items", shown_lines),
                use_container_width=True)
            st.caption("费用与逐包内容仅填写已知事实；留空表示未提供，不代表金额或重量为零。")
            adjustment_fields = {"kind": "费用类别", "name": "说明", "amount": "金额", "currency": "币种",
                "included_in_invoice_price": "已包含在货价内"}
            adjustment_rows = draft.values["adjustments"]
            adjustment_key = store.widget_key(*scope, "adjustments")
            adjustments = st.data_editor(pd.DataFrame(adjustment_rows, columns=list(adjustment_fields)),
                num_rows="dynamic", hide_index=True, key=adjustment_key,
                on_change=store.capture_editor, args=(*scope, adjustment_key, "adjustments", adjustment_rows),
                column_config={"kind": st.column_config.SelectboxColumn("费用类别", options=[
                    "freight", "insurance", "commission", "packing", "discount", "rebate", "drawback", "bounty", "assist", "other"]),
                    "included_in_invoice_price": st.column_config.CheckboxColumn("已包含在货价内")},
                use_container_width=True)
            from core.models import TradePackage
            package_fields = list(TradePackage.__dataclass_fields__)
            shown_packages = draft.values["packages"]
            package_key = store.widget_key(*scope, "packages")
            packages = st.data_editor(pd.DataFrame(shown_packages, columns=package_fields), num_rows="dynamic", hide_index=True,
                key=package_key, on_change=store.capture_editor,
                args=(*scope, package_key, "packages", shown_packages), use_container_width=True)
            flag_key = store.widget_key(*scope, "invoice_adjustments_confirmed")
            edited.invoice_adjustments_confirmed = st.checkbox("费用及调整项目已核实（无调整也需明确）",
                value=draft.values["invoice_adjustments_confirmed"], key=flag_key,
                on_change=store.capture_widget, args=(*scope, flag_key, "invoice_adjustments_confirmed"))
            submitted = st.button("保存订单资料", disabled=draft.stale)
            if draft.dirty and st.button("放弃订单暂存，载入最新内容"):
                store.discard(*scope, ws.revision, initial, concurrency_token=digest)
                st.rerun()
        if submitted:
            try:
                rows = [{k: "" if pd.isna(row[k]) else str(row[k]) for k in [*row_fields, "_line_id"]}
                    for _, row in updated.iterrows()]
                lines = []
                for index, row in enumerate(rows):
                    line_id = row.pop("_line_id", "")
                    if not any(row.values()):
                        continue
                    # Keep non-visible fields of existing lines when editing simple columns.
                    line = (deepcopy(ws.trade.line_items[int(line_id)]) if line_id.isdigit()
                            and int(line_id) < len(ws.trade.line_items) else TradeLine("", "", "", ""))
                    for key, value in row.items():
                        if key == "packages":
                            setattr(line, key, int(value) if value else 0)
                        else:
                            setattr(line, key, value)
                    line.supplied_fields = list(dict.fromkeys([*[name for name in line.supplied_fields
                        if name not in row or str(row[name]).strip()], *[name for name, value in row.items() if str(value).strip()]]))
                    lines.append(line)
                edited.line_items = lines
                from core.models import TradeAdjustment, TradeAdjustmentKind
                edited.adjustments = []
                for _, row in adjustments.iterrows():
                    if pd.isna(row.get("name")) or not str(row.get("name", "")).strip():
                        continue
                    edited.adjustments.append(TradeAdjustment(
                        kind=TradeAdjustmentKind("other" if pd.isna(row.get("kind")) else str(row["kind"])),
                        name=str(row["name"]), amount="" if pd.isna(row.get("amount")) else str(row["amount"]),
                        currency="" if pd.isna(row.get("currency")) else str(row["currency"]),
                        included_in_invoice_price=False if pd.isna(row.get("included_in_invoice_price")) else bool(row["included_in_invoice_price"])))
                edited.packages = [TradePackage(**{k: "" if pd.isna(row[k]) else str(row[k]) for k in package_fields})
                    for _, row in packages.iterrows() if any(not pd.isna(v) and str(v).strip() for v in row)]
                def save_trade():
                    saved = cases.update_trade(ws.case.case_id, actor, edited,
                        "业务员修订订单资料", expected_revision=draft.base_revision)
                    store.clear(*scope)
                    return saved
                _run(st, save_trade, "订单资料已保存，导出时重新确认。")
            except ValueError:
                st.error("箱数请填写整数。")


def _manual_inputs(st, ws, actor, repository):
    import pandas as pd
    from .manual_inputs import ManualInputService, has_manual_data
    source_types, _ = requirement_groups(ws)
    with st.expander("手动填写资料（可代替上传）", expanded=True):
        choices = list(dict.fromkeys([*source_types, D.PURCHASE_ORDER, *DOC_LABELS]))
        kind = st.selectbox("手动提供的资料类型", choices, format_func=DOC_LABELS.get,
            key=f"manual-source-kind-{ws.case.case_id}")
        records = {r.record_id: r for r in ws.manual_data_records if r.document_type is kind}
        record_key = st.selectbox("填写记录", [None, "new", *records],
            format_func=lambda key: "本类已有填写" if key is None else "新增独立资料" if key == "new"
                else (records[key].title or DOC_LABELS[kind]) + f" · 修订 {records[key].revision}",
            key=f"manual-record-{ws.case.case_id}-{kind.value}")
        if record_key == "new":
            from core.models import DocumentForm
            form = DocumentForm(kind, revision=0)
        else:
            form = records[record_key] if record_key else get_form(ws, kind)
        st.caption("只保存你明确填写的内容，标注为人工提供；不会补造原件或自动确认。")
        if kind not in SELLER_TYPES and kind is not D.PURCHASE_ORDER:
            st.caption("这里记录已知业务资料；原件签发信息只有确知时填写。人工记录不等同机构签发的证书、提单或回执。")
        fields = FORM_FIELDS[kind]
        selected = st.multiselect("资料填写项", list(fields),
            default=list(dict.fromkeys([*(k for k, v in form.values.items() if v),
                *[k for k in ("order_no", "buyer", "seller", "currency", "payment_terms", "notes") if k in fields]])),
            format_func=fields.get, key=f"manual-source-fields-{ws.case.case_id}-{kind.value}-{record_key}-{form.revision}")
        from .drafts import DraftStore
        from validation.export_review import review_digest
        row_fields = ROW_FIELDS[kind]
        initial = {**{name: form.values.get(name, "") for name in fields},
            "title": getattr(form, "title", "") or DOC_LABELS[kind], "rows": form.rows}
        store = DraftStore(st.session_state)
        scope = (ws.case.case_id, actor.user_id + ":manual-source", kind.value + ":" + str(record_key))
        digest = review_digest(ws, kind)
        draft = store.load(*scope, ws.revision, initial, concurrency_token=digest)
        if draft.stale:
            st.warning("资料已有新版本，暂存填写已保留；请重新载入后再保存。")
        elif draft.dirty:
            st.caption("未保存的人工资料已暂存，切换记录或补充方式后仍可继续填写。")
        title_key = store.widget_key(*scope, "title")
        st.text_input("资料名称", draft.values["title"], key=title_key,
            on_change=store.capture_widget, args=(*scope, title_key, "title"))
        for name in selected:
            widget_key = store.widget_key(*scope, name)
            st.text_input(fields[name] + "（提供资料）", draft.values.get(name, ""), key=widget_key,
                on_change=store.capture_widget, args=(*scope, widget_key, name))
        row_key = store.widget_key(*scope, "rows")
        shown_rows = draft.values["rows"]
        st.data_editor(pd.DataFrame(shown_rows, columns=list(row_fields)).fillna(""), num_rows="dynamic", hide_index=True,
            column_config={k: st.column_config.TextColumn(v) for k, v in row_fields.items()},
            use_container_width=True, key=row_key, on_change=store.capture_editor,
            args=(*scope, row_key, "rows", shown_rows))
        save = st.button("保存手动提供的资料", disabled=draft.stale)
        if draft.dirty and st.button("放弃这份人工资料暂存，载入最新内容"):
            store.discard(*scope, ws.revision, initial, concurrency_token=digest)
            st.rerun()
        if save:
            active = store.load(*scope, ws.revision, initial, concurrency_token=digest)
            values = dict(form.values)
            values.update({name: active.values.get(name, "") for name in selected})
            data_rows = [{k: str(row.get(k, "") or "") for k in row_fields} for row in active.values["rows"]]
            data_rows = [row for row in data_rows if any(row.values())]
            service = ManualInputService(repository)
            def save_manual():
                result = (service.save_record(ws.case.case_id, actor, kind, values, data_rows,
                    "人工提供资料，代替上传作为数据输入", record_id=record_key if record_key != "new" else None,
                    title=active.values["title"], expected_revision=active.base_revision) if record_key else
                    service.save(ws.case.case_id, actor, kind, values, data_rows,
                        "人工提供资料，代替上传作为数据输入", expected_revision=active.base_revision))
                store.clear(*scope)
                return result
            _run(st, save_manual, "手动资料已保存；可在生成与导出中用于填表，仍须核对。")
        if has_manual_data(form):
            st.success("已手动提供此类资料；未上传原件。" if not any(
                d.deleted_at is None and d.current_version_id and d.document_type is kind for d in ws.documents)
                else "已保存人工提供的资料，上传原件另外保留。")


def _upload_files(st, ws, actor, documents, repository):
    case_id = ws.case.case_id
    with st.expander("上传文件", expanded=True):
        with st.form(f"simple-upload-{case_id}", clear_on_submit=True):
            uploaded = st.file_uploader("选择原始文件", type=["pdf", "xls", "xlsx", "docx", "jpg", "jpeg", "png", "webp", "txt", "csv"])
            kind = st.selectbox("文件分类", [None, *DOC_LABELS],
                format_func=lambda k: "按文件名自动分类（可修正）" if k is None else DOC_LABELS[k])
            available = [d for d in ws.documents if d.deleted_at is None]
            previous = st.selectbox("保存为", [None, *[d.document_id for d in available]],
                format_func=lambda key: "新文件" if key is None else next(d.title + " 的新版本" for d in available if d.document_id == key))
            collect_after = st.checkbox("保存后采集并填入对应表单", value=True)
            submit = st.form_submit_button("保存文件")
        if submit and uploaded:
            actual = kind or (next(d.document_type for d in available if d.document_id == previous)
                              if previous else classify_source(uploaded.name)[0])
            def save_source():
                saved = documents.upload(case_id, actor, uploaded.name, uploaded.getvalue(),
                    actual, document_id=previous)
                source_id = previous or saved.documents[-1].document_id
                if collect_after:
                    return collect_document(repository, documents.blobs, case_id, source_id,
                        actor, ocr_images=PurePath(uploaded.name).suffix.lower() in
                        {".jpg", ".jpeg", ".png", ".webp"})
                return saved
            _run(st, save_source, "原件已保存。采集内容可在对应表单查看；自动填写仍待人工核对。"
                 if collect_after else "原件已保存，请核对分类。")


def material_inventory(ws):
    """One daily list containing originals, independent manual inputs and file data."""
    from .manual_inputs import has_manual_data
    rows = []
    for item in source_inventory(ws):
        doc = next(d for d in ws.documents if d.document_id == item["文件编号"])
        rows.append({"key": doc.document_id, "kind": doc.document_type, "area": "original",
            "资料": item["文件名"], "分类": item["分类"], "提供方式": "上传原件",
            "状态": "原件已留存，内容按审核结果核对", "版本": item["当前版本"],
            **{name: item[name] for name in ("文件名", "原始路径", "当前版本", "保存版本数")}})
    for record in ws.manual_data_records:
        rows.append({"key": record.record_id, "kind": record.document_type, "area": "record",
            "资料": record.title or DOC_LABELS[record.document_type], "分类": DOC_LABELS[record.document_type],
            "提供方式": "手动填写", "状态": "已填写，待按用途核对" if has_manual_data(record) else "待补充",
            "版本": f"修订 {record.revision}"})
    for form in ws.document_forms:
        if not (form.rows or any(str(v).strip() for v in form.values.values())):
            continue
        rows.append({"key": "form-" + form.document_type.value, "kind": form.document_type, "area": "form",
            "资料": DOC_LABELS[form.document_type] + ("人工资料" if form.manually_provided else "制作数据" if form.document_type in SELLER_TYPES else "来源数据"),
            "分类": DOC_LABELS[form.document_type], "提供方式": "手动填写" if form.manually_provided else "表单填写/来源填入" if form.document_type in SELLER_TYPES else "原件采集/补充",
            "状态": "已保存，完整性与来源见审核", "版本": f"修订 {form.revision}"})
    return rows


def _open_manual_material(case_id, kind, record_id, manual, revision, field_name):
    import streamlit as st
    if manual:
        st.session_state[f"input-mode-{case_id}"] = "手动填写"
        st.session_state[f"manual-source-kind-{case_id}"] = kind
        st.session_state[f"manual-record-{case_id}-{kind.value}"] = record_id or None
    else:
        st.session_state[f"input-mode-{case_id}"] = "按缺项补充"
        st.session_state[f"targeted-method-{case_id}"] = "直接填写"
        st.session_state[f"targeted-input-{case_id}"] = (kind, field_name, None)


def _manual_material_details(st, ws, materials):
    import pandas as pd
    if not materials:
        return
    keys = [item["key"] for item in materials]
    picked = st.selectbox("查看手填或制作资料", keys,
        format_func=lambda key: next(item["资料"] + " · " + item["版本"] for item in materials if item["key"] == key),
        key=f"material-details-{ws.case.case_id}")
    item = next(item for item in materials if item["key"] == picked)
    source = (next(record for record in ws.manual_data_records if record.record_id == picked)
              if item["area"] == "record" else get_form(ws, item["kind"]))
    with st.expander("查看这份填写资料"):
        st.caption("填写数据单独保存；原件数量只统计实际上传的文件。")
        values = [{"资料项": FORM_FIELDS[item["kind"]].get(k, k), "内容": v}
                  for k, v in source.values.items() if str(v).strip()]
        if values:
            st.dataframe(values, hide_index=True, use_container_width=True)
        if source.rows:
            st.dataframe(pd.DataFrame(source.rows).rename(columns=ROW_FIELDS[item["kind"]]),
                hide_index=True, use_container_width=True)
        manual = item["area"] == "record" or getattr(source, "manually_provided", False) or item["kind"] not in SELLER_TYPES
        from .forms import input_form_fields
        field_name = next(iter(input_form_fields(get_form(ws, item["kind"]))), "document_no")
        st.button("继续填写这份资料", key=f"open-material-{picked}", on_click=_open_manual_material,
            args=(ws.case.case_id, item["kind"], picked if item["area"] == "record" else "", manual, ws.revision, field_name))


def _files(st, ws, actor, documents, repository, mapped=None):
    case_id = ws.case.case_id
    mode = st.radio("补充方式", ["上传文件", "手动填写", "按缺项补充"], horizontal=True,
        key=f"input-mode-{case_id}")
    if mode == "上传文件":
        _upload_files(st, ws, actor, documents, repository)
    elif mode == "手动填写":
        _manual_inputs(st, ws, actor, repository)
    else:
        _targeted_upload(st, ws, actor, repository, documents.blobs, mapped or {})
    available = [d for d in ws.documents if d.deleted_at is None and d.current_version_id]
    materials = material_inventory(ws)
    filter_kind = st.selectbox("按分类查看", [None, *dict.fromkeys(item["kind"] for item in materials)],
        format_func=lambda k: "全部文件" if k is None else DOC_LABELS[k])
    visible = [d for d in available if filter_kind is None or d.document_type is filter_kind]
    inventory = source_inventory(ws, filter_kind)
    shown_materials = [item for item in materials if filter_kind is None or item["kind"] is filter_kind]
    st.caption(f"本单共 {len(available)} 份原件，当前分类显示 {len(inventory)} 份；另列填写/制作数据，同一文件的历史版本单独统计。")
    if shown_materials:
        st.dataframe([{k: v for k, v in item.items() if k not in {"key", "kind", "area"}} for item in shown_materials],
            hide_index=True, use_container_width=True,
            column_config={name: None for name in ("文件名", "原始路径", "当前版本", "保存版本数")})
    _manual_material_details(st, ws, [item for item in shown_materials if item["area"] != "original"])
    if not visible:
        st.caption("当前分类没有上传原件；已有填写资料见上方列表。" if shown_materials else "当前分类暂无资料，可上传或直接填写。")
        return
    selected = st.selectbox("查看文件", [d.document_id for d in visible],
        format_func=lambda key: next(f"{d.title} · {DOC_LABELS[d.document_type]} · {i + 1}/{len(visible)}"
            for i, d in enumerate(visible) if d.document_id == key))
    doc = next(d for d in visible if d.document_id == selected)
    with st.expander("修正文件分类"):
        with st.form(f"simple-classify-{doc.document_id}"):
            kind = st.selectbox("修正分类", list(DOC_LABELS), index=list(DOC_LABELS).index(doc.document_type), format_func=DOC_LABELS.get)
            submit = st.form_submit_button("保存分类")
        if submit:
            _run(st, lambda: reclassify(repository, case_id, doc.document_id, actor, kind,
                "seller" if kind in SELLER_TYPES else "external", "人工核对文件分类"), "分类已保存。")
    with st.expander("查看选中原件"):
        _source_preview(st, ws, doc, documents, actor, "simple")
    with st.expander("采集并填入表单 / 移除文件"):
        targets = list(dict.fromkeys([doc.document_type, *SELLER_TYPES]))
        target = st.selectbox("采集后填入哪份表单", targets, format_func=DOC_LABELS.get,
            index=targets.index(doc.document_type if doc.document_type in SELLER_TYPES
                                else D.COMMERCIAL_INVOICE), key=f"simple-target-{doc.document_id}")
        st.caption("可以用客户订单或工厂资料填写我方文件；采集不会修改订单主数据或自动确认。")
        if st.button("采集并填入表单", key=f"simple-collect-{doc.document_id}"):
            version = next(v for v in doc.versions if v.version_id == doc.current_version_id)
            _run(st, lambda: collect_document(repository, documents.blobs, case_id, doc.document_id,
                actor, target_type=target, ocr_images=version.media_type.startswith("image/")),
                "内容已采集并尝试填入表单；人工修改保留，请到生成与导出查看。")
        confirmed = st.checkbox("确认移除当前文件，保留原件和历史", key=f"simple-remove-{doc.document_id}")
        if st.button("移除文件", disabled=not confirmed):
            _run(st, lambda: documents.delete_document(case_id, doc.document_id, actor,
                "业务员移除当前资料"), "文件已移除，历史保留。")


def _targeted_upload(st, ws, actor, repository, blobs, mapped):
    """Fill one precise input manually or associate its genuine source file."""
    from .forms import input_form_fields as form_fields, input_form_row_fields as form_row_fields, input_rows as effective_rows
    from core.models import DataRequirement
    slots = {(kind, item.field_name, item.row_number): item
        for kind, items in mapped.items() for item in items}
    for kind in mapped:
        form = get_form(ws, kind)
        for name, label in form_fields(form).items():
            slots.setdefault((kind, name, None), DataRequirement(kind, name, label, required=False))
        for index, row in enumerate(effective_rows(ws.trade, form), 1):
            for name, label in form_row_fields(form).items():
                slots.setdefault((kind, name, index), DataRequirement(kind, name, label, required=False,
                    row_number=index, value=row.get(name, "")))
    for kind in mapped:
        for canonical, alias in _identity_aliases(kind).items():
            if (kind, canonical, None) in slots:
                slots.pop((kind, alias, None), None)
    if not slots:
        return
    with st.expander("补充缺项或单个资料项", expanded=True):
        keys = sorted(slots, key=lambda key: (not (slots[key].required and slots[key].status != "provided"),
            key[0].value, key[2] or 0, key[1]))
        selector_key = f"targeted-input-{ws.case.case_id}"
        if st.session_state.get(selector_key) not in keys:
            st.session_state.pop(selector_key, None)
        selected = st.selectbox("本次补充哪一项", keys,
            format_func=lambda key: DOC_LABELS[key[0]] + " · " + slots[key].label
                + (f" · 明细第{key[2]}行" if key[2] else "")
                + ("（待补）" if slots[key].required and slots[key].status != "provided" else "（单项资料）"),
            key=selector_key)
        method = st.radio("此项资料的提供方式", ["直接填写", "上传原件"], horizontal=True,
            key=f"targeted-method-{ws.case.case_id}")
        if method == "直接填写":
            from .requirement_inputs import RequirementInputService
            from review.simple_editor import render_target_editor
            from validation.export_review import review_digest
            service = RequirementInputService(repository, blobs)
            try:
                target = service.manual_target(ws, selected[0], selected[1], selected[2])
            except CrossBotError as exc:
                st.info(exc.message)
                return
            st.caption("仅补充当前选中项，记录为人工填写；上传的原件、其他资料及历史保持保留，生成前仍需核对。")
            digest = review_digest(ws, selected[0])
            render_target_editor(st, ws, actor, selected[0], target, repository, None, digest,
                save_action=lambda value, rows, reason: service.fill(ws.case.case_id, actor,
                    selected[0], selected[1], selected[2], value=value, rows=rows, reason=reason,
                    expected_revision=ws.revision, expected_review_digest=digest), key_prefix="requirement-fill",
                save_label="保存此项资料", value_label="填写内容", reason_default="为选中资料项补充人工填写数据")
            return
        upload = st.file_uploader("为此资料项上传文件", type=["pdf", "xls", "xlsx", "docx", "jpg", "jpeg", "png", "webp", "txt", "csv"],
            key=f"targeted-file-{ws.case.case_id}")
        source_type = st.selectbox("此原件的文件分类", [None, *DOC_LABELS],
            format_func=lambda key: "按文件名自动分类" if key is None else DOC_LABELS[key],
            key=f"targeted-kind-{ws.case.case_id}")
        st.caption("文件会关联到此资料项；只尝试填入有明确依据的对应内容。无法识别时保留原件并继续提示待补，普通上传也可继续使用。")
        if st.button("上传并关联此资料项", disabled=upload is None):
            from .requirement_inputs import RequirementInputService
            _run(st, lambda: RequirementInputService(repository, blobs).upload(ws.case.case_id, actor,
                selected[0], selected[1], selected[2], upload.name, upload.getvalue(),
                source_type=source_type, expected_revision=ws.revision),
                "原件已保存并关联到所选资料项；请查看是否已采集到对应内容，仍须核对。")


def _file_options(st, ws, actor, repository):
    import pandas as pd
    from .forms import form_fields, form_row_fields, form_context_fields, _required_fields
    from .catalog import REQUIRED_ROW_FIELDS, ROW_REQUIRED_TYPES, DEFAULT_FIELDS, DEFAULT_ROW_FIELDS, DEFAULT_CONTEXT_FIELDS
    from .simple_export import TRADE_EXTRA_FIELDS
    sources, deliveries = requirement_groups(ws)
    choices = list(dict.fromkeys([*deliveries, *sources, *DOC_LABELS]))
    kind = st.selectbox("调整哪份文件", choices, format_func=DOC_LABELS.get,
        key=f"configure-file-{ws.case.case_id}")
    form = get_form(ws, kind)
    group = st.radio("资料分组", ["表头", "明细", "补充资料"], horizontal=True,
        key=f"configure-group-{ws.case.case_id}-{kind.value}")
    contexts = {**TRADE_EXTRA_FIELDS, "adjustments": "费用与调整", "packages": "逐包内容", "line_items": "订单补充明细"}
    fields, row_fields, context_fields = list(form_fields(form)), list(form_row_fields(form)), list(form_context_fields(form, ws))
    needs, row_needs, context_needs = list(form.required_input_fields), list(form.required_input_row_fields), list(form.required_input_context_fields)
    has_rows = kind in ROW_REQUIRED_TYPES or bool(row_fields) or bool(row_needs)
    required = (_required_fields(kind) if group == "表头" else
        (set(REQUIRED_ROW_FIELDS.get(kind, ())) | ({"product_code"} if "product_code" in ROW_FIELDS[kind] else set()))
        if group == "明细" and has_rows else set())
    labels, enabled, needed = ((FORM_FIELDS[kind], fields, needs) if group == "表头" else
        (ROW_FIELDS[kind], row_fields, row_needs) if group == "明细" else (contexts, context_fields, context_needs))
    st.caption("需要提供与导出显示分别设置。核心必需项及适用条件要求自动保留；不导出不会删除原资料。")
    source_only = kind not in SELLER_TYPES
    if source_only:
        st.caption("这是外部资料：调整录入与核对项，原件始终完整保存；不生成或裁剪机构原件。")
    frame = pd.DataFrame([{"字段": name, "资料项": label, "必需": name in required,
        "需要提供": name in required or name in needed, "导出显示": name in enabled}
        for name, label in labels.items()])
    with st.form(f"configure-fields-{ws.case.case_id}-{kind.value}-{group}-{form.revision}"):
        edited = st.data_editor(frame, hide_index=True, use_container_width=True,
            disabled=["字段", "资料项", "必需"] + (["导出显示"] if source_only else []),
            column_config={"字段": None}, key=f"field-options-{ws.case.case_id}-{kind.value}-{group}-{form.revision}")
        saved = st.form_submit_button("保存本文件资料项")
    if saved:
        selected = [str(row["字段"]) for _, row in edited.iterrows() if bool(row["导出显示"])]
        selected_needs = [str(row["字段"]) for _, row in edited.iterrows() if bool(row["需要提供"])]
        if group == "表头": fields, needs = selected, selected_needs
        elif group == "明细": row_fields, row_needs = selected, selected_needs
        else: context_fields, context_needs = selected, selected_needs
        _run(st, lambda: FormService(repository).configure_fields(ws.case.case_id, actor, kind,
            fields, row_fields, "调整本单文件资料项", expected_revision=ws.revision,
            context_fields=context_fields, required_input_fields=needs, required_input_row_fields=row_needs,
            required_input_context_fields=context_needs), "本文件设置已保存，仅影响本单。")
    if st.button("恢复本文件默认资料项"):
        _run(st, lambda: FormService(repository).configure_fields(ws.case.case_id, actor, kind,
            list(DEFAULT_FIELDS.get(kind, FORM_FIELDS[kind])), list(DEFAULT_ROW_FIELDS.get(kind, ROW_FIELDS[kind])),
            "恢复文件基础默认", expected_revision=ws.revision,
            context_fields=list(DEFAULT_CONTEXT_FIELDS.get(kind, ())), required_input_fields=[],
            required_input_row_fields=[], required_input_context_fields=[]), "已恢复此类文件的基础默认。")


def _template_options(st, ws, actor, repository, blobs):
    if "admin" not in actor.roles:
        return
    from .templates import TemplateService
    kind = st.selectbox("管理哪类文件的模板", SELLER_TYPES, format_func=DOC_LABELS.get)
    st.caption("模板上传和版本维护由管理员负责。业务员无需模板也能审核并生成默认 Excel/PDF。")
    upload = st.file_uploader("上传此文件的 Excel 模板", type=["xlsx"])
    if st.button("保存模板版本", disabled=upload is None):
        _run(st, lambda: TemplateService(repository, blobs).upload(ws.case.case_id, actor, kind,
            upload.name, upload.getvalue(), "管理员上传导出模板", expected_revision=ws.revision), "模板版本已保存。")
    templates = [t for t in ws.document_templates if t.document_type is kind]
    if templates:
        st.dataframe([{"模板": t.filename, "版本": t.version_number, "上传人": t.uploaded_by}
            for t in templates], hide_index=True, use_container_width=True)


def _case_settings(st, ws, actor, cases, repository, blobs, defaults_service=None, expanded=False):
    with st.expander("调整资料与交付清单", expanded=expanded):
        sections = ["文件清单", "文件资料项", "我的默认"] + (["模板管理"] if "admin" in actor.roles else [])
        section = st.radio("设置内容", sections, horizontal=True, key=f"case-setting-{ws.case.case_id}")
        if section == "文件资料项":
            _file_options(st, ws, actor, repository)
        elif section == "模板管理":
            _template_options(st, ws, actor, repository, blobs)
        elif section == "我的默认":
            if defaults_service is None:
                st.info("个人默认服务尚未连接。")
                return
            prefs = defaults_service.get(actor)
            st.caption(f"个人默认归属：{actor.user_id} · 版本 {prefs.revision if prefs else 0}。只保存文件和字段选择，不保存客户、金额等业务值。")
            st.caption(f"本单创建/最近应用的默认：{ws.defaults_user_id or '系统基础设置'} · 版本 {ws.defaults_revision}；本单后续调整单独保存。")
            if st.button("将本单配置保存为我的默认"):
                _run(st, lambda: defaults_service.save_from_case(ws.case.case_id, actor,
                    expected_revision=prefs.revision if prefs else 0), "已保存为你的个人默认，以后新建交易时采用；旧交易不变。")
            if st.button("应用我的默认到本单", disabled=prefs is None or prefs.revision == 0):
                _run(st, lambda: defaults_service.apply_to_case(ws.case.case_id, actor,
                    expected_workspace_revision=ws.revision), "已应用你的默认到本单，请重新核对受影响文件。")
        else:
            with st.form(f"simple-required-{ws.case.case_id}"):
                sources = st.multiselect("需要提供的资料（上传或手动填写）", list(DOC_LABELS),
                    default=ws.required_source_types if ws.required_source_types is not None else
                        [k for k in ws.base_required_document_types if k not in SELLER_TYPES], format_func=DOC_LABELS.get)
                deliveries = st.multiselect("需要制作并交付的文件", SELLER_TYPES,
                    default=ws.required_delivery_types if ws.required_delivery_types is not None else
                        [k for k in ws.base_required_document_types if k in SELLER_TYPES], format_func=DOC_LABELS.get)
                save = st.form_submit_button("保存两类清单")
            if save:
                _run(st, lambda: cases.set_document_checklist(ws.case.case_id, actor, sources, deliveries,
                    expected_revision=ws.revision), "本单清单已更新。")
            if st.button("恢复默认生成清单（发票和装箱单）"):
                _run(st, lambda: cases.set_document_checklist(ws.case.case_id, actor, sources,
                    [D.COMMERCIAL_INVOICE, D.PACKING_LIST], "恢复基础文件清单", expected_revision=ws.revision), "已恢复基础生成清单。")


def _latest_download(st, ws, actor, documents, kind, output_format, audience):
    from review.policy import current_artifacts
    current = {a.artifact_id for a in current_artifacts(ws)}
    latest = None
    for artifact in reversed(ws.artifacts):
        receipts = [event.after for event in ws.audit if event.action == "simple_export_confirmed"
            and event.entity_id == artifact.artifact_id and isinstance(event.after, dict)]
        saved_audience = receipts[-1].get("audience", "internal") if receipts else "internal"
        if (artifact.artifact_id in current and artifact.document_type is kind
                and artifact.filename.lower().endswith("." + output_format) and saved_audience == audience):
            latest = artifact
            break
    if latest:
        try:
            st.download_button("下载当前文件", _download(st, documents, ws, actor,
                artifact_id=latest.artifact_id), latest.filename, mime=latest.media_type,
                key=f"latest-delivery-{latest.artifact_id}", type="primary")
            st.caption(f"当前有效版本 v{latest.version_number} · {latest.filename}")
        except CrossBotError as exc:
            st.error("当前文件暂不能下载：" + exc.message)


def _review_grid(st, ws, actor, kind, group, rows, digest, **options):
    import pandas as pd
    from .drafts import DraftStore
    store = DraftStore(st.session_state)
    scope = (ws.case.case_id, actor.user_id + ":source-grid", kind.value + ":" + group)
    initial = {"rows": rows}
    draft = store.load(*scope, ws.revision, initial, concurrency_token=digest)
    if draft.stale:
        st.warning("原件核对依据已有新版本，暂存内容已保留；请重新载入后保存。")
    elif draft.dirty:
        st.caption("未保存的核对值已暂存，切换文件后仍可继续处理。")
    widget_key = store.widget_key(*scope, "rows")
    displayed = draft.values["rows"]
    updated = st.data_editor(pd.DataFrame(displayed), key=widget_key,
        on_change=store.capture_editor, args=(*scope, widget_key, "rows", displayed), **options)
    if draft.dirty and st.button("放弃" + group + "暂存，载入最新内容"):
        store.discard(*scope, ws.revision, initial, concurrency_token=digest)
        st.rerun()
    fingerprint = sha256(json.dumps(displayed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return updated, draft, store, scope, fingerprint


def _identity_aliases(kind):
    if kind in {D.COMMERCIAL_INVOICE, D.PROFORMA_INVOICE, D.COMBINED_INVOICE_PACKING_LIST}:
        return {"document_no": "invoice_no", "document_date": "invoice_date"}
    if kind is D.PACKING_LIST:
        return {"document_no": "packing_list_no", "document_date": "packing_date"}
    if kind is D.SALES_CONTRACT:
        return {"document_no": "contract_no"}
    return {}


def _full_file_editor(st, ws, actor, kind, form, repository, explain, scalar_origins):
    import pandas as pd
    from .forms import input_values, input_rows, input_form_fields, input_form_row_fields
    from .drafts import DraftStore
    from validation.export_review import review_digest
    aliases = _identity_aliases(kind)
    headers = {name: label for name, label in input_form_fields(form).items() if name not in aliases.values()}
    values = input_values(ws.trade, form)
    for canonical, alias in aliases.items():
        if canonical in headers:
            headers[canonical] = FORM_FIELDS[kind].get(alias, headers[canonical])
            if not values.get(canonical):
                values[canonical] = values.get(alias, "")
    columns = input_form_row_fields(form)
    initial = {**{name: values.get(name, "") for name in headers}, "rows": input_rows(ws.trade, form)}
    store = DraftStore(st.session_state)
    scope = (ws.case.case_id, actor.user_id + ":full-file", kind.value)
    draft = store.load(*scope, ws.revision, initial, concurrency_token=review_digest(ws, kind))
    with st.expander("填写与修改当前文件"):
        if draft.stale:
            st.warning("已有较新的资料版本，暂存内容已保留；请重新载入后再保存。")
        elif draft.dirty:
            st.caption("未保存内容已在当前会话暂存，切换文件或导出格式后仍可继续填写。")
        if any(form.values.get(c) and form.values.get(a) and form.values[c] != form.values[a] for c, a in aliases.items()):
            st.warning("历史编号或日期存在不同值，请在审核区核对；这里统一显示当前文件的主要编号和日期。")
        default_fields = list(dict.fromkeys([*(k for k, v in values.items() if v),
            *form.field_candidates, *form.manual_fields, *form.required_input_fields, "document_no", "document_date", "notes"]))
        chosen = st.multiselect("显示填写项", list(headers),
            default=[k for k in default_fields if k in headers], format_func=headers.get,
            key=f"simple-form-fields-{ws.case.case_id}-{kind.value}-{form.revision}")
        for name in chosen:
            widget_key = store.widget_key(*scope, name)
            st.text_input(headers[name], draft.values.get(name, ""), key=widget_key,
                help=explain(scalar_origins.get(name) or scalar_origins.get(aliases.get(name))),
                on_change=store.capture_widget, args=(*scope, widget_key, name))
        shown_rows = draft.values["rows"]
        rows_key = store.widget_key(*scope, "rows")
        updated = st.data_editor(pd.DataFrame(shown_rows, columns=list(columns)).fillna(""), num_rows="dynamic", hide_index=True,
            column_config={name: st.column_config.TextColumn(label) for name, label in columns.items()},
            use_container_width=True, key=rows_key, on_change=store.capture_editor,
            args=(*scope, rows_key, "rows", shown_rows))
        save = st.button("保存补充内容", disabled=draft.stale)
        if draft.dirty and st.button("放弃此文件暂存，载入最新内容"):
            store.discard(*scope, ws.revision, initial)
            st.rerun()
        if save:
            rendered_rows = [{name: "" if pd.isna(row[name]) else str(row[name]) for name in columns}
                for _, row in updated.iterrows()]
            if rendered_rows != draft.values["rows"]:
                store.patch(*scope, {"rows": rendered_rows})
            active = store.load(*scope, ws.revision, initial, concurrency_token=review_digest(ws, kind))
            saved_values = dict(form.values)
            for name in chosen:
                saved_values[name] = active.values.get(name, "")
                alias = aliases.get(name)
                if alias and (saved_values[name] != active.base_values.get(name, "") or not saved_values.get(alias)):
                    saved_values[alias] = saved_values[name]
            rows = [{name: str(row.get(name, "") or "") for name in columns} for row in active.values["rows"]]
            rows = [row for row in rows if any(row.values())]
            def save_file():
                saved = FormService(repository).save(ws.case.case_id, actor, kind, saved_values, rows,
                    "人工填写文件内容", confirm=False, expected_workspace_revision=active.base_revision)
                store.clear(*scope)
                return saved
            _run(st, save_file, "补充内容已保存。")


def _export_history(st, ws, actor, documents, kind=None):
    with st.expander("历史版本与多文件打包"):
        items = list(reversed(ws.artifacts))
        from review.policy import current_artifacts
        current_ids = {a.artifact_id for a in current_artifacts(ws)}
        if items:
            st.dataframe([{"文件": a.filename, "版本": a.version_number,
                "情况": "当前有效" if a.artifact_id in current_ids else "历史/需重新核对",
                "规则审核": "当前规则通过" if a.artifact_id in current_ids else "历史审核结果，需重新核对",
                "人工确认": "已确认" if any(event.action == "simple_export_confirmed" and
                    event.entity_id == a.artifact_id for event in ws.audit) else "旧流程文件",
                "生成人": a.created_by} for a in items], hide_index=True, use_container_width=True)
            latest = next((a for a in items if a.document_type is kind), None)
            if latest:
                warnings = [c.message for c in latest.checks if c.status is not CrossCheckStatus.PASS]
                if warnings:
                    with st.expander("此文件的缺失与冲突提示"):
                        for warning in warnings:
                            st.warning(warning)
        originals = [d for d in ws.documents if d.deleted_at is None and d.current_version_id]
        doc_ids = st.multiselect("选择需要导出的原件", [d.document_id for d in originals],
            format_func=lambda key: next(d.title for d in originals if d.document_id == key))
        artifact_ids = st.multiselect("选择需要导出的生成文件", [a.artifact_id for a in items],
            default=[],
            format_func=lambda key: next(a.filename + ("（历史版本）" if a.status == "stale" else "") for a in items if a.artifact_id == key))
        if artifact_ids:
            for key in artifact_ids:
                artifact = next(a for a in items if a.artifact_id == key)
                try:
                    st.download_button("下载 " + artifact.filename,
                        _download(st, documents, ws, actor, artifact_id=key), artifact.filename,
                        mime=artifact.media_type, key=f"simple-download-{key}")
                except CrossBotError as exc:
                    st.error("无法下载此版本：" + exc.message)
        if doc_ids or artifact_ids:
            if st.button("打包所选文件"):
                try:
                    content = selected_bundle(ws, documents, actor, doc_ids, artifact_ids)
                    st.download_button("下载所选文件 ZIP", content, ws.case.case_number + "-文件.zip", mime="application/zip")
                except CrossBotError as exc:
                    st.error("所选文件打包未完成：" + exc.message + "。请检查原件和生成版本是否仍可读取。")



def _exports(st, ws, actor, documents, repository, blobs, cases):
    import pandas as pd
    from .simple_export import SimpleExportService
    from .prefill import PrefillService
    from .forms import input_values, input_rows
    from .provenance import form_value_origins, ORIGIN_LABELS
    st.caption("直接填写业务数据即可制作我方文件，无需原文件或模板；已有资料也可以采集填入。")
    _, delivery_types = requirement_groups(ws)
    choices = list(dict.fromkeys(delivery_types))
    if not choices:
        st.info("请在“需生成的内容”下方“调整资料与交付清单 → 文件清单”选择本单需要生成的内容；历史文件仍可在下面查看和下载。")
        _export_history(st, ws, actor, documents)
        return
    kind = st.selectbox("需要生成的文件", choices, format_func=DOC_LABELS.get,
                        index=choices.index(D.COMMERCIAL_INVOICE) if D.COMMERCIAL_INVOICE in choices else 0)
    form = get_form(ws, kind)
    from .forms import input_form_fields, input_form_row_fields
    active_headers, active_rows = input_form_fields(form), input_form_row_fields(form)
    export_format = st.selectbox("导出格式", ["Excel", "PDF"], key=f"output-format-{ws.case.case_id}-{kind.value}")
    output_format = "pdf" if export_format == "PDF" else "xlsx"
    audience = "internal" if st.session_state.get(f"audience-{ws.case.case_id}-{kind.value}", "对外交付") == "内部核对" else "customer"
    origins = form_value_origins(ws, kind, include_inputs=True)
    scalar_origins = {item.field_name: item for item in origins if item.row_number is None}

    def explain(item):
        if item is None:
            return "尚未填写"
        parts = [ORIGIN_LABELS[item.origin_kind]]
        if item.filename:
            parts.append(item.filename + (f" · v{item.version_number}" if item.version_number else ""))
        if item.location:
            parts.append(item.location)
        if item.actor_id:
            parts.append("办理人：" + item.actor_id)
        if item.note:
            parts.append(item.note)
        return " · ".join(parts)

    review_service = SimpleExportService(repository, blobs)
    pending_review = review_service.assess(ws.case.case_id, actor, kind)
    fingerprint = sha256((pending_review.input_digest + "|" +
        "|".join(a.artifact_id for a in ws.artifacts)).encode()).hexdigest()
    confirm_key = f"simple-confirm-{ws.case.case_id}-{fingerprint}-{kind.value}"
    confirmed = st.session_state.get(confirm_key, False)
    report = review_service.assess(ws.case.case_id, actor, kind, human_confirmed=confirmed)
    problem_report = review_service.assess(ws.case.case_id, actor, kind, human_confirmed=True)
    required_checks = [check for check in report.checks if check.required]
    problem_checks = [check for check in problem_report.checks if check.required]
    issues = [check for check in problem_checks if check.status is not CrossCheckStatus.PASS and check.rule_code != "EXPORT_HUMAN_CONFIRMATION"]
    if any(check.rule_code == "INPUT_DATA_REQUIREMENTS" for check in issues):
        issues = [check for check in issues if check.rule_code != "DOCUMENT_COMPLETENESS"]
    st.markdown("**审核结果**")
    if not issues:
        st.success("规则检查通过。核对当前内容后，勾选确认即可生成。")
    elif any(check.status is CrossCheckStatus.FAIL for check in issues):
        st.error("当前文件审核未通过，请先处理下面的问题。待核对预览仍可下载。")
    else:
        st.info("当前文件还有待补充或待确认的内容；完成下方项目后才能正式生成。")
    if issues:
        from review.simple_editor import target_label
        st.dataframe([{"检查结果": "不通过" if check.status is CrossCheckStatus.FAIL else "待处理",
            "修改位置": "、".join(target_label(ws, target) for target in check.edit_targets[:2]),
            "问题": _review_message(check, kind), "当前值": _review_value(check.left_value),
            "对照值": _review_value(check.right_value)}
            for check in issues], hide_index=True, use_container_width=True)
    from review.simple_editor import render_issue_editor
    selected_edit = render_issue_editor(st, ws, actor, kind, issues, repository, cases,
        report.input_digest, _review_message)
    from review.quality_inputs import pending_source_fields, confirm_source_fields
    from core.models import SourceFieldSelection
    source_fields = pending_source_fields(ws, kind)
    if source_fields:
        with st.expander("核对参与比对的外部字段"):
            st.caption("外部原件的采集值需要真人核对。可在此修正并一次确认所列字段；确认后仍会运行规则。")
            field_rows = []
            source_labels = {"customer_name": "买方/客户", "product_name": "品名",
                "product_code": "产品编码", "quantity": "数量", "unit": "数量单位",
                "unit_price": "单价", "packages": "箱数", "gross_weight_kg": "毛重 kg",
                "net_weight_kg": "净重 kg", "currency": "币种", "total_amount": "金额",
                "order_no": "订单号", "shipper": "发货人", "consignee": "收货人", "incoterm": "贸易术语"}
            for field in source_fields:
                document = next(doc for doc in ws.documents if doc.document_id == field.document_id)
                version = next(v for v in document.versions if v.version_id == field.version_id)
                candidate = next(item for item in version.fields if item.name == field.field_name)
                location = " · ".join(str(value) for value in (candidate.page_number,
                    candidate.sheet_name, candidate.cell_reference) if value is not None)
                field_rows.append({"核对": True, "原件": version.original_filename,
                    "版本": f"v{version.version_number}",
                    "字段": source_labels.get(field.field_name, field.field_name), "字段名": field.field_name,
                    "位置": location, "采集值": str(candidate.value) if candidate.value is not None else "",
                    "确认值": field.value,
                    "document_id": field.document_id, "version_id": field.version_id})
            reviewed_fields, field_draft, field_store, field_scope, field_fingerprint = _review_grid(
                st, ws, actor, kind, "外部字段", field_rows, report.input_digest, hide_index=True,
                column_config={"document_id": None, "version_id": None, "字段名": None},
                disabled=["原件", "版本", "字段", "字段名", "位置", "采集值", "document_id", "version_id"],
                use_container_width=True)
            source_ack = st.checkbox("我已逐项核对上表所选字段的原件和确认值",
                key=f"quality-source-ack-{ws.case.case_id}-{kind.value}-{report.input_digest}-{field_fingerprint}")
            if st.button("确认外部字段并重新审核", disabled=not source_ack or field_draft.stale):
                selections = [SourceFieldSelection(str(row["document_id"]), str(row["version_id"]),
                    str(row["字段名"]), "" if pd.isna(row["确认值"]) else str(row["确认值"]))
                    for _, row in reviewed_fields.iterrows() if row["核对"]]
                def save_source_fields():
                    result = confirm_source_fields(repository, ws.case.case_id, actor,
                        kind, selections, expected_review_digest=report.input_digest, human_confirmed=source_ack)
                    field_store.clear(*field_scope)
                    return result
                _run(st, save_source_fields, "所选外部字段已核对，规则已按当前版本重新计算。")
    from review.quality_inputs import pending_source_rows, confirm_source_rows
    from core.models import SourceRowSelection
    source_rows = pending_source_rows(ws, kind)
    if source_rows:
        with st.expander("核对外部原件的全部产品明细"):
            st.caption("按原件逐行核对，可修正采集值；每份原件一次确认完整商品集，不能遗漏或删除行。原表及旧值保留。")
            columns = list(dict.fromkeys(["product_code", "description", "quantity", "unit",
                *(k for row in source_rows for k in row.values)]))
            row_data = []
            for source in source_rows:
                doc = next(d for d in ws.documents if d.document_id == source.document_id)
                version = next(v for v in doc.versions if v.version_id == source.version_id)
                row_data.append({"原件": version.original_filename, "版本": version.version_number,
                    "原表序号": source.table_index + 1, "原行号": source.row_number,
                    **{k: source.values.get(k, "") for k in columns},
                    "document_id": source.document_id, "version_id": source.version_id})
            updated, row_draft, row_store, row_scope, row_fingerprint = _review_grid(
                st, ws, actor, kind, "原件明细", row_data, report.input_digest, hide_index=True,
                disabled=["原件", "版本", "原表序号", "原行号", "document_id", "version_id"],
                column_config={"document_id": None, "version_id": None}, use_container_width=True)
            rows_ack = st.checkbox("我已对照原件核对上表全部产品行", key=
                f"external-row-ack-{ws.case.case_id}-{kind.value}-{report.input_digest}-{row_fingerprint}")
            if st.button("确认原件明细并重新审核", disabled=not rows_ack or row_draft.stale):
                selections = [SourceRowSelection(str(row["document_id"]), str(row["version_id"]),
                    int(row["原表序号"]) - 1, int(row["原行号"]),
                    {k: "" if pd.isna(row[k]) else str(row[k]) for k in columns}) for _, row in updated.iterrows()]
                def save_source_rows():
                    result = confirm_source_rows(repository, ws.case.case_id, actor, kind, selections,
                        expected_review_digest=report.input_digest, human_confirmed=rows_ack)
                    row_store.clear(*row_scope)
                    return result
                _run(st, save_source_rows, "原件完整明细已核对，规则已更新。")
    _full_file_editor(st, ws, actor, kind, form, repository, explain, scalar_origins)
    with st.expander("预览当前文件"):
        if st.button("预览文件", key=f"preview-{ws.case.case_id}-{kind.value}-{output_format}"):
            try:
                preview = SimpleExportService(repository, blobs).preview(ws.case.case_id, actor, kind,
                    output_format=output_format, audience=audience)
                st.download_button("下载待核对预览", preview.content,
                    f"{kind.value}-待核对预览.{output_format}", mime=preview.media_type,
                    key=f"preview-download-{ws.case.case_id}-{kind.value}-{output_format}")
                if output_format == "pdf":
                    import pypdfium2 as pdfium
                    pdf = pdfium.PdfDocument(preview.content)
                    try:
                        page = pdf[0]
                        bitmap = page.render(scale=1.2)
                        try:
                            st.image(bitmap.to_pil(), caption=f"实际 PDF · 第1页/共{len(pdf)}页 · 待人工核对")
                        finally:
                            bitmap.close()
                            page.close()
                    finally:
                        pdf.close()
                st.caption("预览不产生正式版本或批准记录。")
            except CrossBotError as exc:
                st.error(exc.message)
    confirmed = st.checkbox("我已人工核对当前资料，确认按现有内容生成", key=confirm_key)
    report = review_service.assess(ws.case.case_id, actor, kind, human_confirmed=confirmed)
    from .drafts import DraftStore
    unsaved = [draft for draft in DraftStore(st.session_state).list_dirty(ws.case.case_id)
        if draft.editor_id.startswith(actor.user_id + ":")]
    if unsaved:
        st.warning("本单还有未保存的填写，请先保存；也可在上方“查看和处理未保存内容”中放弃，再确认生成。")
    if st.button("确认并生成", disabled=not report.ready or bool(unsaved), type="primary"):
        _run(st, lambda: SimpleExportService(repository, blobs).generate(ws.case.case_id, actor, kind,
            human_confirmed=confirmed, output_format=output_format, audience=audience,
            expected_review_digest=report.input_digest), "文件及本次人工确认记录已保存。")
    _latest_download(st, ws, actor, documents, kind, output_format, audience)
    st.caption("更多编辑、来源与导出设置（按需展开）")
    with st.container(key=f"w3-export-secondary-{ws.case.case_id}-{kind.value}"):
        with st.expander("版式与导出选项"):
            audience = "internal" if st.radio("导出用途", ["对外交付", "内部核对"], horizontal=True,
                key=f"audience-{ws.case.case_id}-{kind.value}") == "内部核对" else "customer"
            templates = {t.template_id: t for t in ws.document_templates if t.document_type is kind}
            if templates or ws.selected_template_ids.get(kind.value):
                from .templates import TemplateService
                options = [None, *templates]
                current = ws.selected_template_ids.get(kind.value)
                picked = st.selectbox("使用的导出模板", options, index=options.index(current) if current in options else 0,
                    format_func=lambda key: "默认简单版" if key is None else templates[key].filename + f" · v{templates[key].version_number}",
                    key=f"business-template-{ws.case.case_id}-{kind.value}-{ws.revision}")
                if st.button("使用所选版式"):
                    _run(st, lambda: TemplateService(repository, blobs).select(ws.case.case_id, actor, kind, picked,
                        "业务员选择已有版式", expected_revision=ws.revision), "版式已更新，请重新核对。")
            st.caption("对外交付只包含业务内容；来源、校验和审计留在系统，内部核对版可另行导出。PDF 使用统一排版。")
        from .manual_inputs import ManualInputService, has_manual_data
        manual_sources = [f for f in ws.document_forms if f.document_type is not kind
            and has_manual_data(f) and not f.source_form_references]
        records = {r.record_id: r for r in ws.manual_data_records if has_manual_data(r)}
        if manual_sources or records or form.source_form_references:
            with st.expander("从手动提供的资料填入（可选）", expanded=bool(selected_edit and selected_edit.area.value == "source_links")):
                if manual_sources:
                    source_kind = st.selectbox("选择手动资料", [f.document_type for f in manual_sources],
                        format_func=DOC_LABELS.get, key=f"manual-fill-source-{ws.case.case_id}-{kind.value}")
                    st.caption("仅填入已保存且适用的业务数据，保留人工来源及资料版本；已有内容冲突时需先核对。")
                    if st.button("将手动资料填入当前表单", key=f"manual-fill-{ws.case.case_id}-{kind.value}"):
                        _run(st, lambda: ManualInputService(repository).apply(ws.case.case_id, actor,
                            source_kind, kind, "使用人工提供资料填写我方文件", expected_revision=ws.revision), "手动资料已填入；请补充本文件编号、日期并核对。")
                if records:
                    source_id = st.selectbox("选择独立手动资料", list(records),
                        format_func=lambda key: (records[key].title or DOC_LABELS[records[key].document_type])
                            + f" · 修订 {records[key].revision}", key=f"manual-record-fill-{ws.case.case_id}-{kind.value}")
                    if st.button("使用这份独立资料填写", key=f"manual-record-apply-{ws.case.case_id}-{kind.value}"):
                        _run(st, lambda: ManualInputService(repository).apply(ws.case.case_id, actor,
                            records[source_id].document_type, kind, "使用指定独立人工资料填写文件",
                            source_record_id=source_id, expected_revision=ws.revision), "指定资料已填入，仍须核对。")
                if form.source_form_references:
                    refs = {(ref.document_type, ref.record_id): ref for ref in form.source_form_references}
                    st.caption("已关联：" + "、".join(f"{DOC_LABELS[ref.document_type]} · 修订 {ref.revision}"
                        for ref in refs.values()))
                    unlink_key = st.selectbox("不再跟随的手动资料", list(refs),
                        format_func=lambda key: DOC_LABELS[key[0]] + (" · " + key[1] if key[1] else ""),
                        key=f"manual-detach-source-{ws.case.case_id}-{kind.value}")
                    detach_ack = st.checkbox("我已核对保留内容，改为独立维护此文件", key=
                        f"manual-detach-ack-{ws.case.case_id}-{kind.value}-{form.revision}")
                    if st.button("解除所选资料关联，保留填写内容", disabled=not detach_ack,
                            key=f"manual-detach-{ws.case.case_id}-{kind.value}"):
                        _run(st, lambda: ManualInputService(repository).detach(ws.case.case_id, actor,
                            kind, unlink_key[0], "人工核对保留内容后解除来源关联，改为独立维护",
                            source_record_id=unlink_key[1], expected_revision=ws.revision),
                            "已解除资料关联，保留填写与历史；生成前仍须通过审核。")
        with st.expander("当前填写内容及来源"):
            st.caption("逐项展示当前值的来源；采集候选另列，人工修改不会被误标为原文件内容。")
            origin_rows = []
            for item in origins:
                label = (FORM_FIELDS[kind] if item.row_number is None else ROW_FIELDS[kind]).get(item.field_name, item.field_name)
                origin_rows.append({"填写位置": "表头" if item.row_number is None else f"明细第 {item.row_number} 行",
                    "填写项": label, "当前内容": item.value,
                    "来源类型": ORIGIN_LABELS[item.origin_kind], "来源文件": item.filename,
                    "版本": f"v{item.version_number}" if item.version_number else "",
                    "表格/位置": item.location, "办理人": item.actor_id,
                    "说明": item.note})
            if origin_rows:
                st.dataframe(origin_rows, hide_index=True, use_container_width=True)
            else:
                st.caption("暂无已填写内容；可先采集资料或手工填写。")
            if any(item.origin_kind == "unknown" for item in origins):
                st.info("部分历史内容尚无可靠来源记录，已标为来源待补。不会自动认定为人工填写；可重新采集或由人工核实后修订。")
        available = [d for d in ws.documents if d.deleted_at is None and d.current_version_id]
        with st.expander("从已有资料填入（可选）", expanded=bool(selected_edit and selected_edit.area.value == "source_links")):
            source_ids = st.multiselect("选择填表来源", [d.document_id for d in available],
                default=[key for key in form.source_document_ids if any(d.document_id == key for d in available)],
                format_func=lambda key: next(d.title for d in available if d.document_id == key),
                key=f"simple-prefill-sources-{ws.case.case_id}-{kind.value}-{form.revision}")
            st.caption("使用已经采集的内容；不同候选保留来源，不覆盖人工修改。无需来源时可直接填写下方表单。")
            if st.button("将采集结果填入当前表单", disabled=not source_ids,
                         key=f"simple-prefill-{ws.case.case_id}-{kind.value}"):
                _run(st, lambda: PrefillService(repository).fill(ws.case.case_id, actor, kind, source_ids),
                    "采集结果已填入，候选仍待人工核对。")
            if form.source_document_ids and st.button("撤下自动填入内容，保留人工修改",
                    key=f"simple-unlink-{ws.case.case_id}-{kind.value}"):
                _run(st, lambda: PrefillService(repository).fill(ws.case.case_id, actor, kind, []),
                    "已撤下自动填入内容，人工修改保留。")
        if form.field_candidates or form.row_candidates:
            with st.expander("查看填写来源与候选"):
                candidates = []
                for name, sources in form.field_candidates.items():
                    if name not in active_headers:
                        continue
                    for source in sources:
                        candidates.append({"填写项": FORM_FIELDS[kind].get(name, name),
                            "采集候选": source.value,
                            "来源文件": next((d.title for d in available if d.document_id == source.document_id), source.document_id),
                            "文件版本": source.version_id,
                            "位置": " · ".join(str(v) for v in (source.page_number, source.sheet_name, source.cell_reference) if v is not None),
                            "填写方式": "人工维护" if name in form.manual_fields else "采集候选（待核对）"})
                if candidates:
                    st.dataframe(candidates, hide_index=True, use_container_width=True)
                if any(len({source.value for source in sources}) > 1
                       for sources in form.field_candidates.values()):
                    st.info("部分字段有不同来源值，未替你选择。请对照来源填写，或交由核对模块处理。")
                if form.row_candidates:
                    st.caption(f"保留 {len(form.row_candidates)} 行来源候选；来源版本和单元格随数据保存。")
                    if not form.rows and not form.rows_manually_edited:
                        st.info("明细候选尚未填入；可以选择单一来源，或对照来源手工填写明细。")
                st.caption("来源记录用于追溯；不代表已完成完整性核对、人工确认或业务校验。")
        case_issues = [check for check in report.checks if not check.required
                       and check.status is not CrossCheckStatus.PASS]
        if case_issues:
            with st.expander("整单资料提示"):
                st.caption("这些提示用于跟进整单资料，与当前单份文件的生成条件分开。")
                for check in case_issues:
                    st.write(_review_message(check, kind))
        with st.expander("查看审核依据"):
            st.dataframe([{"检查": _review_message(check, kind), "结果": check.status.value,
                "当前来源": check.left_source or "", "对照来源": check.right_source or "",
                "当前规范值": str(check.normalized_left) if check.normalized_left is not None else "",
                "对照规范值": str(check.normalized_right) if check.normalized_right is not None else "",
                "当前单位": check.left_unit or "", "对照单位": check.right_unit or ""}
                for check in required_checks], hide_index=True, use_container_width=True)
        _export_history(st, ws, actor, documents, kind)


def _draft_overview(st, ws, actor):
    import pandas as pd
    from .drafts import DraftStore
    store = DraftStore(st.session_state)
    drafts = [draft for draft in store.list_dirty(ws.case.case_id) if draft.editor_id.startswith(actor.user_id + ":")]
    if not drafts:
        return
    st.info(f"本单有 {len(drafts)} 处未保存的填写，已在当前会话暂存。")
    with st.expander("查看和处理未保存内容"):
        labels = {"full-file": "文件填写", "requirement-fill": "按项填写", "review-correction": "审核修改",
            "manual-source": "人工资料", "trade": "订单资料", "source-field": "原件字段核对",
            "source-rows": "原件明细核对", "source-grid": "原件批量核对"}
        def label(index):
            item = drafts[index]
            kind = next((kind for kind in DOC_LABELS if item.target_id.split(":")[0] == kind.value), None)
            return f"{index + 1}. " + labels.get(item.editor_id.split(":", 1)[-1], "填写资料") + (
                " · " + DOC_LABELS[kind] if kind else "")
        summary_key = f"draft-summary-{ws.case.case_id}"
        if st.session_state.get(summary_key, 0) not in range(len(drafts)):
            st.session_state.pop(summary_key, None)
        index = st.selectbox("选择暂存填写", range(len(drafts)), format_func=label, key=summary_key)
        draft = drafts[index]
        field_labels = {key: value for fields in FORM_FIELDS.values() for key, value in fields.items()}
        field_labels.update({"value": "填写内容", "reason": "修改说明", "title": "资料名称", "invoice_adjustments_confirmed": "费用已核实"})
        values = [{"填写项": field_labels.get(name, name), "暂存内容": str(value)}
            for name, value in draft.values.items() if not isinstance(value, (dict, list)) and str(value or "").strip()]
        if values:
            st.dataframe(values, hide_index=True, use_container_width=True)
        for name in ("rows", "line_items", "adjustments", "packages"):
            if draft.values.get(name):
                row_labels = {key: value for columns in ROW_FIELDS.values() for key, value in columns.items()}
                st.dataframe(pd.DataFrame(draft.values[name]).drop(columns=["supplied_fields", "_line_id"], errors="ignore")
                    .rename(columns=row_labels), hide_index=True, use_container_width=True)
        st.caption("暂存内容尚未写入业务资料。可回到对应入口继续；已不需要的暂存可在此放弃。")
        if st.button("放弃这份暂存", key=f"discard-summary-{draft.key}"):
            store.clear(draft.case_id, draft.editor_id, draft.target_id)
            st.rerun()


def render_workspace(ws, actor, cases, documents, repository, blobs, defaults_service=None):
    import streamlit as st
    st.markdown("""<style>
    [class*="st-key-w3-secondary"] details:not([open]) > summary p,
    [class*="st-key-w3-export-secondary"] details:not([open]) > summary p {font-size:0.85rem;color:#687386;}
    [class*="st-key-w3-secondary"] details[open] > summary p,
    [class*="st-key-w3-export-secondary"] details[open] > summary p {font-size:1rem;}
    </style>""", unsafe_allow_html=True)
    if ws.case.status is CaseStatus.CLOSED:
        st.info("此交易已归档，可查看和下载历史文件。")
        for doc in ws.documents:
            if doc.deleted_at is None:
                with st.expander(doc.title):
                    _source_preview(st, ws, doc, documents, actor, "closed")
        for artifact in ws.artifacts:
            st.download_button(artifact.filename, _download(st, documents, ws, actor,
                artifact_id=artifact.artifact_id), artifact.filename, key=artifact.artifact_id)
        return
    rows = file_inventory(ws)
    source_types, delivery_types = requirement_groups(ws)
    from .requirements import delivery_requirements
    mapped = {kind: items for kind, items in delivery_requirements(ws).items() if kind in delivery_types}
    data_pending = any(i.required and i.status != "provided" for items in mapped.values() for i in items)
    st.markdown("**需提供的资料**")
    pending = [(kind, item) for kind, items in mapped.items() for item in items
               if item.required and item.status != "provided"]
    missing_sources = [r["文件分类"] for r in rows if r["需要提供资料"] and not r["原件"] and not r["手动填写"]]
    if pending:
        st.warning(f"有 {len(pending)} 项资料待补充或核实。可在下方补资料，或选中文件核对处理。")
        st.dataframe([{"用于生成": DOC_LABELS[kind], "待处理资料": item.label,
            "位置": f"明细第 {item.row_number} 行" if item.row_number else "表头/业务资料",
            "状态": "待补充" if item.status == "missing" else "待核实来源"}
            for kind, item in pending], hide_index=True, use_container_width=True,
            height=min(248, 38 + 35 * len(pending)))
    elif mapped:
        st.success("当前必需资料已填写，生成前请继续核对并确认。")
    if missing_sources:
        st.caption("待提供来源资料：" + "、".join(missing_sources))
    st.markdown("**需生成的内容**")
    delivery_rows = [r for r in rows if r["需要制作交付"]]
    missing_outputs = [r["文件分类"] for r in delivery_rows if not r["原件"] and not r["生成文件"]]
    if missing_outputs:
        st.warning("缺失文件（待制作）：" + "、".join(missing_outputs))
    elif delivery_rows:
        st.success("清单中的交付文件均已有文件版本；不代表已发送或审核通过。")
    else:
        st.caption("尚未设置需要制作并交付的文件，请在“需生成的内容”下方的“调整资料与交付清单”中选择。")
    if delivery_rows:
        display = []
        for row in delivery_rows:
            kind = next(k for k, label in DOC_LABELS.items() if label == row["文件分类"])
            requirements = mapped.get(kind, [])
            display.append({"文件": row["文件分类"], "情况": row["交付情况"],
                "待补/待核实": sum(i.required and i.status != "provided" for i in requirements)})
        st.dataframe(display, hide_index=True, use_container_width=True)
    if not source_types and not delivery_types:
        st.info("尚未设置本单两类清单，当前不表示资料完整。")
    with st.container(key="w3-secondary-checklist"):
        _case_settings(st, ws, actor, cases, repository, blobs, defaults_service)
    _draft_overview(st, ws, actor)
    files, exports = st.tabs(["文件分类与查看", "生成与导出"])
    with files:
        _files(st, ws, actor, documents, repository, mapped)
    with exports:
        _exports(st, ws, actor, documents, repository, blobs, cases)
    st.caption("更多资料与设置（按需展开）")
    with st.container(key="w3-secondary"):
        _edit_trade(st, ws, actor, cases)
        all_inputs = provided_inventory(ws)
        if all_inputs:
            with st.expander("全部已提供资料与用途"):
                st.dataframe(all_inputs, hide_index=True, use_container_width=True)
        if mapped:
            with st.expander("逐项对应生成所需数据与来源"):
                requirement_rows = []
                state_labels = {"missing": "待补充", "review_required": "待核实来源/初始值", "provided": "已有数据"}
                for target, requirements in mapped.items():
                    for item in requirements:
                        origin = item.origin
                        requirement_rows.append({"用于生成": DOC_LABELS[target], "资料项": item.label,
                            "位置": f"明细第 {item.row_number} 行" if item.row_number else "表头/业务资料",
                            "必需": item.required, "当前值": item.value, "状态": "可选，尚未提供"
                                if not item.required and item.status == "missing" else state_labels[item.status],
                            "来源": (origin.filename or {"manual": "人工填写", "trade": "订单资料",
                                "derived": "确定性计算", "file": "原件", "default": "系统初始值"}.get(origin.origin_kind,
                                "来源待核实")) if origin else "尚未提供",
                            "来源位置/依据": (origin.location or origin.note) if origin else item.message})
                st.dataframe(requirement_rows, hide_index=True, use_container_width=True)

