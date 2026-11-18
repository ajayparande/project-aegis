"""Offline Upstox V3 historical response normalization (no network access)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from .contracts import HistoricalCandle, HistoricalCandleBatch
from .normalization import parse_decimal, parse_timestamp, parse_volume


class UpstoxHistoricalAdapter:
    provider_id = "upstox"

    def parse_historical_response(
        self, payload: object, *, instrument_key: str, interval: str, fetched_at: datetime
    ) -> HistoricalCandleBatch:
        if not isinstance(instrument_key, str) or not instrument_key.strip():
            raise ValueError("instrument_key is required")
        if interval not in {"1m", "1d"}:
            raise ValueError("only 1m and 1d are supported by this initial adapter")
        if (
            not isinstance(fetched_at, datetime)
            or fetched_at.tzinfo is None
            or fetched_at.utcoffset() is None
        ):
            raise ValueError("fetched_at must be timezone-aware")
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise ValueError("Upstox response must have success status")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
            raise TypeError("Upstox data.candles must be a list")
        rows = data["candles"]
        if len(rows) > 100_000:
            raise ValueError("response exceeds candle limit")
        normalized: list[HistoricalCandle] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, list) or len(row) < 6:
                raise ValueError(f"candle {idx} must contain timestamp, OHLC and volume")
            candle = HistoricalCandle(
                timestamp=parse_timestamp(row[0]),
                open=parse_decimal(row[1], "open"),
                high=parse_decimal(row[2], "high"),
                low=parse_decimal(row[3], "low"),
                close=parse_decimal(row[4], "close"),
                volume=parse_volume(row[5]),
            )
            normalized.append(candle)
        normalized.sort(key=lambda candle: candle.timestamp.astimezone(UTC))
        # Hash the response data without credentials, headers or request URLs.
        # Only the candle array is accepted as provenance material.
        canonical = json.dumps(
            rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return HistoricalCandleBatch(
            provider_id=self.provider_id,
            provider_instrument_key=instrument_key,
            interval=interval,
            candles=tuple(normalized),
            fetched_at=fetched_at,
            response_sha256=digest,
        )
