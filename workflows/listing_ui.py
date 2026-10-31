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


def render_listing(config=None, ready=True):
    config = config or AppConfig(mock_mode=True)
    mode = "模拟" if config.mock_mode else "真实，消耗额度"
    st.subheader("W2 · 按所选平台生成文案")
    st.info("模拟模式：不调用真实模型，不消耗API额度；仅验证所选平台与资料传递，不执行翻译。" if config.mock_mode else "真实模式：点击生成会发送产品和所选平台资料，消耗额度。知识条目仍为模拟参考，输出须人工核对。")
    description = st.text_area("中文产品描述", "DEMO-CUP-001\n便携式咖啡杯，316不锈钢\n容量：350ml", key="listing_description")
    platforms = st.multiselect("重点适配平台", list(PLATFORM_STYLES), default=["Amazon", "TikTok"], key="listing_platforms")
    language = st.selectbox("目标语言", ["英文", "德文"], key="listing_language")
    country = st.text_input("目标市场（可留空）", key="listing_country")
    render_knowledge_preview(description, platforms=platforms, country=country.strip() or None)
    if not platforms:
        st.warning("请至少选择一个平台。")
    if st.button(f"生成所选平台文案（{mode}）", key="listing_generate", disabled=not platforms or not ready):
        request = ListingRequest(description, list(platforms), country.strip() or None, language)
        try:
            client = MockListingClient() if config.mock_mode else HttpChatClient(config, json_mode=True)
            results = generate_listings(request, client)
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
        st.write(response.content)
        for message in response.pending_confirmations:
            st.warning(message)
