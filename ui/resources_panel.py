"""Three separate resource views backed by the existing governance service."""
from dataclasses import asdict, fields
import csv
import hashlib
import io
import json
from pathlib import Path
import re

import streamlit as st

from auth.service import ROLE_PERMISSIONS
from core.errors import CrossBotError, ValidationError
from core.identity import Permission
from core.v3 import (KnowledgeData, LifecycleStatus, ProductData, ProhibitedTermData,
                     ResourceKind, SourceKind)
from knowledge.governance import KnowledgeService
from storage.blobs import LocalBlobStore

CATEGORIES = {
    "products": (ResourceKind.PRODUCT, "产品资料", "产品事实、SKU、规格、包装与有依据的声明", ProductData),
    "knowledge": (ResourceKind.KNOWLEDGE, "知识库", "业务说明、FAQ、平台规则与适用条件", KnowledgeData),
    "risk_terms": (ResourceKind.PROHIBITED_TERM, "风险词库", "风险词项、匹配方式、风险级别与替换建议", ProhibitedTermData),
}
STATUS_LABELS = {"draft": "草稿", "in_review": "待审核", "approved": "已审核", "rejected": "已退回",
                 "published": "已发布", "archived": "已归档"}
SAMPLES = {
    ResourceKind.PRODUCT: asdict(ProductData("产品名称（请替换）", "SKU-001", {"材质": "请填写已核实材质"})),
    ResourceKind.KNOWLEDGE: asdict(KnowledgeData("请替换为有依据的业务知识正文", "FAQ", "请填写适用条件")),
    ResourceKind.PROHIBITED_TERM: asdict(ProhibitedTermData("绝对安全", replacement_suggestion="请使用有依据的具体参数")),
}


def _validate_content(kind, content):
    model = next(value[3] for value in CATEGORIES.values() if value[0] == kind)
    if not isinstance(content, dict) or set(content) - {field.name for field in fields(model)}:
        raise ValidationError("内容字段与当前分类不匹配，请使用本分类模板。")
    required = {ResourceKind.PRODUCT: ("name",), ResourceKind.KNOWLEDGE: ("text", "category"),
                ResourceKind.PROHIBITED_TERM: ("term",)}[kind]
    for key in required:
        if not isinstance(content.get(key), str) or not content[key].strip():
            raise ValidationError(f"缺少有效字段：{key}")
    data = asdict(model(**content))
    for field in fields(model):
        value = data[field.name]
        default = SAMPLES[kind][field.name]
        if not isinstance(value, type(default)):
            raise ValidationError(f"字段 {field.name} 的类型不正确，请参照模板。")
        if isinstance(value, list) and field.name not in {"certificates", "source_files"} and not all(isinstance(item, str) for item in value):
            raise ValidationError(f"字段 {field.name} 必须为文字列表。")
    if kind == ResourceKind.PROHIBITED_TERM:
        if data["pattern_type"] not in {"exact", "regex", "synonym"}:
            raise ValidationError("匹配方式仅支持 exact / regex / synonym。")
        if data["severity"] not in {"info", "warning", "blocking"}:
            raise ValidationError("风险级别仅支持 info / warning / blocking。")
        if data["pattern_type"] == "regex":
            try:
                re.compile(data["term"])
            except re.error as error:
                raise ValidationError("无效正则表达式。") from error
    return data


def parse_upload(name, raw, kind):
    """Validate a selected category before any original/draft is written."""
    kind = ResourceKind(kind)
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise ValidationError("请选择非空文件，大小不超过 2 MB。")
    try:
        text = raw.decode("utf-8-sig")
        suffix = Path(name).suffix.lower()
        if suffix == ".json":
            payload = json.loads(text)
            if not isinstance(payload, dict) or payload.get("kind") != kind.value:
                raise ValidationError("文件分类与当前入口不一致，请切换分类或下载对应模板。")
            records = payload.get("entries")
        elif suffix == ".csv":
            records = list(csv.DictReader(io.StringIO(text)))
            for record in records:
                if record.pop("kind", None) != kind.value:
                    raise ValidationError("CSV 中存在其他分类，请按类别拆分上传。")
                record["content"] = json.loads(record.get("content", ""))
        elif suffix in {".txt", ".md"} and kind == ResourceKind.KNOWLEDGE:
            records = [{"resource_id": Path(name).stem,
                        "content": {"text": text, "category": "业务说明"}}]
        else:
            raise ValidationError("产品资料和风险词库支持 JSON/CSV；知识库另支持 UTF-8 TXT/Markdown。")
        if not isinstance(records, list) or not records or len(records) > 200:
            raise ValidationError("文件需包含 1–200 条记录。")
        result, identifiers = [], set()
        for record in records:
            if not isinstance(record, dict):
                raise ValidationError("每条记录必须是对象。")
            resource_id = record.get("resource_id")
            if not isinstance(resource_id, str) or not resource_id.strip() or resource_id.strip() in identifiers:
                raise ValidationError("资源标识不能为空，文件内也不能重复。")
            resource_id = resource_id.strip()
            identifiers.add(resource_id)
            result.append({**record, "resource_id": resource_id.strip(),
                           "content": _validate_content(kind, record.get("content"))})
        return result
    except (UnicodeError, json.JSONDecodeError, csv.Error, TypeError, ValueError, RecursionError) as error:
        raise ValidationError("文件无法解析，请使用 UTF-8 编码及本分类模板。") from error


