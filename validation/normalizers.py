"""Pure value conversions; no parser, document mapping, or business approval."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from core.errors import ValidationError


def normalize_decimal(value: object) -> Decimal:
    """Accept finite Decimal, int, or plain ASCII decimal text without rounding."""
    if value is None:
        raise ValidationError("Numeric value is missing", details={"reason": "missing"})
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ValidationError("Unsupported numeric type", details={"reason": "unsupported_type"})
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValidationError("Numeric text is empty", details={"reason": "empty"})
        if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value):
            raise ValidationError("Expected plain decimal text", details={"reason": "invalid_format"})
    result = Decimal(value)
    if not result.is_finite():
        raise ValidationError("Numeric value must be finite", details={"reason": "nonfinite"})
    return result


def convert_weight(value: object, from_unit: str | None, to_unit: str = "kg") -> Decimal:
    """Convert explicit g/kg units exactly, independently of Decimal precision."""
    if from_unit not in ("g", "kg") or to_unit not in ("g", "kg"):
        raise ValidationError("Weight unit must be g or kg", details={"reason": "unsupported_unit"})
    number = normalize_decimal(value)
    shift = (3 if from_unit == "kg" else 0) - (3 if to_unit == "kg" else 0)
    sign, digits, exponent = number.as_tuple()
    return Decimal((sign, digits, exponent + shift))


def normalize_text(value: object) -> str:
    """Trim outer whitespace only; retain case, punctuation and leading zeroes."""
    if value is None:
        raise ValidationError("Text value is missing", details={"reason": "missing"})
    if not isinstance(value, str):
        raise ValidationError("Expected text", details={"reason": "unsupported_type"})
    text = value.strip()
    if not text:
        raise ValidationError("Text value is empty", details={"reason": "empty"})
    return text


def normalize_currency(value: object) -> str:
    """Normalize code syntax; the caller must confirm the currency identity."""
    text = normalize_text(value)
    if not re.fullmatch(r"[A-Za-z]{3}", text):
        raise ValidationError("Expected a confirmed three-letter currency code",
                              details={"reason": "invalid_currency"})
    return text.upper()


def normalize_date(value: object) -> date:
    """Accept a valid YYYY-MM-DD calendar date; never infer locale."""
    text = normalize_text(value)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        raise ValidationError("Expected YYYY-MM-DD", details={"reason": "invalid_date"})
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise ValidationError("Invalid calendar date", details={"reason": "invalid_date"}) from error

