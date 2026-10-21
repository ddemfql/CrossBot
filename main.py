from pathlib import Path
from utils.llm_client import chat_stream

ROOT = Path(__file__).resolve().parent

def load_prompt(filename):
    p = ROOT / "prompts" / filename
    return p.read_text(encoding="utf-8") if p.exists() else "你是一个外贸助手。"

def check_compliance(text):
    risks = []
    kb = ROOT / "knowledge_base" / "违禁词.txt"
    if kb.exists():
        for line in kb.read_text(encoding="utf-8").splitlines():
            w = line.strip()
            if w and w.lower() in text.lower():
                risks.append(f"命中违禁/敏感词：{w}")
    rules = {
        "CE认证": "欧盟市场常需CE合规，需人工确认产品类别与证书。",
        "FDA": "美国FDA相关品类需认证，需人工确认。",
    }
    for k, v in rules.items():
        if k.lower() in text.lower():
            risks.append(f"合规提醒：{v}")
    return sorted(set(risks))

def simple_retrieve(query):
    context = []
    kb_dir = ROOT / "knowledge_base"
    for p in kb_dir.glob("*.txt"):
        if p.name == "违禁词.txt": continue 
        text = p.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if any(q in line for q in query.replace(',', ' ').replace('.', ' ').split()):
                context.append(f"[{p.name}] {line}")
    return "\n".join(context[:3]) if context else "无命中知识库条目。"

def run_workflow_stream(workflow_type, user_input, extra_info=""):
    """工作流生成器：边生成边吐出，最后返回校验结果"""
    context = simple_retrieve(user_input + extra_info)
    
    if workflow_type == "sales":
        sys_prompt = load_prompt("sales_agent.txt")
        user_msg = f"目标语言：{extra_info}\n客户询盘：{user_input}\n知识库上下文：\n{context}"
    else:
        sys_prompt = load_prompt("copywriter.txt")
        user_msg = f"中文描述：{user_input}\n知识库上下文：\n{context}"

    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}]
    
    # 使用生成器
    full_text = ""
    for chunk in chat_stream(messages):
        full_text += chunk
        yield chunk
    
    # 生成完毕后，进行合规校验
    risks = check_compliance(full_text)
    yield ("###RISKS###", risks) # 用特殊标记把风险数据传回给 UI

def iterate_workflow_stream(previous, feedback):
    user = f"上一版输出：\n{previous}\n\n修改意见：{feedback}\n请修改。"
    messages = [{"role": "system", "content": "你是外贸合规与文案迭代助手。"}, {"role": "user", "content": user}]
    
    full_text = ""
    for chunk in chat_stream(messages):
        full_text += chunk
        yield chunk
    
    risks = check_compliance(full_text)
    yield ("###RISKS###", risks)