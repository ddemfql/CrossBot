"""Evidence-based lifecycle views and a portable transaction dossier."""
from __future__ import annotations

import csv
from html import escape
from io import StringIO
import json
from pathlib import PurePath
from urllib.parse import quote

from core.models import DocumentType as D, to_dict
from .catalog import DOC_LABELS, FORM_FIELDS, ROW_FIELDS, SELLER_TYPES


STAGES = (
    ("询盘报价", (D.QUOTATION,), "客户需求、产品报价、报价有效期、价格及交付条件"),
    ("订单确认", (D.PROFORMA_INVOICE, D.SALES_CONTRACT, D.ORDER_CONFIRMATION, D.PURCHASE_ORDER, D.PURCHASE_CONTRACT, D.OTHER),
     "买卖双方、订单条款、客户 PO、合同及待分类资料"),
    ("产品合规", (D.PRODUCT_SPEC, D.PACKAGING_ARTWORK, D.COMPLIANCE_DRAFT, D.COMPLIANCE, D.TEST_REPORT),
     "规格参数、包装标签、真实检测/合规材料、适用要求和核对依据"),
    ("生产验货", (D.PRODUCTION_ORDER, D.INSPECTION_RECORD, D.INSPECTION_REPORT, D.PHOTO),
     "生产与包装要求、实际验货记录、第三方报告及出货照片"),
    ("出运单据", (D.COMMERCIAL_INVOICE, D.PACKING_LIST, D.COMBINED_INVOICE_PACKING_LIST),
     "实际货物明细、价格、件数、毛净重、唛头、单据版本和审批证据"),
    ("运输申报", (D.BOOKING_REQUEST, D.SHIPPING_INSTRUCTION, D.CUSTOMS_WORKSHEET,
                   D.ISF_WORKSHEET, D.BILL_OF_LADING, D.AIR_WAYBILL,
                   D.CERTIFICATE_OF_ORIGIN, D.INSURANCE_POLICY, D.ISF_RECEIPT, D.FORWARDER_RECEIPT),
     "订舱/运输指示、申报准备信息、外部运输/原产地/保险/申报文件及业务节点"),
    ("收款归档", (D.PAYMENT_NOTICE, D.STATEMENT_OF_ACCOUNT, D.BANK_RECEIPT),
     "付款通知、对账明细、银行凭据、已登记收款、未收余额和归档审计"),
)


def transaction_title(workspace) -> str:
    if workspace.import_context and workspace.import_context.archive_name:
        return workspace.import_context.archive_name
    return workspace.trade.order_no or workspace.case.case_number


def stage_summary(workspace) -> list[dict]:
    """Describe retained evidence without inferring that a trade stage occurred."""
    summaries = []
    for stage_name, kinds, retained in STAGES:
        forms = [form for form in workspace.document_forms if form.document_type in kinds]
        linked_ids = {key for form in forms for key in form.source_document_ids}
        documents = [doc for doc in workspace.documents if doc.deleted_at is None and
                     (doc.document_type in kinds or doc.document_id in linked_ids)]
        versions = [version for document in documents for version in document.versions]
        current = [version for document in documents for version in document.versions
                   if version.version_id == document.current_version_id]
        artifacts = [artifact for artifact in workspace.artifacts if artifact.document_type in kinds]
        available_types = {doc.document_type for doc in documents}
        available_types.update(artifact.document_type for artifact in artifacts
                               if artifact.status in {"draft", "approved"})
        for form in forms:
            if form.source_document_ids and any(doc.document_id in form.source_document_ids for doc in documents):
                available_types.add(form.document_type)
        missing = [DOC_LABELS[kind] for kind in kinds if kind in workspace.required_document_types
                   and kind not in available_types]
        empty = [DOC_LABELS[kind] for kind in kinds if kind not in available_types
                 and not any(form.document_type is kind and (any(form.values.values()) or form.rows)
                             for form in forms)]
        has_records = bool(documents or artifacts or any(any(form.values.values()) or form.rows for form in forms))
        if stage_name == "收款归档":
            has_records = has_records or bool(workspace.payments)
        summaries.append({"stage": stage_name, "kinds": kinds,
            "status": "已有留存资料，按项核对" if has_records else "暂无留存资料",
            "document_count": len(documents), "version_count": len(versions),
            "table_count": sum(len(version.source_tables) for version in current),
            "candidate_count": sum(len(version.fields) for version in current),
            "confirmed_count": sum(bool(field.confirmed_by) for version in current for field in version.fields),
            "filled_form_count": sum(bool(any(form.values.values()) or form.rows) for form in forms),
            "confirmed_form_count": sum(bool(form.confirmed_by) for form in forms),
            "artifact_count": len(artifacts), "missing_required": missing,
            "empty_catalog": empty, "data_retained": retained,
            "formats": sorted({artifact.filename.rsplit(".", 1)[-1].upper() for artifact in artifacts})})
    return summaries


