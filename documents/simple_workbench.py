"""Compact file inventory, editable data and human-confirmed Excel export."""
from copy import deepcopy
from io import BytesIO
from pathlib import PurePath
from zipfile import ZipFile, ZIP_DEFLATED
from hashlib import sha256
import json

from core.errors import CrossBotError
from core.models import CaseStatus, CrossCheckStatus, DocumentType as D, TradeLine, to_dict
from documents.catalog import DOC_LABELS, SELLER_TYPES, FORM_FIELDS, ROW_FIELDS
from documents.forms import FormService, effective_values, get_form
from documents.intake import classify_source
from documents.workbench import reclassify, collect_document


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
    content = _download(st, documents, ws, actor, document_id=doc.document_id, version_id=selected)
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
    kinds = list(dict.fromkeys([*workspace.required_document_types,
        *(d.document_type for d in workspace.documents if d.deleted_at is None),
        *(a.document_type for a in workspace.artifacts)]))
    rows = []
    for kind in kinds:
        originals = [d for d in workspace.documents if d.deleted_at is None
                     and d.current_version_id and d.document_type is kind]
        generated = [a for a in workspace.artifacts if a.document_type is kind
                     and a.status in {"draft", "approved"}]
        form = get_form(workspace, kind)
        has_data = bool(form.rows or any(value for name, value in
                        effective_values(workspace.trade, form).items() if name != "currency")
                        or workspace.trade.line_items)
        rows.append({"文件分类": DOC_LABELS[kind], "需要": kind in workspace.required_document_types,
            "原件": len(originals), "生成文件": len(generated),
            "情况": ("已有原件" if originals else "已生成" if generated else
                ("待生成" if has_data else "待填写") if kind in SELLER_TYPES else "待获取")})
    return rows


