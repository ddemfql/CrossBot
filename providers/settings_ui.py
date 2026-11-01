"""Per-user model settings. Reading configuration never performs a request."""
from dataclasses import dataclass, field
import hashlib

import streamlit as st
from core.config import AppConfig
from core.errors import CrossBotError, ErrorCode
from providers.llm_client import HttpChatClient, resolve_api_endpoint
from providers.user_settings import ProviderSettings, UserSettingsRepository

PROTOCOL_LABELS = {"auto": "自动", "responses": "Responses（推荐）",
                   "chat_completions": "Chat Completions（备用）"}


@dataclass(repr=False)
class _SettingsContext:
    repository: UserSettingsRepository
    token: str = field(repr=False)
    principal: object = field(repr=False)


def _clear_provider_state():
    for name in list(st.session_state):
        if name.startswith(("provider_", "_provider_")):
            del st.session_state[name]


def load_user_provider_settings(auth, token, principal, *, cipher=None):
    """Call after login validation, before rendering provider widgets, on every run."""
    try:
        current = auth.require_session(token)
        if current.user_id != principal.user_id:
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "配置不属于当前登录账号")
    except CrossBotError:
        _clear_provider_state()
        raise
    marker = (auth.store.path, current.user_id, hashlib.sha256(token.encode()).hexdigest())
    if st.session_state.get("_provider_owner") != marker:
        _clear_provider_state()
        repository = UserSettingsRepository(auth, cipher=cipher)
        try:
            settings = repository.load(token, current)
        except CrossBotError as error:
            if error.code != ErrorCode.CONFIGURATION:
                raise
            settings = ProviderSettings()
            st.session_state["_provider_notice"] = error.message
        st.session_state.update({"provider_url": settings.url, "provider_model": settings.model,
                                 "provider_protocol": PROTOCOL_LABELS[settings.protocol],
                                 "provider_key": settings.api_key, "provider_remember": settings.remember,
                                 "provider_confirm": False, "_provider_owner": marker,
                                 "_provider_context": _SettingsContext(repository, token, current)})
    else:
        st.session_state["_provider_context"].principal = current
    if "provider_remember" in st.session_state:
        st.session_state["provider_remember"] = st.session_state["provider_remember"]


def _delete_saved_settings():
    st.session_state["provider_remember"] = False
    context = st.session_state.get("_provider_context")
    if context:
        try:
            context.repository.delete(context.token, context.principal)
        except CrossBotError:
            st.session_state["_provider_notice"] = "已保存的配置未能删除，请重新登录后重试"


def _forget_remembered_key():
    if not st.session_state.get("provider_remember"):
        _delete_saved_settings()


def _save_settings():
    context = st.session_state.get("_provider_context")
    if context is None:
        return
    try:
        context.repository.save(context.token, context.principal, ProviderSettings(
            url=st.session_state.get("provider_url", ""), model=st.session_state.get("provider_model", ""),
            protocol={label: protocol for protocol, label in PROTOCOL_LABELS.items()}.get(
                st.session_state.get("provider_protocol"), "auto"),
            api_key=st.session_state.get("provider_key", ""),
            remember=st.session_state.get("provider_remember", False)))
        st.success("设置已保存")
    except CrossBotError as error:
        st.error(error.message)


def clear_key():
    st.session_state.provider_key = ""
    st.session_state.provider_confirm = False
    st.session_state.provider_remember = False
    _delete_saved_settings()


def clear_confirmation():
    st.session_state.provider_confirm = False


