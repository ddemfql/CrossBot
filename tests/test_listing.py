import json
from dataclasses import replace
import unittest
from unittest.mock import Mock

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.schemas import ListingRequest
from providers.listing_mock import MockListingClient
from workflows.listing import build_listing_messages, generate_listings
from workflows.animal_lamp_demo import animal_listing_request


class ListingTests(unittest.TestCase):
    def request(self, **changes):
        data = dict(product_description='316不锈钢, "350ml"\n颜色：白色\n适配平台：这不是字段',
                    platforms=["Amazon"], target_country="DE", target_language="德文", correlation_id="listing-test")
        data.update(changes)
        return ListingRequest(**data)

    def test_each_selection_is_sent_and_returned_exactly(self):
        for platforms in [["Amazon"], ["TikTok"], ["阿里国际站"], ["Amazon", "TikTok"], ["TikTok", "阿里国际站", "Amazon"]]:
            with self.subTest(platforms=platforms):
                client = Mock(wraps=MockListingClient())
                request = self.request(platforms=platforms)
                results = generate_listings(request, client)
                self.assertEqual(list(results), platforms)
                payload = json.loads(client.complete.call_args.args[0][1]["content"])
                for field, value in request.to_dict().items():
                    self.assertEqual(payload[field], value)
                self.assertEqual(set(payload["platform_guidance"]), set(platforms))
                for response in results.values():
                    self.assertEqual(response.correlation_id, "listing-test")
                    self.assertTrue(response.pending_confirmations)
                    self.assertIn("模拟", response.content)

    def test_animal_demo_api_prompt_pins_listing_fields_and_exclusions(self):
        system = build_listing_messages(animal_listing_request())[0]["content"]
        payload = json.loads(build_listing_messages(animal_listing_request())[1]["content"])
        self.assertIn("标题、5条", system)
        self.assertIn("不要写订单价格", system)
        self.assertIn("健康功效", system)
        self.assertEqual(payload["platforms"], ["Amazon", "TikTok"])
        self.assertIn("DEMO-ANIMAL-LAMP-001", payload["product_description"])

    def test_animal_demo_listing_respects_selected_language_without_english_override(self):
        for language in ["德文", "中文"]:
            with self.subTest(language=language):
                request = replace(animal_listing_request(), target_language=language)
                messages = build_listing_messages(request)
                payload = json.loads(messages[1]["content"])
                self.assertEqual(payload["target_language"], language)
                self.assertIn("target_language决定文案语言", messages[0]["content"])
                self.assertNotIn("只输出英文标题", messages[0]["content"])
                self.assertEqual(payload["platforms"], request.platforms)

    def test_empty_unsupported_duplicate_or_wrong_platform_type_never_calls(self):
        for platforms in [[], None, "Amazon", ["Unknown"], ["Amazon", "Amazon"], [False], [["Amazon"]]]:
            with self.subTest(platforms=platforms):
                client = Mock()
                with self.assertRaises(ValidationError):
                    generate_listings(self.request(platforms=platforms), client)
                client.complete.assert_not_called()

    def test_required_and_optional_type_errors_never_call(self):
        for field, values in [("product_description", ["", " \n", 42]), ("target_language", [None, ""]),
                              ("target_country", ["", 42]), ("correlation_id", [None, ""])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    client = Mock()
                    with self.assertRaises(ValidationError):
                        generate_listings(self.request(**{field: value}), client)
                    client.complete.assert_not_called()

    def test_unknown_market_is_not_inferred(self):
        payload = json.loads(build_listing_messages(self.request(target_country=None))[1]["content"])
        self.assertIsNone(payload["target_country"])

    def test_extra_missing_or_empty_platform_outputs_are_rejected(self):
        invalid = [{"Amazon": "a", "TikTok": "b"}, {}, {"TikTok": "b"}, {"Amazon": " "}, {"Amazon": 42}]
        for listings in invalid:
            with self.subTest(listings=listings), self.assertRaises(CrossBotError) as caught:
                generate_listings(self.request(), Mock(complete=Mock(return_value=json.dumps({"listings": listings}))))
            self.assertEqual(caught.exception.code, ErrorCode.PARSE_FAILED)

    def test_invalid_json_wrong_envelope_and_duplicate_keys_are_rejected(self):
        for raw in ["not-json", "[]", "null", "{}", '{"listings":{"Amazon":"a"},"extra":"b"}',
                    '{"listings":{"Amazon":"a","Amazon":"b"}}', None]:
            with self.subTest(raw=raw), self.assertRaises(CrossBotError):
                generate_listings(self.request(), Mock(complete=Mock(return_value=raw)))

    def test_error_is_not_a_normal_result_and_not_retried(self):
        client = Mock(complete=Mock(side_effect=RuntimeError("PRIVATE_TEST")))
        with self.assertRaises(CrossBotError) as caught:
            generate_listings(self.request(), client)
        self.assertNotIn("PRIVATE_TEST", str(caught.exception))
        client.complete.assert_called_once()

    def test_structured_errors_keep_code(self):
        error = CrossBotError(ErrorCode.PROVIDER_TIMEOUT, "超时", retryable=True)
        with self.assertRaises(CrossBotError) as caught:
            generate_listings(self.request(), Mock(complete=Mock(side_effect=error)))
        self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