def save_upload_draft(service, token, blobs, *, kind, resource_id, content, source, raw, filename,
                      demo=False, parent_version_id=None, **metadata):
    """UI adapter: authorize first, keep original, then delegate version creation."""
    service.auth.require_permission(token, Permission.KNOWLEDGE_EDIT)
    kind = ResourceKind(kind)
    content = _validate_content(kind, content)
    if not source.strip() or not resource_id.strip():
        raise ValidationError("请填写资源标识与内容来源。")
    if demo and not resource_id.startswith("demo:"):
        resource_id = "demo:" + resource_id
    existing = service.list_versions(token, resource_id=resource_id)
    if existing:
        latest = max(existing, key=lambda item: item.version_number)
        if latest.kind != kind:
            raise ValidationError("该标识已用于其他分类，请更换资源标识。")
        if latest.version_id != parent_version_id:
            raise ValidationError("该资源已有更新，请刷新页面并核对父版本。")
        if all(latest.content.get(key) == value for key, value in content.items()):
            raise ValidationError("与最新版本内容相同，无需重复上传。")
    key = blobs.put(raw)
    content["_upload"] = {"sha256": key, "filename": Path(filename).name}
    return service.create_draft(token, resource_id=resource_id, kind=kind, content=content, source=source,
                                source_kind=SourceKind.DEMO if demo else SourceKind.LOCAL,
                                parent_version_id=parent_version_id, **metadata)


def render_resources(auth, token, principal, page, *, demo=False):
    kind, title, description, _ = CATEGORIES[page]
    service = KnowledgeService(auth)
    permissions = ROLE_PERMISSIONS[principal.active_role]
    st.title(title)
    st.caption(description)
    notice = st.session_state.pop("_resource_notice", None)
    if notice:
        st.success(notice)
    st.info("上传先保存为草稿，须独立审核后发布。上传资料尚不会自动替换 W1/W2 使用的内置演示资料。")
    versions = [item for item in service.list_versions(token, kind=kind)
                if (item.source_kind == SourceKind.DEMO) == demo]
    columns = st.columns(3)
    columns[0].metric("资源", len({item.resource_id for item in versions}))
    columns[1].metric("待审核", sum(item.status == LifecycleStatus.IN_REVIEW for item in versions))
    columns[2].metric("已发布版本", sum(item.status == LifecycleStatus.PUBLISHED for item in versions))
    if Permission.KNOWLEDGE_EDIT in permissions:
        with st.expander(f"上传{title}", expanded=not versions):
            _render_upload(service, token, kind, title, demo)
    else:
        st.caption("当前岗位可查看资料；上传需管理员或具有资料编辑权限的岗位。")
    search = st.text_input(f"搜索{title}", key=f"{page}_search", placeholder="资源标识、正文、SKU 或词项")
    status = st.selectbox("状态筛选", ["全部", *STATUS_LABELS], key=f"{page}_status",
                          format_func=lambda value: STATUS_LABELS.get(value, value))
    filtered = [item for item in versions if (status == "全部" or item.status.value == status)
                and (not search or search.casefold() in json.dumps([item.resource_id, item.content], ensure_ascii=False).casefold())]
    if not filtered:
        st.caption("暂无符合条件的资料。")
    for item in filtered:
        content_title = item.content.get("name") or item.content.get("term") or item.content.get("category") or item.resource_id
        with st.expander(f"{content_title} · {item.resource_id} · V{item.version_number} · {STATUS_LABELS[item.status.value]}"):
            st.caption(f"来源：{item.source} · 市场：{item.market or '未限定'} · 平台：{item.platform or '未限定'}")
            st.json({key: value for key, value in item.content.items() if key != "_upload"})
            original = item.content.get("_upload")
            if original:
                try:
                    raw = LocalBlobStore(Path(auth.store.path).parent / "resource-files").get(original["sha256"])
                    st.download_button("下载上传原件", raw, file_name=original["filename"],
                                       key=f"resource_original_{item.version_id}", on_click="ignore")
                except (CrossBotError, OSError) as error:
                    st.error(str(error))
            _render_lifecycle(service, token, principal, item, permissions)


