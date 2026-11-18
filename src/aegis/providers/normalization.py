"""Strict, broker-neutral parsing helpers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a nonempty ISO-8601 string")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include UTC offset")
    return result


def parse_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise TypeError(f"{field} has invalid numeric type")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field} must not be blank")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return result


def parse_volume(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("volume must be a nonnegative integer")
    return value
