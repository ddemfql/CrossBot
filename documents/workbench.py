"""Document collection services and the grouped transaction file workbench."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
from pathlib import PurePath
from zipfile import ZIP_DEFLATED, ZipFile

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (Actor, CaseStatus, DocumentStatus, DocumentType, FieldSource,
                         ReviewStatus, ReviewTask, Workspace, to_dict)
from review.policy import PERMISSIONS, audit, invalidate, require


def collect_document(repository, blobs, case_id: str, document_id: str, actor: Actor,
                     target_type=None, *, ocr_images=False):
    """Collect source structure without replacing confirmed values or originals."""
    from .intake import collect_source
    workspace = repository.get(case_id)
    require(workspace, actor, "parse")
    document = next((item for item in workspace.documents
                     if item.document_id == document_id and item.deleted_at is None), None)
    if document is None:
        raise CrossBotError(ErrorCode.NOT_FOUND, "资料不存在。")
    version = next((item for item in document.versions
                    if item.version_id == document.current_version_id), None)
    if version is None:
        raise CrossBotError(ErrorCode.NOT_FOUND, "资料当前版本不存在。")
    text, tables, fields, warnings = collect_source(
        blobs.get(version.storage_path), PurePath(version.original_filename).suffix.lower(),
        ocr_images=ocr_images)
    before = to_dict(version)
    # Human edits survive a new transcription; obsolete machine candidates do
    # not. Prior workspace snapshots retain the earlier machine extraction.
    previous = {field.name: field for field in version.fields
                if field.confirmed_by or field.source is FieldSource.HUMAN}
    version.fields = list(previous.values()) + [field for field in fields if field.name not in previous]
    version.source_text, version.source_tables = text, tables
    version.extraction_warnings = warnings
    version.parser_name, version.parser_version = "source-intake", "w3-intake-1"
    version.status = DocumentStatus.PARSED if text or tables or fields else DocumentStatus.PARSE_FAILED
    version.parse_error = None if version.status is DocumentStatus.PARSED else "未采集到结构化内容，请查看原件并人工补录。"
    document.status = version.status
    changed = to_dict(version) != before
    unconfirmed = [field.name for field in version.fields if not field.confirmed_by]
    if unconfirmed and not any(task.document_id == document_id
            and task.status is ReviewStatus.PENDING
            and set(task.field_names) == set(unconfirmed) for task in workspace.review_tasks):
        task = ReviewTask(case_id=case_id, document_id=document_id,
                          required=document.required, field_names=unconfirmed)
        workspace.review_tasks.append(task)
        workspace.case.review_task_ids.append(task.task_id)
        changed = True
    from .catalog import FORM_FIELDS
    from .prefill import prefill_form, refresh_prefills
    changed = refresh_prefills(workspace) or changed
    target = DocumentType(target_type) if target_type is not None else document.document_type
    if target in FORM_FIELDS:
        existing = next((form for form in workspace.document_forms if form.document_type is target), None)
        ids = list(existing.source_document_ids) if existing else []
        changed = prefill_form(workspace, target, [*ids, document_id]) or changed
    if not changed:
        return workspace
    invalidate(workspace, actor, "采集内容或预填来源变化，请核对当前版本。")
    workspace.case.status = CaseStatus.PENDING_REVIEW
    audit(workspace, actor, "source_collected", "document_version", version.version_id,
          after={"table_count": len(tables), "field_count": len(version.fields)},
          reason="采集原文与表格；保留人工已确认字段。")
    repository.save(workspace, expected_revision=workspace.revision)
    return workspace


def reclassify(repository, case_id: str, document_id: str, actor: Actor,
               kind: DocumentType, responsibility: str, reason: str):
    workspace = repository.get(case_id)
    require(workspace, actor, "upload")
    if responsibility not in {"seller", "external"} or not reason.strip():
        raise ValidationError("请选择资料分组并填写分类依据。")
    try:
        kind = DocumentType(kind)
    except ValueError:
        raise ValidationError("资料类型无效。") from None
    document = next((item for item in workspace.documents
                     if item.document_id == document_id and item.deleted_at is None), None)
    if document is None:
        raise CrossBotError(ErrorCode.NOT_FOUND, "资料不存在。")
    before = {"document_type": document.document_type.value, "responsibility": document.responsibility}
    invalidate(workspace, actor, reason.strip())
    document.document_type, document.responsibility = kind, responsibility
    document.required = kind in workspace.required_document_types
    document.classification_note = reason.strip()
    for form in workspace.document_forms:
        if form.document_type is not kind and document_id in form.source_document_ids:
            form.source_document_ids.remove(document_id)
            form.confirmed_by = form.confirmed_at = None
            form.revision += 1
    matching_form = next((form for form in workspace.document_forms if form.document_type is kind), None)
    if matching_form is not None and document_id not in matching_form.source_document_ids:
        matching_form.source_document_ids.append(document_id)
        matching_form.confirmed_by = matching_form.confirmed_at = None
        matching_form.revision += 1
    from .prefill import refresh_prefills
    refresh_prefills(workspace)
    audit(workspace, actor, "document_reclassified", "document", document_id,
          before=before, after={"document_type": kind.value, "responsibility": responsibility},
          reason=reason.strip())
    repository.save(workspace, expected_revision=workspace.revision)
    return workspace


def inventory(workspace: Workspace, kinds, responsibility: str | None = None) -> list[dict]:
    from .catalog import DOC_LABELS, SELLER_TYPES
    result = []
    for kind in kinds:
        form = next((item for item in workspace.document_forms if item.document_type is kind), None)
        linked = set(form.source_document_ids if form else [])
        documents = [doc for doc in workspace.documents if doc.deleted_at is None
                     and (doc.document_type is kind or doc.document_id in linked)
                     and (responsibility is None or (doc.responsibility or
                          ("seller" if doc.document_type in SELLER_TYPES else "external")) == responsibility)]
        artifacts = [item for item in workspace.artifacts if item.document_type is kind]
        result.append({"文件名称": DOC_LABELS[kind], "原件数": len(documents),
                       "已填字段": sum(bool(value) for value in form.values.values()) if form else 0,
                       "明细行": len(form.rows) if form else 0,
                       "填写确认": form.confirmed_by or "" if form else "",
                       "生成版本数": len(artifacts)})
    return result


def export_bundle(workspace: Workspace, documents, actor: Actor) -> bytes:
    """Export named files and structure; hashes remain the storage authority."""
    from .catalog import DOC_LABELS, SELLER_TYPES, FORM_FIELDS, ROW_FIELDS
    from .forms import get_form, render_blank, render_form
    from .lifecycle import assistance_rows, render_dossier, rows_csv
    require(workspace, actor, "download")
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("结构化数据.json", json.dumps(to_dict(workspace), ensure_ascii=False,
                                                     indent=2, allow_nan=False))
        archive.writestr("订单档案.html", render_dossier(workspace))
        records = assistance_rows(workspace)
        archive.writestr("虚拟业务员整理记录.json", json.dumps(to_dict(
            getattr(workspace, "assistance_records", [])), ensure_ascii=False, indent=2))
        archive.writestr("虚拟业务员整理记录.csv", rows_csv(records,
            ["时间", "整理方式", "来源文件", "来源版本", "来源位置", "填写字段", "原值", "建议填写值", "填写依据", "执行身份"]))
        for kind, label in DOC_LABELS.items():
            folder = "我方需要准备的文件" if kind in SELLER_TYPES else "外部资料"
            base = f"{folder}/{label}/"
            form = get_form(workspace, kind)
            payload = to_dict(form)
            payload["values"] = {name: form.values.get(name, "") for name in FORM_FIELDS.get(kind, {})}
            archive.writestr(base + "表单数据.json", json.dumps(payload, ensure_ascii=False, indent=2))
            field_rows = [{"字段": name_label, "值": form.values.get(name, ""),
                "填写核对人": form.confirmed_by or "", "来源原件": "; ".join(form.source_document_ids)}
                for name, name_label in FORM_FIELDS.get(kind, {}).items()]
            archive.writestr(base + "表单字段.csv", rows_csv(field_rows,
                ["字段", "值", "填写核对人", "来源原件"]))
            row_labels = ROW_FIELDS.get(kind, {})
            archive.writestr(base + "表单明细.csv", rows_csv([
                {row_labels[key]: row.get(key, "") for key in row_labels} for row in form.rows],
                list(row_labels.values())))
            if kind in SELLER_TYPES:
                archive.writestr(base + "空白填写模板.html", render_blank(kind).content)
                if any(form.values.values()) or form.rows:
                    rendered = render_form(kind, form)
                    state = "填写内容已核对，仍为填写稿" if form.confirmed_by else "未人工核对的整理填写稿"
                    html = rendered.content.decode("utf-8").replace("<body>",
                        '<body><p class="draft">' + state + '</p>', 1)
                    archive.writestr(base + "当前填写稿.html", html.encode("utf-8"))
        for document in workspace.documents:
            if document.deleted_at is not None:
                continue
            group = document.responsibility or ("seller" if document.document_type in SELLER_TYPES else "external")
            folder = "我方需要准备的文件" if group == "seller" else "外部资料"
            label = DOC_LABELS.get(document.document_type, "其他资料")
            for version in document.versions:
                content = documents.download(workspace.case.case_id, actor, document.document_id,
                                             version.version_id)
                filename = PurePath(version.original_filename).name.replace("\\", "_")
                archive.writestr(f"{folder}/{label}/{document.document_id}/v{version.version_number}/{filename}", content)
        for artifact in workspace.artifacts:
            content = documents.download(workspace.case.case_id, actor, artifact_id=artifact.artifact_id)
            label = DOC_LABELS.get(artifact.document_type, artifact.document_type.value)
            archive.writestr(f"我方需要准备的文件/{label}/生成草稿/{artifact.filename}", content)
    return output.getvalue()


def _execute(st, action, success):
    try:
        action()
        st.success(success)
        st.rerun()
    except CrossBotError as exc:
        st.error(exc.message)
    except (TypeError, ValueError) as exc:
        st.error(str(exc))


def _source_preview(st, workspace, document, documents, actor, context=""):
    version_id = st.selectbox("文件版本", [item.version_id for item in reversed(document.versions)],
        format_func=lambda key: next(f"v{v.version_number} · {v.original_filename}" for v in document.versions if v.version_id == key),
        key=f"center-version-{context}-{document.document_id}")
    version = next(item for item in document.versions if item.version_id == version_id)
    st.caption(version.original_path or version.original_filename)
    st.write({"上传人": version.uploaded_by, "来源方": version.source_party,
              "有效期": version.expires_on or "", "状态": version.status.value,
              "SHA-256": version.sha256})
    if version.parse_error:
        st.warning(version.parse_error)
    for warning in version.extraction_warnings:
        st.caption(warning)
    content = documents.download(workspace.case.case_id, actor, document.document_id, version.version_id)
    st.download_button("下载原始文件", content, version.original_filename,
                       mime=version.media_type, key=f"center-download-{context}-{version.version_id}")
    preview = st.checkbox("显示原件预览", key=f"center-preview-{context}-{version.version_id}")
    if preview:
        if version.media_type.startswith("image/"):
            st.image(content, caption=version.original_filename, use_container_width=True)
        elif version.media_type == "application/pdf":
            try:
                import pypdfium2 as pdfium
                pdf = pdfium.PdfDocument(content)
                try:
                    number = st.number_input("PDF 页码", min_value=1, max_value=len(pdf),
                        value=1, step=1, key=f"center-pdf-page-{context}-{version.version_id}")
                    page = pdf[int(number) - 1]
                    bitmap = page.render(scale=1.4)
                    try:
                        st.image(bitmap.to_pil().copy(), caption=f"原件第 {number} / {len(pdf)} 页",
                                 use_container_width=True)
                    finally:
                        bitmap.close()
                        page.close()
                finally:
                    pdf.close()
            except Exception:
                st.warning("暂时无法预览此 PDF，请下载原件查看。")
        elif not version.source_text and not version.source_tables:
            st.info("请下载原件查看，或采集文件内容后查看下方原文和表格。")
    if version.source_tables:
        table_index = st.selectbox("工作表 / 文档表格", list(range(len(version.source_tables))),
            format_func=lambda index: version.source_tables[index].name or f"表格 {index + 1}",
            key=f"center-table-{context}-{version.version_id}")
        table = version.source_tables[table_index]
        st.caption(f"保留原文件单元格顺序 · {len(table.rows)} 行")
        width = max((len(row) for row in table.rows), default=0)
        import pandas as pd
        st.dataframe(pd.DataFrame([list(row) + [""] * (width - len(row)) for row in table.rows]),
                     use_container_width=True, height=420)
    if version.source_text:
        st.text_area("采集原文", version.source_text, height=260,
                     key=f"center-text-{context}-{version.version_id}", disabled=True)
    if not version.source_text and not version.source_tables and not version.fields:
        st.caption("结构化内容暂空，可采集或人工填写。")
    return version


def _field_review(st, workspace, document, version, actor, reviews, context=""):
    from review.service import MANUAL_FIELD_NAMES
    case_id = workspace.case.case_id
    if version.fields:
        st.dataframe([{"字段": field.name, "机器候选": str(field.value) if field.value is not None else "",
            "虚拟业务员建议": str(field.assisted_value) if field.assisted_value is not None else "",
            "建议来源依据": field.assistance_note or "",
            "人工确认": str(field.confirmed_value) if field.confirmed_by else "",
            "确认人": field.confirmed_by or "", "页码": str(field.page_number) if field.page_number is not None else "",
            "工作表": field.sheet_name or "", "单元格": field.cell_reference or "",
            "依据": field.note or ""} for field in version.fields], hide_index=True,
            use_container_width=True)
    current = document.current_version_id == version.version_id
    if current and version.fields and set(actor.roles) & PERMISSIONS["confirm_field"]:
        selected = st.selectbox("选择待核对字段", [field.name for field in version.fields],
                                key=f"center-selected-field-{context}-{version.version_id}")
        with st.form(f"center-confirm-field-{context}-{version.version_id}"):
            original = next(field for field in version.fields if field.name == selected)
            proposed = (original.confirmed_value if original.confirmed_by else
                        original.assisted_value if original.assisted_value is not None else original.value)
            value = st.text_input("最终确认值", "" if proposed is None else str(proposed),
                                  key=f"center-final-value-{context}-{version.version_id}-{selected}")
            reason = st.text_input("核对依据")
            submitted = st.form_submit_button("确认选中字段")
        if submitted:
            _execute(st, lambda: reviews.confirm_field(case_id, document.document_id, selected,
                     value, actor, reason), "字段已确认，原机器候选保留。")
    if current and set(actor.roles) & PERMISSIONS["manual_field"]:
        with st.form(f"center-manual-{context}-{version.version_id}"):
            name = st.selectbox("人工补录字段", sorted(MANUAL_FIELD_NAMES))
            value = st.text_input("字段值")
            reason = st.text_input("补录依据")
            submitted = st.form_submit_button("保存人工字段")
        if submitted:
            _execute(st, lambda: reviews.enter_manual_field(case_id, document.document_id,
                     name, value, actor, reason), "人工字段已保存。")


def _form_editor(st, workspace, kind, actor, repository, documents, context=""):
    from .catalog import FORM_FIELDS, ROW_FIELDS, SELLER_TYPES, DOC_LABELS
    from .forms import FormService, get_form, render_blank
    form = get_form(workspace, kind)
    field_labels, row_labels = FORM_FIELDS.get(kind, {}), ROW_FIELDS.get(kind, {})
    st.markdown("**结构化表单**")
    if form.source_document_ids:
        names = [doc.title for doc in workspace.documents if doc.document_id in form.source_document_ids]
        st.caption("关联来源：" + "；".join(names))
    st.caption(f"表单版本 {form.revision} · " + (f"填写内容由 {form.confirmed_by} 核对" if form.confirmed_by else "待人工核对；缺失内容留空"))
    can_edit = bool(set(actor.roles) & PERMISSIONS["manual_field"]) and workspace.case.status is not CaseStatus.CLOSED
    if field_labels:
        with st.form(f"center-form-{context}-{workspace.case.case_id}-{kind.value}-{form.revision}"):
            columns = st.columns(2)
            values = {}
            for index, (name, label) in enumerate(field_labels.items()):
                multiline = (name.endswith(("_text", "_terms", "_requirements", "_notes")) or
                             name in {"notes", "observed_result", "technical_requirements"})
                widget = columns[index % 2].text_area if multiline else columns[index % 2].text_input
                values[name] = widget(label, value=form.values.get(name, ""),
                    key=f"center-value-{context}-{workspace.case.case_id}-{kind.value}-{form.revision}-{name}", disabled=not can_edit)
            rows = deepcopy(form.rows)
            if row_labels:
                import pandas as pd
                rows_frame = pd.DataFrame([{key: row.get(key, "") for key in row_labels}
                                          for row in rows], columns=list(row_labels))
                edited = st.data_editor(rows_frame, num_rows="dynamic", use_container_width=True,
                    column_config={key: st.column_config.TextColumn(label) for key, label in row_labels.items()},
                    hide_index=True, disabled=not can_edit,
                    key=f"center-rows-{context}-{workspace.case.case_id}-{kind.value}-{form.revision}")
                rows = [{key: "" if pd.isna(row[key]) else str(row[key]) for key in row_labels}
                        for _, row in edited.iterrows()]
                rows = [row for row in rows if any(row.values())]
            reason = st.text_input("填写 / 修订依据", disabled=not can_edit)
            buttons = st.columns(2)
            saved = buttons[0].form_submit_button("保存填写内容", disabled=not can_edit)
            confirmed = buttons[1].form_submit_button("人工核对并保存", disabled=not can_edit)
        if saved or confirmed:
            _execute(st, lambda: FormService(repository).save(workspace.case.case_id, actor,
                kind, values, rows, reason, confirm=confirmed), "表单已保存。")
    else:
        st.caption("暂无填写内容。")
    if kind in SELLER_TYPES:
        blank = render_blank(kind)
        st.download_button("下载空白填写模板", blank.content,
            file_name=DOC_LABELS[kind] + ".html", mime=blank.media_type,
            key=f"center-blank-{context}-{workspace.case.case_id}-{kind.value}")
        if set(actor.roles) & PERMISSIONS["generate"]:
            if st.button("生成当前文件草稿", key=f"center-generate-{context}-{workspace.case.case_id}-{kind.value}",
                         disabled=workspace.case.status is CaseStatus.CLOSED):
                _execute(st, lambda: documents.generate(workspace.case.case_id, actor, kind), "新草稿已生成，请核对后使用。")
    else:
        st.caption("此类文件由相应主体提供；这里保存原件和核对信息。")


def render_document_center(workspace, actor, repository, blobs, documents, reviews):
    import streamlit as st
    from .catalog import DOC_LABELS, SELLER_TYPES, EXTERNAL_TYPES
    st.caption("按具体文件整理原件、填写表单和来源数据；缺失资料留空。")
    case_id = workspace.case.case_id
    st.download_button("下载全部结构化数据 JSON", json.dumps(to_dict(workspace), ensure_ascii=False,
        indent=2, allow_nan=False).encode("utf-8"), f"{workspace.case.case_number}-结构化数据.json",
        mime="application/json", key=f"center-json-{case_id}")
    if st.button("准备按名称打包全部原件与草稿", key=f"center-bundle-{case_id}"):
        try:
            content = export_bundle(workspace, documents, actor)
            st.download_button("下载我方文件和外部资料 ZIP", content,
                f"{workspace.case.case_number}-文件与表单.zip", mime="application/zip",
                key=f"center-bundle-download-{case_id}")
        except CrossBotError as exc:
            st.error(exc.message)
    groups = st.tabs(["我方需要准备的文件", "外部资料"])
    for tab, kinds, group in zip(groups, (SELLER_TYPES, EXTERNAL_TYPES), ("seller", "external")):
        with tab:
            group_documents = [doc for doc in workspace.documents if doc.deleted_at is None and
                (doc.responsibility or ("seller" if doc.document_type in SELLER_TYPES else "external")) == group]
            kinds = tuple(dict.fromkeys((*kinds, *[doc.document_type for doc in group_documents])))
            st.dataframe(inventory(workspace, kinds, group), hide_index=True, use_container_width=True)
            if group_documents:
                st.markdown("**原件索引**")
                st.dataframe([{"文件名称": DOC_LABELS.get(doc.document_type, doc.document_type.value),
                    "原始文件": doc.title, "原始路径": next((v.original_path for v in doc.versions
                    if v.version_id == doc.current_version_id), ""), "版本数": len(doc.versions)}
                    for doc in group_documents], hide_index=True, use_container_width=True)
            initial = next((index for index, candidate in enumerate(kinds)
                            if any(doc.document_type is candidate for doc in group_documents)), 0)
            kind = st.selectbox("查看具体文件", kinds, format_func=lambda item: DOC_LABELS[item],
                                key=f"center-kind-{case_id}-{group}", index=initial)
            form = next((item for item in workspace.document_forms if item.document_type is kind), None)
            linked_ids = set(form.source_document_ids if form else [])
            available = [doc for doc in workspace.documents if doc.deleted_at is None
                         and (doc.document_type is kind or doc.document_id in linked_ids)
                         and doc in group_documents]
            detail_tabs = st.tabs(["原始文件与采集信息", "填写表单", "生成版本"])
            with detail_tabs[0]:
                if set(actor.roles) & PERMISSIONS["upload"]:
                    with st.form(f"center-upload-{group}-{case_id}-{kind.value}", clear_on_submit=True):
                        uploaded = st.file_uploader("添加原始文件", type=["pdf", "xls", "xlsx", "docx", "jpg", "jpeg", "png", "webp", "txt", "csv"])
                        revision_of = st.selectbox("版本归属", [None, *[doc.document_id for doc in available]],
                            format_func=lambda key: "新资料" if key is None else next(doc.title for doc in available if doc.document_id == key))
                        source = st.text_input("文件来源方")
                        expiry = st.date_input("有效期（如适用）", value=None)
                        submitted = st.form_submit_button("保存原件", disabled=workspace.case.status is CaseStatus.CLOSED)
                    if submitted and uploaded:
                        _execute(st, lambda: documents.upload(case_id, actor, uploaded.name,
                            uploaded.getvalue(), kind, document_id=revision_of, source_party=source,
                            expires_on=expiry.isoformat() if expiry else None), "原件已保存，可继续采集文件内容。")
                if not available:
                    st.info("原始文件暂空。")
                else:
                    selected_id = st.selectbox("选择原始文件", [doc.document_id for doc in available],
                        format_func=lambda key: next(doc.title for doc in available if doc.document_id == key),
                        key=f"center-doc-{group}-{case_id}-{kind.value}")
                    document = next(doc for doc in available if doc.document_id == selected_id)
                    version = _source_preview(st, workspace, document, documents, actor, group)
                    if set(actor.roles) & PERMISSIONS["parse"] and document.current_version_id == version.version_id:
                        if st.button("采集文件原文、表格和候选字段", key=f"center-collect-{group}-{case_id}-{kind.value}-{version.version_id}", disabled=workspace.case.status is CaseStatus.CLOSED):
                            _execute(st, lambda: collect_document(repository, blobs, case_id,
                                document.document_id, actor), "文件内容已采集，候选值等待人工核对。")
                    _field_review(st, workspace, document, version, actor, reviews, group)
                    if set(actor.roles) & PERMISSIONS["upload"]:
                        with st.form(f"center-classify-{group}-{case_id}-{kind.value}-{document.document_id}"):
                            corrected_kind = st.selectbox("修正文件类型", list(DOC_LABELS),
                                index=list(DOC_LABELS).index(document.document_type), format_func=lambda item: DOC_LABELS[item])
                            responsibility = st.selectbox("文件分组", ["seller", "external"],
                                index=1 if document.responsibility == "external" else 0,
                                format_func=lambda value: "我方需要准备的文件" if value == "seller" else "外部资料")
                            reason = st.text_input("分类依据")
                            changed = st.form_submit_button("保存分类", disabled=workspace.case.status is CaseStatus.CLOSED)
                        if changed:
                            _execute(st, lambda: reclassify(repository, case_id, document.document_id,
                                actor, corrected_kind, responsibility, reason), "资料分类已保存。")
                    if "admin" in actor.roles:
                        with st.form(f"center-delete-{group}-{case_id}-{kind.value}-{document.document_id}"):
                            reason = st.text_input("移除资料原因")
                            checked = st.checkbox("确认移出当前工作区并保留原件和历史")
                            deleted = st.form_submit_button("移除资料", disabled=workspace.case.status is CaseStatus.CLOSED)
                        if deleted:
                            if checked:
                                _execute(st, lambda: documents.delete_document(case_id, document.document_id,
                                    actor, reason), "资料已移出当前工作区，原件及历史保留。")
                            else:
                                st.error("请先确认移除操作。")
            with detail_tabs[1]:
                _form_editor(st, workspace, kind, actor, repository, documents, group)
            with detail_tabs[2]:
                artifacts = [item for item in workspace.artifacts if item.document_type is kind]
                if not artifacts:
                    st.caption("生成文件暂空。")
                for artifact in reversed(artifacts):
                    st.write(f"v{artifact.version_number} · {artifact.filename} · {artifact.status}")
                    st.caption(f"交易修订 {artifact.input_revision} · 模板 {artifact.template_id} {artifact.template_version} · SHA-256 {artifact.sha256}")
                    content = documents.download(case_id, actor, artifact_id=artifact.artifact_id)
                    st.download_button("下载此版本", content, artifact.filename, mime=artifact.media_type,
                        key=f"center-artifact-{group}-{artifact.artifact_id}")
                    if st.checkbox("预览此草稿", key=f"center-artifact-preview-{group}-{artifact.artifact_id}"):
                        if artifact.media_type == "text/html":
                            import streamlit.components.v1 as components
                            components.html(content.decode("utf-8"), height=650, scrolling=True)
                        elif artifact.filename.lower().endswith(".xlsx"):
                            from openpyxl import load_workbook
                            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
                            try:
                                sheet_name = st.selectbox("草稿工作表", workbook.sheetnames,
                                    key=f"center-artifact-sheet-{group}-{artifact.artifact_id}")
                                sheet = workbook[sheet_name]
                                st.dataframe([["" if cell is None else str(cell) for cell in row]
                                              for row in sheet.iter_rows(values_only=True)],
                                             use_container_width=True)
                            finally:
                                workbook.close()
