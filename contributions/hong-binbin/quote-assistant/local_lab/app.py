"""Local learning lab with explicit, user-triggered DeepSeek draft generation."""
import json
import platform
from importlib.metadata import version
from importlib import reload as reload_module

import streamlit as st
from pydantic import ValidationError

from local_lab.schema import SampleInput
from local_lab.catalog import load_catalog
from local_lab.quotation import calculate_goods
from local_lab.business_checks import check_payment_terms
from local_lab import ui_loader
from local_lab.payment_ratio import check_payment_ratio

# Keep the session and its drafts while refreshing a changed stateless UI module.
panel = reload_module(ui_loader).load_revision_panel()
render_ai_panel = panel.render_ai_panel
from local_lab.rfq_panel import render_rfq_inputs, render_rfq_result

st.set_page_config(page_title="询盘与报价小助手", page_icon="📨", layout="wide")
st.title("询盘与报价小助手")
st.caption("洪彬斌 · AI+出海 · 本机演示")
st.info("模拟商品与价格，仅生成待人工确认的草稿，不会发送给客户。")
inputs_tab, generate_tab, history_tab = st.tabs(["1 填写需求", "2 检查并生成", "3 修订与下载"])

with inputs_tab:
    st.subheader("填写本次询盘")
    st.caption("选择商品、填写数量和付款条件，再到「检查并生成」核对结果。")
    with st.expander("商品目录与数据来源"):
        st.caption("本地 products.json · 商品和供应信息均为模拟设定。修改文件后可重新读取。")
        st.button("重新读取商品文件", key="reload_catalog")
    product = None
    quantity = 0
    try:
        catalog = load_catalog()
    except OSError:
        st.error("无法读取商品文件，请检查 planning/fixtures/products.json 是否存在并且可读。")
    except ValidationError:
        st.error("商品文件格式有误。请检查双引号和逗号、商品名称、唯一编号、USD 币种，以及起订量和价格档位。")
    else:
        with st.expander("查看全部商品"):
            st.dataframe(
                [{"商品编号": p.sku, "商品名称": p.name_zh, "材质": p.material} for p in catalog.products],
                hide_index=True,
            )
        st.caption(f"已从本地文件读取 {len(catalog.products)} 款商品。")

        st.write("选择商品并输入数量，按 Enter 确认。单价与货品金额由程序计算。")
        by_sku = {p.sku: p for p in catalog.products}
        selected_sku = st.selectbox(
            "选择商品", options=list(by_sku),
            format_func=lambda sku: f"{sku} · {by_sku[sku].name_zh}",
            key="quote_sku",
        )
        product = by_sku[selected_sku]
        if "quote_quantity" not in st.session_state:
            st.session_state.quote_quantity = 300
        quantity = st.number_input(
            "采购数量（个）", min_value=0, max_value=1_000_000,
            value=None, step=1, key="quote_quantity",
        )
        st.caption(f"最小起订量（MOQ）是至少订购多少个。这款商品为 {product.moq} 个。")
        with st.expander("查看模拟价格档位"):
            st.table([
                {"达到数量（个）": tier.min_quantity, "货品单价（USD/个）": f"{tier.unit_price:.2f}"}
                for tier in sorted(product.price_tiers, key=lambda t: t.min_quantity)
            ])
        try:
            quote = calculate_goods(product, quantity)
        except ValueError as exc:
            st.warning(str(exc))
        else:
            price_column, total_column = st.columns(2)
            price_column.metric("适用单价（USD/个）", f"{quote.unit_price:.2f}")
            total_column.metric("货品金额（USD）", f"{quote.goods_total:,.2f}")
            st.write(f"计算过程：{quote.quantity:,} 个 × {quote.unit_price:.2f} USD/个 = {quote.goods_total:,.2f} USD。")
            st.caption(f"采用“达到 {quote.tier_min_quantity:,} 个”的价格档位，单价来自商品文件。")
        st.caption("模拟价格练习：这里只计算货品金额，不含运费、税费和定制费；本段计算不调用 AI。")

    st.subheader("付款与场景")
    payment_terms = st.text_input(
        "付款条件（模拟练习）", key="payment_terms", max_chars=500,
        placeholder="拟议条款：30% 预付款，70% 发货前支付，待双方确认",
    )
    platform_report = render_rfq_inputs(product)

