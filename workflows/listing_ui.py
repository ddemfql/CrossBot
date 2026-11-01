"""W2 page orchestration. Stale results are hidden when selection or facts change."""
from copy import deepcopy
import streamlit as st

from core.errors import CrossBotError
from core.schemas import ListingRequest
from providers.listing_mock import MockListingClient
from workflows.listing import PLATFORM_STYLES, generate_listings, revise_listings, listing_knowledge_context
from core.config import AppConfig
from providers.llm_client import HttpChatClient
from workflows.demo_case import demo_listing_request
from workflows.animal_lamp_demo import animal_listing_request
from workflows.result_ui import render_generation_result


def load_listing_demo():
    request = demo_listing_request()
    for key, value in {"listing_description": request.product_description,
                       "listing_country": request.target_country}.items():
        st.session_state[key] = value


def load_animal_lamp_listing_demo():
    request = animal_listing_request()
    for key, value in {"listing_description": request.product_description,
                       "listing_country": request.target_country}.items():
        st.session_state[key] = value


def render_listing(config=None, ready=True, *, case_id=None, generation_service=None, auth_token=None, step=None):
    config = config or AppConfig(mock_mode=True)
    mode = "模拟" if config.mock_mode else "真实，消耗额度"
    st.subheader("W2 · 按所选平台生成文案")
    if config.mock_mode and step in (None, 1):
        with st.expander("演示资料与案例"):
            st.button("载入德国台灯演示资料", key="listing_demo", on_click=load_listing_demo)
            st.button("载入动物台灯固定案例（自编演示数据）", key="listing_animal_lamp_demo", on_click=load_animal_lamp_listing_demo)
            st.caption("仅填充业务输入，保留当前平台、语言和历史；不会自动生成。")
    st.caption("模拟生成 · 本地运行，不消耗额度，不执行翻译" if config.mock_mode else "真实生成 · 使用所配置的模型服务与账户额度")
    for key, value in {"listing_description": "DEMO-CUP-001\n便携式咖啡杯，316不锈钢\n容量：350ml",
                       "listing_platforms": ["Amazon", "TikTok"]}.items():
        if key not in st.session_state:
            st.session_state[key] = value if config.mock_mode or step is None or key == "listing_platforms" else ""
    if step in (None, 1):
        with st.container(border=True):
            description = st.text_area("中文产品描述", key="listing_description", height=190)
            platforms = st.multiselect("重点适配平台", list(PLATFORM_STYLES), key="listing_platforms")
            columns = st.columns(2)
            language = columns[0].selectbox("文案输出语言", ["英文", "德文", "中文"], key="listing_language",
                            help="决定标题、卖点和描述的语言，与产品资料原文及目标市场无关。模拟模式不执行翻译。")
            country = columns[1].text_input("目标市场（可留空）", key="listing_country")
        with st.expander("已发布知识与平台规则（当前输入）"):
            try:
                auth = generation_service.auth if generation_service is not None else None
                if auth is not None and case_id:
                    from core.identity import Permission
                    auth.require_permission(auth_token, Permission.CASE_READ, case_id)
                preview = listing_knowledge_context(
                    ListingRequest(description, list(platforms), country.strip() or None, language),
                    auth=auth, auth_token=auth_token)
            except CrossBotError as error:
                st.error(error.message)
            else:
                if preview["database_status"] == "not_connected":
                    st.caption("未接入授权数据库；产品知识未知。")
                if preview["product_status"] == "unknown":
                    st.caption("未命中唯一的已发布产品，产品事实待确认。")
                st.json(preview)
        if step == 1:
            st.button("下一步 · 生成文案", key="listing_next", disabled=not platforms, on_click=_next_step)
            return
    else:
        description = st.session_state.get("listing_description", "")
        platforms = st.session_state.get("listing_platforms", [])
        language = st.session_state.get("listing_language", "英文")
        country = st.session_state.get("listing_country", "")
    if step == 2:
        st.caption(f"输出语言：{language} · 平台：{' / '.join(platforms)} · 市场：{country or '未填'}")
    if not platforms:
        st.warning("请至少选择一个平台。")
    if step in (None, 2) and st.button(f"生成所选平台文案（{mode}）", key="listing_generate", disabled=not platforms or not ready):
        context = None
        if case_id:
            from core.v3 import RequestContext
            context = RequestContext(case_id)
        request = ListingRequest(description, list(platforms), country.strip() or None, language, context=context)
        try:
            client = MockListingClient() if config.mock_mode else HttpChatClient(config, json_mode=True)
            results = generate_listings(request, client, generation_service=generation_service, auth_token=auth_token)
        except CrossBotError as error:
            st.error(error.message)
        else:
            # Commit the whole successful batch atomically; do not save partial platform output.
            st.session_state.listing_request = deepcopy(request)
            st.session_state.listing_results = results
    saved = st.session_state.get("listing_request")
    if saved is None:
        st.info("填写资料后生成文案，成功记录会在这里显示。")
        return
    current = (description, platforms, language, country.strip() or None)
    previous = (saved.product_description, saved.platforms, saved.target_language, saved.target_country)
    if current != previous and step != 3:
        st.caption("输入或平台已修改，旧结果暂不显示。生成成功后显示本次结果。")
        return
    with st.expander("核对本次结构化输入"):
        st.json(saved.to_dict())
    if current == previous:
        feedback_key = f"listing_feedback_{case_id or 'local'}_{saved.correlation_id}"
        feedback = st.text_area("修改要求", key=feedback_key)
        if st.button(f"修订所选平台文案（{mode}）", key="listing_revise", disabled=not ready or not feedback.strip()):
            try:
                client = MockListingClient() if config.mock_mode else HttpChatClient(config, json_mode=True)
                revised = revise_listings(saved, st.session_state.listing_results, feedback, client,
                                          generation_service=generation_service, auth_token=auth_token)
            except CrossBotError as error:
                st.error(error.message)
            else:
                st.session_state.listing_results = revised
    for platform, response in st.session_state.listing_results.items():
        label = "模拟结果" if response.provider == "mock" else "真实模型结果" if response.provider == "real" else "未标注来源"
        st.subheader(f"{platform} · {label}")
        st.caption(f"本版来源：{response.provider or '未标注'}；模型：{response.model or '未标注'}")
        render_generation_result(response)
    if step == 3:
        if current != previous:
            st.warning("以下为已保存的历史输入与结果，当前表单的新改动尚未生成。")
        st.info("请核对事实、语言、风险与待确认项；导出为草稿，不会自动发布到平台。")


def _next_step():
    st.session_state["listing_step"] = 2
