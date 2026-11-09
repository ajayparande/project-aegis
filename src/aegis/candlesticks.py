"""Deterministic candlestick-pattern and price-action detection library."""

from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, time, timedelta
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    InvalidOperation,
    localcontext,
)
from itertools import pairwise
from types import MappingProxyType
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "Candle",
    "CandlestickPatternError",
    "DetectionConfig",
    "PatternResult",
    "SessionContext",
    "detect_all_patterns",
    "detect_multi_candle_patterns",
    "detect_pattern",
    "detect_patterns",
    "detect_price_action",
    "detect_single_candle_patterns",
    "detect_two_candle_patterns",
    "prepare_candles",
]

_PATTERN_DEFINITION_VERSION = "1.0.0"


class CandlestickPatternError(ValueError):
    """Raised when candle data or detection configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Candle:
    """Immutable OHLCV candle using Decimal-based price storage."""

    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    is_complete: bool = True

    @classmethod
    def from_values(
        cls,
        timestamp: str,
        open: Decimal | int | str,
        high: Decimal | int | str,
        low: Decimal | int | str,
        close: Decimal | int | str,
        volume: int,
        *,
        is_complete: bool = True,
    ) -> Candle:
        return cls(
            timestamp=timestamp,
            open=_to_decimal(open, "open"),
            high=_to_decimal(high, "high"),
            low=_to_decimal(low, "low"),
            close=_to_decimal(close, "close"),
            volume=_to_int(volume, "volume"),
            is_complete=is_complete,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, str):
            raise CandlestickPatternError("timestamp must be an ISO-8601 string.")
        _timestamp_instant(self.timestamp)
        if any(
            not isinstance(value, Decimal) for value in (self.open, self.high, self.low, self.close)
        ):
            raise CandlestickPatternError("OHLC values must be Decimal values.")
        if any(not value.is_finite() for value in (self.open, self.high, self.low, self.close)):
            raise CandlestickPatternError("OHLC values must be finite Decimal values.")
        if self.high < self.low:
            raise CandlestickPatternError(
                f"Candle {self.timestamp} has invalid OHLC ordering: high < low."
            )
        if self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0:
            raise CandlestickPatternError(f"Candle {self.timestamp} contains a non-positive price.")
        if self.open < self.low or self.open > self.high:
            raise CandlestickPatternError(
                f"Candle {self.timestamp} has an open outside its low/high range."
            )
        if self.close < self.low or self.close > self.high:
            raise CandlestickPatternError(
                f"Candle {self.timestamp} has a close outside its low/high range."
            )
        if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0:
            raise CandlestickPatternError(
                f"Candle {self.timestamp} volume must be a non-negative integer."
            )
        if not isinstance(self.is_complete, bool) or not self.is_complete:
            raise CandlestickPatternError("Only completed candles are accepted.")

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def full_range(self) -> Decimal:
        return self.high - self.low

    @property
    def upper_shadow(self) -> Decimal:
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> Decimal:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def is_doji(self) -> bool:
        return self.body == 0

    @property
    def is_zero_range(self) -> bool:
        return self.full_range == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "open": _decimal_text(self.open),
            "high": _decimal_text(self.high),
            "low": _decimal_text(self.low),
            "close": _decimal_text(self.close),
            "volume": self.volume,
            "is_complete": self.is_complete,
        }


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    """Explicit threshold configuration for deterministic detection."""

    doji_body_ratio: Decimal = Decimal("0.10")
    long_wick_ratio: Decimal = Decimal(2)
    short_wick_ratio: Decimal = Decimal("0.25")
    equal_tolerance: Decimal = Decimal("0.000001")
    min_range: Decimal = Decimal("0.01")
    support_zone_tolerance: Decimal = Decimal("0.01")
    breakout_buffer: Decimal = Decimal("0.0001")
    gap_tolerance: Decimal = Decimal("0.0001")
    trend_tolerance: Decimal = Decimal("0.0001")
    marubozu_body_ratio: Decimal = Decimal("0.8")
    marubozu_max_shadow_ratio: Decimal = Decimal("0.1")
    pin_max_body_ratio: Decimal = Decimal("0.4")
    long_legged_min_shadow_ratio: Decimal = Decimal("0.3")
    spinning_top_max_body_ratio: Decimal = Decimal("0.3")
    rejection_min_shadow_ratio: Decimal = Decimal("0.5")
    confirmation_lookback: int = 2
    range_lookback: int = 20
    range_max_width_ratio: Decimal = Decimal("0.03")
    range_max_net_change_ratio: Decimal = Decimal("0.5")
    pattern_lookback: int = 100

    def __post_init__(self) -> None:
        for field_name in (
            "doji_body_ratio",
            "long_wick_ratio",
            "short_wick_ratio",
            "equal_tolerance",
            "min_range",
            "support_zone_tolerance",
            "breakout_buffer",
            "gap_tolerance",
            "trend_tolerance",
            "marubozu_body_ratio",
            "marubozu_max_shadow_ratio",
            "pin_max_body_ratio",
            "long_legged_min_shadow_ratio",
            "spinning_top_max_body_ratio",
            "rejection_min_shadow_ratio",
            "range_max_width_ratio",
            "range_max_net_change_ratio",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise CandlestickPatternError(f"{field_name} must be a finite Decimal.")
            if value < 0 or (field_name in {"doji_body_ratio", "long_wick_ratio"} and value == 0):
                raise CandlestickPatternError(f"{field_name} is outside its valid range.")
        if self.doji_body_ratio > 1 or self.short_wick_ratio > 1:
            raise CandlestickPatternError("Body and short-wick ratios must not exceed 1.")
        if any(
            value > 1
            for value in (
                self.marubozu_body_ratio,
                self.marubozu_max_shadow_ratio,
                self.pin_max_body_ratio,
                self.long_legged_min_shadow_ratio,
                self.spinning_top_max_body_ratio,
                self.rejection_min_shadow_ratio,
            )
        ):
            raise CandlestickPatternError("Pattern ratios must not exceed 1.")
        if (
            self.marubozu_body_ratio == 0
            or self.pin_max_body_ratio == 0
            or self.long_legged_min_shadow_ratio == 0
            or self.spinning_top_max_body_ratio == 0
            or self.rejection_min_shadow_ratio == 0
        ):
            raise CandlestickPatternError("Pattern ratios must be positive.")
        if (
            self.spinning_top_max_body_ratio <= self.doji_body_ratio
            or self.long_legged_min_shadow_ratio > Decimal("0.5")
            or self.marubozu_body_ratio + Decimal(2) * self.marubozu_max_shadow_ratio > 1
        ):
            raise CandlestickPatternError(
                "Pattern ratio thresholds have an impossible relationship."
            )
        if self.min_range == 0:
            raise CandlestickPatternError("min_range must be positive.")
        if self.range_max_width_ratio <= 0 or self.range_max_width_ratio > 1:
            raise CandlestickPatternError("range_max_width_ratio must be in (0, 1].")
        if self.range_max_net_change_ratio > 1:
            raise CandlestickPatternError("range_max_net_change_ratio must be in [0, 1].")
        if (
            isinstance(self.confirmation_lookback, bool)
            or not isinstance(self.confirmation_lookback, int)
            or self.confirmation_lookback < 1
        ):
            raise CandlestickPatternError("confirmation_lookback must be at least 1.")
        if (
            isinstance(self.range_lookback, bool)
            or not isinstance(self.range_lookback, int)
            or self.range_lookback < 2
        ):
            raise CandlestickPatternError("range_lookback must be an integer of at least 2.")
        if (
            isinstance(self.pattern_lookback, bool)
            or not isinstance(self.pattern_lookback, int)
            or self.pattern_lookback < self.confirmation_lookback * 2 + 1
        ):
            raise CandlestickPatternError(
                "pattern_lookback must cover at least the confirmation windows."
            )


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Optional session metadata required for opening-range detection."""

    session_open: str
    session_close: str
    timezone: str = "Asia/Kolkata"
    market_name: str | None = None
    opening_range_minutes: int = 5
    candle_interval_minutes: int = 1

    def __post_init__(self) -> None:
        if not self.session_open or not self.session_close:
            raise CandlestickPatternError("session_open and session_close must be supplied.")
        if not self.timezone:
            raise CandlestickPatternError("timezone must be non-empty.")
        opening = _parse_session_time(self.session_open, "session_open")
        closing = _parse_session_time(self.session_close, "session_close")
        if opening >= closing:
            raise CandlestickPatternError("session_open must be earlier than session_close.")
        if self.market_name is not None and not isinstance(self.market_name, str):
            raise CandlestickPatternError("market_name must be a string or None.")
        if (
            isinstance(self.opening_range_minutes, bool)
            or not isinstance(self.opening_range_minutes, int)
            or self.opening_range_minutes < 1
        ):
            raise CandlestickPatternError("opening_range_minutes must be a positive integer.")
        if (
            isinstance(self.candle_interval_minutes, bool)
            or not isinstance(self.candle_interval_minutes, int)
            or self.candle_interval_minutes < 1
        ):
            raise CandlestickPatternError("candle_interval_minutes must be a positive integer.")
        if self.opening_range_minutes * 60 > (
            closing.hour * 3600 + closing.minute * 60 + closing.second
        ) - (opening.hour * 3600 + opening.minute * 60 + opening.second):
            raise CandlestickPatternError("Opening range must fit within the declared session.")
        if self.opening_range_minutes % self.candle_interval_minutes:
            raise CandlestickPatternError(
                "opening_range_minutes must be a multiple of candle_interval_minutes."
            )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise CandlestickPatternError(f"Unknown session timezone: {self.timezone!r}.") from exc