def selected_bundle(workspace, service, actor, document_ids, artifact_ids):
    """Package only selected current originals and selected artifact versions."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for doc in workspace.documents:
            if doc.document_id not in document_ids or doc.deleted_at is not None:
                continue
            version = next(v for v in doc.versions if v.version_id == doc.current_version_id)
            name = PurePath(version.original_filename.replace("\\", "/")).name
            archive.writestr(f"原件/{DOC_LABELS[doc.document_type]}/{doc.document_id}/{name}",
                service.download(workspace.case.case_id, actor, document_id=doc.document_id))
        for item in workspace.artifacts:
            if item.artifact_id in artifact_ids:
                archive.writestr(f"生成文件/{item.artifact_id}/{PurePath(item.filename).name}",
                    service.download(workspace.case.case_id, actor, artifact_id=item.artifact_id))
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
        with st.form(f"simple-trade-{ws.case.case_id}-{ws.trade.revision}"):
            edited = deepcopy(ws.trade)
            cols = st.columns(2)
            fields = {"order_no": "订单号", "buyer": "客户", "seller": "卖方",
                "destination_country": "目的国家/地区", "currency": "币种", "incoterm": "贸易术语",
                "payment_terms": "付款条件", "transport_mode": "运输方式"}
            for index, (key, label) in enumerate(fields.items()):
                setattr(edited, key, cols[index % 2].text_input(label, getattr(ws.trade, key)))
            row_fields = {"product_code": "产品编码", "description": "品名", "quantity": "数量",
                "unit_price": "单价", "unit": "单位", "packages": "箱数",
                "gross_weight_kg": "毛重 kg", "net_weight_kg": "净重 kg"}
            data = pd.DataFrame([{**{k: str(getattr(line, k)) for k in row_fields},
                "_line_id": str(index)} for index, line in enumerate(ws.trade.line_items)],
                columns=[*row_fields, "_line_id"])
            updated = st.data_editor(data, num_rows="dynamic", hide_index=True,
                column_config={**{k: st.column_config.TextColumn(v) for k, v in row_fields.items()},
                               "_line_id": None}, disabled=["_line_id"],
                use_container_width=True)
            submitted = st.form_submit_button("保存订单资料")
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
                        setattr(line, key, int(value or 0) if key == "packages" else value)
                    lines.append(line)
                edited.line_items = lines
                _run(st, lambda: cases.update_trade(ws.case.case_id, actor, edited,
                    "业务员修订订单资料"), "订单资料已保存，导出时重新确认。")
            except ValueError:
                st.error("箱数请填写整数。")


def _files(st, ws, actor, documents, repository):
    case_id = ws.case.case_id
    with st.expander("上传文件"):
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
    available = [d for d in ws.documents if d.deleted_at is None]
    filter_kind = st.selectbox("按分类查看", [None, *dict.fromkeys(d.document_type for d in available)],
        format_func=lambda k: "全部文件" if k is None else DOC_LABELS[k])
    visible = [d for d in available if filter_kind is None or d.document_type is filter_kind]
    if not visible:
        st.info("暂无文件，请上传或导入资料。")
        return
    selected = st.selectbox("查看文件", [d.document_id for d in visible],
        format_func=lambda key: next(f"{d.title} · {DOC_LABELS[d.document_type]}" for d in visible if d.document_id == key))
    doc = next(d for d in visible if d.document_id == selected)
    with st.form(f"simple-classify-{doc.document_id}"):
        kind = st.selectbox("修正分类", list(DOC_LABELS), index=list(DOC_LABELS).index(doc.document_type), format_func=DOC_LABELS.get)
        submit = st.form_submit_button("保存分类")
    if submit:
        _run(st, lambda: reclassify(repository, case_id, doc.document_id, actor, kind,
            "seller" if kind in SELLER_TYPES else "external", "人工核对文件分类"), "分类已保存。")
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


def _exports(st, ws, actor, documents, repository, blobs):
    import pandas as pd
    from .simple_export import SimpleExportService
    from .prefill import PrefillService
    from .forms import effective_rows
    from .provenance import form_value_origins, ORIGIN_LABELS
    st.caption("直接填写业务数据即可制作我方文件，无需原文件或模板；已有资料也可以采集填入。")
    kind = st.selectbox("需要生成的文件", SELLER_TYPES, format_func=DOC_LABELS.get,
                        index=SELLER_TYPES.index(D.COMMERCIAL_INVOICE))
    form = get_form(ws, kind)
    origins = form_value_origins(ws, kind)
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

    with st.expander("当前填写内容及来源", expanded=True):
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
    with st.expander("从已有资料填入（可选）"):
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
    with st.expander("补充此文件内容（可选）"):
        display_values = effective_values(ws.trade, form)
        default_fields = list(dict.fromkeys([*(k for k, v in display_values.items() if v),
            *form.field_candidates, *form.manual_fields, "document_no", "document_date", "notes"]))
        chosen = st.multiselect("显示填写项", list(FORM_FIELDS[kind]),
            default=[k for k in default_fields if k in FORM_FIELDS[kind]], format_func=FORM_FIELDS[kind].get,
            key=f"simple-form-fields-{ws.case.case_id}-{kind.value}-{form.revision}")
        with st.form(f"simple-form-{ws.case.case_id}-{kind.value}-{form.revision}"):
            values = dict(form.values)
            for name in chosen:
                values[name] = st.text_input(FORM_FIELDS[kind][name], display_values.get(name, ""),
                    help=explain(scalar_origins.get(name)))
            row_fields = ROW_FIELDS[kind]
            frame = pd.DataFrame(effective_rows(ws.trade, form), columns=list(row_fields)).fillna("")
            updated = st.data_editor(frame, num_rows="dynamic", hide_index=True,
                column_config={k: st.column_config.TextColumn(v) for k, v in row_fields.items()},
                use_container_width=True, key=f"simple-form-rows-{ws.case.case_id}-{kind.value}-{form.revision}")
            save = st.form_submit_button("保存补充内容")
        if save:
            rows = [{k: "" if pd.isna(row[k]) else str(row[k]) for k in row_fields}
                for _, row in updated.iterrows()]
            rows = [row for row in rows if any(row.values())]
            _run(st, lambda: FormService(repository).save(ws.case.case_id, actor, kind,
                values, rows, "人工填写文件内容", confirm=False), "补充内容已保存。")
    with st.expander("导出前查看已保存内容"):
        st.caption("此处展示当前已保存的数据；修改表单后请先保存，再生成文件。")
        st.dataframe([{"填写项": label, "内容": effective_values(ws.trade, form).get(name, "")}
            for name, label in FORM_FIELDS[kind].items()], hide_index=True, use_container_width=True)
        preview_rows = effective_rows(ws.trade, form)
        if preview_rows:
            st.dataframe(pd.DataFrame(preview_rows, columns=list(ROW_FIELDS[kind])).fillna(""),
                hide_index=True, use_container_width=True)
        if st.button("准备待人工核对的 Excel 预览", key=f"simple-preview-{ws.case.case_id}-{kind.value}"):
            try:
                preview = SimpleExportService(repository, blobs).preview(ws.case.case_id, actor, kind)
                st.download_button("下载待人工核对预览", preview.content,
                    f"{kind.value}-待人工核对预览.xlsx", mime=preview.media_type,
                    key=f"simple-preview-download-{ws.case.case_id}-{kind.value}")
                st.caption("预览未经过人工确认，不记为正式生成版本，也不会改变交易状态。")
            except CrossBotError as exc:
                st.error(exc.message)
    fingerprint = sha256(json.dumps({"trade": to_dict(ws.trade), "forms": to_dict(ws.document_forms),
        "sources": [(d.document_id, d.current_version_id, d.document_type.value, d.deleted_at)
                    for d in ws.documents], "artifacts": [a.artifact_id for a in ws.artifacts]},
        ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()
    confirmed = st.checkbox("我已人工核对当前资料，确认按现有内容生成",
        key=f"simple-confirm-{ws.case.case_id}-{fingerprint}-{kind.value}")
    if st.button("生成 Excel", disabled=not confirmed, type="primary"):
        _run(st, lambda: SimpleExportService(repository, blobs).generate(
            ws.case.case_id, actor, kind, human_confirmed=confirmed), "Excel 已生成，人工确认记录已保存。")
    items = list(reversed(ws.artifacts))
    if items:
        st.dataframe([{"文件": a.filename, "版本": a.version_number,
            "情况": "历史版本" if a.status == "stale" else "可下载",
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
        default=[items[0].artifact_id] if items else [],
        format_func=lambda key: next(a.filename + ("（历史版本）" if a.status == "stale" else "") for a in items if a.artifact_id == key))
    if artifact_ids:
        for key in artifact_ids:
            artifact = next(a for a in items if a.artifact_id == key)
            st.download_button("下载 " + artifact.filename,
                _download(st, documents, ws, actor, artifact_id=key), artifact.filename,
                mime=artifact.media_type, key=f"simple-download-{key}")
    if doc_ids or artifact_ids:
        if st.button("打包所选文件"):
            content = selected_bundle(ws, documents, actor, doc_ids, artifact_ids)
            st.download_button("下载所选文件 ZIP", content, ws.case.case_number + "-文件.zip", mime="application/zip")


def render_workspace(ws, actor, cases, documents, repository, blobs):
    import streamlit as st
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
    _edit_trade(st, ws, actor, cases)
    rows = file_inventory(ws)
    missing = [r["文件分类"] for r in rows if r["需要"] and not r["原件"] and not r["生成文件"]]
    if missing:
        st.warning("缺失文件：" + "、".join(missing))
    elif ws.required_document_types:
        st.success("当前清单所需文件均已留存。")
    else:
        st.info("尚未设置本单所需文件清单，当前仅展示已有资料；不表示资料完整。")
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    with st.expander("调整本单所需文件"):
        with st.form(f"simple-required-{ws.case.case_id}"):
            required = st.multiselect("所需文件", list(DOC_LABELS),
                default=ws.base_required_document_types, format_func=DOC_LABELS.get)
            save = st.form_submit_button("保存文件清单")
        if save:
            _run(st, lambda: cases.set_required_documents(ws.case.case_id, actor, required), "文件清单已更新。")
        st.caption("按这笔交易的实际要求选择；已有条件规则仍会补充适用文件。")
    files, exports = st.tabs(["文件分类与查看", "生成与导出"])
    with files:
        _files(st, ws, actor, documents, repository)
    with exports:
        _exports(st, ws, actor, documents, repository, blobs)