with generate_tab:
    st.subheader("核对当前输入")
    payment_check = check_payment_terms(payment_terms)
    if payment_check.status == "provided":
        st.info(payment_check.message)
    else:
        st.warning(payment_check.message)
    st.caption("练习规则 C01：付款条件空白或仅填写“待确认”等占位文字时，保留提醒。来源：本项目模拟案例 T05；这是自定义信息完整性检查，不代表平台规则或合规审核。")
    with st.expander("这一步的判断过程"):
        st.write("输入付款条件 → 去掉首尾空格 → 检查是否为空或常见待确认占位词 → 更新提示。这里只做文字完整性检查，不理解或批准付款约定，也不会自动填入示例。")

    st.divider()
    st.subheader("付款比例检查")
    st.caption("根据「填写需求」中的付款条件检查，不调用 AI；明确的比例错误会阻止生成。")
    ratio_check = check_payment_ratio(payment_terms)
    if ratio_check.status == "invalid":
        st.error(ratio_check.message)
    elif ratio_check.status == "consistent":
        st.success(ratio_check.message)
    else:
        st.info(ratio_check.message)
    if ratio_check.total is not None:
        st.table([
            {"项目": "预付款", "比例": f"{ratio_check.advance:g}%"},
            {"项目": "发货前支付", "比例": f"{ratio_check.before_shipment:g}%"},
            {"项目": "合计", "比例": f"{ratio_check.total:g}%"},
            {"项目": "本练习要求", "比例": "100%"},
        ])
    st.caption("C02 · 项目自定义算术一致性规则：仅检查明确写出的两笔付款是否合计 100%。不判断法律效力、交易风险或双方是否同意；其他写法转人工核对。")

    st.subheader("业务场景检查")
    render_rfq_result(platform_report)

render_ai_panel(product, quantity, payment_terms, platform_report=platform_report,
                generation_container=generate_tab, history_container=history_tab)

with st.expander("学习与调试：运行环境、JSON 输入校验"):
    st.divider()
    st.subheader("运行环境与输入校验")
    a, b, c = st.columns(3)
    a.metric("Python", platform.python_version())
    b.metric("Streamlit", version("streamlit"))
    c.metric("Pydantic", version("pydantic"))

    st.subheader("试一试：输入 → 校验 → 查看与下载")
    st.write("把 quantity 改成 0 或负数，可查看错误反馈；改回正整数后可以下载规范化数据。")
    sample = {
        "product_name": "示例产品",
        "quantity": 100,
        "target_language": "英语",
        "notes": "本地验证样例，不调用模型。",
    }
    payload = st.text_area(
        "输入 JSON",
        value=json.dumps(sample, ensure_ascii=False, indent=2),
        height=240,
        key="payload",
    )
    if st.button("校验输入", type="primary", key="validate"):
        st.session_state.pop("validated", None)
        st.session_state.pop("validation_errors", None)
        try:
            result = SampleInput.model_validate_json(payload)
            st.session_state.validated = {
                "source": payload,
                "result": result.model_dump(),
            }
        except ValidationError as exc:
            st.session_state.validation_errors = {
                "source": payload,
                "errors": [
                    {
                        "字段": ".".join(str(part) for part in err["loc"]) or "JSON",
                        "原因": err["msg"],
                        "类型": err["type"],
                    }
                    for err in exc.errors(include_url=False, include_input=False)
                ],
            }

    validated = st.session_state.get("validated")
    errors = st.session_state.get("validation_errors")
    if validated and validated["source"] == payload:
        st.success("本地数据校验通过。本次校验不调用模型。")
        st.json(validated["result"])
        st.download_button(
            "下载校验结果",
            data=json.dumps(validated["result"], ensure_ascii=False, indent=2),
            file_name="validated-sample.json",
            mime="application/json",
        )
    elif errors and errors["source"] == payload:
        st.error("输入未通过校验，请根据下面的字段提示修改。")
        st.table(errors["errors"])
    elif validated or errors:
        st.warning("输入已修改，请重新校验。旧结果不代表当前输入。")

    with st.expander("数据字段约定"):
        st.json(SampleInput.model_json_schema())

    st.caption("数据仅保留在当前页面会话；如需保留，请下载结果。重开会话后需重新输入。")
