"""W2 page orchestration. Stale results are hidden when selection or facts change."""
from copy import deepcopy
import streamlit as st

from core.errors import CrossBotError
from core.schemas import ListingRequest
from providers.listing_mock import MockListingClient
from workflows.listing import PLATFORM_STYLES, generate_listings
from knowledge.ui import render_knowledge_preview
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


def render_listing(config=None, ready=True, *, case_id=None, generation_service=None, auth_token=None):
    config = config or AppConfig(mock_mode=True)
    mode = "模拟" if config.mock_mode else "真实，消耗额度"
    st.subheader("W2 · 按所选平台生成文案")
    st.button("载入德国台灯演示资料", key="listing_demo", on_click=load_listing_demo)
    st.button("载入动物台灯固定案例（自编演示数据）", key="listing_animal_lamp_demo", on_click=load_animal_lamp_listing_demo)
    st.caption(f"当前 Case：{case_id or '未关联'}。载入案例只填充产品资料和目标市场；输出平台与语言保持当前选择。")
    st.info("模拟模式：不调用真实模型，不消耗API额度；仅验证所选平台与资料传递，不执行翻译。" if config.mock_mode else "真实模式：点击生成会发送产品和所选平台资料，消耗额度。知识条目仍为模拟参考，输出须人工核对。")
    for key, value in {"listing_description": "DEMO-CUP-001\n便携式咖啡杯，316不锈钢\n容量：350ml",
                       "listing_platforms": ["Amazon", "TikTok"]}.items():
        if key not in st.session_state:
            st.session_state[key] = value
    description = st.text_area("中文产品描述", key="listing_description")
    platforms = st.multiselect("重点适配平台", list(PLATFORM_STYLES), key="listing_platforms")
    language = st.selectbox("文案输出语言", ["英文", "德文", "中文"], key="listing_language",
                            help="决定标题、卖点和描述的语言，与产品资料原文及目标市场无关。模拟模式不执行翻译。")
    country = st.text_input("目标市场（可留空）", key="listing_country")
    render_knowledge_preview(description, platforms=platforms, country=country.strip() or None)
    if not platforms:
        st.warning("请至少选择一个平台。")
    if st.button(f"生成所选平台文案（{mode}）", key="listing_generate", disabled=not platforms or not ready):
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
        return
    current = (description, platforms, language, country.strip() or None)
    previous = (saved.product_description, saved.platforms, saved.target_language, saved.target_country)
    if current != previous:
        st.caption("输入或平台已修改，旧结果暂不显示。生成成功后显示本次结果。")
        return
    with st.expander("核对本次结构化输入"):
        st.json(saved.to_dict())
    for platform, response in st.session_state.listing_results.items():
        label = "模拟结果" if response.provider == "mock" else "真实模型结果" if response.provider == "real" else "未标注来源"
        st.subheader(f"{platform} · {label}")
        st.caption(f"本版来源：{response.provider or '未标注'}；模型：{response.model or '未标注'}")
        render_generation_result(response)
