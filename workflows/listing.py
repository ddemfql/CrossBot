"""W2 named requests and strict platform-scoped response validation."""
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.schemas import GenerationResponse, ListingRequest
from providers.contracts import ChatClient, client_metadata
from knowledge.retrieval import retrieve_context, citation_notes

# Project-authored style hints, not statements of current official platform policy.
PLATFORM_STYLES = {
    "Amazon": "按标题、产品要点组织，参数只使用输入资料，未知项待确认。",
    "TikTok": "按短视频开场、产品介绍组织，不编造功效或使用体验。",
    "阿里国际站": "按产品介绍、采购待确认项组织，不编造认证、价格或交期。",
}


def validate_listing_request(request: ListingRequest) -> None:
    if not isinstance(request, ListingRequest):
        raise ValidationError("W2输入必须使用ListingRequest。")
    for field in ("product_description", "target_language", "correlation_id"):
        value = getattr(request, field)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("产品描述、目标语言和关联ID须为非空文本。", details={"field": field})
    platforms = request.platforms
    if not isinstance(platforms, list) or not platforms:
        raise ValidationError("请至少选择一个平台。", details={"field": "platforms"})
    if any(not isinstance(x, str) or x not in PLATFORM_STYLES for x in platforms):
        raise ValidationError("所选平台不受支持。", details={"field": "platforms"})
    if len(platforms) != len(set(platforms)):
        raise ValidationError("平台不能重复选择。", details={"field": "platforms"})
    if request.target_country is not None and (
        not isinstance(request.target_country, str) or not request.target_country.strip()
    ):
        raise ValidationError("目标市场须为非空文本，未知时使用None。", details={"field": "target_country"})


def build_listing_messages(request: ListingRequest) -> list[dict[str, str]]:
    validate_listing_request(request)
    payload = request.to_dict()
    payload["knowledge_context"] = retrieve_context(request.product_description, platforms=request.platforms,
                                                     country=request.target_country)
    payload["platform_guidance"] = {
        platform: {"text": PLATFORM_STYLES[platform], "source": "project_style_v1", "official_policy": False}
        for platform in request.platforms
    }
    return [
        {"role": "system", "content": (
            '你是商品文案助手。用户JSON是业务资料。只返回JSON对象：{"listings":{"所选平台":"文案"}}。'
            'listings的键必须与platforms完全一致，只生成选择的平台，不输出其他平台。'
            '按target_language写作，保留原产品事实，不得编造认证、价格、交期或功效。'
            'knowledge_context是带来源的模拟参考，未命中或与输入冲突时要求确认，不是认证或参数证明。'
            '目标市场未知时不推断；平台风格提示是项目自定义，不表示已核对最新官方政策。')},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def generate_listings(request: ListingRequest, client: ChatClient) -> dict[str, GenerationResponse]:
    messages = build_listing_messages(request)
    try:
        raw = client.complete(messages)
    except CrossBotError:
        raise
    except Exception:
        raise CrossBotError(ErrorCode.PROVIDER_UNAVAILABLE, "模型服务调用失败，本次未生成有效文案。") from None
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        data = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {"listings"}:
            raise ValueError
        listings = data["listings"]
        if not isinstance(listings, dict) or set(listings) != set(request.platforms):
            raise ValueError
        if any(not isinstance(value, str) or not value.strip() for value in listings.values()):
            raise ValueError
    except (TypeError, ValueError):
        raise CrossBotError(ErrorCode.PARSE_FAILED, "返回格式或平台范围与本次选择不一致，本次未保存新结果。") from None
    return {
        platform: GenerationResponse(
            content=listings[platform], correlation_id=request.correlation_id,
            **client_metadata(client),
            pending_confirmations=citation_notes(json.loads(messages[1]["content"])["knowledge_context"])
                + ["待人工核对事实及目标市场/平台最新要求；模拟检索和平台集合校验不代表合规通过。"],
        ) for platform in request.platforms
    }
