"""Boundary, money precision, and page recalculation checks."""
import unittest
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from local_lab.catalog import Product, load_catalog
from local_lab.quotation import calculate_goods


class QuotationChecks(unittest.TestCase):
    def setUp(self):
        self.product = next(p for p in load_catalog().products if p.sku == "BAG-001")

    def test_tier_boundaries(self):
        for quantity, price, total in [(100, "1.20", "120.00"), (300, "1.20", "360.00"),
                                       (999, "1.20", "1198.80"), (1000, "1.05", "1050.00")]:
            with self.subTest(quantity=quantity):
                result = calculate_goods(self.product, quantity)
                self.assertEqual(result.unit_price, Decimal(price))
                self.assertEqual(result.goods_total, Decimal(total))

    def test_no_quote_for_invalid_quantity(self):
        for quantity in [0, -1, 99, True, "300", 300.5, 1_000_001]:
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                calculate_goods(self.product, quantity)

    def test_tiers_need_not_be_in_file_order(self):
        data = self.product.model_dump()
        data["price_tiers"].reverse()
        result = calculate_goods(Product.model_validate(data), 1000)
        self.assertEqual(result.goods_total, Decimal("1050.00"))

    def test_invalid_price_tables_rejected(self):
        for tiers in [[], [{"min_quantity": 100, "unit_price": "NaN"}],
                      [{"min_quantity": 100, "unit_price": "-1.00"}],
                      [{"min_quantity": 100, "unit_price": "1.234"}],
                      [{"min_quantity": 200, "unit_price": "1.20"}],
                      [{"min_quantity": 100, "unit_price": "1.20"}]*2]:
            with self.subTest(tiers=tiers), self.assertRaises(ValidationError):
                Product.model_validate({**self.product.model_dump(), "price_tiers": tiers})

    def test_decimal_precision(self):
        product = Product(sku="TEST", name_zh="测试", material="测试", moq=1,
                          price_tiers=[{"min_quantity": 1, "unit_price": "0.10"}])
        self.assertEqual(calculate_goods(product, 3).goods_total, Decimal("0.30"))

    def test_page_recalculates_and_removes_invalid_total(self):
        page = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=30).run()
        def total():
            return [m.value for m in page.metric if m.label == "货品金额（USD）"]
        self.assertFalse(page.exception)
        self.assertEqual(total(), ["360.00"])
        page.number_input(key="quote_quantity").set_value(1000).run()
        self.assertEqual(total(), ["1,050.00"])
        page.number_input(key="quote_quantity").set_value(50).run()
        self.assertEqual(total(), [])
        page.number_input(key="quote_quantity").set_value(300).run()
        page.selectbox(key="quote_sku").select("BAG-002").run()
        self.assertEqual(total(), ["405.00"])
        self.assertFalse(page.exception)


if __name__ == "__main__":
    unittest.main(verbosity=2)