@dataclass(frozen=True, slots=True)
class PatternResult:
    """Structured, serializable detection result."""

    name: str
    pattern_id: str
    category: str
    direction: str | None
    start_timestamp: str | None = None
    end_timestamp: str | None = None
    confirmation_timestamp: str | None = None
    instrument: str | None = None
    interval: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    is_confirmed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _freeze_mapping(self.details))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pattern_id": self.pattern_id,
            "category": self.category,
            "direction": self.direction,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "confirmation_timestamp": self.confirmation_timestamp,
            "instrument": self.instrument,
            "interval": self.interval,
            "details": _json_safe(self.details),
            "metadata": _json_safe(self.metadata),
            "is_confirmed": self.is_confirmed,
        }


def prepare_candles(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]], *, normalize: bool = False
) -> list[Candle]:
    """Validate and normalize candle input; default is fail-closed for unsorted sequences."""

    prepared: list[Candle] = []
    seen_timestamps: set[datetime] = set()
    for index, candle in enumerate(candles):
        if isinstance(candle, Candle):
            prepared_candle = candle
        elif isinstance(candle, dict):
            try:
                prepared_candle = Candle(
                    timestamp=candle["timestamp"],
                    open=_to_decimal(candle["open"], f"open[{index}]"),
                    high=_to_decimal(candle["high"], f"high[{index}]"),
                    low=_to_decimal(candle["low"], f"low[{index}]"),
                    close=_to_decimal(candle["close"], f"close[{index}]"),
                    volume=_to_int(candle["volume"], f"volume[{index}]"),
                    is_complete=candle.get("is_complete", True),
                )
            except KeyError as exc:  # pragma: no cover - defensive branch
                raise CandlestickPatternError(
                    f"Candle at index {index} is missing required OHLCV fields."
                ) from exc
        elif isinstance(candle, tuple) and len(candle) == 6:
            prepared_candle = Candle.from_values(
                timestamp=candle[0],
                open=candle[1],
                high=candle[2],
                low=candle[3],
                close=candle[4],
                volume=candle[5],
            )
        else:
            raise CandlestickPatternError(
                f"Unsupported candle entry at index {index}: {type(candle)!r}."
            )
        instant = _timestamp_instant(prepared_candle.timestamp)
        if instant in seen_timestamps:
            raise CandlestickPatternError(
                f"Duplicate candle instant detected: {prepared_candle.timestamp}."
            )
        seen_timestamps.add(instant)
        prepared.append(prepared_candle)
    if not prepared:
        return []
    if not normalize:
        for previous, current in pairwise(prepared):
            if _timestamp_instant(previous.timestamp) >= _timestamp_instant(current.timestamp):
                raise CandlestickPatternError(
                    "Candle sequence must be strictly chronological by timestamp instant."
                )
    else:
        prepared = sorted(prepared, key=lambda item: _timestamp_instant(item.timestamp))
        for previous, current in pairwise(prepared):
            if _timestamp_instant(previous.timestamp) >= _timestamp_instant(current.timestamp):
                raise CandlestickPatternError(
                    "Normalized candle sequence still contains duplicate timestamp instants."
                )
    return prepared


def _to_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise CandlestickPatternError(f"{label} cannot be a boolean.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:  # pragma: no cover - defensive branch
            raise CandlestickPatternError(
                f"{label} is not a valid decimal value: {value!r}."
            ) from exc
    elif isinstance(value, float):
        result = Decimal(str(value))
    else:
        raise CandlestickPatternError(f"{label} must be a Decimal, int, float, or string value.")
    if not result.is_finite():
        raise CandlestickPatternError(f"{label} must be finite.")
    return result


def _to_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise CandlestickPatternError(f"{label} cannot be a boolean.")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError as exc:
            raise CandlestickPatternError(
                f"{label} is not a valid integer value: {value!r}."
            ) from exc
    else:
        raise CandlestickPatternError(f"{label} must be an integer or integer-like string.")
    if result < 0:
        raise CandlestickPatternError(f"{label} must be non-negative.")
    return result


_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})"
)


def _timestamp_instant(value: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_PATTERN.fullmatch(value):
        raise CandlestickPatternError(
            "timestamp must be an ISO-8601 datetime with an explicit UTC offset."
        )
    if value.endswith("-00:00"):
        raise CandlestickPatternError("timestamp offset -00:00 is ambiguous and unsupported.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise CandlestickPatternError(f"Invalid ISO-8601 timestamp: {value!r}.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandlestickPatternError("timestamp must include an explicit UTC offset.")
    return parsed.astimezone(UTC)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CandlestickPatternError("Pattern result details and metadata must be mappings.")
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


@contextmanager
def _detection_context(
    candles: Sequence[Candle], config: DetectionConfig
) -> Generator[None, None, None]:
    values = [
        value
        for candle in candles
        for value in (candle.open, candle.high, candle.low, candle.close)
    ]
    values.extend(
        getattr(config, field_name)
        for field_name in (
            "doji_body_ratio",
            "long_wick_ratio",
            "short_wick_ratio",
            "equal_tolerance",
            "min_range",
            "support_zone_tolerance",
            "breakout_buffer",
            "gap_tolerance",
            "trend_tolerance",
            "marubozu_body_ratio",
            "marubozu_max_shadow_ratio",
            "pin_max_body_ratio",
            "long_legged_min_shadow_ratio",
            "spinning_top_max_body_ratio",
            "rejection_min_shadow_ratio",
            "range_max_width_ratio",
            "range_max_net_change_ratio",
        )
    )
    exponents: list[int] = []
    for value in values:
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int):
            raise CandlestickPatternError("Detection arithmetic requires finite Decimals.")
        exponents.append(exponent)
    adjusted = [value.adjusted() for value in values]
    precision = max(
        80,
        max(adjusted) - min(exponents) + max(len(v.as_tuple().digits) for v in values) + 16,
    )
    with localcontext(
        Context(prec=precision, rounding=ROUND_HALF_EVEN, Emax=MAX_EMAX, Emin=MIN_EMIN)
    ):
        yield


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=str)]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _parse_session_time(value: str, label: str) -> time:
    if not isinstance(value, str):
        raise CandlestickPatternError(f"{label} must be a time string.")
    text = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise CandlestickPatternError(f"{label} must be a valid HH:MM or HH:MM:SS time string.")


def _range(candle: Candle) -> Decimal:
    return candle.full_range


def _body_ratio(candle: Candle) -> Decimal:
    return Decimal(0) if _range(candle) == 0 else abs(candle.close - candle.open) / _range(candle)


def _shadow_ratio(candle: Candle, side: Literal["upper", "lower"]) -> Decimal:
    body = candle.body
    if body == 0:
        return Decimal(0)
    shadow = candle.upper_shadow if side == "upper" else candle.lower_shadow
    return shadow / body


def _pattern_result(
    *,
    name: str,
    pattern_id: str,
    category: str,
    direction: str | None,
    candle: Candle | None = None,
    start: Candle | None = None,
    end: Candle | None = None,
    confirmation: Candle | None = None,
    instrument: str | None = None,
    interval: str | None = None,
    details: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    is_confirmed: bool = True,
) -> PatternResult:
    result_metadata = dict(metadata or {})
    if category in {"single_candle", "two_candle", "multi_candle"}:
        result_metadata.setdefault(
            "interpretation",
            "Completed-candle formation only; trend context and predictive value are not assessed.",
        )
    return PatternResult(
        name=name,
        pattern_id=pattern_id,
        category=category,
        direction=direction,
        start_timestamp=start.timestamp if start else (candle.timestamp if candle else None),
        end_timestamp=end.timestamp if end else (candle.timestamp if candle else None),
        confirmation_timestamp=confirmation.timestamp
        if confirmation
        else (candle.timestamp if candle else None),
        instrument=instrument,
        interval=interval,
        details=details or {},
        metadata=result_metadata,
        is_confirmed=is_confirmed,
    )


