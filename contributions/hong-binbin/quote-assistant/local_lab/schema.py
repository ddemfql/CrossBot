"""Small validation example used only to check the local toolchain."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(strict=True, gt=0, le=1_000_000)
    target_language: Literal["英语", "西班牙语"]
    notes: str = Field(default="", max_length=2000)

