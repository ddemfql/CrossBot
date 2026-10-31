"""Small no-network review exercise and saved output check display."""
import json
import streamlit as st
from local_lab.payment_wording import check_payment_wording, RISK_EXAMPLE, PENDING_EXAMPLE


def show_wording_report(report):
    if report["status"] == "risk":
        st.warning(report["status_label"])
    else:
        st.info(report["status_label"])
    for finding in report["findings"]:
        st.write("风险标记：疑似误写" if finding["severity"] == "risk" else "风险标记：人工判断")
        st.text(finding["original"])
        st.write("文字批注：" + finding["reason"])
        st.write("建议表达（人工核对后使用）：")
        st.text(finding["suggestion"])
        st.caption(finding["suggestion_zh"])
    st.caption(report["limitation"])
    with st.expander("C03 依据与适用范围"):
        st.write(report["basis"])
        st.write(report["category"])
        st.write(report["reference_note"])
        st.link_button(report["reference_title"], report["reference_url"])
        st.caption("本地规则来源：" + report["source"])


def render_wording_practice(payment_terms, record=None):
    st.subheader("检查英文付款表达")
    st.write("先用示例体验检查。这里只调用本地规则，不消耗 API 额度，不改动报价或历史草稿。")
    st.caption("本应用未记录双方接受条款的证据；已填写付款条件不代表双方已同意。")
    if record:
        st.write(f"V{record['version']} 的英文检查")
        if record.get("wording_report"):
            show_wording_report(record["wording_report"])
        else:
            st.caption("此旧版本未保存英文检查报告，不会自动补写；可把原稿复制到下面单独检查。")
    def load_example(text):
        st.session_state.wording_practice = text
        st.session_state.pop("wording_practice_report", None)
    st.button("填入误写示例", key="wording_risk_example", on_click=load_example, args=(RISK_EXAMPLE,))
    st.button("填入待确认示例", key="wording_pending_example", on_click=load_example, args=(PENDING_EXAMPLE,))
    if "wording_practice" not in st.session_state:
        st.session_state.wording_practice = RISK_EXAMPLE
    text = st.text_area("待检查的英文（练习区）", key="wording_practice", max_chars=12000)
    if st.button("检查这段英文（不调用 AI）", key="check_wording", disabled=not text.strip()):
        st.session_state.wording_practice_report = check_payment_wording(text, payment_terms)
    report = st.session_state.get("wording_practice_report")
    if report and report["input_english_reply"] == text and report["input_payment_terms"] == payment_terms:
        show_wording_report(report)
        st.download_button("下载本次练习检查（JSON）", data=json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8-sig"),
                           file_name="payment-wording-practice.json", mime="application/json", key="download_wording")
    elif report:
        st.info("输入已改变，请重新检查；旧结果不代表当前文字。")


from hashlib import sha256
from pathlib import Path
SOURCE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()
