from dataclasses import replace
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from core.config import AppConfig
from core.errors import CrossBotError, ErrorCode
from providers.llm_client import HttpChatClient, MAX_BYTES, NoRedirect


class Response:
    status = 200

    def __init__(self, raw=None):
        self.raw = raw or json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "Valid reply"}}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.raw[:size]


class ClientTests(unittest.TestCase):
    def config(self, **changes):
        return replace(AppConfig(openai_api_key="test-token-not-a-real-key", openai_base_url="https://api.deepseek.com",
                                 model_name="deepseek-flash", mock_mode=False), **changes)

    def test_explicit_request_params_and_no_secret_in_repr(self):
        for json_mode in [False, True]:
            with patch("providers.llm_client.request.build_opener") as build:
                opener = build.return_value
                opener.open.return_value = Response()
                client = HttpChatClient(self.config(), json_mode=json_mode)
                messages = [{"role": "user", "content": "JSON 询盘\n产品"}]
                self.assertEqual(client.complete(messages), "Valid reply")
                req = opener.open.call_args.args[0]
                body = json.loads(req.data)
                self.assertEqual(req.full_url, "https://api.deepseek.com/chat/completions")
                self.assertEqual(body["messages"], messages)
                self.assertFalse(body["stream"])
                self.assertEqual(body["thinking"], {"type": "disabled"})
                self.assertEqual(body["max_tokens"], 4096 if json_mode else 1800)
                self.assertEqual("response_format" in body, json_mode)
                self.assertEqual(opener.open.call_args.kwargs["timeout"], 60)
                self.assertIsInstance(build.call_args.args[0], NoRedirect)
                self.assertNotIn("test-token", repr(client))
                opener.open.assert_called_once()

    def test_compatible_https_endpoint_and_arbitrary_key_prefix(self):
        with patch("providers.llm_client.request.build_opener") as build:
            build.return_value.open.return_value = Response()
            client = HttpChatClient(self.config(openai_base_url="https://example.test/v1/", model_name="vendor-model"))
            client.complete([])
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, "https://example.test/v1/chat/completions")
            self.assertNotIn("thinking", json.loads(req.data))

    def test_invalid_configuration_never_opens_network(self):
        invalid = [dict(openai_api_key=""), dict(openai_api_key="has space"), dict(openai_api_key="x\ny"),
                   dict(mock_mode=True), dict(model_name=""), dict(request_timeout_seconds=float("nan")),
                   dict(request_timeout_seconds=0), dict(request_timeout_seconds=True)]
        invalid += [dict(openai_base_url=x) for x in ["http://example.test", "https://user:password@example.test", "https://example.test/?token=x", "https://example.test/#frag", "https://example.test:bad", ""]]
        for change in invalid:
            with self.subTest(change=change), patch("providers.llm_client.request.build_opener") as build:
                with self.assertRaises(CrossBotError) as caught:
                    HttpChatClient(self.config(**change)).complete([])
                self.assertEqual(caught.exception.code, ErrorCode.CONFIGURATION)
                build.assert_not_called()

    def test_http_errors_are_structured_redacted_and_not_retried(self):
        expected = {400: ErrorCode.INVALID_INPUT, 401: ErrorCode.PROVIDER_AUTH, 402: ErrorCode.PROVIDER_AUTH,
                    403: ErrorCode.PROVIDER_AUTH, 404: ErrorCode.CONFIGURATION, 422: ErrorCode.INVALID_INPUT,
                    429: ErrorCode.PROVIDER_RATE_LIMIT, 500: ErrorCode.PROVIDER_UNAVAILABLE,
                    503: ErrorCode.PROVIDER_UNAVAILABLE, 302: ErrorCode.CONFIGURATION}
        for status, code in expected.items():
            with self.subTest(status=status), patch("providers.llm_client.request.build_opener") as build:
                body = io.BytesIO(b"PRIVATE_ERROR_BODY")
                build.return_value.open.side_effect = HTTPError("https://example.test", status, "PRIVATE_MESSAGE", {}, body)
                with self.assertRaises(CrossBotError) as caught:
                    HttpChatClient(self.config()).complete([])
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("PRIVATE", str(caught.exception.to_dict()))
                self.assertEqual(caught.exception.retryable, status == 429 or status >= 500)
                build.return_value.open.assert_called_once()
                self.assertTrue(body.closed)

    def test_timeout_and_connection_errors_never_return_normal_text(self):
        for problem, code in [(TimeoutError("private"), ErrorCode.PROVIDER_TIMEOUT),
                              (URLError(TimeoutError("private")), ErrorCode.PROVIDER_TIMEOUT),
                              (URLError("private"), ErrorCode.PROVIDER_UNAVAILABLE),
                              (OSError("private"), ErrorCode.PROVIDER_UNAVAILABLE)]:
            with self.subTest(problem=type(problem).__name__), patch("providers.llm_client.request.build_opener") as build:
                build.return_value.open.side_effect = problem
                with self.assertRaises(CrossBotError) as caught:
                    HttpChatClient(self.config()).complete([])
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private", str(caught.exception))
                build.return_value.open.assert_called_once()

    def test_blank_truncated_malformed_or_oversized_output_is_not_saved(self):
        samples = [b"not-json", b"{}", b'{"choices":[]}', b"null", b"\xff", b"x" * (MAX_BYTES + 1)]
        for text, finish in [("", "stop"), ("   ", "stop"), ("partial", "length"), (None, "stop"), ("filtered", "content_filter")]:
            samples.append(json.dumps({"choices": [{"finish_reason": finish, "message": {"content": text}}]}).encode())
        for raw in samples:
            with self.subTest(size=len(raw)), patch("providers.llm_client.request.build_opener") as build:
                build.return_value.open.return_value = Response(raw)
                with self.assertRaises(CrossBotError) as caught:
                    HttpChatClient(self.config()).complete([])
                self.assertEqual(caught.exception.code, ErrorCode.PARSE_FAILED)

    def test_redirect_handler_does_not_forward_credentials(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))


if __name__ == "__main__":
    unittest.main()
