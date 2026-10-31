import streamlit as st

from local_lab.dialogue import ChangeProposal, interpret_change, proposed_context
from local_lab.deepseek_client import build_context, GenerationError
from local_lab.revisions import compare_contexts


def render_dialogue(product, quantity, payment_terms):
    st.subheader("用一句话修订报价")
    st.write("例如：数量改成 500 个，付款条件不变。先让 AI 理解，核对变化后应用，再到「检查并生成」生成新版。")
    st.caption("本步支持数量和付款条件。理解请求最多输出 700 tokens；生成新版另调用一次，两次均消耗 API 额度。")
    context = None
    if product is not None:
        try:
            context = build_context(product, quantity, payment_terms)
        except ValueError:
            pass
    message = st.text_input("本轮修改要求", key="change_message", max_chars=1000)
    pending = st.session_state.get("pending_change")
    if pending and (pending["base"] != context or pending["message"] != message):
        st.session_state.pop("pending_change", None)
    if st.button("让 AI 理解修改", key="interpret_change", disabled=context is None):
        st.session_state.pop("pending_change", None)
        try:
            with st.spinner("正在理解修改要求……"):
                proposal = interpret_change(st.session_state.get("deepseek_api_key", ""), message, context)
            st.session_state.pending_change = {"base": context, "message": message, "proposal": proposal.model_dump()}
        except GenerationError as exc:
            st.error(str(exc))
    pending = st.session_state.get("pending_change")
    if pending:
        proposal = ChangeProposal.model_validate(pending["proposal"])
        if proposal.clarification:
            st.warning(proposal.clarification)
        if proposal.unsupported:
            st.warning("本步暂不支持：" + "；".join(proposal.unsupported))
        try:
            updated = proposed_context(product, quantity, payment_terms, proposal)
        except ValueError as exc:
            st.info(str(exc))
        else:
            st.table(compare_contexts(context, updated))
            def apply_change():
                st.session_state.quote_quantity = updated["quantity"]
                st.session_state.payment_terms = updated["payment_terms"]
                st.session_state.applied_dialogue = {"context": updated, "user_message": message,
                                                    "feedback": compare_contexts(context, updated)}
                st.session_state.pop("pending_change", None)
            st.button("应用已核对的修改", on_click=apply_change, key="apply_change")
    active = st.session_state.get("applied_dialogue")
    if active and active["context"] == context:
        st.info("本轮修改已应用。请到「检查并生成」点击生成，新版本会记录本轮要求和变化。")


from hashlib import sha256
from pathlib import Path
SOURCE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()
