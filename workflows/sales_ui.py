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
from workflows.animal_lamp_demo import animal_sales_request
from workflows.inquiry_analysis import analyze_inquiry, extract_inquiry_candidates
from providers.inquiry_mock import MockInquiryClient
from core.models import to_dict
from workflows.customer_simulator_ui import render_customer_simulator
from workflows.result_ui import render_generation_result


def load_sales_demo():
    request = demo_sales_request()
    for key, value in {"sales_inquiry": request.inquiry, "sales_product": request.product_info,
                       "sales_country": request.customer_country,
                       "sales_quantity": request.requested_quantity}.items():
        st.session_state[key] = value


def load_animal_lamp_sales_demo():
    request = animal_sales_request()
    for key, value in {"sales_inquiry": request.inquiry, "sales_product": request.product_info,
                       "sales_country": request.customer_country,
                       "sales_quantity": request.requested_quantity}.items():
        st.session_state[key] = value


def render_sales(config=None, ready=True, *, case_id=None, generation_service=None, auth_token=None, step=None):
    config = config or AppConfig(mock_mode=True)
    mode = "模拟" if config.mock_mode else "真实，消耗额度"
    st.subheader("W1 · 询盘回复与修订")
    if config.mock_mode and step in (None, 1):
        with st.expander("演示资料与案例"):
            st.button("载入德国台灯演示资料", key="sales_demo", on_click=load_sales_demo)
            st.button("载入动物台灯固定案例（自编演示数据）", key="sales_animal_lamp_demo", on_click=load_animal_lamp_sales_demo)
            st.caption("仅填充业务输入，保留当前语言和历史；不会自动生成。")
    st.caption("模拟生成 · 本地运行，不消耗额度，不执行翻译" if config.mock_mode else "真实生成 · 使用所配置的模型服务与账户额度")
    for key, value in {"sales_inquiry": "Need 500 USB lamps.\nPlease confirm specifications.",
                       "sales_product": "DEMO-LAMP-001\nUSB台灯，ABS材质\n颜色：白色"}.items():
        if key not in st.session_state:
            st.session_state[key] = value if config.mock_mode or step is None else ""
    if step in (None, 1):
        with st.container(border=True):
            inquiry = st.text_area("客户询盘", key="sales_inquiry", height=155)
            product = st.text_area("中文产品信息", key="sales_product", height=155)
            columns = st.columns(3)
            language = columns[0].selectbox("回复输出语言", ["英文", "德文", "中文"], key="sales_language",
                                    help="决定首次生成和后续修订的回复语言，与客户询盘原文的语言无关。模拟模式不执行翻译。")
            country = columns[1].text_input("客户国家（可留空）", key="sales_country")
            quantity = columns[2].number_input("已知采购数量（0表示未知）", min_value=0, step=1, key="sales_quantity")
    else:
        inquiry = st.session_state.get("sales_inquiry", "")
        product = st.session_state.get("sales_product", "")
        language = st.session_state.get("sales_language", "英文")
        country = st.session_state.get("sales_country", "")
        quantity = st.session_state.get("sales_quantity", 0)
    context = None
    if case_id:
        from core.v3 import RequestContext
        context = RequestContext(case_id)
    current_request = SalesRequest(inquiry, product, language, country.strip() or None, quantity or None, context=context)
    if step in (None, 1):
        _render_input_tools(current_request, config, ready, generation_service, auth_token, mode)
        if step == 1:
            st.button("下一步 · 生成与修订", key="sales_next", on_click=_next_step)
            return
    if step == 2:
        st.caption(f"输出语言：{language} · 客户国家：{country or '未填'} · 已知数量：{quantity or '待确认'}")
    client = MockChatClient() if config.mock_mode else HttpChatClient(config)
    if step in (None, 2) and st.button(f"生成首版（{mode}）", key="sales_generate", disabled=not ready):
        try:
            st.session_state.sales_conversation = start_sales(current_request, client, generation_service=generation_service, auth_token=auth_token)
        except CrossBotError as error:
            st.error(error.message)
    conversation = st.session_state.get("sales_conversation")
    if conversation is None:
        st.info("填写资料后生成首版，成功记录会在这里显示。")
        return
    saved = conversation.request
    can_revise = True
    if (inquiry, product, language, country.strip() or None, quantity or None) != (
        saved.inquiry, saved.product_info, saved.target_language, saved.customer_country, saved.requested_quantity
    ):
        st.warning("表单已修改。修订仍依据历史输入；请生成首版开启新会话，成功后才替换已有历史。")
        can_revise = False
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
            render_generation_result(turn.response)
            st.caption(f"本版来源：{turn.response.provider or '未标注'}；模型：{turn.response.model or '未标注'}")
    if step == 3:
        st.info("请核对事实、语言、风险与待确认项。导出仍为草稿，不会自动批准、发送给客户或关闭 Case。")
        return
    feedback = st.text_area("本轮修改意见", key="sales_feedback")
    if st.button(f"生成修订版（{mode}）", key="sales_revise", disabled=not (can_revise and ready)):
        try:
            updated = revise_sales(conversation, feedback, client, generation_service=generation_service, auth_token=auth_token)
        except CrossBotError as error:
            st.error(error.message)
        else:
            st.session_state.sales_conversation = updated
            st.rerun()


def _next_step():
    st.session_state["sales_step"] = 2


def _render_input_tools(current_request, config, ready, generation_service, auth_token, mode):
    inquiry, product, country = current_request.inquiry, current_request.product_info, current_request.customer_country or ""
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
    if config.mock_mode:
        render_customer_simulator(current_request, config, ready, generation_service=generation_service, auth_token=auth_token)
    render_knowledge_preview(product, country=country.strip() or None)
