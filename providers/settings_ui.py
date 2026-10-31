"""Session-only model settings. Reading configuration never performs a request."""
import streamlit as st
from core.config import AppConfig
from core.errors import CrossBotError
from providers.llm_client import HttpChatClient, resolve_api_endpoint


def clear_key():
    st.session_state.provider_key = ""
    st.session_state.provider_confirm = False


def clear_confirmation():
    st.session_state.provider_confirm = False


def render_provider_settings():
    mode = st.radio("模型模式", ["模拟", "真实"], key="provider_mode")
    mock = mode == "模拟"
    if mock:
        st.caption("模拟：只验证流程，不发送网络请求。")
        return AppConfig(mock_mode=True), True
    with st.expander("真实模型设置（仅本机会话）", expanded=True):
        base = st.text_input("服务基础地址", "https://api.tokenhubcloud.net", key="provider_url", on_change=clear_key)
        protocol_label = st.selectbox("接口协议", ["自动", "Responses（推荐）", "Chat Completions（备用）"],
                                      key="provider_protocol", on_change=clear_confirmation)
        protocol = {"自动": "auto", "Responses（推荐）": "responses",
                    "Chat Completions（备用）": "chat_completions"}[protocol_label]
        model = st.text_input("模型名称", "gpt-6.1-sol", key="provider_model", on_change=clear_confirmation)
        key = st.text_input("API Key", type="password", key="provider_key")
        st.button("清除密钥", key="provider_clear", on_click=clear_key)
        st.caption("TokenHubCloud 可填 https://api.tokenhubcloud.net，自动使用 /v1/responses；其他兼容服务可手动选协议。更换地址会清除密钥；模型名和额度以对应平台控制台为准。")
        endpoint_valid = True
        try:
            endpoint, resolved_protocol = resolve_api_endpoint(base, protocol)
            st.caption(f"本次接口：{endpoint} · {resolved_protocol}")
        except CrossBotError as error:
            endpoint_valid = False
            st.warning(str(error))
        confirmed = st.checkbox("我确认将当前模拟业务资料及历史草稿发送至上述服务，并使用账户额度。", key="provider_confirm")
        st.caption("只有点击生成或测试连接才请求；测试仅发送固定问候，不发送 Case 资料，也可能消耗额度。网络等待60秒，不自动重试。")
        config = AppConfig(openai_api_key=key, openai_base_url=base, model_name=model,
                           mock_mode=False, request_timeout_seconds=60, model_api_protocol=protocol)
        ready = bool(confirmed and key.strip() and base.strip() and model.strip() and endpoint_valid)
        if st.button("测试连接（消耗少量额度）", key="provider_test", disabled=not ready):
            try:
                with st.spinner("正在发送固定测试问候…"):
                    HttpChatClient(config).complete([{"role": "user", "content": "Reply with OK only."}])
                st.success("连接成功，当前地址、密钥和模型可完成文本请求。")
            except CrossBotError as error:
                st.error(f"连接测试失败：{error}")
    if not ready:
        st.warning("请补全本机设置并确认后再生成。")
    return config, ready
