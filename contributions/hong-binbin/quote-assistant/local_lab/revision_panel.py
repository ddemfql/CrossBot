"""Generation and session revision history; compatible with a step-five session."""
import streamlit as st
from contextlib import nullcontext
from local_lab.dialogue_panel import render_dialogue
from hashlib import sha256
from pathlib import Path

from local_lab.deepseek_client import MODEL, GenerationError, build_context, generate_draft
from local_lab.revisions import append_version, compare_contexts, export_history, export_text, request_context
from local_lab.revisions import attach_current_dialogue, business_context
from local_lab.payment_ratio import check_payment_ratio
from local_lab.check_report import build_check_report
from local_lab.payment_wording import check_payment_wording
from local_lab.payment_wording_panel import render_wording_practice, show_wording_report
from local_lab.rfq_panel import render_rfq_check, show_rfq_report
from local_lab.rfq_check import report_request
from local_lab.history_recovery import restore_archived_rfq, restore_demo_round1

SOURCE_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()


def render_ai_panel(product, quantity, payment_terms, *, platform_report=None,
                    generation_container=None, history_container=None):
    # Migrate the existing successful draft before invalidating it on input changes.
    if "quote_history" not in st.session_state:
        old = st.session_state.get("ai_draft")
        st.session_state.quote_history = append_version([], old["context"], old["draft"]) if old else []
    history = st.session_state.quote_history
    if platform_report is None:
        platform_report = render_rfq_check(product)
    with generation_container if generation_container is not None else nullcontext():
        st.divider()
        st.subheader("生成两份业务草稿")
        st.write("使用「填写需求」中的商品、采购数量和付款条件，生成英文客户回复与中文内部核对清单。")
        st.caption(f"DeepSeek 官方 API · {MODEL} · 仅点击生成时消耗 API 额度；最多输出 1800 tokens，无自动重试。新版请求会附带上一版业务资料和变化项。")
        def clear_key():
            st.session_state["deepseek_api_key"] = ""
        with st.expander("模型设置 · DeepSeek 密钥"):
            st.text_input("DeepSeek API Key（仅本机会话）", type="password", key="deepseek_api_key")
            st.button("清除密钥", on_click=clear_key, key="clear_api_key")
            st.caption("密钥只保留在当前页面会话内存，不写入项目文件或下载文件；请求仅发送到 api.deepseek.com。")
        context = None
        if product is not None:
            try:
                context = build_context(product, quantity, payment_terms)
            except ValueError:
                pass
        previous = history[-1] if history else None
        report = build_check_report(payment_terms)
        active = st.session_state.get("applied_dialogue")
        if context and active and active["context"] == context:
            context["dialogue"] = {"user_message": active["user_message"], "feedback": active["feedback"]}
        if context and report_request(platform_report):
            context["platform_request"] = report_request(platform_report)
        if context is None:
            st.info("先选择有效商品并填写满足最低订购量的数量，再生成草稿。")
        else:
            st.write(f"本次报价：{context['product']['name']} · {quantity:,} 个 · 单价 {context['unit_price']} USD · 货品金额 {context['goods_total']} USD")
            with st.expander("查看将发送给模型的资料（不含密钥）"):
                st.json(request_context(context, previous, report, platform_report))
        saved = st.session_state.get("ai_draft")
        if saved and (context is None or business_context(saved["context"]) != business_context(context)):
            st.session_state.pop("ai_draft", None)
        if previous and (context is None or business_context(previous["context"]) != business_context(context)):
            st.info(f"当前输入与 V{previous['version']} 不同。旧版已保留在「修订与下载」，尚未为当前输入生成新版。")
        invalid_ratio = check_payment_ratio(payment_terms).status == "invalid"
        if invalid_ratio:
            st.warning("付款比例存在明确错误，请先按付款比例提示修改，再生成新版。历史版本仍可查看和下载。")
        if st.button("生成英文回复与中文清单", key="generate_ai", disabled=context is None or invalid_ratio):
            st.session_state.pop("ai_draft", None)
            try:
                with st.spinner("正在请求 DeepSeek，请稍候……"):
                    draft = generate_draft(st.session_state.get("deepseek_api_key", ""), request_context(context, previous, report, platform_report))
                wording_report = check_payment_wording(draft.english_reply, context["payment_terms"])
                history = append_version(history, context, draft.model_dump(), report, wording_report, platform_report)
                st.session_state.quote_history = history
                st.session_state.revision_view = history[-1]["version"]
                st.session_state.ai_draft = {"context": context, "draft": draft.model_dump()}
            except GenerationError as exc:
                st.error(str(exc))
                st.info("本次未生成新版本；已有历史版本仍保留。")
        saved = st.session_state.get("ai_draft")
        if saved:
            st.info("草稿已保存到历史。请切换到「3 修订与下载」→「查看版本与下载」，下载所选版本 TXT 或全部版本记录 JSON。")
            st.caption("以下为本次成功生成的草稿，需核对金额、语言与待确认条件；未发送给客户。")
            st.write("英文客户回复")
            st.text(saved["draft"]["english_reply"])
            st.write("中文采购/跟单核对清单")
            st.text(saved["draft"]["chinese_checklist"])

    with history_container if history_container is not None else nullcontext():
        with st.expander("恢复正式演示进度（第一轮 V1/V2）"):
            st.write("恢复已下载的第一轮：1000个、1.05 USD/个、1050 USD、拟议50%/50%、RFQ涤纶要求。将替换当前填写需求与历史；被替换的历史会暂存在本会话供下载。")
            st.caption("仅恢复已归档的第一轮，不含之后的新版本。不会调用模型或更改密钥。")
            def recover_round1():
                try:
                    restore_demo_round1(st.session_state)
                except ValueError as exc:
                    st.session_state.history_recovery_error = str(exc)
                else:
                    st.session_state.pop("history_recovery_error", None)
                    st.session_state.history_recovery_notice = "已恢复正式演示第一轮 V1/V2，当前1000个、1050美元、RFQ场景。可继续第二轮付款修订。"
            st.button("恢复第一轮 V1/V2（替换当前会话）", key="restore_demo_round1", on_click=recover_round1)
            replaced = st.session_state.get("history_before_recovery")
            if replaced:
                st.download_button("下载恢复前的历史（JSON）", data=export_history(replaced).encode("utf-8-sig"),
                                   file_name="quote-history-before-recovery.json", mime="application/json", key="download_before_recovery")
        if not history:
            st.subheader("恢复已保存的历史")
            st.info("刷新可能开启新会话。此前已归档的 RFQ V1 仍在本机，可恢复原草稿、检查报告和填写需求，无需调用 AI。")
            st.caption("备份：10月29日 RFQ V1 · 500个 × 1.20 USD = 600.00 USD。仅恢复此份已归档记录，不包含之后未下载的内容。")
            def recover_history():
                try:
                    restore_archived_rfq(st.session_state)
                except ValueError as exc:
                    st.session_state.history_recovery_error = str(exc)
                else:
                    st.session_state.pop("history_recovery_error", None)
                    st.session_state.history_recovery_notice = "已从本地备份恢复 RFQ V1 和填写需求；原生成时间、草稿与报告保持不变，未调用 AI。"
            st.button("恢复已归档的 RFQ V1", key="restore_archived_rfq", on_click=recover_history)
        if st.session_state.get("history_recovery_error"):
            st.error(st.session_state.history_recovery_error)
        if st.session_state.get("history_recovery_notice"):
            st.info(st.session_state.history_recovery_notice)
        render_dialogue(product, quantity, payment_terms)
        st.divider()
        st.subheader("查看版本与下载")
        st.caption("每次成功生成都会新增版本；历史只在当前会话内，刷新或重启可能丢失，请及时下载。")
        if not history:
            st.info("当前没有历史。可用上方按钮恢复已归档的 V1，也可到「检查并生成」创建新版本。")
            with st.expander("学习练习：检查英文付款表达"):
                render_wording_practice(payment_terms)
            return
        candidate = attach_current_dialogue(history, active)
        if candidate is not history:
            st.info("最新版未记录原始修改要求，但当前会话仍有与其事实和变更一致的要求，可事后补充关联；不会重新生成或改动原草稿。")
            if st.button("补充关联当前修改要求（不调用 AI）", key="attach_dialogue"):
                history = candidate
                st.session_state.quote_history = history
                st.success("已补充关联并标明来源；原版本数量、生成时间及草稿均保留。")
        if st.session_state.get("revision_view") not in [r["version"] for r in history]:
            st.session_state.revision_view = history[-1]["version"]
        version = st.selectbox("查看历史版本", options=[r["version"] for r in history],
                               index=None, format_func=lambda v: f"V{v}", key="revision_view")
        record = history[version-1]
        st.caption(f"V{version} · 生成时间（UTC）：{record['created_at']}")
        if record["context"].get("dialogue"):
            st.write("本轮用户修改要求")
            st.text(record["context"]["dialogue"]["user_message"])
        elif record.get("dialogue_attachment"):
            st.write("本轮用户修改要求（事后补充关联）")
            st.text(record["dialogue_attachment"]["user_message"])
            st.caption(record["dialogue_attachment"]["notice"])
        else:
            st.caption("此版本未保存原始修改要求，不能据此证明一轮对话；可能来自表单修改或旧版记录。")
        if context is None or business_context(record["context"]) != business_context(context):
            st.info("正在查看历史输入对应的草稿，不代表当前输入。")
        if version > 1:
            st.write(f"V{version-1} → V{version}：业务资料变化（金额来自代码计算）")
            changes = compare_contexts(history[version-2]["context"], record["context"])
            if changes:
                st.table(changes)
            else:
                st.write("业务资料未改变，本版是对相同资料重新生成。")
        with st.expander(f"查看 V{version} 的两份草稿"):
            st.text(record["draft"]["english_reply"])
            st.text(record["draft"]["chinese_checklist"])
        st.download_button("下载所选版本（TXT）", data=export_text(record).encode("utf-8-sig"),
                           file_name=f"simulated-quote-v{version}.txt", mime="text/plain", key="download_quote")
        st.download_button("下载全部版本记录（JSON）", data=export_history(history).encode("utf-8-sig"),
                           file_name="simulated-quote-history.json", mime="application/json", key="download_history")
        st.subheader("本版校验依据")
        saved_report = record.get("check_report")
        if saved_report:
            st.caption(f"以下是 V{version} 生成时保存的检查结果，随版本保留，不随当前输入改写。")
            st.table([
                {"项目": "规则", "内容": f"{saved_report['rule_id']} v{saved_report['rule_version']} · {saved_report['rule_name']}"},
                {"项目": "结果", "内容": saved_report["status_label"]},
                {"项目": "说明", "内容": saved_report["message"]},
                {"项目": "检查输入", "内容": saved_report["input_payment_terms"]},
                {"项目": "依据", "内容": saved_report["basis"]},
                {"项目": "来源", "内容": saved_report["source"]},
            ])
            st.caption("检查由本地程序执行，独立于 AI 草稿。" + saved_report["limitation"])
        else:
            st.info("此旧版本未保存生成时的校验报告，不会自动补写。成功生成新版后，将同时保存校验结果和依据。")
        with st.expander(f"查看 V{version} 的平台规则预检"):
            if record.get("platform_report"):
                st.caption("这是本版生成时保存的结果，不随上方当前输入改变。")
                show_rfq_report(record["platform_report"], key_prefix="history_rfq")
            else:
                st.caption("此旧版本未保存平台规则预检报告，不会自动补写。")
        st.divider()
        with st.expander(f"查看 V{version} 的英文付款表达检查"):
            if record.get("wording_report"):
                show_wording_report(record["wording_report"])
            else:
                st.caption("此旧版本未保存英文检查报告，不会自动补写；可在学习练习中单独检查。")
        with st.expander("学习练习：检查英文付款表达"):
            render_wording_practice(payment_terms)
