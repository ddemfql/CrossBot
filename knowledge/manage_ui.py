"""Product/knowledge/term drafts and independent review, using only A's services."""
import json
from datetime import date
import streamlit as st
from core.errors import CrossBotError, ValidationError
from core.identity import Permission
from auth.service import ROLE_PERMISSIONS
from core.models import to_dict
from core.v3 import ProductData, KnowledgeData, ProhibitedTermData, ResourceKind, SourceKind, LifecycleStatus
from knowledge.governance import KnowledgeService


def can(principal, action):
    return action in ROLE_PERMISSIONS[principal.active_role]


def render_knowledge_management(auth, token, principal):
    service = KnowledgeService(auth)
    st.subheader("产品、知识与词项版本")
    st.caption("草稿 → 独立审核 → 管理员发布；新版本不覆盖旧版本。演示/外部参考不能直接发布为正式事实。")
    published = service.list_published(token)
    with st.expander("当前已发布版本（正式检索入口）"):
        st.json([to_dict(v) for v in published])
        if not published:
            st.info("暂无已发布知识。原8条演示JSON不会自动进入正式知识库。")
    if can(principal, Permission.KNOWLEDGE_EDIT):
        with st.expander("创建草稿／新版本"):
            kind = st.selectbox("资料类型", list(ResourceKind), format_func=lambda x: x.value, key="v3_resource_kind")
            with st.form("v3_draft_form"):
                resource_id = st.text_input("资料标识", key="v3_resource_id")
                parent_id = st.text_input("父版本ID（新资料留空，修改已有资料须填写）", key="v3_resource_parent")
                if kind == ResourceKind.PRODUCT:
                    name = st.text_input("产品名称")
                    sku = st.text_input("SKU")
                    material = st.text_input("材质（逗号分隔）")
                    specs = st.text_area("参数（每行：名称=值，未知参数不填）")
                    allowed = st.text_area("允许宣传点（每行一项）")
                    evidence = st.text_area("需要证据的声明（每行一项）")
                elif kind == ResourceKind.KNOWLEDGE:
                    text = st.text_area("知识正文")
                    category = st.text_input("分类")
                    applicability = st.text_input("适用说明")
                else:
                    term = st.text_input("词项")
                    pattern = st.selectbox("匹配方式", ["exact", "synonym", "regex"])
                    severity = st.selectbox("风险级别", ["warning", "blocking", "info"])
                    replacement = st.text_input("替换建议")
                    synonyms = st.text_area("同义表达（每行一项）")
                    st.caption("正则保存时校验语法；生成时当前不执行任意正则，会提示需人工/D规则执行器复核。")
                source = st.text_input("资料来源与依据")
                source_url = st.text_input("来源网址（可留空）")
                source_kind = st.selectbox("来源性质", list(SourceKind), index=list(SourceKind).index(SourceKind.DEMO), format_func=lambda x: x.value)
                effective = st.text_input("生效日期（YYYY-MM-DD，未知留空）")
                market = st.text_input("适用国家（全局留空）")
                platform = st.text_input("适用平台（全局留空）")
                scope = st.text_input("适用产品SKU或资料标识（全局留空）")
                language = st.text_input("资料语言（可留空）")
                save = st.form_submit_button("保存新草稿")
            if save:
                try:
                    if effective.strip():
                        date.fromisoformat(effective.strip())
                    if kind == ResourceKind.PRODUCT:
                        if not name.strip():
                            raise ValidationError("产品名称不能为空。")
                        values = {}
                        for line in specs.splitlines():
                            if line.strip():
                                k, sep, v = line.partition("=")
                                if not sep or not k.strip() or not v.strip() or k.strip() in values:
                                    raise ValidationError("参数每行需有唯一名称和值。")
                                values[k.strip()] = v.strip()
                        content = to_dict(ProductData(name.strip(), sku.strip(), specifications=values,
                            materials=[v.strip() for v in material.split(",") if v.strip()],
                            allowed_claims=[v for v in allowed.splitlines() if v.strip()],
                            claims_requiring_evidence=[v for v in evidence.splitlines() if v.strip()]))
                    elif kind == ResourceKind.KNOWLEDGE:
                        if not text.strip() or not category.strip():
                            raise ValidationError("正文与分类不能为空。")
                        content = to_dict(KnowledgeData(text, category, applicability))
                    else:
                        content = to_dict(ProhibitedTermData(term, pattern, severity, replacement,
                                                              [v for v in synonyms.splitlines() if v.strip()]))
                    version = service.create_draft(token, resource_id=resource_id, kind=kind, content=content,
                        source=source, parent_version_id=parent_id.strip() or None, source_url=source_url.strip() or None,
                        source_kind=source_kind, effective_date=effective.strip() or None, market=market.strip() or None,
                        platform=platform.strip() or None, product_scope=scope.strip() or None, language=language.strip() or None)
                    st.success("草稿已保存：" + version.version_id)
                except CrossBotError as error:
                    st.error(error.message)
                except ValueError:
                    st.error("日期或内容格式不合法，未保存草稿。")
    versions = service.list_versions(token)
    if not versions:
        st.caption("当前无可见版本。")
        return
    by_id = {v.version_id: v for v in versions}
    selected = st.selectbox("版本与审核队列", list(by_id), key="v3_resource_selected",
        format_func=lambda k: f"{by_id[k].resource_id} · V{by_id[k].version_number} · {by_id[k].status.value}")
    version = by_id[selected]
    st.json(to_dict(version))
    reason = st.text_input("审核／发布／回滚原因", key="v3_resource_reason")
    action = None
    if st.button("提交审核", key="v3_resource_submit", disabled=not (
            can(principal, Permission.KNOWLEDGE_EDIT) and version.status == LifecycleStatus.DRAFT)):
        action = lambda: service.submit(token, selected)
    independent = version.created_by != principal.user_id and version.status == LifecycleStatus.IN_REVIEW
    for label, approve in [("审核通过", True), ("退回知识版本", False)]:
        if st.button(label, key="v3_resource_review_"+str(approve), disabled=not (
                can(principal, Permission.KNOWLEDGE_REVIEW) and independent)):
            action = lambda approve=approve: service.review(token, selected, approve=approve, reason=reason)
    if st.button("发布已审核版本", key="v3_resource_publish", disabled=not (
            can(principal, Permission.KNOWLEDGE_PUBLISH) and version.status == LifecycleStatus.APPROVED
            and version.source_kind not in {SourceKind.DEMO, SourceKind.EXTERNAL_REFERENCE})):
        action = lambda: service.publish(token, selected, reason=reason)
    if st.button("回滚至此已发布历史版本", key="v3_resource_rollback", disabled=not (
            can(principal, Permission.KNOWLEDGE_ROLLBACK) and version.status in {LifecycleStatus.PUBLISHED, LifecycleStatus.ARCHIVED})):
        action = lambda: service.rollback(token, version.resource_id, selected, reason=reason)
    if action:
        try:
            action()
            st.rerun()
        except CrossBotError as error:
            st.error(error.message)
    with st.expander("发布／回滚历史"):
        st.json(to_dict(service.publications(token, version.resource_id)))