def assistance_rows(workspace) -> list[dict]:
    result = []
    names = {document.document_id: document.title for document in workspace.documents}
    for record in getattr(workspace, "assistance_records", []):
        def value(name):
            item = getattr(record, name, "")
            if item is None:
                return ""
            return item.isoformat() if hasattr(item, "isoformat") else str(item)
        result.append({"时间": value("created_at"), "整理方式": value("method"),
            "来源文件": names.get(value("document_id"), value("document_id")),
            "来源版本": value("version_id"), "来源位置": value("location"),
            "填写字段": value("field_name"), "原值": value("previous_value"),
            "建议填写值": value("proposed_value"), "填写依据": value("reason"),
            "执行身份": value("actor_kind")})
    return result


def rows_csv(rows: list[dict], columns=None) -> bytes:
    output = StringIO(newline="")
    columns = list(columns or (rows[0].keys() if rows else []))
    writer = csv.DictWriter(output, fieldnames=columns)
    if columns:
        writer.writeheader()
        for row in rows:
            # Protect spreadsheet readers while preserving exact values in JSON.
            writer.writerow({key: ("'" + str(value) if str(value).startswith(("=", "+", "-", "@"))
                                   else value) for key, value in row.items() if key in columns})
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _table(headers, rows):
    head = "".join(f"<th>{escape(str(value))}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape('' if value is None else str(value))}</td>"
                                      for value in row) + "</tr>" for row in rows)
    return f"<div class='table'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def render_dossier(workspace) -> bytes:
    """Readable local HTML archive, containing no implied approval or signatures."""
    title = transaction_title(workspace)
    parts = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
        f"<title>{escape(title)} · 订单档案</title>",
        "<style>body{font:15px/1.6 Arial,'Microsoft YaHei',sans-serif;max-width:1280px;margin:32px auto;padding:0 24px;color:#233e38}h1{font-size:28px}h2{margin-top:36px}h3{margin-top:26px}.table{overflow:auto}table{border-collapse:collapse;width:100%;margin:14px 0}th,td{border:1px solid #c4d3cd;padding:8px;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#e7efeb}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f5;padding:16px}.note{color:#735226;background:#fff5df;padding:16px}@media print{body{margin:12mm;max-width:none}.table{overflow:visible}h2,h3{break-after:avoid}}</style>",
        f"<h1>{escape(title)}</h1><p>工作区编号：{escape(workspace.case.case_number)} · 修订 {workspace.revision}</p>",
        "<p class='note'>这是当前留存资料与填写内容的档案。资料存在不表示阶段已完成；机器采集、虚拟业务员建议和人工确认分别保留。缺失内容保持空白。</p>"]
    if workspace.import_context:
        context = workspace.import_context
        parts.append(_table(["原始资料包", "来源声明的交易状态", "来源声明的收款状态", "包 SHA-256"],
            [[context.archive_name, context.reported_trade_state, context.reported_receivable_state, context.archive_sha256]]))
    parts.append(_table(["工作台单证状态", "已登记业务节点", "交易资料确认人", "人工批准记录数"],
        [[workspace.case.status.value, workspace.milestone.value, workspace.trade.confirmed_by or "", len(workspace.approvals)]]))
    parts.append("<h2>外贸流程与留存情况</h2>")
    parts.append(_table(["阶段", "资料情况", "原件数", "表格数", "已填表单", "本单必需待补", "可导出格式"],
        [[item["stage"], item["status"], item["document_count"], item["table_count"], item["filled_form_count"],
          "；".join(item["missing_required"]), "原始格式 / JSON / CSV / HTML" +
          (" / " + " / ".join(item["formats"]) if item["formats"] else "")]
         for item in stage_summary(workspace)]))
    parts.append("<h2>交易主数据</h2><pre>" + escape(json.dumps(to_dict(workspace.trade), ensure_ascii=False, indent=2)) + "</pre>")
    for stage_name, kinds, retained in STAGES:
        parts.append(f"<h2>{escape(stage_name)}</h2><p>留存内容：{escape(retained)}</p>")
        for kind in kinds:
            form = next((item for item in workspace.document_forms if item.document_type is kind), None)
            parts.append(f"<h3>{escape(DOC_LABELS[kind])}</h3>")
            values = form.values if form else {}
            labels = FORM_FIELDS.get(kind, {})
            parts.append("<p>填写核对人：" + escape(form.confirmed_by or "" if form else "") + "</p>")
            parts.append(_table(["字段", "留存值"], [[label, values.get(name, "")] for name, label in labels.items()]))
            if form and form.rows:
                row_labels = ROW_FIELDS.get(kind, {})
                parts.append(_table(list(row_labels.values()), [[row.get(key, "") for key in row_labels] for row in form.rows]))
    parts.append("<h2>原始文件与采集结构</h2>")
    parts.append("<p>原件保持原始格式和内容；以下原件链接在整笔订单 ZIP 解压后可用。</p>")
    for document in workspace.documents:
        if document.deleted_at is not None:
            continue
        parts.append(f"<h3>{escape(DOC_LABELS.get(document.document_type, document.document_type.value))} · {escape(document.title)}</h3>")
        for version in document.versions:
            parts.append(_table(["版本", "文件名称", "原始路径", "SHA-256", "采集状态"],
                [[version.version_number, version.original_filename, version.original_path, version.sha256, version.status.value]]))
            group = document.responsibility or ("seller" if document.document_type in SELLER_TYPES else "external")
            folder = "我方需要准备的文件" if group == "seller" else "外部资料"
            label = DOC_LABELS.get(document.document_type, "其他资料")
            filename = PurePath(version.original_filename).name.replace("\\", "_")
            original_link = f"{folder}/{label}/{document.document_id}/v{version.version_number}/{filename}"
            parts.append(f"<p><a href='{escape(quote(original_link, safe='/'), quote=True)}'>打开此版本原件</a></p>")
            parts.append(_table(["上传时间", "上传人", "来源方", "有效期", "解析说明"],
                [[version.uploaded_at.isoformat(), version.uploaded_by, version.source_party,
                  version.expires_on or "", version.parse_error or ""]]))
            if version.fields:
                parts.append(_table(["字段", "机器候选", "虚拟业务员建议", "建议来源依据", "人工值", "人工确认人", "页码/工作表/单元格", "候选/核对说明"],
                    [[field.name, field.value, field.assisted_value, field.assistance_note or "",
                      field.confirmed_value if field.confirmed_by else "", field.confirmed_by or "",
                      " / ".join(str(item) for item in (field.page_number, field.sheet_name, field.cell_reference) if item), field.note or ""]
                     for field in version.fields]))
            for source_table in version.source_tables:
                parts.append(f"<h4>{escape(source_table.name)}</h4>")
                width = max((len(row) for row in source_table.rows), default=0)
                parts.append(_table([str(index + 1) for index in range(width)],
                    [list(row) + [""] * (width - len(row)) for row in source_table.rows]))
            if version.source_text:
                parts.append("<pre>" + escape(version.source_text) + "</pre>")
            if version.extraction_warnings:
                parts.append("<p>采集说明：" + escape("；".join(version.extraction_warnings)) + "</p>")
    parts.append("<h2>虚拟业务员整理记录</h2>")
    records = assistance_rows(workspace)
    if records:
        parts.append(_table(list(records[0]), [list(record.values()) for record in records]))
    else:
        parts.append("<p>暂无留存记录。</p>")
    parts.append("<h2>人工收款登记</h2>" + _table(["日期", "币种", "金额", "流水参考", "登记人"],
        [[item.received_on, item.currency, item.amount, item.reference, item.recorded_by] for item in workspace.payments]))
    parts.append("<h2>生成文件版本</h2>" + _table(["文件", "版本", "状态", "模板", "交易修订", "表单修订", "输出 SHA-256"],
        [[item.filename, item.version_number, item.status, item.template_id + " " + item.template_version,
          item.input_revision, item.form_revision, item.sha256] for item in workspace.artifacts]))
    parts.append("<h2>人工批准记录</h2>" + _table(["批准人", "时间", "交易修订", "意见", "规则摘要", "文件版本与产物哈希"],
        [[item.actor_id, item.approved_at.isoformat(), item.input_revision, item.reason,
          item.checks_sha256, json.dumps({"document_versions": item.document_versions,
              "artifact_hashes": item.artifact_hashes}, ensure_ascii=False)] for item in workspace.approvals]))
    parts.append("<h2>岗位待办</h2>" + _table(["任务", "岗位", "办理人", "状态", "说明"],
        [[item.title, item.role, item.assignee_id or "", item.status, item.note] for item in workspace.tasks]))
    parts.append("<h2>审计历史</h2>" + _table(["时间", "人员", "操作", "对象", "依据"],
        [[event.occurred_at.isoformat(), event.actor_id, event.action, event.entity_type, event.reason or ""] for event in workspace.audit]))
    parts.append("</html>")
    return "\n".join(parts).encode("utf-8")


