"""Deterministic goods-only totals; no model, freight, or tax calculation."""
from dataclasses import dataclass
from decimal import Decimal

from local_lab.catalog import Product


@dataclass(frozen=True)
class GoodsQuote:
    sku: str
    quantity: int
    tier_min_quantity: int
    unit_price: Decimal
    goods_total: Decimal


def calculate_goods(product: Product, quantity: int) -> GoodsQuote:
    if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
        raise ValueError("数量必须是 1 到 1,000,000 之间的整数。")
    if quantity < product.moq:
        raise ValueError(f"该商品最少订购 {product.moq} 个；当前数量不足，暂不计算。")

    # Pick the highest threshold reached by this quantity, regardless of file order.
    tier = max(
        (tier for tier in product.price_tiers if tier.min_quantity <= quantity),
        key=lambda tier: tier.min_quantity,
    )
    total = (tier.unit_price * quantity).quantize(Decimal("0.01"))
    return GoodsQuote(product.sku, quantity, tier.min_quantity, tier.unit_price, total)
