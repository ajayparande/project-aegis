"""Deterministic, causal Decimal-based technical indicators for completed candles."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, time
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    InvalidOperation,
    localcontext,
)
from types import MappingProxyType
from typing import Any, Literal
from zoneinfo import ZoneInfo

from aegis.candlesticks import Candle, SessionContext, prepare_candles

__all__ = [
    "IndicatorError",
    "IndicatorResult",
    "PriceField",
    "absolute_returns",
    "average_true_range",
    "bollinger_bands",
    "exponential_moving_average",
    "macd",
    "on_balance_volume",
    "percentage_change",
    "percentage_returns",
    "rate_of_change",
    "relative_strength_index",
    "simple_moving_average",
    "stochastic_oscillator",
    "typical_price",
    "volume_moving_average",
    "volume_weighted_average_price",
    "weighted_moving_average",
]

_SCHEMA_VERSION = "1.0.0"
_DECIMAL_PRECISION = 50
_ROUNDING = ROUND_HALF_EVEN
PriceField = Literal["open", "high", "low", "close"]
CandleInput = Candle | dict[str, Any] | tuple[Any, ...]


class IndicatorError(ValueError):
    """Raised when indicator configuration or computation is invalid."""


@dataclass(frozen=True, slots=True)
class IndicatorResult:
    """Timestamp-aligned, immutable, JSON-safe indicator output."""

    indicator: str
    timestamps: tuple[str, ...]
    columns: Mapping[str, tuple[Decimal | None, ...]]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.indicator:
            raise IndicatorError("indicator must be non-empty.")
        if any(not isinstance(timestamp, str) for timestamp in self.timestamps):
            raise IndicatorError("timestamps must be strings.")
        frozen_columns: dict[str, tuple[Decimal | None, ...]] = {}
        for name, values in self.columns.items():
            if not name or len(values) != len(self.timestamps):
                raise IndicatorError("Each result column must match the timestamp count.")
            if any(
                value is not None and (not isinstance(value, Decimal) or not value.is_finite())
                for value in values
            ):
                raise IndicatorError("Indicator values must be Decimal or None.")
            frozen_columns[name] = tuple(values)
        if not frozen_columns:
            raise IndicatorError("At least one result column is required.")
        if not isinstance(self.metadata, Mapping):
            raise IndicatorError("metadata must be a mapping.")
        object.__setattr__(self, "columns", MappingProxyType(frozen_columns))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-safe representation with canonical decimals."""
        return {
            "schema_version": _SCHEMA_VERSION,
            "indicator": self.indicator,
            "timestamps": list(self.timestamps),
            "columns": {
                name: [_decimal_text(value) if value is not None else None for value in values]
                for name, values in self.columns.items()
            },
            "metadata": _json_safe(self.metadata),
        }


def simple_moving_average(
    candles: Sequence[CandleInput],
    period: int = 20,
    *,
    price_field: PriceField = "close",
    normalize: bool = False,
) -> IndicatorResult:
    """Trailing arithmetic mean; the first value follows ``period`` observations."""
    return _moving_average_result("sma", candles, period, price_field, normalize, "sma")


def exponential_moving_average(
    candles: Sequence[CandleInput],
    period: int = 20,
    *,
    price_field: PriceField = "close",
    normalize: bool = False,
) -> IndicatorResult:
    """EMA seeded with the first ``period``-observation SMA, alpha=2/(period+1)."""
    prepared = _prepare(candles, normalize)
    values = _price_values(prepared, price_field)
    _period(period)
    with _decimal_context(values):
        output = _ema(values, period)
    return _result("ema", prepared, {"ema": output}, {"period": period, "price_field": price_field})


