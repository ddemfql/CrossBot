"""Session-only model settings. Reading configuration never performs a request."""
import streamlit as st
from core.config import AppConfig


def clear_key():
    st.session_state.provider_key = ""
    st.session_state.provider_confirm = False


def render_provider_settings():
    mode = st.radio("模型模式", ["模拟", "真实"], key="provider_mode")
    mock = mode == "模拟"
    if mock:
        st.caption("模拟：只验证流程，不发送网络请求。")
        return AppConfig(mock_mode=True), True
    with st.expander("真实模型设置（仅本机会话）", expanded=True):
        base = st.text_input("服务基础地址", "https://api.openai.com/v1", key="provider_url", on_change=clear_key)
        model = st.text_input("模型名称", "gpt-6.1-sol", key="provider_model")
        key = st.text_input("API Key", type="password", key="provider_key")
        st.button("清除密钥", key="provider_clear", on_click=clear_key)
        st.caption("默认 OpenAI GPT API（Responses API）；更换为兼容服务地址和模型后，使用 Chat Completions。更换地址会清除密钥。API Key 在 OpenAI API Platform 管理；可用额度与费用以 API 组织账户为准。")
        confirmed = st.checkbox("我确认将当前模拟业务资料及历史草稿发送至上述服务，并使用账户额度。", key="provider_confirm")
        st.caption("点击生成才请求；网络等待60秒，W1最多1800/W2最多4096输出tokens；不自动重试，手动重试可能增加费用。")
    ready = bool(confirmed and key.strip() and base.strip() and model.strip())
    if not ready:
        st.warning("请补全本机设置并确认后再生成。")
    return AppConfig(openai_api_key=key, openai_base_url=base, model_name=model,
                     mock_mode=False, request_timeout_seconds=60), ready
