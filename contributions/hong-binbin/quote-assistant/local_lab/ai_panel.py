"""One explicit model call per click; changing source data invalidates old drafts."""
import streamlit as st

from local_lab.deepseek_client import MODEL, GenerationError, build_context, generate_draft


def render_ai_panel(product, quantity, payment_terms):
    st.subheader("第五步：AI 生成两份草稿")
    st.write("使用上方所选商品、采购数量和付款条件，生成英文客户回复与中文内部核对清单。")
    st.caption(f"DeepSeek 官方 API · {MODEL} · 仅点击生成时发送这些资料并消耗 API 额度；每次最多输出 1800 tokens，无自动重试。")
    def clear_key():
        st.session_state["deepseek_api_key"] = ""
    st.text_input("DeepSeek API Key（仅本机会话）", type="password", key="deepseek_api_key")
    st.button("清除密钥", on_click=clear_key, key="clear_api_key")
    st.caption("密钥只保留在当前页面会话内存，不写入项目文件；请求仅发送到 api.deepseek.com。不要把密钥发到聊天中。")
    context = None
    if product is not None:
        try:
            context = build_context(product, quantity, payment_terms)
        except ValueError:
            pass
    if context is None:
        st.info("先选择有效商品并填写满足最低订购量的数量，再生成草稿。")
    else:
        with st.expander("查看将发送给模型的资料（不含密钥）"):
            st.json(context)
    saved = st.session_state.get("ai_draft")
    if saved and saved["context"] != context:
        st.session_state.pop("ai_draft", None)
        st.info("报价资料已变化，旧 AI 草稿已移除，请重新生成。")
    if st.button("生成英文回复与中文清单", key="generate_ai", disabled=context is None):
        st.session_state.pop("ai_draft", None)
        try:
            with st.spinner("正在请求 DeepSeek，请稍候……"):
                draft = generate_draft(st.session_state.get("deepseek_api_key", ""), context)
            st.session_state["ai_draft"] = {"context": context, "draft": draft.model_dump()}
        except GenerationError as exc:
            st.error(str(exc))
    saved = st.session_state.get("ai_draft")
    if saved:
        st.caption("以下为模型返回的草稿，需核对金额、语言和未确认条件后使用；没有自动发送给客户。")
        st.write("英文客户回复")
        st.text(saved["draft"]["english_reply"])
        st.write("中文采购/跟单核对清单")
        st.text(saved["draft"]["chinese_checklist"])
