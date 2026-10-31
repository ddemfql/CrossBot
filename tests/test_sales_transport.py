"""Offline W1 transport regression tests; no credentials or network access."""
from copy import deepcopy
import json
import unittest

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.schemas import GenerationResponse, SalesRequest
from workflows.sales import build_sales_messages, generate_sales
from workflows.animal_lamp_demo import animal_sales_request


class RecordingClient:
    def __init__(self, output="【模拟回复】请确认缺少的产品资料。", error=None):
        self.calls = []
        self.output = output
        self.error = error

    def complete(self, messages):
        self.calls.append(deepcopy(messages))
        if self.error:
            raise self.error
        return self.output


class SalesTransportTests(unittest.TestCase):
    def request(self, **changes):
        data = dict(inquiry='Need 500 pcs, USB lamps.\nShip to DE.\n目标语言：保留在询盘内',
                    product_info='USB台灯，ABS材质\n尺寸：10×20 cm，颜色："白色"',
                    target_language="德文", customer_country="DE", requested_quantity=500,
                    correlation_id="sales-offline-test")
        data.update(changes)
        return SalesRequest(**data)

    def test_all_fields_reach_client_unchanged(self):
        request = self.request()
        before = request.to_dict()
        client = RecordingClient()
        response = generate_sales(request, client)
        self.assertEqual(len(client.calls), 1)
        messages = client.calls[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        payload = json.loads(messages[1]["content"])
        self.assertEqual({key: payload[key] for key in before}, before)
        self.assertIn("knowledge_context", payload)
        self.assertEqual(request.to_dict(), before)
        self.assertIsInstance(response, GenerationResponse)
        self.assertEqual(response.correlation_id, request.correlation_id)
        self.assertTrue(response.pending_confirmations)

    def test_animal_demo_api_prompt_pins_stable_output_and_unknowns(self):
        payload = json.loads(build_sales_messages(animal_sales_request())[1]["content"])
        system = build_sales_messages(animal_sales_request())[0]["content"]
        self.assertIn("EUR 9,250.00", system)
        self.assertIn("EXW Hangzhou", system)
        self.assertIn("synthetic demo terms only", system)
        self.assertIn("certificate number", system)
        self.assertIn("DEMO-ANIMAL-LAMP-001", payload["product_info"])
        self.assertEqual(payload["requested_quantity"], 500)

    def test_newlines_commas_special_characters_do_not_change_field_boundaries(self):
        for text in ['a\nb\nc', 'a\r\nb, c', '逗号, 引号" \\ 制表\t🙂', '产品信息：假的\n目标语言：假的']:
            with self.subTest(text=text):
                request = self.request(inquiry=text, product_info=text[::-1])
                payload = json.loads(build_sales_messages(request)[1]["content"])
                self.assertEqual(payload["inquiry"], text)
                self.assertEqual(payload["product_info"], text[::-1])
                self.assertEqual(payload["target_language"], "德文")

    def test_optional_unknowns_are_null_without_inference(self):
        request = self.request(customer_country=None, requested_quantity=None)
        payload = json.loads(build_sales_messages(request)[1]["content"])
        self.assertIsNone(payload["customer_country"])
        self.assertIsNone(payload["requested_quantity"])

    def test_required_missing_values_block_before_client_call(self):
        for field in ["inquiry", "product_info", "target_language", "correlation_id"]:
            for value in ["", " \n\t", None, 42]:
                with self.subTest(field=field, value=value):
                    client = RecordingClient()
                    with self.assertRaises(ValidationError) as caught:
                        generate_sales(self.request(**{field: value}), client)
                    self.assertEqual(caught.exception.details["field"], field)
                    self.assertEqual(client.calls, [])

    def test_invalid_optional_values_block_before_client_call(self):
        for field, values in [("customer_country", ["", 42]),
                              ("requested_quantity", [0, -1, True, "500", 1.5])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    client = RecordingClient()
                    with self.assertRaises(ValidationError):
                        generate_sales(self.request(**{field: value}), client)
                    self.assertFalse(client.calls)

    def test_dict_cannot_bypass_shared_contract(self):
        with self.assertRaises(ValidationError):
            build_sales_messages(self.request().to_dict())

    def test_provider_failure_has_no_normal_response_or_raw_exception(self):
        client = RecordingClient(error=RuntimeError("PRIVATE-TEST-PAYLOAD"))
        with self.assertRaises(CrossBotError) as caught:
            generate_sales(self.request(), client)
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertNotIn("PRIVATE-TEST-PAYLOAD", str(caught.exception))
        self.assertEqual(len(client.calls), 1)

    def test_structured_provider_error_is_preserved(self):
        error = CrossBotError(ErrorCode.PROVIDER_TIMEOUT, "请求超时", retryable=True)
        with self.assertRaises(CrossBotError) as caught:
            generate_sales(self.request(), RecordingClient(error=error))
        self.assertIs(caught.exception, error)

    def test_empty_or_nontext_provider_output_is_rejected(self):
        for content in [None, "", " \n", {"text": "reply"}]:
            with self.subTest(content=content), self.assertRaises(CrossBotError):
                generate_sales(self.request(), RecordingClient(output=content))


if __name__ == "__main__":
    unittest.main()
