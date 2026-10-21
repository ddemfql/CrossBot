import os
import time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

def chat_stream(messages, temperature=0.3):
    """流式调用大模型（返回生成器）"""
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("MODEL_NAME", "gpt-3.5-turbo")
    
    # Mock 模式
    if not api_key or "sk-" not in api_key or "填这里" in api_key or "mock" in api_key:
        mock_text = "【Mock回复】由于目前尚未接入真实 API，这是一段模拟的流式输出文本。"
        if "询盘" in messages[-1]["content"] or "Hi" in messages[-1]["content"]:
            mock_text = "【Mock回复-客服】Dear customer, our desk lamp is CE certified. We suggest you confirm the specific parameters.\n\n⚠️风险提示：命中合规规则CE认证需人工确认。"
        elif "Listing" in messages[-1]["content"] or "描述" in messages[-1]["content"]:
            mock_text = "【Mock回复-运营】\nAmazon版：Professional Desk Lamp, USB powered, 3 color temperatures.\nTikTok版：OMG! This desk lamp is a lifesaver for your eyes! \n\n⚠️风险提示：文案包含绝对化用语。"
        
        for char in mock_text:
            time.sleep(0.02) # 模拟打字机延迟
            yield char
        return

    # 真实 API 调用
    client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True  # 开启流式
        )
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        yield f"\n❌ API 调用失败: {str(e)}"