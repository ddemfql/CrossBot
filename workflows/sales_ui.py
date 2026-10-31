"""W1 page orchestration only; named shared requests and explicit button actions."""
import streamlit as st

from core.errors import CrossBotError
from core.schemas import SalesRequest
from providers.mock_client import MockChatClient
from workflows.conversation import revise_sales, start_sales
from knowledge.ui import render_knowledge_preview
from core.config import AppConfig
from providers.llm_client import HttpChatClient


def render_sales(config=None, ready=True):
    config = config or AppConfig(mock_mode=True)
    mode = "模拟" if config.mock_mode else "真实，消耗额度"
    st.subheader("W1 · 询盘回复与修订")
    st.info("模拟模式：不调用真实模型，不消耗API额度。仅验证流程，不进行翻译或语义改写。" if config.mock_mode else "真实模式：点击生成会发送当前业务资料到所填服务，并消耗额度。知识参考仍全部为模拟资料。")
    st.caption("历史和密钥仅在当前会话，刷新或重启可能丢失；结果须人工复核。")
    inquiry = st.text_area("客户询盘", "Need 500 USB lamps.\nPlease confirm specifications.", key="sales_inquiry")
    product = st.text_area("中文产品信息", "DEMO-LAMP-001\nUSB台灯，ABS材质\n颜色：白色", key="sales_product")
    language = st.selectbox("目标语言", ["英文", "德文"], key="sales_language")
    country = st.text_input("客户国家（可留空）", key="sales_country")
    quantity = st.number_input("已知采购数量（0表示未知）", min_value=0, step=1, key="sales_quantity")
    render_knowledge_preview(product, country=country.strip() or None)
    client = MockChatClient() if config.mock_mode else HttpChatClient(config)
    if st.button(f"生成首版（{mode}）", key="sales_generate", disabled=not ready):
        request = SalesRequest(inquiry, product, language, country.strip() or None, quantity or None)
        try:
            st.session_state.sales_conversation = start_sales(request, client)
        except CrossBotError as error:
            st.error(error.message)
    conversation = st.session_state.get("sales_conversation")
    if conversation is None:
        return
    saved = conversation.request
    if (inquiry, product, language, country.strip() or None, quantity or None) != (
        saved.inquiry, saved.product_info, saved.target_language, saved.customer_country, saved.requested_quantity
    ):
        st.warning("表单已修改。修订仍依据历史输入；请生成首版开启新会话，成功后才替换已有历史。")
        can_revise = False
    else:
        can_revise = True
    expected_provider = "mock" if config.mock_mode else "real"
    if conversation.turns[-1].response.provider != expected_provider:
        st.warning("当前模式与历史不同，请生成首版开启新会话；历史仍保留。")
        can_revise = False
    with st.expander("核对本次会话的结构化输入"):
        st.json(saved.to_dict())
    for number, turn in enumerate(conversation.turns, start=1):
        with st.expander(f"V{number} · {'首次生成' if turn.kind == 'initial' else '修改'}", expanded=number == len(conversation.turns)):
            if turn.feedback:
                st.write("本轮修改要求：", turn.feedback)
            st.write(turn.response.content)
            st.caption(f"本版来源：{turn.response.provider or '未标注'}；模型：{turn.response.model or '未标注'}")
            for risk in turn.response.risks:
                st.warning(risk.message)
            for pending in turn.response.pending_confirmations:
                st.warning(pending)
    feedback = st.text_area("本轮修改意见", key="sales_feedback")
    if st.button(f"生成修订版（{mode}）", key="sales_revise", disabled=not (can_revise and ready)):
        try:
            updated = revise_sales(conversation, feedback, client)
        except CrossBotError as error:
            st.error(error.message)
        else:
            st.session_state.sales_conversation = updated
            st.rerun()
