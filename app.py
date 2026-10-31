import streamlit as st
from main import run_workflow_stream, iterate_workflow_stream

st.set_page_config(page_title="CrossBot", layout="wide")
st.title("CrossBot 外贸智能业务员与合规助手")
st.caption("🎯 赛题闭环：理解 → 生成 → 校验 → 反馈")

# 侧边栏
with st.sidebar:
    st.header("工作流选择")
    wf = st.radio("工作流", ["W1 询盘极速转化", "W2 多平台Listing生成", "W3 交易单证工作台"],
                  index=2 if st.query_params.get("case") else 0, label_visibility="collapsed")
    st.divider()
    if not wf.startswith("W3"):
        st.info("💡 提示：当前处于系统测试模式，接入真实 API 后将输出真实生成结果。")

if wf.startswith("W3"):
    from w3_app import render as render_w3
    render_w3()
    st.stop()

# 初始化状态 (统一使用 messages 数组记录对话)
if "messages" not in st.session_state:
    st.session_state.messages = [] # 格式: {"role": "user/assistant", "content": "...", "risks": []}
if "current_wf" not in st.session_state:
    st.session_state.current_wf = wf

# 切换工作流时，彻底清空对话历史，开启新会话
if st.session_state.current_wf != wf:
    st.session_state.messages = []
    st.session_state.current_wf = wf

col1, col2 = st.columns([1, 1])

# W1 界面
if wf.startswith("W1"):
    with col1:
        st.subheader("客户输入")
        inquiry = st.text_area("客户询盘 (小语种/英文)", "Hi, do you have CE certified desk lamp? Need 500 pcs to Germany.")
        lang = st.selectbox("目标语言", ["英文", "德文"])
    with col2:
        st.subheader("我方信息")
        product = st.text_area("中文产品信息", "桌面护眼台灯，USB供电，三档色温，ABS材质。")
        st.write("")
        if st.button("🚀 生成商务回复", use_container_width=True):
            st.session_state.messages = [] # 清空历史
            prompt = f"目标语言：{lang}\n客户询盘：{inquiry}\n中文产品信息：{product}"
            st.session_state.messages.append({"role": "user", "content": prompt, "hidden": True}) # 隐藏用户输入
            st.rerun() # 刷新页面进入统一渲染流

# W2 界面
else:
    with col1:
        st.subheader("产品输入")
        desc = st.text_area("中文产品描述", "便携式咖啡杯，316不锈钢，保温12小时，适合户外场景。")
    with col2:
        st.subheader("平台设置")
        platform = st.multiselect("重点适配平台", ["Amazon", "TikTok", "阿里国际站"], default=["Amazon", "TikTok"])
        st.write("")
        if st.button("🚀 生成双平台 Listing", use_container_width=True):
            st.session_state.messages = []
            prompt = f"中文描述：{desc}\n适配平台：{','.join(platform)}"
            st.session_state.messages.append({"role": "user", "content": prompt, "hidden": True})
            st.rerun()

# ================= 统一渲染区 =================
if st.session_state.messages:
    st.divider()
    
    # 遍历渲染历史对话
    for msg in st.session_state.messages:
        # 隐藏内部拼接的 Prompt，只展示真实对话
        if not msg.get("hidden"):
            with st.chat_message(msg["role"], avatar="👤" if msg["role"] == "user" else "🤖"):
                st.markdown(msg["content"])
                
        # 如果是 assistant 的回复，展示风险校验
        if msg["role"] == "assistant" and "risks" in msg:
            with st.chat_message("assistant", avatar="🤖"):
                if msg["risks"]:
                    with st.expander("🛡️ 合规校验反馈 (发现风险)", expanded=True):
                        for r in msg["risks"]:
                            st.error(r)
                else:
                    st.success("✅ 合规校验通过，未发现风险。")

    # 如果最后一条是用户输入（刚点了生成按钮），则触发流式生成
    if st.session_state.messages[-1]["role"] == "user":
        prompt_content = st.session_state.messages[-1]["content"]
        
        with st.chat_message("assistant", avatar="🤖"):
            placeholder = st.empty()
            full_text = ""
            risks = []
            
            # 判断调用哪个工作流
            if wf.startswith("W1"):
                # 拆分 prompt 重新传给业务逻辑
                parts = prompt_content.split("\n")
                lang_part = parts[0].replace("目标语言：", "")
                inquiry_part = parts[1].replace("客户询盘：", "")
                product_part = parts[2].replace("中文产品信息：", "")
                generator = run_workflow_stream("sales", inquiry_part, f"{lang_part}, {product_part}")
            else:
                desc_part = prompt_content.split("\n")[0].replace("中文描述：", "")
                plat_part = prompt_content.split("\n")[1].replace("适配平台：", "")
                generator = run_workflow_stream("listing", desc_part, plat_part)

            for chunk in generator:
                if isinstance(chunk, tuple) and chunk[0] == "###RISKS###":
                    risks = chunk[1]
                else:
                    full_text += chunk
                    placeholder.markdown(full_text + "▌")
            placeholder.markdown(full_text)
            
            if risks:
                with st.expander("🛡️ 合规校验反馈 (发现风险)", expanded=True):
                    for r in risks:
                        st.error(r)
            else:
                st.success("✅ 合规校验通过，未发现风险。")
                
        # 将生成结果加入历史记录
        st.session_state.messages.append({"role": "assistant", "content": full_text, "risks": risks})
        st.rerun()

# ================= 底部输入框 =================
if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
    if fb := st.chat_input("输入修改意见，对生成结果进行对话式迭代..."):
        st.session_state.messages.append({"role": "user", "content": fb})
        st.rerun()

    # 如果倒数第二条是用户要求迭代（存在历史记录中）
    if len(st.session_state.messages) >= 3 and st.session_state.messages[-2]["role"] == "user":
        with st.chat_message("assistant", avatar="🤖"):
            placeholder = st.empty()
            full_text = ""
            risks = []
            previous_ai_text = st.session_state.messages[-3]["content"]
            feedback = st.session_state.messages[-2]["content"]
            
            for chunk in iterate_workflow_stream(previous_ai_text, feedback):
                if isinstance(chunk, tuple) and chunk[0] == "###RISKS###":
                    risks = chunk[1]
                else:
                    full_text += chunk
                    placeholder.markdown(full_text + "▌")
            placeholder.markdown(full_text)
            
            if risks:
                with st.expander("🛡️ 最新合规校验反馈", expanded=True):
                    for r in risks:
                        st.error(r)
            else:
                st.success("✅ 最新合规校验通过。")
                
        st.session_state.messages.append({"role": "assistant", "content": full_text, "risks": risks})
        st.rerun()
