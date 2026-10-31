"""Verify rejection cases and real Streamlit widget interactions, offline."""
import json
import unittest
from pathlib import Path

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from local_lab.schema import SampleInput

VALID = {"product_name": "测试产品", "quantity": 12, "target_language": "英语"}
APP = Path(__file__).with_name("app.py")


class EnvironmentChecks(unittest.TestCase):
    def test_valid_json_round_trip(self):
        value = SampleInput.model_validate_json(json.dumps(VALID))
        self.assertEqual(SampleInput.model_validate_json(value.model_dump_json()), value)

    def test_invalid_input_rejected(self):
        for patch in [
            {"quantity": 0}, {"quantity": -1}, {"quantity": True},
            {"quantity": "12"}, {"target_language": "未知"},
            {"product_name": "   "}, {"unexpected": "field"},
        ]:
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                SampleInput.model_validate({**VALID, **patch})
        with self.assertRaises(ValidationError):
            SampleInput.model_validate_json("{broken")

    def test_page_valid_invalid_and_recovery(self):
        page = AppTest.from_file(str(APP), default_timeout=30).run()
        self.assertEqual(len(page.exception), 0)
        page.button(key="validate").click().run()
        self.assertEqual(len(page.success), 1)
        page.text_area(key="payload").set_value(json.dumps({**VALID, "quantity": 0})).run()
        self.assertEqual(len(page.success), 0)
        self.assertTrue(any("输入已修改，请重新校验" in w.value for w in page.warning))
        page.button(key="validate").click().run()
        self.assertEqual(len(page.error), 1)
        self.assertNotIn("validated", page.session_state)
        page.text_area(key="payload").set_value(json.dumps(VALID)).run()
        page.button(key="validate").click().run()
        self.assertEqual(len(page.error), 0)
        self.assertEqual(len(page.success), 1)
        self.assertEqual(len(page.exception), 0)

    def test_page_reports_malformed_json(self):
        page = AppTest.from_file(str(APP), default_timeout=30).run()
        page.text_area(key="payload").set_value("{broken").run()
        page.button(key="validate").click().run()
        self.assertEqual(len(page.error), 1)
        self.assertEqual(len(page.exception), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

