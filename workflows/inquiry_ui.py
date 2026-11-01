"""Employee inquiry workspace, independent of an order Case."""
import streamlit as st

from core.errors import CrossBotError
from core.v3 import ResourceKind
from knowledge.governance import KnowledgeService
from providers.llm_client import HttpChatClient
from ui.workbench import navigate
from workflows.inquiry import InquiryService


def render_inquiries(auth, token, config, ready, *, history=False):
    st.title("询盘回复" if not history else "询盘记录")
    service = InquiryService(auth)
    inquiries = service.list(token)
    next_inquiry = st.session_state.pop("_next_active_inquiry", None)
    if next_inquiry:
        st.session_state["active_inquiry"] = next_inquiry
    error = st.session_state.pop("inquiry_last_error", None)
    if error:
        st.error(error)
    options = [None] + [i.inquiry_id for i in inquiries]
    labels = {i.inquiry_id: i.title for i in inquiries}
    if st.session_state.get("active_inquiry") not in options:
        st.session_state.pop("active_inquiry", None)
    selected = st.selectbox("询盘", options, format_func=lambda x: labels.get(x, "新建询盘"),
                            placeholder="新建询盘", key="active_inquiry")
    inquiry = service.get(token, selected) if selected else None
    if inquiry is None:
        products = KnowledgeService(auth).list_published(token, kind=ResourceKind.PRODUCT)
        labels_product = {v.resource_id: f"{v.content.get('name', v.resource_id)} · {v.content.get('sku', '')}" for v in products}
        columns = st.columns(2)
        product = columns[0].selectbox("产品", [None] + list(labels_product),
                     format_func=lambda x: labels_product.get(x, "按询盘关键词检索"),
                     placeholder="按询盘关键词检索", key="inquiry_product")
        language = columns[1].selectbox("回复语言", ["英文", "中文", "德文", "西班牙文", "法文"], key="inquiry_language")
    else:
        product, language = inquiry.product_resource_id, inquiry.target_language
        st.caption(f"回复语言：{language}")
        for message in inquiry.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        if inquiry.case_id:
            st.success("已生成订单草稿")
            if st.button("前往订单核对", key="inquiry_open_case"):
                st.session_state["selected_case_id"] = inquiry.case_id
                navigate("documents")
                st.rerun()
            st.caption("后续订单变更请在 Case 中复核，首次下单草稿保留原始快照。")
        with st.expander("检索依据与订单字段"):
            st.json({"sources": inquiry.citations, "draft_fields": inquiry.fields})
        st.download_button("导出对话", "\n\n".join(f"{m['role']}: {m['content']}" for m in inquiry.messages),
                           file_name=f"{inquiry.inquiry_id}.txt", mime="text/plain")
    if not ready:
        st.info("请先在模型服务中配置并确认 API。")
    if inquiry and inquiry.awaiting_reply:
        st.warning("客户消息已保存，尚未生成回复。")
        if st.button("重试生成回复", disabled=not ready, key="inquiry_retry"):
            _reply(service, token, inquiry.inquiry_id, config)
        return
    with st.form("customer_inquiry", clear_on_submit=True):
        text = st.text_area("客户新消息", height=130, key="inquiry_customer_text")
        submitted = st.form_submit_button("保存并生成回复", disabled=not ready)
    if submitted:
        try:
            if not text.strip():
                st.warning("请填写客户消息。")
                return
            if not inquiry:
                inquiry = service.create(token, target_language=language, product_resource_id=product)
            service.receive(token, inquiry.inquiry_id, text)
            st.session_state["_next_active_inquiry"] = inquiry.inquiry_id
            _reply(service, token, inquiry.inquiry_id, config)
        except CrossBotError as error:
            st.error(error.message)
    if inquiry and inquiry.messages:
        with st.expander("调整回复"):
            with st.form("inquiry_revision", clear_on_submit=True):
                instruction = st.text_area("调整要求", key="inquiry_revision_text")
                revise = st.form_submit_button("生成修改稿", disabled=not ready)
            if revise and instruction.strip():
                _reply(service, token, inquiry.inquiry_id, config, instruction)


def _reply(service, token, inquiry_id, config, instruction=None):
    try:
        with st.spinner("正在生成回复"):
            service.reply(token, inquiry_id, HttpChatClient(config, json_mode=True), revision_instruction=instruction)
    except CrossBotError as error:
        st.session_state["inquiry_last_error"] = error.message
    st.rerun()
