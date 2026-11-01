"""Read-only current-input preview; historical responses retain their own citations."""
import streamlit as st
from core.errors import CrossBotError
from knowledge.retrieval import retrieve_context


def render_knowledge_preview(product_text, *, platforms=(), country=None):
    with st.expander("知识库来源与适用条件（当前输入预览）"):
        st.caption("支持固定演示SKU：DEMO-LAMP-001、DEMO-CUP-001、DEMO-DE-LAMP-001、DEMO-ANIMAL-LAMP-001。动物台灯参数为自编演示资料；风险词库为项目内部提示，不是官方违禁词全集。历史引用以各版结果为准。")
        try:
            context = retrieve_context(product_text, platforms=platforms, country=country)
        except CrossBotError as error:
            st.error(error.message)
            return
        if context["product_status"] == "unknown":
            st.warning("产品资料：未知、待确认")
        else:
            st.info("产品资料：命中模拟参考")
        for note in context["notes"]:
            st.write(note)
        st.json(context)
