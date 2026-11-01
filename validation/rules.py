"""Pure comparisons of caller-prepared values, not formal Case check results.

context_confirmed means that applicability, roles, item pairing, value selection,
scope and the chosen comparison policy are already established by the caller.
These helpers do not select ExtractedField.value vs normalized_value, inspect
document versions/sources, build evidence, or approve a Case.
"""
from __future__ import annotations

from decimal import Context, Decimal, localcontext

from core.errors import ValidationError
from core.models import CrossCheckStatus, FieldValueStatus
from validation.normalizers import (
    convert_weight, normalize_currency, normalize_decimal, normalize_text,
)


def _confirmed(left_status: FieldValueStatus | None,
               right_status: FieldValueStatus | None, context_confirmed: bool) -> bool:
    return (left_status is FieldValueStatus.CONFIRMED
            and right_status is FieldValueStatus.CONFIRMED
            and context_confirmed is True)


def _validate_policy(value: Decimal | None, name: str) -> None:
    if value is not None and (not isinstance(value, Decimal)
                              or not value.is_finite() or value < 0):
        raise ValidationError(f"{name} must be a finite nonnegative Decimal",
                              details={"reason": "invalid_policy", "field": name})


def _compare_numbers(left: Decimal, right: Decimal, minimum: Decimal,
                     tolerance: Decimal) -> CrossCheckStatus:
    if left < minimum or right < minimum:
        return CrossCheckStatus.PENDING_REVIEW
    # Subtraction must not discard a difference beyond the ambient precision.
    values = (left, right, tolerance)
    precision = max(value.adjusted() for value in values) - min(
        value.as_tuple().exponent for value in values
    ) + 2
    with localcontext(Context(prec=max(1, precision))):
        difference = (left - right).copy_abs()
    return CrossCheckStatus.PASS if difference <= tolerance else CrossCheckStatus.FAIL


def compare_quantity(
    left: object, right: object, *,
    left_status: FieldValueStatus | None = None,
    right_status: FieldValueStatus | None = None,
    context_confirmed: bool = False,
    left_unit: str | None = None, right_unit: str | None = None,
    minimum: Decimal | None = None, allow_fractional: bool | None = None,
) -> CrossCheckStatus:
    """Compare a pre-paired quantity/count; no box/item conversion or row matching."""
    _validate_policy(minimum, "minimum")
    if (not _confirmed(left_status, right_status, context_confirmed)
            or minimum is None or type(allow_fractional) is not bool):
        return CrossCheckStatus.PENDING_REVIEW
    try:
        if normalize_text(left_unit) != normalize_text(right_unit):
            return CrossCheckStatus.PENDING_REVIEW
        left_number, right_number = normalize_decimal(left), normalize_decimal(right)
    except ValidationError:
        return CrossCheckStatus.PENDING_REVIEW
    if not allow_fractional and any(
        value != value.to_integral_value() for value in (left_number, right_number)
    ):
        return CrossCheckStatus.PENDING_REVIEW
    return _compare_numbers(left_number, right_number, minimum, Decimal(0))


def compare_weight(
    left: object, right: object, *,
    left_status: FieldValueStatus | None = None,
    right_status: FieldValueStatus | None = None,
    context_confirmed: bool = False,
    left_unit: str | None = None, right_unit: str | None = None,
    minimum: Decimal | None = None, tolerance: Decimal | None = None,
) -> CrossCheckStatus:
    """Compare the same weight type and scope; minimum/tolerance are in kg."""
    _validate_policy(minimum, "minimum")
    _validate_policy(tolerance, "tolerance")
    if (not _confirmed(left_status, right_status, context_confirmed)
            or minimum is None or tolerance is None):
        return CrossCheckStatus.PENDING_REVIEW
    try:
        left_number = convert_weight(left, left_unit)
        right_number = convert_weight(right, right_unit)
    except ValidationError:
        return CrossCheckStatus.PENDING_REVIEW
    return _compare_numbers(left_number, right_number, minimum, tolerance)


def compare_money(
    left: object, right: object, *,
    left_status: FieldValueStatus | None = None,
    right_status: FieldValueStatus | None = None,
    context_confirmed: bool = False,
    left_currency: str | None = None, right_currency: str | None = None,
    minimum: Decimal | None = None, tolerance: Decimal | None = None,
) -> CrossCheckStatus:
    """Compare same-currency amounts, without rounding or currency conversion."""
    _validate_policy(minimum, "minimum")
    _validate_policy(tolerance, "tolerance")
    if (not _confirmed(left_status, right_status, context_confirmed)
            or minimum is None or tolerance is None):
        return CrossCheckStatus.PENDING_REVIEW
    try:
        if normalize_currency(left_currency) != normalize_currency(right_currency):
            return CrossCheckStatus.PENDING_REVIEW
        left_number, right_number = normalize_decimal(left), normalize_decimal(right)
    except ValidationError:
        return CrossCheckStatus.PENDING_REVIEW
    return _compare_numbers(left_number, right_number, minimum, tolerance)


def compare_text(
    left: object, right: object, *,
    left_status: FieldValueStatus | None = None,
    right_status: FieldValueStatus | None = None,
    context_confirmed: bool = False,
) -> CrossCheckStatus:
    """Compare confirmed literals; no implicit aliases, case folding or translation."""
    if not _confirmed(left_status, right_status, context_confirmed):
        return CrossCheckStatus.PENDING_REVIEW
    try:
        equal = normalize_text(left) == normalize_text(right)
    except ValidationError:
        return CrossCheckStatus.PENDING_REVIEW
    return CrossCheckStatus.PASS if equal else CrossCheckStatus.FAIL


def compare_currency(
    left: object, right: object, *,
    left_status: FieldValueStatus | None = None,
    right_status: FieldValueStatus | None = None,
    context_confirmed: bool = False,
) -> CrossCheckStatus:
    """Report code conflicts separately from the amount comparison."""
    if not _confirmed(left_status, right_status, context_confirmed):
        return CrossCheckStatus.PENDING_REVIEW
    try:
        equal = normalize_currency(left) == normalize_currency(right)
    except ValidationError:
        return CrossCheckStatus.PENDING_REVIEW
    return CrossCheckStatus.PASS if equal else CrossCheckStatus.FAIL


# Preserve the W3 workspace adapter while sharing the quality branch's pure
# comparisons. New review orchestration uses these explicit comparison APIs.
from .workspace_rules import DeterministicRules, number, trade_values, validate_trade

