"""Explicit, isolated local fallback tests; never change a business Case."""
import streamlit as st

from core.errors import CrossBotError
from providers.mock_client import MockChatClient
from providers.listing_mock import MockListingClient
from workflows.animal_lamp_demo import animal_listing_request, animal_sales_request
from workflows.conversation import start_sales
from workflows.listing import generate_listings
from workflows.result_ui import render_generation_result


def render_offline():
    st.title("断网保底测试")
    st.caption("独立本地测试 · 固定动物台灯样例 · 无需 API Key")
    st.info("这里只验证本地模拟流程。不会发送网络请求、写入当前 Case 或替换真实模型结果。W3 原有本地上传与解析可在单证工作台使用。")
    columns = st.columns(2)
    try:
        if columns[0].button("运行 W1 本地测试", key="offline_sales"):
            st.session_state["offline_sales_result"] = start_sales(animal_sales_request(), MockChatClient())
        if columns[1].button("运行 W2 本地测试", key="offline_listing"):
            st.session_state["offline_listing_result"] = generate_listings(animal_listing_request(), MockListingClient())
    except CrossBotError as error:
        st.error(error.message)
    conversation = st.session_state.get("offline_sales_result")
    if conversation:
        st.subheader("W1 · 本地模拟结果")
        render_generation_result(conversation.turns[-1].response)
    for platform, result in st.session_state.get("offline_listing_result", {}).items():
        st.subheader(f"W2 · {platform} · 本地模拟结果")
        render_generation_result(result)
