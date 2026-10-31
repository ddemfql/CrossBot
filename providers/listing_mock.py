"""Network-free W2 output demonstrating exact selected platform boundaries."""
import json


class MockListingClient:
    provider = "mock"
    model = "local-demo"
    def complete(self, messages: list[dict[str, str]]) -> str:
        data = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        return json.dumps({"listings": {
            platform: (
                "【模拟文案】仅验证平台选择与资料传递，未调用模型或翻译。\n\n"
                f"目标语言：{data['target_language']}\n目标市场：{data['target_country'] or '未知'}\n\n"
                f"产品资料：\n{data['product_description']}\n\n"
                f"组织提示：{data['platform_guidance'][platform]['text']}\n\n"
                "事实和正式平台要求待人工核对。"
            ) for platform in data["platforms"]
        }}, ensure_ascii=False)
