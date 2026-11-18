"""Bounded, read-only Upstox V3 historical HTTP client."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from .contracts import HistoricalCandleBatch
from .upstox import UpstoxHistoricalAdapter

_BASE_URL = "https://api.upstox.com"
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+[|][A-Za-z0-9_.-]+$")
_MAX_RESPONSE_BYTES = 8_000_000
_IST = ZoneInfo("Asia/Kolkata")


class UpstoxHistoricalHTTPError(RuntimeError):
    """Sanitized HTTP failure; never includes tokens or response bodies."""


class UpstoxHistoricalHTTPClient:
    """Read-only historical retrieval, independent of Aegis persistence."""

    def __init__(
        self,
        access_token: str,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: tuple[float, float] = (5.0, 15.0),
        min_request_interval: float = 1.0,
        max_attempts: int = 3,
    ) -> None:
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("access_token must be nonempty")
        if "\r" in access_token or "\n" in access_token:
            raise ValueError("access_token contains invalid characters")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool):
            raise TypeError("max_attempts must be an integer")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if (
            not isinstance(min_request_interval, (int, float))
            or isinstance(min_request_interval, bool)
            or not math.isfinite(min_request_interval)
            or not 0 <= min_request_interval <= 60
        ):
            raise ValueError("invalid min_request_interval")
        if (
            not isinstance(timeout, tuple)
            or len(timeout) != 2
            or any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not math.isfinite(x)
                or not 0 < x <= 60
                for x in timeout
            )
        ):
            raise ValueError("timeout must contain two positive bounded values")

        self._token = access_token
        if session is None:
            self._session = requests.Session()
            # Prevent implicit environment proxy, netrc, and CA configuration.
            self._session.trust_env = False
        else:
            # Explicitly injected sessions are trusted caller-owned dependencies.
            self._session = session
        self._sleep = sleep
        self._timeout = timeout
        self._min_request_interval = float(min_request_interval)
        self._max_attempts = max_attempts
        self._next_request_at = 0.0
        self._adapter = UpstoxHistoricalAdapter()
        self._fetch_lock = threading.Lock()

    def fetch(
        self,
        *,
        instrument_key: str,
        interval: str,
        from_date: date,
        to_date: date,
    ) -> HistoricalCandleBatch:
        """Serialize requests to preserve per-client pacing."""
        with self._fetch_lock:
            return self._fetch_serialized(
                instrument_key=instrument_key,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
            )

    def _fetch_serialized(
        self,
        *,
        instrument_key: str,
        interval: str,
        from_date: date,
        to_date: date,
    ) -> HistoricalCandleBatch:
        """Fetch one bounded historical range; no persistence or orders."""
        if not isinstance(instrument_key, str) or not _KEY_PATTERN.fullmatch(instrument_key):
            raise ValueError("invalid instrument_key")
        if interval not in ("1m", "1d"):
            raise ValueError("only 1m and 1d are supported")
        if (
            not isinstance(from_date, date)
            or isinstance(from_date, datetime)
            or not isinstance(to_date, date)
            or isinstance(to_date, datetime)
        ):
            raise TypeError("from_date and to_date must be dates")
        days = (to_date - from_date).days
        max_days = 7 if interval == "1m" else 366
        if days < 0 or days > max_days:
            raise ValueError("historical date range exceeds permitted bounds")

        unit = "minutes" if interval == "1m" else "days"
        key = quote(instrument_key, safe="")
        path = f"/v3/historical-candle/{key}/{unit}/1/{to_date.isoformat()}/{from_date.isoformat()}"
        url = _BASE_URL + path
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

        for attempt in range(self._max_attempts):
            wait = max(0.0, self._next_request_at - time.monotonic())
            if wait:
                self._sleep(wait)
            self._next_request_at = time.monotonic() + self._min_request_interval

            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    timeout=self._timeout,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException:
                if attempt + 1 == self._max_attempts:
                    raise UpstoxHistoricalHTTPError("Upstox network request failed") from None
                self._sleep(min(2**attempt, 4))
                continue

            try:
                status = response.status_code
                if status == 200:
                    chunks = []
                    size = 0
                    try:
                        for chunk in response.iter_content(chunk_size=65536):
                            size += len(chunk)
                            if size > _MAX_RESPONSE_BYTES:
                                raise UpstoxHistoricalHTTPError(
                                    "Upstox response exceeds size limit"
                                )
                            chunks.append(chunk)
                        payload = json.loads(b"".join(chunks))
                    except (ValueError, UnicodeError, requests.RequestException):
                        raise UpstoxHistoricalHTTPError("Invalid Upstox response payload") from None

                    fetched_at = datetime.now(UTC)
                    try:
                        batch = self._adapter.parse_historical_response(
                            payload,
                            instrument_key=instrument_key,
                            interval=interval,
                            fetched_at=fetched_at,
                        )
                        self._validate_window(batch, interval, from_date, to_date, fetched_at)
                        return batch
                    except (TypeError, ValueError, KeyError, OverflowError):
                        raise UpstoxHistoricalHTTPError(
                            "Upstox historical response failed validation"
                        ) from None

                if status in (401, 403):
                    raise UpstoxHistoricalHTTPError(
                        f"Upstox authorization rejected (HTTP {status})"
                    )
                if (status == 429 or 500 <= status <= 599) and attempt + 1 < self._max_attempts:
                    delay = float(min(2**attempt, 4))
                    if status == 429:
                        raw_retry_after = response.headers.get("Retry-After")
                        if raw_retry_after is not None:
                            try:
                                requested_delay = float(raw_retry_after)
                            except (TypeError, ValueError, OverflowError):
                                requested_delay = 0.0
                            if math.isfinite(requested_delay) and 0 <= requested_delay <= 30:
                                delay = max(delay, requested_delay)
                    self._sleep(delay)
                    continue
                raise UpstoxHistoricalHTTPError(f"Upstox historical request failed (HTTP {status})")
            except requests.RequestException:
                if attempt + 1 == self._max_attempts:
                    raise UpstoxHistoricalHTTPError("Upstox response transport failed") from None
                self._sleep(min(2**attempt, 4))
            finally:
                try:
                    response.close()
                except requests.RequestException:
                    pass

        raise UpstoxHistoricalHTTPError("Upstox retry limit reached")

    @staticmethod
    def _validate_window(
        batch: HistoricalCandleBatch,
        interval: str,
        from_date: date,
        to_date: date,
        fetched_at: datetime,
    ) -> None:
        """Reject out-of-range, duplicate, future and unaligned candles."""
        seen: set[datetime] = set()
        upper_bound = fetched_at.astimezone(UTC)

        for candle in batch.candles:
            timestamp = candle.timestamp.astimezone(UTC)
            local_time = timestamp.astimezone(_IST)

            if not from_date <= local_time.date() <= to_date:
                raise ValueError("candle outside requested date range")
            if timestamp > upper_bound:
                raise ValueError("future candle timestamp")
            if timestamp in seen:
                raise ValueError("duplicate candle timestamp")
            if interval == "1m" and (local_time.second != 0 or local_time.microsecond != 0):
                raise ValueError("unaligned minute candle")

            seen.add(timestamp)
