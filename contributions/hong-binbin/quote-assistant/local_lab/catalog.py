"""Read the learning catalog from disk on every page refresh."""
from pathlib import Path
from typing import Literal
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CATALOG_PATH = Path(__file__).resolve().parents[1] / "planning" / "fixtures" / "products.json"


class PriceTier(BaseModel):
    min_quantity: int = Field(strict=True, gt=0)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class Product(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    sku: str = Field(min_length=1)
    name_zh: str = Field(min_length=1)
    material: str = Field(min_length=1)
    moq: int = Field(strict=True, gt=0)
    price_tiers: list[PriceTier] = Field(min_length=1)

    @model_validator(mode="after")
    def check_price_tiers(self):
        thresholds = [tier.min_quantity for tier in self.price_tiers]
        if len(set(thresholds)) != len(thresholds):
            raise ValueError("价格档位的起始数量不能重复")
        if min(thresholds) != self.moq:
            raise ValueError("第一个价格档位须从最小起订量开始")
        return self


class Catalog(BaseModel):
    data_kind: Literal["synthetic"]
    currency: Literal["USD"]
    products: list[Product] = Field(min_length=1)

    @model_validator(mode="after")
    def check_skus(self):
        if len({p.sku for p in self.products}) != len(self.products):
            raise ValueError("商品编号不能重复")
        return self


def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    # No cache: a saved file edit should be visible on the next rerun.
    return Catalog.model_validate_json(path.read_text(encoding="utf-8-sig"))
