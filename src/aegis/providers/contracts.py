"""Broker-neutral, immutable historical candle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HistoricalCandle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError("timestamp must be a timezone-aware datetime")
        for field in ("open", "high", "low", "close"):
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be a positive finite Decimal")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be >= open, close, and low")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be <= open and close")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0:
            raise ValueError("volume must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class HistoricalCandleBatch:
    provider_id: str
    provider_instrument_key: str
    interval: str
    candles: tuple[HistoricalCandle, ...]
    fetched_at: datetime
    response_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("provider_id is required")
        if (
            not isinstance(self.provider_instrument_key, str)
            or not self.provider_instrument_key.strip()
        ):
            raise ValueError("provider_instrument_key is required")
        if not isinstance(self.interval, str) or not self.interval.strip():
            raise ValueError("interval is required")
        if not isinstance(self.candles, tuple) or not all(
            isinstance(c, HistoricalCandle) for c in self.candles
        ):
            raise ValueError("candles must be a tuple of HistoricalCandle")
        if (
            not isinstance(self.fetched_at, datetime)
            or self.fetched_at.tzinfo is None
            or self.fetched_at.utcoffset() is None
        ):
            raise ValueError("fetched_at must be timezone-aware")
        if (
            not isinstance(self.response_sha256, str)
            or len(self.response_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in self.response_sha256)
        ):
            raise ValueError("response_sha256 must be lowercase SHA-256 hex")
        timestamps = [c.timestamp for c in self.candles]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise ValueError("candles must have unique, ascending timestamps")


class HistoricalDataProvider(Protocol):
    """Only market-data access; intentionally no broker order methods."""

    provider_id: str

    def parse_historical_response(
        self, payload: object, *, instrument_key: str, interval: str, fetched_at: datetime
    ) -> HistoricalCandleBatch: ...
