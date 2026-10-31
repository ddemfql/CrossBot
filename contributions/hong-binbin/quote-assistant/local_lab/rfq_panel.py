"""Read user-selected simulation inputs and display a platform-reference check."""
import json
import streamlit as st
from local_lab.rfq_check import SCENES, build_rfq_report


def show_rfq_report(report, key_prefix="current_rfq"):
    if report["status"] == "mismatch":
        st.warning(report["status_label"])
    else:
        st.info(report["status_label"])
    if report["scene"] == "alibaba_rfq":
        product = report["input_product"] or {}
        st.table([{"检查项": "材质", "买家要求": report["input_requested_material"] or "未填写",
                   "报价商品": product.get("material") or "未提供", "结果": report["status_label"]}])
    st.write("处理建议：" + report["suggestion"])
    st.caption(report["freshness"])
    with st.expander("P01 官方依据与预检范围"):
        st.write(report["basis"])
        st.link_button(report["reference_title"], report["reference_url"], key=key_prefix + "_source")
        st.caption(report["reference_version"] + "；查阅日期：" + report["reviewed_on"])
        st.link_button("前往当前规则中心人工核对", report["current_rule_url"], key=key_prefix + "_latest")
        st.write(report["implementation"])
        st.caption(report["limitation"])


def render_rfq_inputs(product):
    st.caption("RFQ 是买家的采购需求。选择场景后，填写明确的买家材质要求。")
    scene = st.selectbox("本次业务场景", options=list(SCENES), format_func=SCENES.get, key="rfq_scene")
    required = st.text_input("买家要求的材质（模拟 RFQ）", key="rfq_required_material", max_chars=200,
                             placeholder="例如：涤纶；请填写明确要求，不用猜测", disabled=scene != "alibaba_rfq")
    report = build_rfq_report(scene, required, product)
    st.caption(report["simulation_notice"])
    return report


def render_rfq_result(report):
    show_rfq_report(report)
    st.caption("以下下载仅包含当前 RFQ 预检，不含报价草稿或历史。全部版本记录请到「3 修订与下载」→「查看版本与下载」。")
    st.download_button("下载当前 RFQ 预检（JSON）", data=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8-sig"),
                       file_name="rfq-material-check.json", mime="application/json", key="download_rfq_check")
    return report


def render_rfq_check(product):
    report = render_rfq_inputs(product)
    return render_rfq_result(report)


from hashlib import sha256
from pathlib import Path
SOURCE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()
