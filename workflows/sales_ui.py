"""W1 page orchestration only; named shared requests and explicit button actions."""
import streamlit as st

from core.errors import CrossBotError
from core.schemas import SalesRequest
from providers.mock_client import MockChatClient
from workflows.conversation import revise_sales, start_sales
from knowledge.ui import render_knowledge_preview
from core.config import AppConfig
from providers.llm_client import HttpChatClient
from workflows.demo_case import demo_sales_request
from workflows.inquiry_analysis import analyze_inquiry, extract_inquiry_candidates
from providers.inquiry_mock import MockInquiryClient
from core.models import to_dict
from workflows.customer_simulator_ui import render_customer_simulator


def load_sales_demo():
    request = demo_sales_request()
    for key, value in {"sales_inquiry": request.inquiry, "sales_product": request.product_info,
                       "sales_language": request.target_language, "sales_country": request.customer_country,
                       "sales_quantity": request.requested_quantity}.items():
        st.session_state[key] = value


def render_sales(config=None, ready=True, *, case_id=None, generation_service=None, auth_token=None):
    config = config or AppConfig(mock_mode=True)
    mode = "模拟" if config.mock_mode else "真实，消耗额度"
    st.subheader("W1 · 询盘回复与修订")
    st.button("载入德国台灯演示资料", key="sales_demo", on_click=load_sales_demo)
    st.caption(f"当前 Case：{case_id or '未关联'}。演示资料仅载入表单，不替换历史；生成草稿仍需人工复核。")
    st.info("模拟模式：不调用真实模型，不消耗API额度。仅验证流程，不进行翻译或语义改写。" if config.mock_mode else "真实模式：点击生成会发送当前业务资料到所填服务，并消耗额度。知识参考仍全部为模拟资料。")
    st.caption("W1历史按Case保存在本地数据库；API Key仅留在当前浏览器会话。结果须人工复核。")
    for key, value in {"sales_inquiry": "Need 500 USB lamps.\nPlease confirm specifications.",
                       "sales_product": "DEMO-LAMP-001\nUSB台灯，ABS材质\n颜色：白色"}.items():
        if key not in st.session_state:
            st.session_state[key] = value
    inquiry = st.text_area("客户询盘", key="sales_inquiry")
    product = st.text_area("中文产品信息", key="sales_product")
    language = st.selectbox("目标语言", ["英文", "德文"], key="sales_language")
    country = st.text_input("客户国家（可留空）", key="sales_country")
    quantity = st.number_input("已知采购数量（0表示未知）", min_value=0, step=1, key="sales_quantity")
    context = None
    if case_id:
        from core.v3 import RequestContext
        context = RequestContext(case_id)
    current_request = SalesRequest(inquiry, product, language, country.strip() or None, quantity or None, context=context)
    with st.expander("询盘意图与字段候选"):
        st.caption("本地预览（不调用API）；下方的独立识别按钮按当前模式运行。")
        st.caption("有限中英关键词与精确原文位置，不覆盖语义；否定、多个数量和不同目的地需人工判断。候选不会自动写回表单。")
        candidates = analyze_inquiry(current_request)
        if candidates:
            st.json([to_dict(item) for item in candidates])
        else:
            st.write("未发现可定位候选，请人工补充；不代表询盘没有要求。")
        st.caption("下方可单独识别候选：模拟模式用本地模板；真实模式额外调用一次API。模型引用原文，代码计算字符位置；找不到或有歧义的引用会拦截，语义仍需人工判断。")
        if st.button(f"识别询盘候选（{mode}）", key="sales_extract", disabled=not ready):
            try:
                fields = extract_inquiry_candidates(current_request,
                    MockInquiryClient() if config.mock_mode else HttpChatClient(config, json_mode=True))
            except CrossBotError as error:
                st.error(error.message)
            else:
                st.session_state.sales_candidates = (inquiry, fields)
        saved_candidates = st.session_state.get("sales_candidates")
        if saved_candidates and saved_candidates[0] == inquiry:
            st.json([to_dict(item) for item in saved_candidates[1]])
            st.caption("候选已保存于当前会话，尚未人工确认；请在上方输入框手动核对补充，不自动修改数量或国家。")
        elif saved_candidates:
            st.caption("询盘已改变，旧识别候选不适用于当前文字。")
    render_customer_simulator(current_request, config, ready, generation_service=generation_service, auth_token=auth_token)
    render_knowledge_preview(product, country=country.strip() or None)
    client = MockChatClient() if config.mock_mode else HttpChatClient(config)
    if st.button(f"生成首版（{mode}）", key="sales_generate", disabled=not ready):
        request = SalesRequest(inquiry, product, language, country.strip() or None, quantity or None, context=context)
        try:
            st.session_state.sales_conversation = start_sales(request, client, generation_service=generation_service, auth_token=auth_token)
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
            updated = revise_sales(conversation, feedback, client, generation_service=generation_service, auth_token=auth_token)
        except CrossBotError as error:
            st.error(error.message)
        else:
            st.session_state.sales_conversation = updated