def render_lifecycle(workspace, actor, repository=None, blobs=None, documents=None, reviews=None):
    import streamlit as st
    st.caption("按交易流程查看留存资料。目录项是否适用需要结合本单条件确认；留存数量不代表业务完成。")
    st.download_button("导出整笔订单档案 HTML", render_dossier(workspace),
        f"{workspace.case.case_number}-订单档案.html", mime="text/html", key=f"lifecycle-dossier-{workspace.case.case_id}")
    if documents is not None:
        if st.button("准备导出整笔订单资料包", key=f"lifecycle-export-{workspace.case.case_id}"):
            from .workbench import export_bundle
            content = export_bundle(workspace, documents, actor)
            st.download_button("下载订单全流程 ZIP", content, f"{workspace.case.case_number}-全流程资料.zip",
                               mime="application/zip", key=f"lifecycle-bundle-{workspace.case.case_id}")
    summaries = stage_summary(workspace)
    st.dataframe([{"阶段": item["stage"], "留存情况": item["status"], "原件": item["document_count"],
        "来源表格": item["table_count"], "候选字段": item["candidate_count"], "已填表单": item["filled_form_count"],
        "本单必需待补": "；".join(item["missing_required"])} for item in summaries], hide_index=True, use_container_width=True)
    for number, item in enumerate(summaries, 1):
        with st.expander(f"{number}. {item['stage']} · {item['status']}"):
            st.write("**保存什么：** " + item["data_retained"])
            st.write("**本阶段文件：** " + "、".join(DOC_LABELS[kind] for kind in item["kinds"]))
            st.write(f"原件 {item['document_count']} 份、历史版本 {item['version_count']} 个、来源表格 {item['table_count']} 张、"
                     f"候选字段 {item['candidate_count']} 个（人工确认 {item['confirmed_count']} 个）、"
                     f"已填表单 {item['filled_form_count']} 张（核对 {item['confirmed_form_count']} 张）、生成文件 {item['artifact_count']} 版。")
            if item["missing_required"]:
                st.warning("本单已选必需文件待补：" + "、".join(item["missing_required"]))
            if item["empty_catalog"]:
                st.caption("暂空目录（按本单适用性补充）：" + "、".join(item["empty_catalog"]))
            st.write("**在哪里查看/导出：** 打开“文件与表单”，在我方文件或外部资料中选择上述文件；可查看原件、原文、工作表、候选字段和填写表单。")
            st.write("**导出格式：** 原件保持原格式；全部结构化数据为 JSON；表单和整理记录为 JSON/CSV；我方填写模板为 HTML；已生成的 PI/CI/PL 为 XLSX，其他文件草稿为 HTML。上方可下载整笔订单 HTML 档案和 ZIP 资料包。")
    st.markdown("**虚拟业务员整理记录**")
    records = assistance_rows(workspace)
    st.caption("记录机器如何整理和建议填写；建议值仍需人工核对，不能代替批准。")
    if not records:
        st.caption("暂无留存记录。")
        return
    source_names = sorted({record["来源文件"] for record in records})
    selected = st.multiselect("按来源文件筛选", source_names,
                              key=f"lifecycle-assistance-source-{workspace.case.case_id}")
    query = st.text_input("搜索填写字段或依据", key=f"lifecycle-assistance-query-{workspace.case.case_id}")
    visible = [record for record in records if (not selected or record["来源文件"] in selected)
               and (not query or query.casefold() in " ".join(record.values()).casefold())]
    st.dataframe(visible, hide_index=True, use_container_width=True)
    st.download_button("导出虚拟业务员整理记录 CSV", rows_csv(records),
        f"{workspace.case.case_number}-虚拟业务员整理记录.csv", mime="text/csv",
        key=f"lifecycle-assistance-csv-{workspace.case.case_id}")