def _render_upload(service, token, kind, title, demo):
    sample = {"kind": kind.value, "entries": [{"resource_id": f"{kind.value}-001", "content": SAMPLES[kind]}]}
    st.download_button(f"下载{title} JSON 模板", json.dumps(sample, ensure_ascii=False, indent=2).encode("utf-8"),
                       file_name=f"{kind.value}-template.json", key=f"template_{kind.value}", on_click="ignore")
    st.caption("JSON/CSV 支持多条记录，预览后一次保存所选一条。CSV 列：kind、resource_id、content（JSON）。每次上传保留原件与版本。")
    types = ["json", "csv", "txt", "md"] if kind == ResourceKind.KNOWLEDGE else ["json", "csv"]
    uploaded = st.file_uploader(f"选择{title}文件", type=types, key=f"upload_{kind.value}", max_upload_size=2)
    if uploaded is None:
        return
    raw = uploaded.getvalue()
    try:
        records = parse_upload(uploaded.name, raw, kind)
    except CrossBotError as error:
        st.error(str(error))
        return
    selected = st.selectbox("选择本次保存的条目", range(len(records)),
                            format_func=lambda index: records[index]["resource_id"], key=f"record_{kind.value}")
    record = records[selected]
    st.json(record["content"])
    record_key = hashlib.sha256(raw + str(selected).encode()).hexdigest()[:16]
    with st.form(f"save_resource_{kind.value}_{record_key}"):
        resource_id = st.text_input("资源标识", record["resource_id"])
        source = st.text_input("内容来源（必填）", str(record.get("source", "")), placeholder="如：供应商参数表 2026-10，经产品岗核对")
        columns = st.columns(2)
        market = columns[0].text_input("适用国家或市场", str(record.get("market", "")))
        platform = columns[1].text_input("适用平台", str(record.get("platform", "")))
        product_scope = st.text_input("适用产品范围 / SKU", str(record.get("product_scope", "")))
        effective = st.text_input("生效日期（选填，YYYY-MM-DD）", str(record.get("effective_date", "")))
        submitted = st.form_submit_button(f"保存到{title}草稿", type="primary")
    if submitted:
        try:
            if effective:
                from datetime import date
                date.fromisoformat(effective)
            target_id = "demo:" + resource_id if demo and not resource_id.startswith("demo:") else resource_id
            previous = service.list_versions(token, resource_id=target_id)
            latest = max(previous, key=lambda item: item.version_number) if previous else None
            version = save_upload_draft(service, token, LocalBlobStore(Path(service.auth.store.path).parent / "resource-files"),
                kind=kind, resource_id=resource_id, content=record["content"], source=source, raw=raw,
                filename=uploaded.name, demo=demo, parent_version_id=latest.version_id if latest else None,
                market=market.strip() or None, platform=platform.strip() or None,
                product_scope=product_scope.strip() or None, effective_date=effective or None)
            st.session_state["_resource_notice"] = f"已保存：{version.resource_id} · V{version.version_number} · 草稿。"
            st.rerun()
        except (CrossBotError, ValueError, OSError) as error:
            st.error(str(error))


def _render_lifecycle(service, token, principal, item, permissions):
    prefix = f"resource_{item.version_id}"
    try:
        if item.status == LifecycleStatus.DRAFT and Permission.KNOWLEDGE_EDIT in permissions:
            if st.button("提交审核", key=prefix + "_submit"):
                service.submit(token, item.version_id)
                st.rerun()
        if item.status == LifecycleStatus.IN_REVIEW and Permission.KNOWLEDGE_REVIEW in permissions:
            reason = st.text_input("审核意见", key=prefix + "_reason")
            columns = st.columns(2)
            own = principal.user_id == item.created_by
            if own:
                st.caption("本人提交的资料需由另一名有审核权限的成员审核。")
            approve = columns[0].button("审核通过", key=prefix + "_approve", disabled=own)
            reject = columns[1].button("退回", key=prefix + "_reject", disabled=own)
            if approve or reject:
                service.review(token, item.version_id, approve=approve, reason=reason)
                st.rerun()
        if item.status == LifecycleStatus.APPROVED and Permission.KNOWLEDGE_PUBLISH in permissions:
            st.caption("演示资料不发布为正式资料。" if item.source_kind == SourceKind.DEMO else "发布为正式资料仍不会自动替换当前内置检索库。")
            if st.button("发布已审核版本", key=prefix + "_publish", disabled=item.source_kind == SourceKind.DEMO):
                service.publish(token, item.version_id)
                st.rerun()
    except CrossBotError as error:
        st.error(error.message)
