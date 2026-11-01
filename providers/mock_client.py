"""Explicit, network-free demo client. It records feedback, without pretending to translate."""
import json


class MockChatClient:
    provider = "mock"
    model = "local-demo"
    def complete(self, messages: list[dict[str, str]]) -> str:
        data = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        revision = data.get("revision")
        feedback = revision["feedback"] if revision else "首次请求"
        return ("【模拟草稿】仅验证资料传递与版本流程，未调用模型，未执行翻译或语义改写。\n\n"
                f"目标语言：{data['target_language']}\n\n客户询盘：\n{data['inquiry']}\n\n"
                f"产品信息：\n{data['product_info']}\n\n本轮要求：\n{feedback}\n\n"
                "价格、交期、认证等资料仍需人工核实。")