def _single_candle_patterns(
    candles: Sequence[Candle], config: DetectionConfig
) -> list[PatternResult]:
    results: list[PatternResult] = []
    for candle in candles:
        if candle.full_range <= config.min_range:
            continue
        body_ratio = _body_ratio(candle)
        upper_ratio = _shadow_ratio(candle, "upper")
        lower_ratio = _shadow_ratio(candle, "lower")

        upper_bound = candle.full_range * config.short_wick_ratio
        lower_bound = candle.full_range * config.short_wick_ratio
        if (
            candle.body > 0
            and candle.is_bullish
            and candle.lower_shadow >= config.long_wick_ratio * candle.body
            and candle.upper_shadow <= upper_bound
        ):
            results.append(
                _pattern_result(
                    name="Hammer",
                    pattern_id="hammer",
                    category="single_candle",
                    direction="BULLISH",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
        if (
            candle.body > 0
            and candle.is_bearish
            and candle.lower_shadow >= config.long_wick_ratio * candle.body
            and candle.upper_shadow <= upper_bound
        ):
            results.append(
                _pattern_result(
                    name="Hanging Man",
                    pattern_id="hanging_man",
                    category="single_candle",
                    direction="BEARISH",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
        if (
            candle.body > 0
            and candle.is_bullish
            and candle.upper_shadow >= config.long_wick_ratio * candle.body
            and candle.lower_shadow <= lower_bound
        ):
            results.append(
                _pattern_result(
                    name="Inverted Hammer",
                    pattern_id="inverted_hammer",
                    category="single_candle",
                    direction="BULLISH",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
        if (
            candle.body > 0
            and candle.is_bearish
            and candle.upper_shadow >= config.long_wick_ratio * candle.body
            and candle.lower_shadow <= lower_bound
        ):
            results.append(
                _pattern_result(
                    name="Shooting Star",
                    pattern_id="shooting_star",
                    category="single_candle",
                    direction="BEARISH",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
        if candle.full_range > 0 and body_ratio <= config.doji_body_ratio:
            results.append(
                _pattern_result(
                    name="Doji",
                    pattern_id="doji",
                    category="single_candle",
                    direction="NEUTRAL",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
            if candle.lower_shadow >= candle.full_range * Decimal(
                "0.6"
            ) and candle.upper_shadow <= candle.full_range * Decimal("0.1"):
                results.append(
                    _pattern_result(
                        name="Dragonfly Doji",
                        pattern_id="dragonfly_doji",
                        category="single_candle",
                        direction="NEUTRAL",
                        candle=candle,
                        details={
                            "body_ratio": _decimal_text(body_ratio),
                            "upper_shadow": _decimal_text(candle.upper_shadow),
                            "lower_shadow": _decimal_text(candle.lower_shadow),
                        },
                    )
                )
            if candle.upper_shadow >= candle.full_range * Decimal(
                "0.6"
            ) and candle.lower_shadow <= candle.full_range * Decimal("0.1"):
                results.append(
                    _pattern_result(
                        name="Gravestone Doji",
                        pattern_id="gravestone_doji",
                        category="single_candle",
                        direction="NEUTRAL",
                        candle=candle,
                        details={
                            "body_ratio": _decimal_text(body_ratio),
                            "upper_shadow": _decimal_text(candle.upper_shadow),
                            "lower_shadow": _decimal_text(candle.lower_shadow),
                        },
                    )
                )
            if (
                candle.upper_shadow >= candle.full_range * config.long_legged_min_shadow_ratio
                and candle.lower_shadow >= candle.full_range * config.long_legged_min_shadow_ratio
                and candle.upper_shadow < candle.full_range * Decimal("0.6")
                and candle.lower_shadow < candle.full_range * Decimal("0.6")
            ):
                results.append(
                    _pattern_result(
                        name="Long-Legged Doji",
                        pattern_id="long_legged_doji",
                        category="single_candle",
                        direction="NEUTRAL",
                        candle=candle,
                        details={
                            "body_ratio": _decimal_text(body_ratio),
                            "upper_shadow": _decimal_text(candle.upper_shadow),
                            "lower_shadow": _decimal_text(candle.lower_shadow),
                        },
                    )
                )
        if (
            candle.full_range > 0
            and body_ratio > config.doji_body_ratio
            and body_ratio <= config.spinning_top_max_body_ratio
            and upper_ratio >= Decimal("0.5")
            and lower_ratio >= Decimal("0.5")
        ):
            results.append(
                _pattern_result(
                    name="Spinning Top",
                    pattern_id="spinning_top",
                    category="single_candle",
                    direction="NEUTRAL",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
        if (
            candle.is_bullish
            and candle.body >= candle.full_range * config.marubozu_body_ratio
            and candle.upper_shadow <= candle.full_range * config.marubozu_max_shadow_ratio
            and candle.lower_shadow <= candle.full_range * config.marubozu_max_shadow_ratio
        ):
            results.append(
                _pattern_result(
                    name="Bullish Marubozu",
                    pattern_id="bullish_marubozu",
                    category="single_candle",
                    direction="BULLISH",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
        if (
            candle.is_bearish
            and candle.body >= candle.full_range * config.marubozu_body_ratio
            and candle.upper_shadow <= candle.full_range * config.marubozu_max_shadow_ratio
            and candle.lower_shadow <= candle.full_range * config.marubozu_max_shadow_ratio
        ):
            results.append(
                _pattern_result(
                    name="Bearish Marubozu",
                    pattern_id="bearish_marubozu",
                    category="single_candle",
                    direction="BEARISH",
                    candle=candle,
                    details={
                        "body_ratio": _decimal_text(body_ratio),
                        "upper_shadow": _decimal_text(candle.upper_shadow),
                        "lower_shadow": _decimal_text(candle.lower_shadow),
                    },
                )
            )
        if (
            candle.full_range > 0
            and candle.body > 0
            and candle.body <= candle.full_range * config.pin_max_body_ratio
        ):
            pin_direction: str | None = None
            if (
                candle.lower_shadow >= config.long_wick_ratio * candle.body
                and candle.upper_shadow <= config.short_wick_ratio * candle.full_range
            ):
                pin_direction = "BULLISH"
            elif (
                candle.upper_shadow >= config.long_wick_ratio * candle.body
                and candle.lower_shadow <= config.short_wick_ratio * candle.full_range
            ):
                pin_direction = "BEARISH"
            if pin_direction is not None:
                results.append(
                    _pattern_result(
                        name="Pin Bar",
                        pattern_id="pin_bar",
                        category="single_candle",
                        direction=pin_direction,
                        candle=candle,
                        details={
                            "body_ratio": _decimal_text(body_ratio),
                            "upper_shadow": _decimal_text(candle.upper_shadow),
                            "lower_shadow": _decimal_text(candle.lower_shadow),
                        },
                    )
                )
    return results


def _two_candle_patterns(candles: Sequence[Candle], config: DetectionConfig) -> list[PatternResult]:
    results: list[PatternResult] = []
    for index in range(len(candles) - 1):
        previous = candles[index]
        current = candles[index + 1]
        tolerance = config.equal_tolerance
        previous_body_low = min(previous.open, previous.close)
        previous_body_high = max(previous.open, previous.close)
        current_body_low = min(current.open, current.close)
        current_body_high = max(current.open, current.close)
        midpoint = (previous.open + previous.close) / Decimal(2)

        if (
            previous.is_bearish
            and current.is_bullish
            and current_body_low <= previous_body_low
            and current_body_high >= previous_body_high
            and current.body >= previous.body
        ):
            results.append(
                _pattern_result(
                    name="Bullish Engulfing",
                    pattern_id="bullish_engulfing",
                    category="two_candle",
                    direction="BULLISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.is_bullish
            and current.is_bearish
            and current_body_low <= previous_body_low
            and current_body_high >= previous_body_high
            and current.body >= previous.body
        ):
            results.append(
                _pattern_result(
                    name="Bearish Engulfing",
                    pattern_id="bearish_engulfing",
                    category="two_candle",
                    direction="BEARISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.is_bearish
            and current.is_bullish
            and previous.body > current.body
            and current_body_low >= previous_body_low
            and current_body_high <= previous_body_high
        ):
            results.append(
                _pattern_result(
                    name="Bullish Harami",
                    pattern_id="bullish_harami",
                    category="two_candle",
                    direction="BULLISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.is_bullish
            and current.is_bearish
            and previous.body > current.body
            and current_body_low >= previous_body_low
            and current_body_high <= previous_body_high
        ):
            results.append(
                _pattern_result(
                    name="Bearish Harami",
                    pattern_id="bearish_harami",
                    category="two_candle",
                    direction="BEARISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.full_range > 0
            and current.full_range > 0
            and _is_doji(current, config)
            and previous.body > 0
            and current.body < previous.body
            and current_body_low >= previous_body_low
            and current_body_high <= previous_body_high
        ):
            results.append(
                _pattern_result(
                    name="Harami Cross",
                    pattern_id="harami_cross",
                    category="two_candle",
                    direction="BULLISH" if previous.is_bearish else "BEARISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.is_bearish
            and current.is_bullish
            and current.open < previous.low
            and current.close > midpoint
            and current.close < previous.open
        ):
            results.append(
                _pattern_result(
                    name="Piercing Line",
                    pattern_id="piercing_line",
                    category="two_candle",
                    direction="BULLISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.is_bullish
            and current.is_bearish
            and current.open > previous.high
            and current.close < midpoint
            and current.close > previous.open
        ):
            results.append(
                _pattern_result(
                    name="Dark Cloud Cover",
                    pattern_id="dark_cloud_cover",
                    category="two_candle",
                    direction="BEARISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.is_bearish
            and current.is_bullish
            and abs(previous.low - current.low) <= tolerance
        ):
            results.append(
                _pattern_result(
                    name="Tweezer Bottom",
                    pattern_id="tweezer_bottom",
                    category="two_candle",
                    direction="BULLISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={"low_tolerance": _decimal_text(tolerance)},
                )
            )
        if (
            previous.is_bullish
            and current.is_bearish
            and abs(previous.high - current.high) <= tolerance
        ):
            results.append(
                _pattern_result(
                    name="Tweezer Top",
                    pattern_id="tweezer_top",
                    category="two_candle",
                    direction="BEARISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={"high_tolerance": _decimal_text(tolerance)},
                )
            )

        if previous.is_bearish and current.is_bullish and current.open > previous.high:
            results.append(
                _pattern_result(
                    name="Bullish Kicker",
                    pattern_id="bullish_kicker",
                    category="two_candle",
                    direction="BULLISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )
        if previous.is_bullish and current.is_bearish and current.open < previous.low:
            results.append(
                _pattern_result(
                    name="Bearish Kicker",
                    pattern_id="bearish_kicker",
                    category="two_candle",
                    direction="BEARISH",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )

        if (
            previous.is_bearish
            and current.is_bearish
            and abs(previous.close - current.close) <= tolerance
        ):
            results.append(
                _pattern_result(
                    name="Matching Low",
                    pattern_id="matching_low",
                    category="two_candle",
                    direction="NEUTRAL",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={"low_tolerance": _decimal_text(tolerance)},
                )
            )
        if (
            previous.is_bullish
            and current.is_bullish
            and abs(previous.close - current.close) <= tolerance
        ):
            results.append(
                _pattern_result(
                    name="Matching High",
                    pattern_id="matching_high",
                    category="two_candle",
                    direction="NEUTRAL",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={"high_tolerance": _decimal_text(tolerance)},
                )
            )

        if (
            current.high <= previous.high
            and current.low >= previous.low
            and (current.high < previous.high or current.low > previous.low)
        ):
            results.append(
                _pattern_result(
                    name="Inside Bar",
                    pattern_id="inside_bar",
                    category="two_candle",
                    direction="NEUTRAL",
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={},
                )
            )
        if current.high > previous.high and current.low < previous.low:
            results.append(
                _pattern_result(
                    name="Outside Bar",
                    pattern_id="outside_bar",
                    category="two_candle",
                    direction=(
                        "BULLISH"
                        if current.is_bullish
                        else "BEARISH"
                        if current.is_bearish
                        else "NEUTRAL"
                    ),
                    start=previous,
                    end=current,
                    confirmation=current,
                    details={
                        "prior_high": _decimal_text(previous.high),
                        "prior_low": _decimal_text(previous.low),
                    },
                )
            )
    return results


def _multi_candle_patterns(
    candles: Sequence[Candle], config: DetectionConfig
) -> list[PatternResult]:
    results: list[PatternResult] = []
    for index in range(2, len(candles)):
        first, second, third = candles[index - 2 : index + 1]
        midpoint = (first.open + first.close) / Decimal(2)
        if (
            first.is_bearish
            and second.body < first.body
            and third.is_bullish
            and third.close > midpoint
        ):
            pattern_id = "morning_doji_star" if _is_doji(second, config) else "morning_star"
            results.append(
                _pattern_result(
                    name="Morning Doji Star"
                    if pattern_id.endswith("doji_star")
                    else "Morning Star",
                    pattern_id=pattern_id,
                    category="multi_candle",
                    direction="BULLISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={"midpoint": _decimal_text(midpoint)},
                )
            )
        if (
            first.is_bullish
            and second.body < first.body
            and third.is_bearish
            and third.close < midpoint
        ):
            pattern_id = "evening_doji_star" if _is_doji(second, config) else "evening_star"
            results.append(
                _pattern_result(
                    name="Evening Doji Star"
                    if pattern_id.endswith("doji_star")
                    else "Evening Star",
                    pattern_id=pattern_id,
                    category="multi_candle",
                    direction="BEARISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={"midpoint": _decimal_text(midpoint)},
                )
            )

        if (
            first.is_bearish
            and second.is_bullish
            and min(second.open, second.close) >= min(first.open, first.close)
            and max(second.open, second.close) <= max(first.open, first.close)
            and first.body > second.body
            and third.is_bullish
            and third.close > first.open
        ):
            results.append(
                _pattern_result(
                    name="Three Inside Up",
                    pattern_id="three_inside_up",
                    category="multi_candle",
                    direction="BULLISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )
        if (
            first.is_bullish
            and second.is_bearish
            and min(second.open, second.close) >= min(first.open, first.close)
            and max(second.open, second.close) <= max(first.open, first.close)
            and first.body > second.body
            and third.is_bearish
            and third.close < first.open
        ):
            results.append(
                _pattern_result(
                    name="Three Inside Down",
                    pattern_id="three_inside_down",
                    category="multi_candle",
                    direction="BEARISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )
        if (
            first.is_bearish
            and second.is_bullish
            and second.open <= first.close
            and second.close >= first.open
            and third.is_bullish
            and third.close > second.close
        ):
            results.append(
                _pattern_result(
                    name="Three Outside Up",
                    pattern_id="three_outside_up",
                    category="multi_candle",
                    direction="BULLISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )
        if (
            first.is_bullish
            and second.is_bearish
            and second.open >= first.close
            and second.close <= first.open
            and third.is_bearish
            and third.close < second.close
        ):
            results.append(
                _pattern_result(
                    name="Three Outside Down",
                    pattern_id="three_outside_down",
                    category="multi_candle",
                    direction="BEARISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )
        if (
            first.is_bullish
            and second.is_bullish
            and third.is_bullish
            and first.close < second.close < third.close
            and first.open <= second.open < first.close
            and second.open <= third.open < second.close
            and all(
                candle.close >= candle.low + candle.full_range * Decimal("0.66")
                for candle in (first, second, third)
            )
        ):
            results.append(
                _pattern_result(
                    name="Three White Soldiers",
                    pattern_id="three_white_soldiers",
                    category="multi_candle",
                    direction="BULLISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )
        if (
            first.is_bearish
            and second.is_bearish
            and third.is_bearish
            and first.close > second.close > third.close
            and first.close < second.open <= first.open
            and second.close < third.open <= second.open
            and all(
                candle.close <= candle.low + candle.full_range * Decimal("0.34")
                for candle in (first, second, third)
            )
        ):
            results.append(
                _pattern_result(
                    name="Three Black Crows",
                    pattern_id="three_black_crows",
                    category="multi_candle",
                    direction="BEARISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )

    for index in range(4, len(candles)):
        first, *correction, fifth = candles[index - 4 : index + 1]
        if (
            first.is_bullish
            and all(candle.is_bearish for candle in correction)
            and all(first.low <= candle.low and candle.high <= first.high for candle in correction)
            and all(candle.body < first.body for candle in correction)
            and fifth.is_bullish
            and fifth.close > first.close
        ):
            results.append(
                _pattern_result(
                    name="Rising Three Methods",
                    pattern_id="rising_three_methods",
                    category="multi_candle",
                    direction="BULLISH",
                    start=first,
                    end=fifth,
                    confirmation=fifth,
                    details={},
                )
            )
        if (
            first.is_bearish
            and all(candle.is_bullish for candle in correction)
            and all(first.low <= candle.low and candle.high <= first.high for candle in correction)
            and all(candle.body < first.body for candle in correction)
            and fifth.is_bearish
            and fifth.close < first.close
        ):
            results.append(
                _pattern_result(
                    name="Falling Three Methods",
                    pattern_id="falling_three_methods",
                    category="multi_candle",
                    direction="BEARISH",
                    start=first,
                    end=fifth,
                    confirmation=fifth,
                    details={},
                )
            )

    for index in range(3, len(candles)):
        first, second, third, fourth = candles[index - 3 : index + 1]
        if (
            first.is_bullish
            and second.is_bullish
            and third.is_bullish
            and first.close < second.close < third.close
            and first.open <= second.open < first.close
            and second.open <= third.open < second.close
            and fourth.is_bearish
            and fourth.open >= third.close
            and fourth.close <= first.open
        ):
            results.append(
                _pattern_result(
                    name="Bullish Three-Line Strike",
                    pattern_id="bullish_three_line_strike",
                    category="multi_candle",
                    direction="BULLISH",
                    start=first,
                    end=fourth,
                    confirmation=fourth,
                    details={},
                )
            )
        if (
            first.is_bearish
            and second.is_bearish
            and third.is_bearish
            and first.close > second.close > third.close
            and first.close < second.open <= first.open
            and second.close < third.open <= second.open
            and fourth.is_bullish
            and fourth.open <= third.close
            and fourth.close >= first.open
        ):
            results.append(
                _pattern_result(
                    name="Bearish Three-Line Strike",
                    pattern_id="bearish_three_line_strike",
                    category="multi_candle",
                    direction="BEARISH",
                    start=first,
                    end=fourth,
                    confirmation=fourth,
                    details={},
                )
            )

    for index in range(2, len(candles)):
        first, second, third = candles[index - 2 : index + 1]
        if (
            first.is_bearish
            and _is_doji(second, config)
            and second.high + config.gap_tolerance < first.low
            and third.is_bullish
            and third.low > second.high + config.gap_tolerance
        ):
            results.append(
                _pattern_result(
                    name="Bullish Abandoned Baby",
                    pattern_id="bullish_abandoned_baby",
                    category="multi_candle",
                    direction="BULLISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )
        if (
            first.is_bullish
            and _is_doji(second, config)
            and second.low > first.high + config.gap_tolerance
            and third.is_bearish
            and third.high + config.gap_tolerance < second.low
        ):
            results.append(
                _pattern_result(
                    name="Bearish Abandoned Baby",
                    pattern_id="bearish_abandoned_baby",
                    category="multi_candle",
                    direction="BEARISH",
                    start=first,
                    end=third,
                    confirmation=third,
                    details={},
                )
            )

    swing_points = _detect_swing_points(
        candles,
        lookback=config.confirmation_lookback,
        right_confirmation=config.confirmation_lookback,
    )
    highs = [item for item in swing_points if item.pattern_id == "swing_high"]
    lows = [item for item in swing_points if item.pattern_id == "swing_low"]
    candle_indexes = {candle.timestamp: index for index, candle in enumerate(candles)}
    for points, is_top, opposing in ((highs, True, lows), (lows, False, highs)):
        opposing_positions = [candle_indexes[item.start_timestamp or ""] for item in opposing]
        for left, right in pairwise(points):
            left_pos = candle_indexes[left.start_timestamp or ""]
            right_pos = candle_indexes[right.start_timestamp or ""]
            if right_pos <= left_pos + 1 or right_pos - left_pos > config.pattern_lookback:
                continue
            left_price = Decimal(str(left.details["pivot"]))
            right_price = Decimal(str(right.details["pivot"]))
            if abs(left_price - right_price) > config.support_zone_tolerance:
                continue
            right_confirmation = candle_indexes[right.confirmation_timestamp or ""]
            start = bisect_right(opposing_positions, left_pos)
            end = bisect_left(opposing_positions, right_pos)
            opposing_swings = [
                point
                for point in opposing[start:end]
                if candle_indexes[point.confirmation_timestamp or ""] <= right_confirmation
            ]
            if not opposing_swings:
                continue
            neckline_point = (
                min(opposing_swings, key=lambda point: Decimal(str(point.details["pivot"])))
                if is_top
                else max(opposing_swings, key=lambda point: Decimal(str(point.details["pivot"])))
            )
            neckline = Decimal(str(neckline_point.details["pivot"]))
            confirmation_end = min(len(candles), right_confirmation + config.pattern_lookback + 1)
            for later in candles[right_confirmation + 1 : confirmation_end]:
                broken = (
                    later.close < neckline - config.breakout_buffer
                    if is_top
                    else later.close > neckline + config.breakout_buffer
                )
                if broken:
                    results.append(
                        _pattern_result(
                            name="Double Top" if is_top else "Double Bottom",
                            pattern_id="double_top" if is_top else "double_bottom",
                            category="multi_candle",
                            direction="BEARISH" if is_top else "BULLISH",
                            start=candles[left_pos],
                            end=candles[right_pos],
                            confirmation=later,
                            details={
                                "first_pivot": _decimal_text(left_price),
                                "second_pivot": _decimal_text(right_price),
                                "neckline": _decimal_text(neckline),
                            },
                        )
                    )
                    break
    return results


def _detect_market_structure(
    candles: Sequence[Candle],
    config: DetectionConfig,
    swings: Sequence[PatternResult] | None = None,
) -> list[PatternResult]:
    if swings is None:
        swings = _detect_swing_points(
            candles,
            lookback=config.confirmation_lookback,
            right_confirmation=config.confirmation_lookback,
        )
    by_timestamp = {candle.timestamp: candle for candle in candles}
    previous_by_kind: dict[str, PatternResult] = {}
    results: list[PatternResult] = []
    for swing in swings:
        kind = "high" if swing.pattern_id == "swing_high" else "low"
        previous = previous_by_kind.get(kind)
        previous_by_kind[kind] = swing
        if previous is None:
            continue
        earlier = Decimal(str(previous.details["pivot"]))
        current = Decimal(str(swing.details["pivot"]))
        if current > earlier + config.trend_tolerance:
            pattern_id = "hh" if kind == "high" else "hl"
        elif current < earlier - config.trend_tolerance:
            pattern_id = "lh" if kind == "high" else "ll"
        else:
            continue
        confirm = by_timestamp[swing.confirmation_timestamp or ""]
        previous_pivot = by_timestamp[previous.start_timestamp or ""]
        current_pivot = by_timestamp[swing.start_timestamp or ""]
        results.append(
            _pattern_result(
                name=pattern_id.upper(),
                pattern_id=pattern_id,
                category="price_action",
                direction=("BULLISH" if pattern_id in {"hh", "hl"} else "BEARISH"),
                start=previous_pivot,
                end=current_pivot,
                confirmation=confirm,
                details={
                    "previous_pivot": _decimal_text(earlier),
                    "current_pivot": _decimal_text(current),
                },
                metadata={
                    "previous_swing_confirmation": previous.confirmation_timestamp,
                    "swing_confirmation": swing.confirmation_timestamp,
                },
            )
        )
    return results


def _detect_trend_structure(
    candles: Sequence[Candle],
    config: DetectionConfig,
    swings: Sequence[PatternResult] | None = None,
) -> list[PatternResult]:
    if swings is None:
        swings = _detect_swing_points(
            candles,
            lookback=config.confirmation_lookback,
            right_confirmation=config.confirmation_lookback,
        )
    ordered_swings = sorted(
        swings, key=lambda item: _timestamp_instant(item.confirmation_timestamp or "")
    )
    by_timestamp = {candle.timestamp: candle for candle in candles}
    confirmed_highs: list[PatternResult] = []
    confirmed_lows: list[PatternResult] = []
    results: list[PatternResult] = []
    for swing in ordered_swings:
        (confirmed_highs if swing.pattern_id == "swing_high" else confirmed_lows).append(swing)
        if len(confirmed_highs) < 2 or len(confirmed_lows) < 2:
            results.append(
                _pattern_result(
                    name="Insufficient Trend Structure",
                    pattern_id="insufficient_trend_structure",
                    category="price_action",
                    direction="NEUTRAL",
                    start=by_timestamp[swing.start_timestamp or ""],
                    end=by_timestamp[swing.start_timestamp or ""],
                    confirmation=by_timestamp[swing.confirmation_timestamp or ""],
                    details={
                        "confirmed_swing_highs": len(confirmed_highs),
                        "confirmed_swing_lows": len(confirmed_lows),
                    },
                )
            )
            continue
        high_change = Decimal(str(confirmed_highs[-1].details["pivot"])) - Decimal(
            str(confirmed_highs[-2].details["pivot"])
        )
        low_change = Decimal(str(confirmed_lows[-1].details["pivot"])) - Decimal(
            str(confirmed_lows[-2].details["pivot"])
        )
        if high_change > config.trend_tolerance and low_change > config.trend_tolerance:
            pattern_id, name, direction = "trend_up", "Uptrend", "BULLISH"
        elif high_change < -config.trend_tolerance and low_change < -config.trend_tolerance:
            pattern_id, name, direction = "trend_down", "Downtrend", "BEARISH"
        elif (
            abs(high_change) <= config.trend_tolerance and abs(low_change) <= config.trend_tolerance
        ):
            pattern_id, name, direction = "trend_range", "Range / Neutral", "NEUTRAL"
        else:
            pattern_id, name, direction = (
                "insufficient_trend_structure",
                "Insufficient Trend Structure",
                "NEUTRAL",
            )
        confirmed_pivot = by_timestamp[swing.start_timestamp or ""]
        confirmed_at = by_timestamp[swing.confirmation_timestamp or ""]
        results.append(
            _pattern_result(
                name=name,
                pattern_id=pattern_id,
                category="price_action",
                direction=direction,
                start=by_timestamp[confirmed_highs[-2].start_timestamp or ""],
                end=confirmed_pivot,
                confirmation=confirmed_at,
                details={
                    "latest_swing_high_change": _decimal_text(high_change),
                    "latest_swing_low_change": _decimal_text(low_change),
                },
                metadata={
                    "high_confirmation": confirmed_highs[-1].confirmation_timestamp,
                    "low_confirmation": confirmed_lows[-1].confirmation_timestamp,
                },
            )
        )
    return results


def _detect_mother_bar(candles: Sequence[Candle], config: DetectionConfig) -> list[PatternResult]:
    results: list[PatternResult] = []
    for mother_index, mother in enumerate(candles[:-1]):
        window_end = min(len(candles), mother_index + config.pattern_lookback + 1)
        for child in candles[mother_index + 1 : window_end]:
            if (
                child.high <= mother.high
                and child.low >= mother.low
                and (child.high < mother.high or child.low > mother.low)
            ):
                results.append(
                    _pattern_result(
                        name="Mother Bar",
                        pattern_id="mother_bar",
                        category="price_action",
                        direction="NEUTRAL",
                        start=mother,
                        end=child,
                        confirmation=child,
                        details={
                            "mother_high": _decimal_text(mother.high),
                            "mother_low": _decimal_text(mother.low),
                            "inside_timestamp": child.timestamp,
                        },
                    )
                )
                break
            if child.high > mother.high or child.low < mother.low:
                break
    return results


def _detect_rejection_and_pullback(
    candles: Sequence[Candle],
    config: DetectionConfig,
    zones: Sequence[dict[str, Any]] | None = None,
) -> list[PatternResult]:
    results: list[PatternResult] = []
    if zones is None:
        _, zones = _confirmed_zones(candles, config)
    for zone in zones:
        formation_index = zone["formation_index"]
        window_end = min(len(candles), formation_index + config.pattern_lookback + 1)
        for index in range(formation_index + 1, window_end):
            candle = candles[index]
            lower_rejection = (
                zone["kind"] == "support"
                and candle.low <= zone["high"]
                and candle.close > zone["high"]
                and candle.lower_shadow >= candle.full_range * config.rejection_min_shadow_ratio
            )
            upper_rejection = (
                zone["kind"] == "resistance"
                and candle.high >= zone["low"]
                and candle.close < zone["low"]
                and candle.upper_shadow >= candle.full_range * config.rejection_min_shadow_ratio
            )
            if lower_rejection or upper_rejection:
                results.append(
                    _pattern_result(
                        name="Support Rejection" if lower_rejection else "Resistance Rejection",
                        pattern_id="support_rejection"
                        if lower_rejection
                        else "resistance_rejection",
                        category="price_action",
                        direction="BULLISH" if lower_rejection else "BEARISH",
                        start=candles[_index_by_timestamp(candles, zone["start"])],
                        end=candle,
                        confirmation=candle,
                        details={
                            "zone_id": zone["zone_id"],
                            "zone_low": _decimal_text(zone["low"]),
                            "zone_high": _decimal_text(zone["high"]),
                        },
                    )
                )
    for index in range(2, len(candles)):
        impulse, retracement, confirmation = candles[index - 2 : index + 1]
        if (
            impulse.is_bullish
            and retracement.is_bearish
            and retracement.low > impulse.low
            and confirmation.is_bullish
            and confirmation.close > impulse.high
        ):
            results.append(
                _pattern_result(
                    name="Bullish Pullback Continuation",
                    pattern_id="pullback",
                    category="price_action",
                    direction="BULLISH",
                    start=impulse,
                    end=confirmation,
                    confirmation=confirmation,
                    details={"retracement_low": _decimal_text(retracement.low)},
                    metadata={"method": "three-candle OHLC approximation"},
                )
            )
        if (
            impulse.is_bearish
            and retracement.is_bullish
            and retracement.high < impulse.high
            and confirmation.is_bearish
            and confirmation.close < impulse.low
        ):
            results.append(
                _pattern_result(
                    name="Bearish Pullback Continuation",
                    pattern_id="pullback",
                    category="price_action",
                    direction="BEARISH",
                    start=impulse,
                    end=confirmation,
                    confirmation=confirmation,
                    details={"retracement_high": _decimal_text(retracement.high)},
                    metadata={"method": "three-candle OHLC approximation"},
                )
            )
    return results


def _detect_supply_demand(candles: Sequence[Candle]) -> list[PatternResult]:
    results: list[PatternResult] = []
    for index in range(1, len(candles)):
        base, impulse = candles[index - 1 : index + 1]
        if base.full_range <= 0:
            continue
        base_is_small = base.body <= base.full_range * Decimal("0.3")
        volume_expansion = impulse.volume > base.volume
        if (
            base_is_small
            and volume_expansion
            and impulse.is_bullish
            and impulse.body >= base.full_range * Decimal("1.5")
        ):
            results.append(
                _pattern_result(
                    name="Demand Zone (OHLCV approximation)",
                    pattern_id="demand_zone",
                    category="price_action",
                    direction="BULLISH",
                    start=base,
                    end=impulse,
                    confirmation=impulse,
                    details={
                        "zone_low": _decimal_text(base.low),
                        "zone_high": _decimal_text(base.high),
                        "base_volume": base.volume,
                        "impulse_volume": impulse.volume,
                    },
                    metadata={
                        "method": "small-base bullish impulse with increased volume",
                        "limitation": "OHLCV approximation, not order-book liquidity",
                    },
                )
            )
        if (
            base_is_small
            and volume_expansion
            and impulse.is_bearish
            and impulse.body >= base.full_range * Decimal("1.5")
        ):
            results.append(
                _pattern_result(
                    name="Supply Zone (OHLCV approximation)",
                    pattern_id="supply_zone",
                    category="price_action",
                    direction="BEARISH",
                    start=base,
                    end=impulse,
                    confirmation=impulse,
                    details={
                        "zone_low": _decimal_text(base.low),
                        "zone_high": _decimal_text(base.high),
                        "base_volume": base.volume,
                        "impulse_volume": impulse.volume,
                    },
                    metadata={
                        "method": "small-base bearish impulse with increased volume",
                        "limitation": "OHLCV approximation, not order-book liquidity",
                    },
                )
            )
    return results


def _detect_fakey(candles: Sequence[Candle], config: DetectionConfig) -> list[PatternResult]:
    results: list[PatternResult] = []
    for index in range(3, len(candles)):
        mother, inside, breakout, reversal = candles[index - 3 : index + 1]
        if not (
            inside.high <= mother.high
            and inside.low >= mother.low
            and (inside.high < mother.high or inside.low > mother.low)
        ):
            continue
        reversed_up = (
            breakout.close > mother.high + config.breakout_buffer
            and mother.low <= reversal.close <= mother.high
        )
        reversed_down = (
            breakout.close < mother.low - config.breakout_buffer
            and mother.low <= reversal.close <= mother.high
        )
        if reversed_up:
            results.append(
                _pattern_result(
                    name="Bearish Fakey",
                    pattern_id="bearish_fakey",
                    category="price_action",
                    direction="BEARISH",
                    start=mother,
                    end=reversal,
                    confirmation=reversal,
                    details={
                        "mother_high": _decimal_text(mother.high),
                        "mother_low": _decimal_text(mother.low),
                        "false_break_high": _decimal_text(breakout.high),
                        "confirmation_close": _decimal_text(reversal.close),
                    },
                )
            )
        if reversed_down:
            results.append(
                _pattern_result(
                    name="Bullish Fakey",
                    pattern_id="bullish_fakey",
                    category="price_action",
                    direction="BULLISH",
                    start=mother,
                    end=reversal,
                    confirmation=reversal,
                    details={
                        "mother_high": _decimal_text(mother.high),
                        "mother_low": _decimal_text(mother.low),
                        "false_break_low": _decimal_text(breakout.low),
                        "confirmation_close": _decimal_text(reversal.close),
                    },
                )
            )
    return results


def _detect_inside_bar_breakout(
    candles: Sequence[Candle], config: DetectionConfig
) -> list[PatternResult]:
    results: list[PatternResult] = []
    for mother, inside, breakout in zip(candles, candles[1:], candles[2:]):
        if not (
            inside.high <= mother.high
            and inside.low >= mother.low
            and (inside.high < mother.high or inside.low > mother.low)
        ):
            continue
        if breakout.close > mother.high + config.breakout_buffer:
            results.append(
                _pattern_result(
                    name="Bullish Inside Bar Breakout",
                    pattern_id="inside_bar_breakout",
                    category="price_action",
                    direction="BULLISH",
                    start=mother,
                    end=breakout,
                    confirmation=breakout,
                    details={
                        "mother_high": _decimal_text(mother.high),
                        "mother_low": _decimal_text(mother.low),
                        "inside_timestamp": inside.timestamp,
                    },
                )
            )
        elif breakout.close < mother.low - config.breakout_buffer:
            results.append(
                _pattern_result(
                    name="Bearish Inside Bar Breakout",
                    pattern_id="inside_bar_breakout",
                    category="price_action",
                    direction="BEARISH",
                    start=mother,
                    end=breakout,
                    confirmation=breakout,
                    details={
                        "mother_high": _decimal_text(mother.high),
                        "mother_low": _decimal_text(mother.low),
                        "inside_timestamp": inside.timestamp,
                    },
                )
            )
    return results


def _detect_opening_range(
    candles: Sequence[Candle],
    session_context: SessionContext | None,
    config: DetectionConfig,
) -> list[PatternResult]:
    if session_context is None:
        return []
    if not isinstance(session_context, SessionContext):
        raise CandlestickPatternError("session_context must be a SessionContext instance.")
    if not candles:
        return []
    zone = ZoneInfo(session_context.timezone)
    session_open = _parse_session_time(session_context.session_open, "session_open")
    session_close = _parse_session_time(session_context.session_close, "session_close")
    opening_duration = timedelta(minutes=session_context.opening_range_minutes)
    interval = timedelta(minutes=session_context.candle_interval_minutes)
    local_dates = sorted({_timestamp_instant(c.timestamp).astimezone(zone).date() for c in candles})
    by_instant = {_timestamp_instant(c.timestamp): c for c in candles}
    results: list[PatternResult] = []
    for session_date in local_dates:
        local_open = datetime.combine(session_date, session_open, tzinfo=zone).replace(fold=0)
        local_open_second_fold = local_open.replace(fold=1)
        local_close = datetime.combine(session_date, session_close, tzinfo=zone).replace(fold=0)
        open_utc = local_open.astimezone(UTC)
        if local_open.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local_open.replace(
            tzinfo=None
        ):
            raise CandlestickPatternError("Session open falls in a nonexistent local time.")
        if local_open.utcoffset() != local_open_second_fold.utcoffset():
            raise CandlestickPatternError("Ambiguous local session opens are unsupported.")
        if local_close.utcoffset() != local_close.replace(fold=1).utcoffset():
            raise CandlestickPatternError("Ambiguous local session closes are unsupported.")
        if local_close.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local_close.replace(
            tzinfo=None
        ):
            raise CandlestickPatternError("Session close falls in a nonexistent local time.")
        if open_utc + opening_duration > local_close.astimezone(UTC):
            raise CandlestickPatternError(
                "Opening range extends beyond the declared session close."
            )
        expected_count = (
            session_context.opening_range_minutes // session_context.candle_interval_minutes
        )
        expected = [open_utc + interval * offset for offset in range(expected_count)]
        selected = [by_instant.get(instant) for instant in expected]
        if any(candle is None for candle in selected):
            continue
        complete = [candle for candle in selected if candle is not None]
        low = min(candle.low for candle in complete)
        high = max(candle.high for candle in complete)
        results.append(
            _pattern_result(
                name="Opening Range",
                pattern_id="opening_range",
                category="price_action",
                direction="NEUTRAL",
                start=complete[0],
                end=complete[-1],
                confirmation=complete[-1],
                details={
                    "session_date": session_date.isoformat(),
                    "session_open": session_context.session_open,
                    "session_close": session_context.session_close,
                    "timezone": session_context.timezone,
                    "market_name": session_context.market_name,
                    "opening_range_minutes": session_context.opening_range_minutes,
                    "candle_interval_minutes": session_context.candle_interval_minutes,
                    "candle_count": len(complete),
                    "range_low": _decimal_text(low),
                    "range_high": _decimal_text(high),
                },
            )
        )
        range_end = open_utc + opening_duration
        close_utc = local_close.astimezone(UTC)
        found_breakout: set[str] = set()
        for candle in candles:
            instant = _timestamp_instant(candle.timestamp)
            local_instant = instant.astimezone(zone)
            if (
                local_instant.date() != session_date
                or instant < range_end
                or instant + interval > close_utc
            ):
                continue
            if candle.close > high + config.breakout_buffer and "up" not in found_breakout:
                results.append(
                    _pattern_result(
                        name="Opening Range Breakout",
                        pattern_id="opening_range_breakout",
                        category="price_action",
                        direction="BULLISH",
                        start=complete[0],
                        end=candle,
                        confirmation=candle,
                        details={
                            "range_high": _decimal_text(high),
                            "range_low": _decimal_text(low),
                            "close": _decimal_text(candle.close),
                            "session_date": session_date.isoformat(),
                        },
                    )
                )
                found_breakout.add("up")
            elif candle.close < low - config.breakout_buffer and "down" not in found_breakout:
                results.append(
                    _pattern_result(
                        name="Opening Range Breakdown",
                        pattern_id="opening_range_breakdown",
                        category="price_action",
                        direction="BEARISH",
                        start=complete[0],
                        end=candle,
                        confirmation=candle,
                        details={
                            "range_high": _decimal_text(high),
                            "range_low": _decimal_text(low),
                            "close": _decimal_text(candle.close),
                            "session_date": session_date.isoformat(),
                        },
                    )
                )
                found_breakout.add("down")
    return results


def _detect_swing_points(
    candles: Sequence[Candle], *, lookback: int = 1, right_confirmation: int = 2
) -> list[PatternResult]:
    if (
        isinstance(lookback, bool)
        or not isinstance(lookback, int)
        or lookback < 1
        or isinstance(right_confirmation, bool)
        or not isinstance(right_confirmation, int)
        or right_confirmation < 1
    ):
        raise CandlestickPatternError("Swing confirmation windows must be positive integers.")
    results: list[PatternResult] = []
    for index in range(lookback, len(candles) - right_confirmation):
        pivot = candles[index]
        left_high = max(candle.high for candle in candles[max(0, index - lookback) : index])
        right_high = max(
            candle.high for candle in candles[index + 1 : index + right_confirmation + 1]
        )
        left_low = min(candle.low for candle in candles[max(0, index - lookback) : index])
        right_low = min(
            candle.low for candle in candles[index + 1 : index + right_confirmation + 1]
        )

        if pivot.high >= left_high and pivot.high > right_high:
            confirmation = candles[index + right_confirmation]
            results.append(
                _pattern_result(
                    name="Swing High",
                    pattern_id="swing_high",
                    category="price_action",
                    direction="BEARISH",
                    start=pivot,
                    end=pivot,
                    confirmation=confirmation,
                    details={
                        "pivot": _decimal_text(pivot.high),
                        "right_confirmation": right_confirmation,
                        "left_confirmation": lookback,
                    },
                    metadata={"pivot_index": index},
                )
            )
        if pivot.low <= left_low and pivot.low < right_low:
            confirmation = candles[index + right_confirmation]
            results.append(
                _pattern_result(
                    name="Swing Low",
                    pattern_id="swing_low",
                    category="price_action",
                    direction="BULLISH",
                    start=pivot,
                    end=pivot,
                    confirmation=confirmation,
                    details={
                        "pivot": _decimal_text(pivot.low),
                        "right_confirmation": right_confirmation,
                        "left_confirmation": lookback,
                    },
                    metadata={"pivot_index": index},
                )
            )
    return results


def _confirmed_zones(
    candles: Sequence[Candle],
    config: DetectionConfig,
    swings: Sequence[PatternResult] | None = None,
) -> tuple[list[PatternResult], list[dict[str, Any]]]:
    if swings is None:
        swings = _detect_swing_points(
            candles,
            lookback=config.confirmation_lookback,
            right_confirmation=config.confirmation_lookback,
        )
    index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    events: list[PatternResult] = []
    zones: list[dict[str, Any]] = []
    for kind, swing_id in (("support", "swing_low"), ("resistance", "swing_high")):
        clusters: list[dict[str, Any]] = []
        clusters_by_bucket: dict[int | Decimal, list[dict[str, Any]]] = {}
        points = [item for item in swings if item.pattern_id == swing_id]
        for point in points:
            price = Decimal(str(point.details["pivot"]))
            bucket: int | Decimal = (
                price // config.support_zone_tolerance
                if config.support_zone_tolerance > 0
                else price
            )
            bucket = int(bucket) if isinstance(bucket, Decimal) else bucket
            neighbor_buckets: Sequence[int | Decimal]
            if config.support_zone_tolerance > 0:
                neighbor_buckets = (bucket - 1, bucket, bucket + 1)
            else:
                neighbor_buckets = (bucket,)
            cluster = next(
                (
                    candidate
                    for candidate in (
                        item for key in neighbor_buckets for item in clusters_by_bucket.get(key, [])
                    )
                    if candidate["low"]
                    - (Decimal(0) if candidate["formed"] else config.support_zone_tolerance)
                    <= price
                    <= candidate["high"]
                    + (Decimal(0) if candidate["formed"] else config.support_zone_tolerance)
                ),
                None,
            )
            point_index = index_by_timestamp[point.start_timestamp or ""]
            confirmation_index = index_by_timestamp[point.confirmation_timestamp or ""]
            if cluster is None:
                cluster = {
                    "kind": kind,
                    "prices": [price],
                    "points": [point],
                    "low": price,
                    "high": price,
                    "formed": False,
                    "bucket": bucket,
                }
                clusters.append(cluster)
                clusters_by_bucket.setdefault(bucket, []).append(cluster)
                continue
            cluster["prices"].append(price)
            cluster["points"].append(point)
            if not cluster["formed"]:
                if len(cluster["points"]) < 2:
                    continue
                cluster["low"] = min(cluster["prices"]) - config.support_zone_tolerance
                cluster["high"] = max(cluster["prices"]) + config.support_zone_tolerance
                cluster["formed"] = True
                cluster["formation_index"] = confirmation_index
                cluster["start"] = cluster["points"][0].start_timestamp
                cluster["zone_id"] = f"{kind}:{cluster['start']}"
                zone_low, zone_high = cluster["low"], cluster["high"]
                events.append(
                    _pattern_result(
                        name="Support Zone" if kind == "support" else "Resistance Zone",
                        pattern_id=f"{kind}_zone",
                        category="price_action",
                        direction="BULLISH" if kind == "support" else "BEARISH",
                        start=candles[index_by_timestamp[cluster["start"]]],
                        end=candles[point_index],
                        confirmation=candles[confirmation_index],
                        details={
                            "zone_id": cluster["zone_id"],
                            "zone_low": _decimal_text(zone_low),
                            "zone_high": _decimal_text(zone_high),
                            "touch_count": 2,
                            "touch_timestamps": [
                                item.start_timestamp for item in cluster["points"]
                            ],
                        },
                        metadata={"formation_confirmation": point.confirmation_timestamp},
                    )
                )
                zones.append(cluster)
            elif cluster["formed"]:
                events.append(
                    _pattern_result(
                        name="Support Zone Touch" if kind == "support" else "Resistance Zone Touch",
                        pattern_id=f"{kind}_zone_touch",
                        category="price_action",
                        direction="BULLISH" if kind == "support" else "BEARISH",
                        start=candles[index_by_timestamp[cluster["start"]]],
                        end=candles[point_index],
                        confirmation=candles[confirmation_index],
                        details={
                            "zone_id": cluster["zone_id"],
                            "zone_low": _decimal_text(cluster["low"]),
                            "zone_high": _decimal_text(cluster["high"]),
                            "touch_count": len(cluster["points"]),
                            "touch_timestamp": point.start_timestamp,
                        },
                        metadata={
                            "formation_confirmation": cluster["points"][1].confirmation_timestamp
                        },
                    )
                )
    return events, sorted(zones, key=lambda zone: zone["formation_index"])


def _detect_support_resistance(
    candles: Sequence[Candle],
    config: DetectionConfig,
    zone_data: tuple[list[PatternResult], list[dict[str, Any]]] | None = None,
) -> list[PatternResult]:
    events, _ = zone_data if zone_data is not None else _confirmed_zones(candles, config)
    return events


def _detect_breakout_retest(
    candles: Sequence[Candle],
    config: DetectionConfig,
    zone_data: tuple[list[PatternResult], list[dict[str, Any]]] | None = None,
) -> list[PatternResult]:
    _, zones = zone_data if zone_data is not None else _confirmed_zones(candles, config)
    results: list[PatternResult] = []
    for zone in zones:
        kind = zone["kind"]
        zone_low: Decimal = zone["low"]
        zone_high: Decimal = zone["high"]
        breakout_index: int | None = None
        window_end = min(len(candles), zone["formation_index"] + config.pattern_lookback + 1)
        for index in range(zone["formation_index"] + 1, window_end):
            candle = candles[index]
            if breakout_index is None:
                broken = (
                    candle.close < zone_low - config.breakout_buffer
                    if kind == "support"
                    else candle.close > zone_high + config.breakout_buffer
                )
                if not broken:
                    continue
                breakout_index = index
                bullish = kind == "resistance"
                results.append(
                    _pattern_result(
                        name="Bullish Resistance Breakout"
                        if bullish
                        else "Bearish Support Breakdown",
                        pattern_id="bullish_resistance_breakout"
                        if bullish
                        else "bearish_support_breakdown",
                        category="price_action",
                        direction="BULLISH" if bullish else "BEARISH",
                        start=candles[_index_by_timestamp(candles, zone["start"])],
                        end=candle,
                        confirmation=candle,
                        details={
                            "zone_id": zone["zone_id"],
                            "zone_low": _decimal_text(zone_low),
                            "zone_high": _decimal_text(zone_high),
                            "close": _decimal_text(candle.close),
                        },
                    )
                )
                continue
            touched = candle.low <= zone_high and candle.high >= zone_low
            failed_without_retest = (
                candle.close < zone_high if kind == "resistance" else candle.close > zone_low
            )
            if touched:
                successful = (
                    candle.close > zone_high if kind == "resistance" else candle.close < zone_low
                )
                name = (
                    "Bullish Resistance Retest"
                    if kind == "resistance"
                    else "Bearish Support Retest"
                )
                pattern_id = (
                    "bullish_resistance_retest"
                    if kind == "resistance"
                    else "bearish_support_retest"
                )
                if not successful:
                    name = (
                        "Failed Bullish Breakout"
                        if kind == "resistance"
                        else "Failed Bearish Breakdown"
                    )
                    pattern_id = (
                        "failed_bullish_breakout"
                        if kind == "resistance"
                        else "failed_bearish_breakdown"
                    )
                results.append(
                    _pattern_result(
                        name=name,
                        pattern_id=pattern_id,
                        category="price_action",
                        direction="BULLISH" if kind == "resistance" else "BEARISH",
                        start=candles[breakout_index],
                        end=candle,
                        confirmation=candle,
                        details={
                            "zone_id": zone["zone_id"],
                            "zone_low": _decimal_text(zone_low),
                            "zone_high": _decimal_text(zone_high),
                        },
                    )
                )
                break
            if failed_without_retest:
                results.append(
                    _pattern_result(
                        name="Failed Bullish Breakout"
                        if kind == "resistance"
                        else "Failed Bearish Breakdown",
                        pattern_id="failed_bullish_breakout"
                        if kind == "resistance"
                        else "failed_bearish_breakdown",
                        category="price_action",
                        direction="BEARISH" if kind == "resistance" else "BULLISH",
                        start=candles[breakout_index],
                        end=candle,
                        confirmation=candle,
                        details={"zone_id": zone["zone_id"]},
                    )
                )
                break
    return results


def _detect_range_context(
    candles: Sequence[Candle], config: DetectionConfig
) -> list[PatternResult]:
    results: list[PatternResult] = []
    window = config.range_lookback
    for end_index in range(window - 1, len(candles)):
        sample = candles[end_index - window + 1 : end_index + 1]
        low = min(candle.low for candle in sample)
        high = max(candle.high for candle in sample)
        width = high - low
        if width <= config.min_range:
            continue
        net_change_ratio = abs(sample[-1].close - sample[0].close) / width
        if (
            width / low <= config.range_max_width_ratio
            and net_change_ratio <= config.range_max_net_change_ratio
        ):
            results.append(
                _pattern_result(
                    name="Consolidation Range",
                    pattern_id="range",
                    category="price_action",
                    direction="NEUTRAL",
                    start=sample[0],
                    end=sample[-1],
                    confirmation=sample[-1],
                    details={
                        "range_low": _decimal_text(low),
                        "range_high": _decimal_text(high),
                        "window_candles": window,
                        "max_width_ratio": _decimal_text(config.range_max_width_ratio),
                        "net_change_ratio": _decimal_text(net_change_ratio),
                    },
                )
            )
    return results


def _index_by_timestamp(candles: Sequence[Candle], timestamp: str) -> int:
    for index, candle in enumerate(candles):
        if candle.timestamp == timestamp:
            return index
    raise CandlestickPatternError(f"Detection references unknown timestamp {timestamp!r}.")


def _is_doji(candle: Candle, config: DetectionConfig) -> bool:
    return (
        candle.full_range > 0
        and abs(candle.close - candle.open) <= candle.full_range * config.doji_body_ratio
    )


def _active_config(config: DetectionConfig | None) -> DetectionConfig:
    if config is None:
        return DetectionConfig()
    if not isinstance(config, DetectionConfig):
        raise CandlestickPatternError("config must be a DetectionConfig instance.")
    return config


def _with_reproducibility_metadata(
    results: Sequence[PatternResult],
    config: DetectionConfig,
    session_context: SessionContext | None = None,
) -> list[PatternResult]:
    configuration = {item.name: _json_safe(getattr(config, item.name)) for item in fields(config)}
    session_configuration = (
        {
            "session_open": session_context.session_open,
            "session_close": session_context.session_close,
            "timezone": session_context.timezone,
            "market_name": session_context.market_name,
            "opening_range_minutes": session_context.opening_range_minutes,
            "candle_interval_minutes": session_context.candle_interval_minutes,
        }
        if session_context is not None
        else None
    )
    return [
        replace(
            result,
            metadata={
                **result.metadata,
                "definition_version": _PATTERN_DEFINITION_VERSION,
                "configuration": configuration,
                **(
                    {"session_context": session_configuration}
                    if session_configuration is not None
                    else {}
                ),
            },
        )
        for result in results
    ]


def detect_single_candle_patterns(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    *,
    config: DetectionConfig | None = None,
) -> list[PatternResult]:
    parsed = prepare_candles(candles)
    active_config = _active_config(config)
    with _detection_context(parsed, active_config):
        results = _sort_results(_single_candle_patterns(parsed, active_config))
    return _with_reproducibility_metadata(results, active_config)


def detect_two_candle_patterns(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    *,
    config: DetectionConfig | None = None,
) -> list[PatternResult]:
    parsed = prepare_candles(candles)
    active_config = _active_config(config)
    with _detection_context(parsed, active_config):
        results = _sort_results(_two_candle_patterns(parsed, active_config))
    return _with_reproducibility_metadata(results, active_config)


def detect_multi_candle_patterns(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    *,
    config: DetectionConfig | None = None,
) -> list[PatternResult]:
    parsed = prepare_candles(candles)
    active_config = _active_config(config)
    with _detection_context(parsed, active_config):
        results = _sort_results(_multi_candle_patterns(parsed, active_config))
    return _with_reproducibility_metadata(results, active_config)


def detect_price_action(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    *,
    config: DetectionConfig | None = None,
    session_context: SessionContext | None = None,
) -> list[PatternResult]:
    parsed = prepare_candles(candles)
    active_config = _active_config(config)
    with _detection_context(parsed, active_config):
        results = _sort_results(
            _detect_price_action_prepared(parsed, active_config, session_context)
        )
    return _with_reproducibility_metadata(results, active_config, session_context)


def detect_pattern(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    pattern_id: str,
    *,
    config: DetectionConfig | None = None,
    session_context: SessionContext | None = None,
) -> list[PatternResult]:
    parsed = prepare_candles(candles)
    if not isinstance(pattern_id, str) or not pattern_id:
        raise CandlestickPatternError("pattern_id must be a non-empty string.")
    active_config = _active_config(config)
    with _detection_context(parsed, active_config):
        candidates = _detect_all_prepared(parsed, active_config, session_context)
    results = _sort_results([item for item in candidates if item.pattern_id == pattern_id])
    return _with_reproducibility_metadata(results, active_config, session_context)


def detect_patterns(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    *,
    include: Iterable[str] | None = None,
    config: DetectionConfig | None = None,
    session_context: SessionContext | None = None,
) -> list[PatternResult]:
    parsed = prepare_candles(candles)
    active_config = _active_config(config)
    if include is None:
        names = None
    elif isinstance(include, str):
        raise CandlestickPatternError("include must be an iterable of detector categories.")
    else:
        try:
            included = list(include)
        except TypeError as exc:
            raise CandlestickPatternError(
                "include must be an iterable of detector categories."
            ) from exc
        if not all(isinstance(name, str) for name in included):
            raise CandlestickPatternError("include categories must be strings.")
        names = set(included)
        unknown = names - {"single_candle", "two_candle", "multi_candle", "price_action"}
        if unknown:
            raise CandlestickPatternError(f"Unknown detector categories: {sorted(unknown)}.")
    with _detection_context(parsed, active_config):
        results = _sort_results(
            _detect_all_prepared(parsed, active_config, session_context, include=names)
        )
    return _with_reproducibility_metadata(results, active_config, session_context)


def _detect_all_prepared(
    parsed: Sequence[Candle],
    config: DetectionConfig,
    session_context: SessionContext | None,
    *,
    include: set[str] | None = None,
) -> list[PatternResult]:
    results: list[PatternResult] = []
    if include is None or "single_candle" in include:
        results.extend(_single_candle_patterns(parsed, config))
    if include is None or "two_candle" in include:
        results.extend(_two_candle_patterns(parsed, config))
    if include is None or "multi_candle" in include:
        results.extend(_multi_candle_patterns(parsed, config))
    if include is None or "price_action" in include:
        results.extend(_detect_price_action_prepared(parsed, config, session_context))
    return results


def _detect_price_action_prepared(
    candles: Sequence[Candle], config: DetectionConfig, session_context: SessionContext | None
) -> list[PatternResult]:
    results: list[PatternResult] = []
    swings = _detect_swing_points(
        candles,
        lookback=config.confirmation_lookback,
        right_confirmation=config.confirmation_lookback,
    )
    zone_data = _confirmed_zones(candles, config, swings)
    results.extend(_detect_market_structure(candles, config, swings))
    results.extend(_detect_trend_structure(candles, config, swings))
    results.extend(_detect_mother_bar(candles, config))
    results.extend(_detect_rejection_and_pullback(candles, config, zone_data[1]))
    results.extend(_detect_supply_demand(candles))
    results.extend(_detect_inside_bar_breakout(candles, config))
    results.extend(_detect_fakey(candles, config))
    results.extend(swings)
    results.extend(_detect_support_resistance(candles, config, zone_data))
    results.extend(_detect_breakout_retest(candles, config, zone_data))
    results.extend(_detect_range_context(candles, config))
    results.extend(_detect_opening_range(candles, session_context, config))
    return results


def _sort_results(results: Sequence[PatternResult]) -> list[PatternResult]:
    deduped: list[PatternResult] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for item in results:
        key = (
            item.pattern_id,
            item.start_timestamp,
            item.end_timestamp,
            item.confirmation_timestamp,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return sorted(
        deduped,
        key=lambda result: (
            _timestamp_instant(result.start_timestamp)
            if result.start_timestamp
            else datetime.min.replace(tzinfo=UTC),
            _timestamp_instant(result.confirmation_timestamp)
            if result.confirmation_timestamp
            else datetime.min.replace(tzinfo=UTC),
            result.name,
        ),
    )


def detect_all_patterns(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    *,
    config: DetectionConfig | None = None,
    session_context: SessionContext | None = None,
) -> list[PatternResult]:
    return detect_patterns(candles, config=config, session_context=session_context)