def weighted_moving_average(
    candles: Sequence[CandleInput],
    period: int = 20,
    *,
    price_field: PriceField = "close",
    normalize: bool = False,
) -> IndicatorResult:
    """Trailing linearly weighted mean, weighting newest observation by ``period``."""
    prepared = _prepare(candles, normalize)
    values = _price_values(prepared, price_field)
    _period(period)
    with _decimal_context(values):
        output: list[Decimal | None] = [None] * len(values)
        if len(values) >= period:
            rolling_sum = sum(values[:period], Decimal(0))
            weighted_sum = sum(
                (value * Decimal(weight) for weight, value in enumerate(values[:period], 1)),
                Decimal(0),
            )
            denominator = Decimal(period * (period + 1) // 2)
            output[period - 1] = weighted_sum / denominator
            for index in range(period, len(values)):
                weighted_sum = weighted_sum - rolling_sum + Decimal(period) * values[index]
                rolling_sum += values[index] - values[index - period]
                output[index] = weighted_sum / denominator
    return _result(
        "wma", prepared, {"wma": tuple(output)}, {"period": period, "price_field": price_field}
    )


def relative_strength_index(
    candles: Sequence[CandleInput],
    period: int = 14,
    *,
    price_field: PriceField = "close",
    normalize: bool = False,
) -> IndicatorResult:
    """Wilder RSI seeded from the first ``period`` close-to-close changes.

    Flat sequences produce 50; gain-only sequences produce 100 and loss-only
    sequences produce 0. The first result is at index ``period``.
    """
    prepared = _prepare(candles, normalize)
    prices = _price_values(prepared, price_field)
    _period(period)
    with _decimal_context(prices):
        output: list[Decimal | None] = [None] * len(prices)
        if len(prices) > period:
            changes = [prices[index] - prices[index - 1] for index in range(1, len(prices))]
            gains = [max(change, Decimal(0)) for change in changes]
            losses = [max(-change, Decimal(0)) for change in changes]
            average_gain = sum(gains[:period], Decimal(0)) / Decimal(period)
            average_loss = sum(losses[:period], Decimal(0)) / Decimal(period)
            output[period] = _rsi_value(average_gain, average_loss)
            for index in range(period + 1, len(prices)):
                change_index = index - 1
                average_gain = (average_gain * Decimal(period - 1) + gains[change_index]) / Decimal(
                    period
                )
                average_loss = (
                    average_loss * Decimal(period - 1) + losses[change_index]
                ) / Decimal(period)
                output[index] = _rsi_value(average_gain, average_loss)
    return _result(
        "rsi", prepared, {"rsi": tuple(output)}, {"period": period, "price_field": price_field}
    )


def macd(
    candles: Sequence[CandleInput],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    *,
    price_field: PriceField = "close",
    normalize: bool = False,
) -> IndicatorResult:
    """MACD from SMA-seeded EMAs; signal EMA seeds from the first valid MACD values."""
    prepared = _prepare(candles, normalize)
    prices = _price_values(prepared, price_field)
    _period(fast_period)
    _period(slow_period)
    _period(signal_period)
    if fast_period >= slow_period:
        raise IndicatorError("fast_period must be smaller than slow_period.")
    with _decimal_context(prices):
        fast = _ema(prices, fast_period)
        slow = _ema(prices, slow_period)
        line: list[Decimal | None] = [
            fast_value - slow_value if fast_value is not None and slow_value is not None else None
            for fast_value, slow_value in zip(fast, slow, strict=True)
        ]
        first_macd = next((index for index, value in enumerate(line) if value is not None), None)
        signal: list[Decimal | None] = [None] * len(line)
        histogram: list[Decimal | None] = [None] * len(line)
        if first_macd is not None:
            valid_macd = [value for value in line[first_macd:] if value is not None]
            signal_valid = _ema(valid_macd, signal_period)
            for offset, signal_value in enumerate(signal_valid):
                index = first_macd + offset
                signal[index] = signal_value
                macd_value = line[index]
                if signal_value is not None and macd_value is not None:
                    histogram[index] = macd_value - signal_value
    return _result(
        "macd",
        prepared,
        {"macd": tuple(line), "signal": tuple(signal), "histogram": tuple(histogram)},
        {
            "fast_period": fast_period,
            "slow_period": slow_period,
            "signal_period": signal_period,
            "price_field": price_field,
        },
    )


def stochastic_oscillator(
    candles: Sequence[CandleInput],
    lookback: int = 14,
    k_smoothing: int = 3,
    d_smoothing: int = 3,
    *,
    normalize: bool = False,
) -> IndicatorResult:
    """Full stochastic %K/%D with SMA smoothing; zero range maps raw %K to 50."""
    prepared = _prepare(candles, normalize)
    _period(lookback)
    _period(k_smoothing)
    _period(d_smoothing)
    high_low_close = [
        value for candle in prepared for value in (candle.high, candle.low, candle.close)
    ]
    with _decimal_context(high_low_close):
        raw_k: list[Decimal | None] = [None] * len(prepared)
        for index in range(lookback - 1, len(prepared)):
            sample = prepared[index - lookback + 1 : index + 1]
            lowest = min(candle.low for candle in sample)
            highest = max(candle.high for candle in sample)
            span = highest - lowest
            raw_k[index] = (
                Decimal(50) if span == 0 else Decimal(100) * (prepared[index].close - lowest) / span
            )
        smoothed_k = _optional_sma(raw_k, k_smoothing)
        smoothed_d = _optional_sma(smoothed_k, d_smoothing)
    return _result(
        "stochastic",
        prepared,
        {"k": tuple(smoothed_k), "d": tuple(smoothed_d)},
        {"lookback": lookback, "k_smoothing": k_smoothing, "d_smoothing": d_smoothing},
    )


def rate_of_change(
    candles: Sequence[CandleInput],
    period: int = 12,
    *,
    price_field: PriceField = "close",
    normalize: bool = False,
) -> IndicatorResult:
    """Percentage change from the price ``period`` supplied observations earlier."""
    prepared = _prepare(candles, normalize)
    prices = _price_values(prepared, price_field)
    _period(period)
    with _decimal_context(prices):
        output: list[Decimal | None] = [None] * len(prices)
        for index in range(period, len(prices)):
            output[index] = Decimal(100) * (prices[index] / prices[index - period] - Decimal(1))
    return _result(
        "roc", prepared, {"roc": tuple(output)}, {"period": period, "price_field": price_field}
    )


def average_true_range(
    candles: Sequence[CandleInput], period: int = 14, *, normalize: bool = False
) -> IndicatorResult:
    """ATR with first-bar high-low true range, SMA seed, then Wilder smoothing."""
    prepared = _prepare(candles, normalize)
    _period(period)
    with _decimal_context([v for c in prepared for v in (c.high, c.low, c.close)]):
        true_ranges: list[Decimal] = []
        for index, candle in enumerate(prepared):
            if index == 0:
                true_ranges.append(candle.high - candle.low)
            else:
                prior_close = prepared[index - 1].close
                true_ranges.append(
                    max(
                        candle.high - candle.low,
                        abs(candle.high - prior_close),
                        abs(candle.low - prior_close),
                    )
                )
        output: list[Decimal | None] = [None] * len(prepared)
        if len(true_ranges) >= period:
            average = sum(true_ranges[:period], Decimal(0)) / Decimal(period)
            output[period - 1] = average
            for index in range(period, len(true_ranges)):
                average = (average * Decimal(period - 1) + true_ranges[index]) / Decimal(period)
                output[index] = average
    return _result(
        "atr", prepared, {"atr": tuple(output)}, {"period": period, "smoothing": "wilder"}
    )


def bollinger_bands(
    candles: Sequence[CandleInput],
    period: int = 20,
    deviations: Decimal | int | str = Decimal(2),
    *,
    price_field: PriceField = "close",
    normalize: bool = False,
) -> IndicatorResult:
    """Population-standard-deviation Bollinger Bands (ddof=0), without quantization."""
    prepared = _prepare(candles, normalize)
    values = _price_values(prepared, price_field)
    _period(period)
    multiplier = _positive_decimal(deviations, "deviations", allow_zero=True)
    with _decimal_context(values):
        middle: list[Decimal | None] = [None] * len(values)
        upper: list[Decimal | None] = [None] * len(values)
        lower: list[Decimal | None] = [None] * len(values)
        for index in range(period - 1, len(values)):
            sample = values[index - period + 1 : index + 1]
            mean = sum(sample, Decimal(0)) / Decimal(period)
            variance = sum(((value - mean) ** 2 for value in sample), Decimal(0)) / Decimal(period)
            standard_deviation = max(variance, Decimal(0)).sqrt()
            band = multiplier * standard_deviation
            middle[index] = mean
            upper[index] = mean + band
            lower[index] = mean - band
    return _result(
        "bollinger_bands",
        prepared,
        {"middle": tuple(middle), "upper": tuple(upper), "lower": tuple(lower)},
        {
            "period": period,
            "deviations": multiplier,
            "standard_deviation": "population_ddof_0",
            "price_field": price_field,
        },
    )


def volume_weighted_average_price(
    candles: Sequence[CandleInput],
    *,
    session_context: SessionContext,
    normalize: bool = False,
) -> IndicatorResult:
    """Session-reset VWAP using typical price and explicit caller-supplied sessions.

    The cumulative value resets at each local session date. A date's first supplied
    bar may be partial-session history; output metadata identifies this condition.
    """
    prepared = _prepare(candles, normalize)
    if not isinstance(session_context, SessionContext):
        raise IndicatorError("VWAP requires a SessionContext.")
    zone = ZoneInfo(session_context.timezone)
    session_open = time.fromisoformat(session_context.session_open)
    session_close = time.fromisoformat(session_context.session_close)
    output: list[Decimal | None] = [None] * len(prepared)
    session_dates: list[str] = []
    first_times: dict[str, str] = {}
    with _decimal_context([v for c in prepared for v in (c.high, c.low, c.close)]):
        cumulative_pv = Decimal(0)
        cumulative_volume = Decimal(0)
        previous_date: str | None = None
        for index, candle in enumerate(prepared):
            local_timestamp = datetime.fromisoformat(candle.timestamp).astimezone(zone)
            local_date = local_timestamp.date().isoformat()
            local_time = local_timestamp.timetz().replace(tzinfo=None)
            if not (session_open <= local_time < session_close):
                raise IndicatorError(
                    f"VWAP candle {candle.timestamp!r} lies outside the configured session."
                )
            if local_date != previous_date:
                cumulative_pv = Decimal(0)
                cumulative_volume = Decimal(0)
                first_times[local_date] = candle.timestamp
                previous_date = local_date
            session_dates.append(local_date)
            typical = (candle.high + candle.low + candle.close) / Decimal(3)
            cumulative_pv += typical * Decimal(candle.volume)
            cumulative_volume += Decimal(candle.volume)
            if cumulative_volume:
                output[index] = cumulative_pv / cumulative_volume
    metadata = {
        "price_proxy": "typical_price=(high+low+close)/3",
        "reset": "local_session_date",
        "bar_timestamp_interval": "session_open_inclusive_session_close_exclusive",
        "session_context": {
            item.name: getattr(session_context, item.name) for item in fields(session_context)
        },
        "starts_at_session_open": {
            date: datetime.fromisoformat(timestamp).astimezone(zone).timetz().replace(tzinfo=None)
            != session_open
            for date, timestamp in first_times.items()
        },
    }
    return _result("vwap", prepared, {"vwap": tuple(output)}, metadata)


def on_balance_volume(
    candles: Sequence[CandleInput], *, normalize: bool = False
) -> IndicatorResult:
    """Cumulative volume; seed zero, add on rising closes, subtract on falling closes."""
    prepared = _prepare(candles, normalize)
    output: list[Decimal | None] = []
    balance = Decimal(0)
    for index, candle in enumerate(prepared):
        if index:
            if candle.close > prepared[index - 1].close:
                balance += Decimal(candle.volume)
            elif candle.close < prepared[index - 1].close:
                balance -= Decimal(candle.volume)
        output.append(balance)
    return _result(
        "obv", prepared, {"obv": tuple(output)}, {"seed": "zero", "equal_close": "unchanged"}
    )


def volume_moving_average(
    candles: Sequence[CandleInput], period: int = 20, *, normalize: bool = False
) -> IndicatorResult:
    """Trailing arithmetic mean of observed completed-bar volume."""
    prepared = _prepare(candles, normalize)
    _period(period)
    volumes = [Decimal(candle.volume) for candle in prepared]
    with _decimal_context(volumes):
        output = _sma(volumes, period)
    return _result("volume_moving_average", prepared, {"volume_ma": output}, {"period": period})


def typical_price(candles: Sequence[CandleInput], *, normalize: bool = False) -> IndicatorResult:
    """Typical price, (high + low + close) / 3, for each completed candle."""
    prepared = _prepare(candles, normalize)
    with _decimal_context([v for c in prepared for v in (c.high, c.low, c.close)]):
        output = tuple((c.high + c.low + c.close) / Decimal(3) for c in prepared)
    return _result(
        "typical_price", prepared, {"typical_price": output}, {"formula": "(high+low+close)/3"}
    )


def absolute_returns(
    candles: Sequence[CandleInput], *, price_field: PriceField = "close", normalize: bool = False
) -> IndicatorResult:
    """Close-to-close absolute change; first observation is unavailable (None)."""
    return _returns("absolute_returns", candles, price_field, normalize, percent=False)


def percentage_returns(
    candles: Sequence[CandleInput], *, price_field: PriceField = "close", normalize: bool = False
) -> IndicatorResult:
    """Close-to-close simple percentage return, expressed in percent units."""
    return _returns("percentage_returns", candles, price_field, normalize, percent=True)


def percentage_change(
    candles: Sequence[CandleInput], *, price_field: PriceField = "close", normalize: bool = False
) -> IndicatorResult:
    """Alias calculation for percentage returns: 100*(current/prior - 1)."""
    return _returns("percentage_change", candles, price_field, normalize, percent=True)


def _returns(
    name: str,
    candles: Sequence[CandleInput],
    price_field: PriceField,
    normalize: bool,
    *,
    percent: bool,
) -> IndicatorResult:
    prepared = _prepare(candles, normalize)
    prices = _price_values(prepared, price_field)
    output: list[Decimal | None] = [None] * len(prices)
    with _decimal_context(prices):
        for index in range(1, len(prices)):
            change = prices[index] - prices[index - 1]
            output[index] = Decimal(100) * change / prices[index - 1] if percent else change
    return _result(name, prepared, {name: tuple(output)}, {"price_field": price_field})


def _moving_average_result(
    name: str,
    candles: Sequence[CandleInput],
    period: int,
    price_field: PriceField,
    normalize: bool,
    column: str,
) -> IndicatorResult:
    prepared = _prepare(candles, normalize)
    values = _price_values(prepared, price_field)
    _period(period)
    with _decimal_context(values):
        output = _sma(values, period)
    return _result(name, prepared, {column: output}, {"period": period, "price_field": price_field})


def _prepare(candles: Sequence[CandleInput], normalize: bool) -> list[Candle]:
    if not isinstance(normalize, bool):
        raise IndicatorError("normalize must be a boolean.")
    return prepare_candles(candles, normalize=normalize)


def _price_values(candles: Sequence[Candle], price_field: PriceField) -> list[Decimal]:
    if not isinstance(price_field, str) or price_field not in {"open", "high", "low", "close"}:
        raise IndicatorError("price_field must be open, high, low, or close.")
    return [getattr(candle, price_field) for candle in candles]


def _period(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise IndicatorError("periods must be positive integers.")


def _positive_decimal(
    value: Decimal | int | str, name: str, *, allow_zero: bool = False
) -> Decimal:
    if isinstance(value, (bool, float)):
        raise IndicatorError(f"{name} must be Decimal, integer, or decimal text.")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise IndicatorError(f"{name} must be a finite decimal value.") from exc
    if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
        raise IndicatorError(f"{name} must be {'non-negative' if allow_zero else 'positive'}.")
    return parsed


@contextmanager
def _decimal_context(values: Sequence[Decimal]) -> Generator[None, None, None]:
    """Use a fixed local context so ambient caller settings cannot change results."""
    del values  # Precision is a versioned fixed contract for this module.
    with localcontext(
        Context(prec=_DECIMAL_PRECISION, rounding=_ROUNDING, Emax=MAX_EMAX, Emin=MIN_EMIN)
    ):
        yield


def _sma(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    output: list[Decimal | None] = [None] * len(values)
    rolling_sum = Decimal(0)
    for index, value in enumerate(values):
        rolling_sum += value
        if index >= period:
            rolling_sum -= values[index - period]
        if index + 1 >= period:
            output[index] = rolling_sum / Decimal(period)
    return tuple(output)


def _optional_sma(values: Sequence[Decimal | None], period: int) -> list[Decimal | None]:
    output: list[Decimal | None] = [None] * len(values)
    rolling: list[Decimal] = []
    for index, value in enumerate(values):
        if value is None:
            rolling.clear()
            continue
        rolling.append(value)
        if len(rolling) > period:
            rolling.pop(0)
        if len(rolling) == period:
            output[index] = sum(rolling, Decimal(0)) / Decimal(period)
    return output


def _ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    output: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return output
    alpha = Decimal(2) / Decimal(period + 1)
    current = sum(values[:period], Decimal(0)) / Decimal(period)
    output[period - 1] = current
    for index in range(period, len(values)):
        current = alpha * values[index] + (Decimal(1) - alpha) * current
        output[index] = current
    return output


def _rsi_value(gain: Decimal, loss: Decimal) -> Decimal:
    if gain == 0 and loss == 0:
        return Decimal(50)
    if loss == 0:
        return Decimal(100)
    if gain == 0:
        return Decimal(0)
    relative_strength = gain / loss
    return Decimal(100) - Decimal(100) / (Decimal(1) + relative_strength)


def _result(
    indicator: str,
    candles: Sequence[Candle],
    columns: Mapping[str, Sequence[Decimal | None]],
    configuration: Mapping[str, Any],
) -> IndicatorResult:
    return IndicatorResult(
        indicator=indicator,
        timestamps=tuple(candle.timestamp for candle in candles),
        columns={name: tuple(values) for name, values in columns.items()},
        metadata={
            "definition_version": _SCHEMA_VERSION,
            "configuration": dict(configuration),
            "numeric": {
                "type": "Decimal",
                "precision": _DECIMAL_PRECISION,
                "rounding": "ROUND_HALF_EVEN",
                "quantization": "none",
            },
            "warmup": "unavailable values are None; no partial windows or backfill",
            "input_policy": "completed supplied candles only; no missing-bar filling",
        },
    )


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            return MappingProxyType({str(key): freeze(nested) for key, nested in item.items()})
        if isinstance(item, (tuple, list)):
            return tuple(freeze(nested) for nested in item)
        return item

    return MappingProxyType({str(key): freeze(item) for key, item in value.items()})
