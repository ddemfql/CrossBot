"""V3 Case UI. Every sensitive operation delegates to authorized services."""
from copy import deepcopy
import json
import streamlit as st

from auth.cases import CaseAccessService
from core.errors import CrossBotError
from core.identity import Permission
from core.models import to_dict
from core.schemas import SalesRequest, ListingRequest
from core.v3 import RequestContext, ResourceKind, AttachmentStatus, LifecycleStatus
from documents.access import AttachmentService
from knowledge.governance import KnowledgeService
from knowledge.manage_ui import can
from providers.mock_client import MockChatClient
from providers.listing_mock import MockListingClient
from providers.inquiry_mock import MockInquiryClient
from providers.customer_mock import MockCustomerClient
from providers.llm_client import HttpChatClient
from providers.search_contract import SearchService
from workflows.demo_case import demo_sales_request, demo_listing_request
from workflows.v3_service import BWorkflowService, artifact_kind, listing_contents, search_customer
from workflows.access import GenerationService


def reset_scope(principal, case_id):
    scope = (principal.user_id, principal.active_role.value, case_id)
    old = st.session_state.get("b_v3_scope")
    if old != scope:
        for key in list(st.session_state):
            if key.startswith("v3_"):
                del st.session_state[key]
        if old is None or old[0] != principal.user_id:
            for key in ["provider_key", "provider_confirm"]:
                st.session_state.pop(key, None)
        st.session_state.b_v3_scope = scope


def load_case_demo(workflow):
    request = demo_sales_request() if workflow == "sales" else demo_listing_request()
    values = ({"v3_inquiry": request.inquiry, "v3_product": request.product_info,
               "v3_language": request.target_language, "v3_country": request.customer_country,
               "v3_quantity": request.requested_quantity} if workflow == "sales" else
              {"v3_description": request.product_description, "v3_platforms": request.platforms,
               "v3_listing_language": request.target_language, "v3_listing_country": request.target_country})
    st.session_state.update(values)


def render_context(auth, token, case):
    products = KnowledgeService(auth).list_published(token, kind=ResourceKind.PRODUCT)
    choices = {p.version_id: p for p in products}
    # A Case may keep a formerly published version after a newer product is published.
    for artifact in GenerationService(auth).list_for_case(token, case.case_id):
        version_id = artifact.context.product_version_id
        if version_id and version_id not in choices:
            version = KnowledgeService(auth).get_version(token, version_id)
            if version.kind == ResourceKind.PRODUCT and version.status in {LifecycleStatus.PUBLISHED, LifecycleStatus.ARCHIVED}:
                choices[version_id] = version
    selected = st.selectbox("固定产品版本（只列已发布，留空使用待核实补充资料）", [""]+list(choices), key="v3_product_version",
        format_func=lambda k: "未引用已发布产品" if not k else f"{choices[k].resource_id} · V{choices[k].version_number} · {choices[k].status.value}")
    parsed = AttachmentService(auth).list_for_case(token, case.case_id)
    ready = {a.reference.version_id: a for a in parsed if a.status == AttachmentStatus.READY}
    references = st.multiselect("引用C已就绪的附件版本", list(ready), key="v3_attachment_refs",
        format_func=lambda k: f"{ready[k].reference.document_id} · {ready[k].method} · {k}")
    return RequestContext(case.case_id, product_version_id=selected or None,
                          attachments=[deepcopy(ready[k].reference) for k in references])


def show_artifact(artifact):
    st.caption(f"记录 {artifact.artifact_id} · 来源 {artifact.provider or artifact.origin} · 模型 {artifact.model or '—'} · {artifact.review_status}")
    if artifact.workflow == "listing":
        for platform, content in listing_contents(artifact).items():
            st.markdown("**"+platform+"**")
            st.write(content)
    elif artifact_kind(artifact) == "inquiry_candidates":
        st.json(json.loads(artifact.content))
    else:
        st.write(artifact.content)
    for risk in artifact.risks:
        st.warning(risk["message"])
    for note in artifact.pending_confirmations:
        st.caption(note)


def apply_customer(content):
    st.session_state.v3_inquiry = content


