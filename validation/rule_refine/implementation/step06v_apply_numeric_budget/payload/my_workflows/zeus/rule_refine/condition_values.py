"""One comparator for local condition evidence and per-log filter evaluation."""

import math
import re
from decimal import Decimal, InvalidOperation


COMPARISON_VERSION = "strict_strings_and_numeric_equal_v1"
JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def evaluate_condition(condition, log):
    """Return a three-valued match and an optional fixed reason.

    Wire-format targets remain strings. Only numeric *source fields* use strict
    decimal equality; strings keep exact case-sensitive semantics. No tolerance,
    generic string coercion, nested lookup, or unknown-to-false conversion.
    Finite floats use the retained JSON-style decimal representation; this does
    not recover precision already lost when the historical JSON was decoded.
    """
    if condition.name not in log:
        return None, "missing_field"
    value = log[condition.name]
    if value is None:
        return None, "null_value"
    if isinstance(value, str):
        if condition.operator == "equal":
            return value == condition.value, None
        if condition.operator == "contain":
            return condition.value in value, None
        if condition.operator == "notcontain":
            return condition.value not in value, None
        return None, "unsupported_operator"
    if type(value) not in (int, float):
        return None, "non_string_value"
    if condition.operator != "equal":
        return None, "non_string_value"
    if isinstance(value, float):
        if not math.isfinite(value):
            return None, "non_finite_number"
        if value.is_integer() and abs(value) > 2**53:
            return None, "numeric_precision_not_supported"
    target = condition.value
    if (
        not isinstance(target, str)
        or len(target) > 4096
        or not JSON_NUMBER.fullmatch(target)
    ):
        return None, "invalid_numeric_literal"
    try:
        expected = Decimal(target)
        observed = Decimal(value) if type(value) is int else Decimal(str(value))
        if not expected.is_finite():
            return None, "invalid_numeric_literal"
        return observed == expected, None
    except (InvalidOperation, ValueError, OverflowError):
        return None, "invalid_numeric_literal"
