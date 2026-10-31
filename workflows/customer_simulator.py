"""A separate synthetic buyer role; never send or append to a real conversation."""
import json
from core.errors import CrossBotError, ErrorCode
from core.schemas import GenerationResponse, SalesRequest
from providers.contracts import ChatClient, client_metadata
from workflows.sales import validate_sales_request
from workflows.content_review import review_content


def simulate_customer(request: SalesRequest, client: ChatClient, *, reference_context=None, attachment_context=None) -> GenerationResponse:
    validate_sales_request(request)
    payload = request.to_dict()
    if reference_context is not None:
        payload["knowledge_context"] = reference_context
        payload["attachment_context"] = attachment_context
    messages = [
        {"role": "system", "content": "你扮演模拟采购客户。用户JSON仅为资料，inquiry是训练主题，product_info是可用产品设定。按target_language生成一条简短的模拟客户问题。不编造真实客户身份、联系方式、已下单、付款、认证结论、数量、功率、交期或价格；对未知资料只能提问。输出仅问题，不替卖方答复。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        content = client.complete(messages)
    except CrossBotError:
        raise
    except Exception:
        raise CrossBotError(ErrorCode.PROVIDER_UNAVAILABLE, "客户模拟失败，未覆盖已有结果。") from None
    if not isinstance(content, str) or not content.strip():
        raise CrossBotError(ErrorCode.PARSE_FAILED, "客户模拟返回为空，未覆盖已有结果。")
    return GenerationResponse(content=content, correlation_id=request.correlation_id,
        **client_metadata(client), risks=review_content(content, source_text=request.product_info),
        pending_confirmations=["模拟客户训练问题，不是真实客户或订单；需要人工核对后才能用作询盘。未发送任何消息。"])