def render_workflow(auth, token, principal, case, config, ready, workflow):
    service = BWorkflowService(auth)
    allowed = can(principal, Permission.W1_GENERATE if workflow == "sales" else Permission.W2_GENERATE)
    if not allowed:
        st.info("当前岗位无此工作流权限；可在有授权的岗位间切换。")
        return
    context = render_context(auth, token, case)
    mode = "模拟" if config.mock_mode else "真实，消耗额度"
    st.caption("每个生成／识别按钮单独调用；模拟不联网。结果保存到当前Case，人工复核后仍不会自动发送。")
    if case.metadata.get("is_demo"):
        st.button("载入德国台灯演示资料", key="v3_load_demo", on_click=load_case_demo, args=(workflow,))
    if workflow == "sales":
        inquiry = st.text_area("客户询盘", key="v3_inquiry")
        product = st.text_area("中文产品信息／待核实补充", key="v3_product")
        language = st.selectbox("目标语言", ["英文", "德文"], key="v3_language")
        if "v3_country" not in st.session_state:
            st.session_state.v3_country = case.destination_country
        country = st.text_input("客户国家", key="v3_country")
        quantity = st.number_input("已知采购数量（0表示未知）", min_value=0, step=1, key="v3_quantity")
        request = SalesRequest(inquiry, product, language, country.strip() or None, quantity or None, context=context)
        client = MockChatClient() if config.mock_mode else HttpChatClient(config)
        if st.button("生成首版（"+mode+"）", key="v3_generate_sales", disabled=not ready):
            try:
                service.sales(token, request, client)
                st.success("首版已保存到Case历史。")
            except CrossBotError as error:
                st.error(error.message)
        histories = [a for a in service.generations.list_for_case(token, case.case_id, workflow="sales") if artifact_kind(a) == "sales"]
        if histories:
            last = histories[-1]
            feedback = st.text_area("本轮修改意见", key="v3_feedback")
            if st.button("生成修订版（"+mode+"）", key="v3_revise", disabled=not ready):
                try:
                    service.sales(token, request, client, feedback=feedback, parent=last.artifact_id)
                    st.rerun()
                except CrossBotError as error:
                    st.error(error.message)
            st.caption("最近客户回复（所有版本见历史页）：")
            show_artifact(last)
        with st.expander("询盘候选识别与独立客户模拟"):
            if st.button("识别询盘候选（"+mode+"）", key="v3_candidates", disabled=not ready):
                try:
                    service.candidates(token, request, MockInquiryClient() if config.mock_mode else HttpChatClient(config, json_mode=True))
                    st.success("候选已持久保存，仍待人工确认。")
                except CrossBotError as error:
                    st.error(error.message)
            topic = st.text_input("客户模拟主题", "询问规格、认证、包装和交期", key="v3_customer_topic")
            if st.button("生成模拟客户问题（"+mode+"）", key="v3_customer", disabled=not ready or not can(principal, Permission.CUSTOMER_SIMULATE)):
                try:
                    simulated = deepcopy(request); simulated.inquiry = topic
                    service.customer(token, simulated, MockCustomerClient() if config.mock_mode else HttpChatClient(config))
                except CrossBotError as error:
                    st.error(error.message)
            simulations = service.generations.list_for_case(token, case.case_id, workflow="customer_simulation")
            if simulations:
                latest = simulations[-1]
                show_artifact(latest)
                st.caption("模拟来源，不是真实客户；确认后只填询盘，不批准订单。")
                st.button("将这条模拟问题填入询盘", key="v3_apply_customer", on_click=apply_customer, args=(latest.content,))
    else:
        description = st.text_area("中文产品描述／待核实补充", key="v3_description")
        if "v3_platforms" not in st.session_state:
            st.session_state.v3_platforms = ["Amazon", "TikTok"]
        platforms = st.multiselect("重点适配平台", ["Amazon", "TikTok", "阿里国际站"], key="v3_platforms")
        language = st.selectbox("目标语言", ["英文", "德文"], key="v3_listing_language")
        if "v3_listing_country" not in st.session_state:
            st.session_state.v3_listing_country = case.destination_country
        country = st.text_input("目标市场", key="v3_listing_country")
        if not platforms:
            st.warning("请至少选择一个平台。")
        if st.button("生成所选平台文案（"+mode+"）", key="v3_generate_listing", disabled=not ready or not platforms):
            try:
                request = ListingRequest(description, platforms, country.strip() or None, language, context=context)
                service.listing(token, request, MockListingClient() if config.mock_mode else HttpChatClient(config, json_mode=True))
            except CrossBotError as error:
                st.error(error.message)
        histories = service.generations.list_for_case(token, case.case_id, workflow="listing")
        if histories:
            latest = histories[-1]
            saved = latest.request_payload
            if (description, platforms, language, country.strip() or None) == (
                    saved["product_description"], saved["platforms"], saved["target_language"], saved["target_country"]):
                show_artifact(latest)
            else:
                st.caption("当前输入与已保存批次不同；旧记录保留在历史页。")