def session_provider_config(*, mock=False):
    """Read configuration without rendering widgets or making network calls."""
    if mock:
        return AppConfig(mock_mode=True), True
    base = st.session_state.get("provider_url", "https://api.tokenhubcloud.net")
    model = st.session_state.get("provider_model", "gpt-6.1-sol")
    key = st.session_state.get("provider_key", "")
    protocol = {"自动": "auto", "Responses（推荐）": "responses",
                "Chat Completions（备用）": "chat_completions"}.get(st.session_state.get("provider_protocol"), "auto")
    valid = True
    try:
        resolve_api_endpoint(base, protocol)
    except CrossBotError:
        valid = False
    config = AppConfig(openai_api_key=key, openai_base_url=base, model_name=model,
                       mock_mode=False, request_timeout_seconds=60, model_api_protocol=protocol)
    return config, bool(valid and key.strip() and base.strip() and model.strip() and st.session_state.get("provider_confirm"))


def render_provider_settings(*, mode=None, show_test=True):
    mode = mode or st.radio("模型模式", ["模拟", "真实"], key="provider_mode")
    mock = mode == "模拟"
    if mock:
        st.caption("模拟：只验证流程，不发送网络请求。")
        return AppConfig(mock_mode=True), True
    with st.expander("真实模型设置", expanded=True):
        if notice := st.session_state.pop("_provider_notice", None):
            st.warning(notice)
        st.session_state.setdefault("provider_url", "https://api.tokenhubcloud.net")
        st.session_state.setdefault("provider_model", "gpt-6.1-sol")
        base = st.text_input("服务基础地址", key="provider_url", on_change=clear_key)
        protocol_label = st.selectbox("接口协议", ["自动", "Responses（推荐）", "Chat Completions（备用）"],
                                      key="provider_protocol", on_change=clear_confirmation)
        protocol = {"自动": "auto", "Responses（推荐）": "responses",
                    "Chat Completions（备用）": "chat_completions"}[protocol_label]
        model = st.text_input("模型名称", key="provider_model", on_change=clear_confirmation)
        key = st.text_input("API Key", type="password", key="provider_key", on_change=clear_confirmation)
        st.button("清除密钥", key="provider_clear", on_click=clear_key)
        if st.session_state.get("_provider_context"):
            st.checkbox("记住此账号的 API Key", key="provider_remember", on_change=_forget_remembered_key)
            if st.button("保存设置", key="provider_save"):
                _save_settings()
            st.button("删除已保存配置", key="provider_forget", on_click=_delete_saved_settings)
        st.caption("TokenHubCloud 可填 https://api.tokenhubcloud.net，自动使用 /v1/responses；其他兼容服务可手动选协议。更换地址会清除密钥；模型名和额度以对应平台控制台为准。")
        endpoint_valid = True
        try:
            endpoint, resolved_protocol = resolve_api_endpoint(base, protocol)
            st.caption(f"本次接口：{endpoint} · {resolved_protocol}")
        except CrossBotError as error:
            endpoint_valid = False
            st.warning(str(error))
        confirmed = st.checkbox("我确认将当前业务资料及历史草稿发送至上述服务，并使用账户额度。", key="provider_confirm")
        st.caption("只有点击生成才发送业务资料。网络等待60秒，不自动重试。勾选记住并保存后，密钥由 Windows 加密保存在当前账号下。")
        if show_test:
            st.caption("测试连接仅发送固定问候，不发送 Case 资料，也可能消耗额度。")
        config = AppConfig(openai_api_key=key, openai_base_url=base, model_name=model,
                           mock_mode=False, request_timeout_seconds=60, model_api_protocol=protocol)
        ready = bool(confirmed and key.strip() and base.strip() and model.strip() and endpoint_valid)
        if show_test and st.button("测试连接（消耗少量额度）", key="provider_test", disabled=not ready):
            try:
                with st.spinner("正在发送固定测试问候…"):
                    HttpChatClient(config).complete([{"role": "user", "content": "Reply with OK only."}])
                st.success("连接成功，当前地址、密钥和模型可完成文本请求。")
            except CrossBotError as error:
                message = str(error).replace(key, "[已隐藏]") if key else str(error)
                st.error(f"连接测试失败：{message}")
    if not ready:
        st.warning("请补全本机设置并确认后再生成。")
    return config, ready
