"""Download-only controls, safe to add to an existing Streamlit session."""
import streamlit as st
from core.errors import CrossBotError
from workflows.exports import business_snapshot, export_bundle, json_bytes, text_bytes


def render_downloads():
    st.divider()
    st.subheader("备份与下载（已有成功记录）")
    st.caption("只导出已保存的W1历史和W2结果，不调用模型，不包含密钥或模型设置。输入框的新改动不会覆盖历史。")
    try:
        snapshot = business_snapshot(st.session_state)
        if snapshot["w1"] is None and snapshot["w2"] is None:
            st.caption("当前没有成功记录可下载。")
            return
        bundle = export_bundle(snapshot)
        st.download_button("下载全部业务记录（JSON）", data=json_bytes(bundle),
                           file_name="crossbot-business-history.json", mime="application/json",
                           key="download_business_json", on_click="ignore")
        if snapshot["w1"] is not None:
            count = len(snapshot["w1"]["versions"])
            st.caption(f"已保存W1：{count}版。下载前核对版本数与页面一致。")
            st.download_button("下载W1全部版本（TXT）", data=text_bytes(bundle, "w1"),
                               file_name="crossbot-w1-history.txt", mime="text/plain",
                               key="download_w1_txt", on_click="ignore")
        if snapshot["w2"] is not None:
            st.caption("已保存W2平台：" + "、".join(snapshot["w2"]["results"]))
            st.download_button("下载W2结果（TXT）", data=text_bytes(bundle, "w2"),
                               file_name="crossbot-w2-results.txt", mime="text/plain",
                               key="download_w2_txt", on_click="ignore")
    except CrossBotError as error:
        st.error(error.message)