def render_attachments(auth, token, case):
    st.subheader("附件解析结果与来源")
    st.caption("B只消费C保存的原件元数据与解析结果；此页不重新上传或实现第二套OCR/PDF/Excel解析器。")
    contents = AttachmentService(auth).list_for_case(token, case.case_id)
    if not contents:
        st.info("当前Case暂无C交付的解析结果。")
    for content in contents:
        with st.expander(f"{content.reference.document_id} · {content.method} · {content.status.value}"):
            st.json(to_dict(content))
            if content.status != AttachmentStatus.READY:
                st.warning("待处理／失败结果不能进入模型请求。")


def render_search(auth, token, principal, case, adapter=None):
    st.subheader("客户背调参考")
    st.caption("阿里/实际搜索适配器未配置时明确不可用；演示记录需独立点击保存，不冒充联网结果或资质判断。")
    query = st.text_input("公司名称／搜索词", key="v3_search_query")
    purpose = st.text_input("查询用途", "核对用户提供的公司名称", key="v3_search_purpose")
    for label, demo in [("执行外部搜索", False), ("保存明确标记的演示查询（不联网）", True)]:
        if st.button(label, key="v3_search_"+str(demo), disabled=not can(principal, Permission.EXTERNAL_SEARCH)):
            try:
                search_customer(auth, token, case.case_id, query, purpose, adapter=adapter, demo=demo)
            except CrossBotError as error:
                st.error(error.message)
    for report in SearchService(auth).list_for_case(token, case.case_id):
        with st.expander(f"{report.source_kind.value} · {report.query}"):
            st.json(to_dict(report))


def render_history(auth, token, principal, case):
    service = BWorkflowService(auth)
    artifacts = service.generations.list_for_case(token, case.case_id)
    indexes = service.conversation_indexes(token, case.case_id)
    feedbacks = {}
    for conversation in indexes.values():
        messages = conversation["messages"]
        for index, message in enumerate(messages):
            if message.get("artifact_id") and index and messages[index - 1].get("feedback"):
                feedbacks[message["artifact_id"]] = messages[index - 1]["feedback"]
    st.subheader("Case生成稿、人工稿与复核")
    for artifact in reversed(artifacts):
        with st.expander(f"{artifact_kind(artifact)} · {artifact.origin} · {artifact.review_status} · {artifact.created_at.isoformat()}"):
            show_artifact(artifact)
            if artifact.artifact_id in feedbacks:
                st.write("本轮修改要求：", feedbacks[artifact.artifact_id])
            with st.expander("本版请求与产品／附件引用"):
                st.json(artifact.request_payload)
            if artifact_kind(artifact) not in {"sales", "listing"}:
                continue
            for risk in service.current_review(token, artifact):
                st.warning("当前规则重检（不覆盖原风险）：" + risk.message)
            write_permission = Permission.W2_GENERATE if artifact.workflow == "listing" else Permission.W1_GENERATE
            if can(principal, write_permission):
                with st.form("v3_manual_"+artifact.artifact_id):
                    if artifact.workflow == "listing":
                        values = {p: st.text_area("人工正文 · "+p, value=text) for p, text in listing_contents(artifact).items()}
                        edited = json.dumps({"listings": values}, ensure_ascii=False)
                    else:
                        edited = st.text_area("人工正文", value=artifact.content)
                    if st.form_submit_button("另存人工稿"):
                        try:
                            service.manual_edit(token, artifact.artifact_id, edited)
                            st.rerun()
                        except CrossBotError as error:
                            st.error(error.message)
            reason = st.text_input("复核原因", key="v3_review_reason_"+artifact.artifact_id)
            eligible = can(principal, Permission.GENERATION_REVIEW) and artifact.actor_id != principal.user_id and artifact.review_status == "pending_review"
            for label, approve in [("复核通过", True), ("退回生成稿", False)]:
                if st.button(label, key="v3_review_"+str(approve)+artifact.artifact_id, disabled=not eligible):
                    try:
                        service.review(token, artifact.artifact_id, approve=approve, reason=reason)
                        st.rerun()
                    except CrossBotError as error:
                        st.error(error.message)
    bundle = service.export_case(token, case.case_id)
    text = "\n\n".join(f"{a['artifact_id']} · {a['workflow']} · {a['origin']} · {a['review_status']}\n{a['content']}" for a in bundle["artifacts"])
    st.download_button("下载当前Case记录（JSON）", json.dumps(bundle, ensure_ascii=False, indent=2).encode(),
                       file_name="crossbot-case-history.json", mime="application/json", on_click="ignore")
    st.download_button("下载当前Case正文（TXT）", text.encode("utf-8-sig"),
                       file_name="crossbot-case-history.txt", mime="text/plain", on_click="ignore")
