"""Optional explicit-cost buyer simulation, independent of successful sales history."""
import streamlit as st
from core.errors import CrossBotError
from core.schemas import SalesRequest
from providers.customer_mock import MockCustomerClient
from providers.llm_client import HttpChatClient
from workflows.customer_simulator import simulate_customer


def apply_customer_question(content):
    st.session_state.sales_inquiry = content


def render_customer_simulator(request, config, ready):
    with st.expander("模拟客户练习（独立生成，不是真实客户）"):
        st.caption("模拟模式使用固定中性问题模板；真实模式单独调用一次API并消耗额度。只生成问题，不自动改询盘或业务历史。")
        topic = st.text_input("客户模拟主题", "询问产品规格、认证资料、包装和交期", key="customer_topic")
        if st.button("生成模拟客户问题", key="customer_generate", disabled=not ready):
            candidate = SalesRequest(topic, request.product_info, request.target_language,
                                     request.customer_country, request.requested_quantity)
            try:
                response = simulate_customer(candidate, MockCustomerClient() if config.mock_mode else HttpChatClient(config))
            except CrossBotError as error:
                st.error(error.message)
            else:
                st.session_state.customer_simulation = response
        response = st.session_state.get("customer_simulation")
        if response:
            st.write(response.content)
            st.caption(f"模拟客户问题的生成来源：{response.provider}；模型/模板：{response.model}")
            for risk in response.risks:
                st.warning(risk.message)
            for pending in response.pending_confirmations:
                st.warning(pending)
            st.button("将这条模拟问题填入询盘", key="customer_apply", on_click=apply_customer_question,
                      args=(response.content,))
