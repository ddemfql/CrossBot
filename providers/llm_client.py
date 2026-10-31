"""Explicit HTTPS chat transport. No .env, implicit retries, logging or SDK defaults."""
import json
import math
import socket
from urllib import error, parse, request

from core.config import AppConfig
from core.errors import CrossBotError, ErrorCode

MAX_BYTES = 1_048_576
USER_AGENT = "CrossBot/1.0"


def resolve_api_endpoint(base, protocol="auto"):
    """Resolve documented protocols without sending a request or credentials."""
    base = base if isinstance(base, str) else ""
    try:
        url = parse.urlsplit(base)
        valid = (url.scheme == "https" and url.hostname and not url.username and not url.password
                 and not url.query and not url.fragment and not any(c.isspace() for c in base))
        url.port
    except ValueError:
        valid = False
    if not valid:
        raise CrossBotError(ErrorCode.CONFIGURATION, "服务地址必须是有效HTTPS地址，且不含登录信息、查询参数或片段。")
    if protocol not in {"auto", "responses", "chat_completions"}:
        raise CrossBotError(ErrorCode.CONFIGURATION, "请选择自动、Responses 或 Chat Completions 协议。")
    if url.path.rstrip("/") in {"/keys", "/login", "/v1/messages"}:
        raise CrossBotError(ErrorCode.CONFIGURATION, "请填写 OpenAI API 基础地址，不要填写密钥管理页、登录页或 Claude 接口。")
    endpoint = base.rstrip("/")
    known_responses = url.hostname in {"api.openai.com", "api.tokenhubcloud.net"}
    if known_responses and not url.path.rstrip("/"):
        endpoint += "/v1"
    suffixes = {"responses": "/responses", "chat_completions": "/chat/completions"}
    explicit = next((name for name, suffix in suffixes.items() if endpoint.endswith(suffix)), None)
    if protocol == "auto":
        protocol = explicit or ("responses" if known_responses else "chat_completions")
    elif explicit and explicit != protocol:
        raise CrossBotError(ErrorCode.CONFIGURATION, "完整接口地址与所选协议不一致，请使用基础地址或切换协议。")
    if not explicit:
        endpoint += suffixes[protocol]
    return endpoint, protocol


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpChatClient:
    provider = "real"

    def __init__(self, config: AppConfig, *, json_mode=False):
        self._config = config
        self.json_mode = json_mode
        self.model = config.model_name

    def __repr__(self):
        return "<HttpChatClient>"

    def complete(self, messages: list[dict[str, str]]) -> str:
        cfg = self._config
        if cfg.mock_mode:
            raise CrossBotError(ErrorCode.CONFIGURATION, "真实客户端不能在模拟配置下调用。")
        key = cfg.openai_api_key
        if not isinstance(key, str) or not key.strip() or not key.isascii() or any(c.isspace() for c in key):
            raise CrossBotError(ErrorCode.CONFIGURATION, "请在本机输入有效API Key，不要包含空格或换行。")
        if not isinstance(cfg.model_name, str) or not cfg.model_name.strip():
            raise CrossBotError(ErrorCode.CONFIGURATION, "请填写模型名称。")
        endpoint, protocol = resolve_api_endpoint(cfg.openai_base_url, cfg.model_api_protocol)
        url = parse.urlsplit(endpoint)
        timeout = cfg.request_timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise CrossBotError(ErrorCode.CONFIGURATION, "网络超时设置须在1到120秒之间。")
        uses_responses = protocol == "responses"
        max_output_tokens = 4096 if self.json_mode else 1800
        if uses_responses:
            instructions = "\n\n".join(m["content"] for m in messages if m.get("role") in {"system", "developer"})
            inputs = [{"role": m["role"], "content": m["content"]}
                      for m in messages if m.get("role") in {"user", "assistant"}]
            body = {"model": cfg.model_name, "instructions": instructions,
                    "input": inputs, "max_output_tokens": max_output_tokens, "store": False}
            if self.json_mode:
                platform_schema = self._listing_schema(messages)
                body["text"] = {"format": platform_schema or {"type": "json_object"}}
        else:
            body = {"model": cfg.model_name, "messages": messages, "stream": False,
                    "max_tokens": max_output_tokens}
            if self.json_mode:
                body["response_format"] = {"type": "json_object"}
            if url.hostname == "api.deepseek.com":
                body["thinking"] = {"type": "disabled"}
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            raise CrossBotError(ErrorCode.INVALID_INPUT, "模型请求资料不能编码为JSON。") from None
        if len(payload) > MAX_BYTES:
            raise CrossBotError(ErrorCode.INVALID_INPUT, "请求资料过大，请精简后手动重试。")
        req = request.Request(endpoint, data=payload,
                              headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                       "Accept": "application/json", "User-Agent": USER_AGENT}, method="POST")
        try:
            with request.build_opener(NoRedirect()).open(req, timeout=timeout) as response:
                if response.status != 200:
                    raise CrossBotError(ErrorCode.PROVIDER_UNAVAILABLE, "接口未返回成功状态。")
                raw = response.read(MAX_BYTES + 1)
        except error.HTTPError as exc:
            code = exc.code
            exc.close()
            mapped = {
                400: (ErrorCode.INVALID_INPUT, "接口拒绝请求参数，请核对服务地址、模型及格式。", False),
                401: (ErrorCode.PROVIDER_AUTH, "认证失败，请在本机核对密钥。", False),
                402: (ErrorCode.PROVIDER_AUTH, "账户额度不足，请自行检查账户。", False),
                403: (ErrorCode.PROVIDER_AUTH, "服务拒绝访问（HTTP 403）：可能是网关客户端拦截、密钥分组或模型权限限制，请核对平台配置。", False),
                404: (ErrorCode.CONFIGURATION, "接口或模型不存在，请核对服务地址和模型名。", False),
                422: (ErrorCode.INVALID_INPUT, "接口参数不受支持，请核对模型与请求格式。", False),
                429: (ErrorCode.PROVIDER_RATE_LIMIT, "请求受限，请稍后决定是否手动重试。", True),
            }
            tag, message, retryable = mapped.get(code, (ErrorCode.PROVIDER_UNAVAILABLE, "模型服务暂不可用，请稍后手动重试。", code >= 500))
            if 300 <= code < 400:
                tag, message, retryable = ErrorCode.CONFIGURATION, "服务地址发生跳转，未转发密钥，请核对直接接口地址。", False
            if f"HTTP {code}" not in message:
                message += f"（HTTP {code}）"
            raise CrossBotError(tag, message, retryable=retryable,
                                details={"http_status": code, "protocol": protocol}) from None
        except (TimeoutError, socket.timeout):
            raise CrossBotError(ErrorCode.PROVIDER_TIMEOUT, "模型请求超时，未保存新结果；可稍后手动重试。", retryable=True) from None
        except error.URLError as exc:
            tag = ErrorCode.PROVIDER_TIMEOUT if isinstance(exc.reason, TimeoutError) else ErrorCode.PROVIDER_UNAVAILABLE
            raise CrossBotError(tag, "网络连接失败或超时，未保存新结果。", retryable=True) from None
        except OSError:
            raise CrossBotError(ErrorCode.PROVIDER_UNAVAILABLE, "网络连接失败，未保存新结果。", retryable=True) from None
        if len(raw) > MAX_BYTES:
            raise CrossBotError(ErrorCode.PARSE_FAILED, "接口返回过大，未保存新结果。")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            if uses_responses:
                if data.get("status") != "completed":
                    raise ValueError
                content = "".join(
                    item["text"]
                    for output in data["output"]
                    for item in output.get("content", [])
                    if item.get("type") == "output_text" and isinstance(item.get("text"), str)
                )
            else:
                choice = data["choices"][0]
                if choice["finish_reason"] != "stop":
                    raise ValueError
                content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise CrossBotError(ErrorCode.PARSE_FAILED, "接口返回为空、格式不完整或生成被截断，未保存新结果。") from None
        return content

    @staticmethod
    def _listing_schema(messages):
        """Use strict structured output when a W2 request declares its platform set."""
        try:
            payload = json.loads(next(m["content"] for m in reversed(messages) if m.get("role") == "user"))
            platforms = payload["platforms"]
            if not isinstance(platforms, list) or not platforms or any(not isinstance(p, str) or not p for p in platforms):
                return None
            properties = {platform: {"type": "string"} for platform in platforms}
            return {
                "type": "json_schema", "name": "platform_listings", "strict": True,
                "schema": {"type": "object", "properties": {
                    "listings": {"type": "object", "properties": properties,
                                 "required": platforms, "additionalProperties": False}},
                    "required": ["listings"], "additionalProperties": False},
            }
        except (StopIteration, KeyError, TypeError, ValueError):
            return None
