"""Independent reference, boundary, and causality tests for technical indicators."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, Inexact, localcontext
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest

from aegis import (
    Candle,
    IndicatorError,
    SessionContext,
    absolute_returns,
    average_true_range,
    bollinger_bands,
    exponential_moving_average,
    macd,
    on_balance_volume,
    percentage_change,
    percentage_returns,
    rate_of_change,
    relative_strength_index,
    simple_moving_average,
    stochastic_oscillator,
    typical_price,
    volume_moving_average,
    volume_weighted_average_price,
    weighted_moving_average,
)


def _candle(
    index: int,
    close: int | str,
    *,
    high: int | str | None = None,
    low: int | str | None = None,
    volume: int = 10,
    day: int = 5,
    minute: int | None = None,
) -> Candle:
    close_value = Decimal(str(close))
    timestamp_minute = index if minute is None else minute
    timestamp = datetime(2026, 1, day, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")) + timedelta(
        minutes=timestamp_minute
    )
    return Candle.from_values(
        timestamp.isoformat(),
        close_value,
        close_value + Decimal(1) if high is None else high,
        close_value - Decimal(1) if low is None else low,
        close_value,
        volume,
    )


def _values(result, column: str) -> tuple[Decimal | None, ...]:
    return result.columns[column]


def _assert_decimal(actual: Decimal | None, expected: str) -> None:
    assert actual is not None
    assert abs(actual - Decimal(expected)) < Decimal("1e-45")


def test_hand_calculated_trend_momentum_and_price_references() -> None:
    candles = [_candle(i, value) for i, value in enumerate((10, 12, 14, 16, 18))]
    assert _values(simple_moving_average(candles, 2), "sma") == (
        None,
        Decimal(11),
        Decimal(13),
        Decimal(15),
        Decimal(17),
    )
    assert _values(exponential_moving_average(candles, 2), "ema") == (
        None,
        Decimal(11),
        Decimal(13),
        Decimal(15),
        Decimal(17),
    )
    wma = _values(weighted_moving_average(candles, 2), "wma")
    assert wma[0] is None
    _assert_decimal(wma[1], "11.333333333333333333333333333333333333333333333333")
    _assert_decimal(wma[2], "13.333333333333333333333333333333333333333333333333")
    assert _values(relative_strength_index(candles, 2), "rsi") == (
        None,
        None,
        Decimal(100),
        Decimal(100),
        Decimal(100),
    )
    macd_result = macd(candles, fast_period=2, slow_period=3, signal_period=2)
    assert _values(macd_result, "macd") == (None, None, Decimal(1), Decimal(1), Decimal(1))
    assert _values(macd_result, "signal") == (None, None, None, Decimal(1), Decimal(1))
    assert _values(macd_result, "histogram") == (None, None, None, Decimal(0), Decimal(0))
    roc = _values(rate_of_change(candles, 2), "roc")
    assert roc[:3] == (None, None, Decimal(40))
    _assert_decimal(roc[3], "33.333333333333333333333333333333333333333333333333")
    assert _values(absolute_returns(candles), "absolute_returns") == (
        None,
        Decimal(2),
        Decimal(2),
        Decimal(2),
        Decimal(2),
    )
    percentage_return_values = _values(percentage_returns(candles), "percentage_returns")
    percentage_change_values = _values(percentage_change(candles), "percentage_change")
    assert percentage_return_values[0] is None
    _assert_decimal(percentage_return_values[1], "20")
    _assert_decimal(
        percentage_return_values[2],
        "16.666666666666666666666666666666666666666666666667",
    )
    _assert_decimal(percentage_change_values[1], "20")
    _assert_decimal(
        percentage_change_values[2],
        "16.666666666666666666666666666666666666666666666667",
    )
    typical_prices = _values(typical_price(candles), "typical_price")
    _assert_decimal(typical_prices[0], "10")
    _assert_decimal(typical_prices[1], "12")


def test_falling_and_sideways_reference_sequences() -> None:
    falling = [_candle(i, value) for i, value in enumerate((14, 12, 10, 8))]
    assert _values(simple_moving_average(falling, 2), "sma") == (
        None,
        Decimal(13),
        Decimal(11),
        Decimal(9),
    )
    assert _values(exponential_moving_average(falling, 2), "ema") == (
        None,
        Decimal(13),
        Decimal(11),
        Decimal(9),
    )
    falling_wma = _values(weighted_moving_average(falling, 2), "wma")
    _assert_decimal(falling_wma[1], "12.666666666666666666666666666666666666666666666667")
    falling_macd = macd(falling, fast_period=2, slow_period=3, signal_period=2)
    assert _values(falling_macd, "macd") == (None, None, Decimal(-1), Decimal(-1))
    assert _values(falling_macd, "signal") == (None, None, None, Decimal(-1))
    assert _values(relative_strength_index(falling, 2), "rsi") == (
        None,
        None,
        Decimal(0),
        Decimal(0),
    )
    falling_roc = _values(rate_of_change(falling, 2), "roc")
    _assert_decimal(falling_roc[2], "-28.571428571428571428571428571428571428571428571429")
    _assert_decimal(falling_roc[3], "-33.333333333333333333333333333333333333333333333333")
    assert _values(on_balance_volume(falling), "obv") == (
        Decimal(0),
        Decimal(-10),
        Decimal(-20),
        Decimal(-30),
    )
    sideways = [_candle(index, 10, high=10, low=10) for index in range(4)]
    assert _values(relative_strength_index(sideways, 2), "rsi")[2:] == (
        Decimal(50),
        Decimal(50),
    )
    bands = bollinger_bands(sideways, 2)
    assert _values(bands, "upper")[1:] == (Decimal(10),) * 3
    assert _values(bands, "lower")[1:] == (Decimal(10),) * 3


def test_hand_calculated_volatility_and_volume_references() -> None:
    candles = [_candle(i, value) for i, value in enumerate((10, 12, 14, 16))]
    atr = _values(average_true_range(candles, 2), "atr")
    assert atr[:2] == (None, Decimal("2.5"))
    assert atr[2:] == (Decimal("2.75"), Decimal("2.875"))
    gap_candles = [
        _candle(0, 10, high=11, low=9),
        _candle(1, 15, high=15, low=14),
    ]
    assert _values(average_true_range(gap_candles, 2), "atr") == (None, Decimal("3.5"))

    bands = bollinger_bands(candles, 2, 1)
    assert _values(bands, "middle")[:2] == (None, Decimal(11))
    assert _values(bands, "upper")[1] == Decimal(12)
    assert _values(bands, "lower")[1] == Decimal(10)
    assert _values(bands, "middle")[2] == Decimal(13)
    assert _values(bands, "upper")[2] == Decimal(14)
    assert _values(bands, "lower")[2] == Decimal(12)

    volume_candles = [
        _candle(0, 10, volume=10),
        _candle(1, 11, volume=5),
        _candle(2, 11, volume=7),
        _candle(3, 9, volume=3),
    ]
    assert _values(on_balance_volume(volume_candles), "obv") == (
        Decimal(0),
        Decimal(5),
        Decimal(5),
        Decimal(2),
    )
    assert _values(volume_moving_average(volume_candles, 2), "volume_ma") == (
        None,
        Decimal("7.5"),
        Decimal(6),
        Decimal(5),
    )


def test_hand_calculated_stochastic_and_flat_denominator_cases() -> None:
    candles = [
        _candle(0, 10, high=11, low=9),
        _candle(1, 11, high=12, low=10),
        _candle(2, 12, high=13, low=11),
        _candle(3, 12, high=13, low=9),
    ]
    stochastic = stochastic_oscillator(candles, lookback=3, k_smoothing=1, d_smoothing=2)
    assert _values(stochastic, "k")[:2] == (None, None)
    assert _values(stochastic, "k")[2:] == (Decimal(75), Decimal(75))
    assert _values(stochastic, "d")[2:] == (None, Decimal(75))

    flat = [_candle(i, 10, high=10, low=10, volume=0) for i in range(4)]
    assert _values(relative_strength_index(flat, 2), "rsi") == (
        None,
        None,
        Decimal(50),
        Decimal(50),
    )
    assert _values(stochastic_oscillator(flat, 2, 1, 1), "k") == (
        None,
        Decimal(50),
        Decimal(50),
        Decimal(50),
    )
    assert _values(average_true_range(flat, 2), "atr") == (
        None,
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )
    assert _values(on_balance_volume(flat), "obv") == (Decimal(0),) * 4
    assert _values(volume_moving_average(flat, 2), "volume_ma") == (
        None,
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )


def test_vwap_uses_explicit_local_sessions_and_resets_between_dates() -> None:
    candles = [
        _candle(0, 10, high=11, low=9, volume=1, day=5, minute=0),
        _candle(1, 20, high=21, low=19, volume=3, day=5, minute=1),
        _candle(2, 30, high=31, low=29, volume=2, day=6, minute=0),
    ]
    context = SessionContext("09:15", "15:30", "Asia/Kolkata", candle_interval_minutes=1)
    result = volume_weighted_average_price(candles, session_context=context)
    values = _values(result, "vwap")
    _assert_decimal(values[0], "10")
    _assert_decimal(values[1], "17.5")
    _assert_decimal(values[2], "30")
    configuration = result.metadata["configuration"]
    assert configuration["session_context"]["timezone"] == "Asia/Kolkata"
    assert configuration["starts_at_session_open"] == {
        "2026-01-05": False,
        "2026-01-06": False,
    }

    zero_volume = [
        _candle(0, 10, high=11, low=9, volume=0, day=5, minute=0),
        _candle(1, 12, high=13, low=11, volume=2, day=5, minute=1),
    ]
    assert _values(volume_weighted_average_price(zero_volume, session_context=context), "vwap") == (
        None,
        Decimal(12),
    )
    with pytest.raises(IndicatorError, match="requires a SessionContext"):
        volume_weighted_average_price(candles, session_context=None)  # type: ignore[arg-type]
    outside_session = [_candle(0, 10, minute=400)]
    with pytest.raises(IndicatorError, match="outside the configured session"):
        volume_weighted_average_price(outside_session, session_context=context)
    close_boundary = [_candle(0, 10, minute=375)]
    with pytest.raises(IndicatorError, match="outside the configured session"):
        volume_weighted_average_price(close_boundary, session_context=context)


def test_warmup_invalid_parameters_and_candle_validation() -> None:
    candles = [_candle(0, 10), _candle(1, 11)]
    assert _values(simple_moving_average(candles, 3), "sma") == (None, None)
    assert _values(macd(candles, 2, 3, 2), "macd") == (None, None)
    with pytest.raises(IndicatorError, match="positive integers"):
        simple_moving_average(candles, True)  # type: ignore[arg-type]
    with pytest.raises(IndicatorError, match="fast_period"):
        macd(candles, 4, 3, 1)
    with pytest.raises(IndicatorError, match="non-negative"):
        bollinger_bands(candles, 2, "-0.1")
    with pytest.raises(IndicatorError, match="finite decimal"):
        bollinger_bands(candles, 2, "not-a-number")
    with pytest.raises(IndicatorError, match="price_field"):
        simple_moving_average(candles, 1, price_field="volume")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="completed"):
        typical_price(
            [
                {
                    "timestamp": candles[0].timestamp,
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "volume": 1,
                    "is_complete": False,
                }
            ]
        )
    with pytest.raises(ValueError, match="chronological"):
        simple_moving_average(candles[::-1], 1)
    normalized = simple_moving_average(candles[::-1], 1, normalize=True)
    assert normalized.timestamps == tuple(candle.timestamp for candle in candles)
    with pytest.raises(ValueError, match="Duplicate"):
        simple_moving_average([candles[0], candles[0]], 1)
    with pytest.raises(ValueError, match="OHLCV"):
        typical_price(
            [{"timestamp": candles[0].timestamp, "open": 10, "high": 11, "low": 9, "close": 10}]
        )


def test_prefix_consistency_determinism_and_serialization_across_indicators() -> None:
    candles = [_candle(i, 100 + i, volume=100 + i) for i in range(36)]
    session = SessionContext("09:15", "15:30", candle_interval_minutes=1)
    calculations = [
        lambda data: simple_moving_average(data, 3),
        lambda data: exponential_moving_average(data, 3),
        lambda data: weighted_moving_average(data, 3),
        lambda data: relative_strength_index(data, 3),
        lambda data: macd(data, 2, 4, 2),
        lambda data: stochastic_oscillator(data, 3, 2, 2),
        lambda data: rate_of_change(data, 3),
        lambda data: average_true_range(data, 3),
        lambda data: bollinger_bands(data, 3),
        lambda data: volume_weighted_average_price(data, session_context=session),
        on_balance_volume,
        lambda data: volume_moving_average(data, 3),
        typical_price,
        absolute_returns,
        percentage_returns,
        percentage_change,
    ]
    gapped_sessions = [
        candles[0],
        candles[3],
        candles[10],
        _candle(40, 145, day=6, minute=0),
    ]
    for sequence in (candles, gapped_sessions):
        input_snapshot = list(sequence)
        for calculate in calculations:
            complete = calculate(sequence)
            assert complete.to_dict() == calculate(sequence).to_dict()
            json.dumps(complete.to_dict(), sort_keys=True)
            for prefix_length in (0, 1, 2, len(sequence) - 1, len(sequence)):
                prefix = calculate(sequence[:prefix_length])
                assert prefix.timestamps == complete.timestamps[:prefix_length]
                for column, values in complete.columns.items():
                    assert prefix.columns[column] == values[:prefix_length]
        assert sequence == input_snapshot


def test_decimal_results_ignore_ambient_context_and_are_immutable() -> None:
    candles = [_candle(i, value) for i, value in enumerate(("1.01", "1.13", "1.22", "1.30"))]
    baseline = bollinger_bands(candles, 3).to_dict()
    with localcontext() as context:
        context.prec = 6
        context.traps[Inexact] = True
        altered = bollinger_bands(candles, 3).to_dict()
    assert baseline == altered
    result = bollinger_bands(candles, 3)
    assert result.metadata["definition_version"] == "1.0.0"
    with pytest.raises(TypeError):
        result.columns["upper"] = ()  # type: ignore[index]
    assert result.to_dict()["columns"]["upper"][0] is None


def test_irregular_observation_gaps_are_not_filled_or_interpolated() -> None:
    first = _candle(0, 10, minute=0)
    second = _candle(1, 14, minute=7)
    result = simple_moving_average([first, second], 2)
    assert result.timestamps == (first.timestamp, second.timestamp)
    assert _values(result, "sma") == (None, Decimal(12))


@pytest.mark.parametrize("interval", (1, 5, 15, 1440))
def test_supported_timeframe_timestamp_alignment(interval: int) -> None:
    start = datetime(2026, 1, 5, 9, 15, tzinfo=UTC)
    candles = [
        Candle.from_values(
            (start + timedelta(minutes=index * interval)).isoformat(),
            10 + index,
            12 + index,
            9 + index,
            11 + index,
            100,
        )
        for index in range(4)
    ]
    result = simple_moving_average(candles, 2)
    assert result.timestamps == tuple(candle.timestamp for candle in candles)
    assert _values(result, "sma") == (None, Decimal("11.5"), Decimal("12.5"), Decimal("13.5"))


def test_full_indicator_bundle_has_bounded_runtime_on_historical_sized_input() -> None:
    session_start = datetime(2026, 1, 5, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    candles = []
    for index in range(5000):
        timestamp = session_start + timedelta(days=index // 360, minutes=index % 360)
        close = Decimal(100 + index % 101)
        candles.append(
            Candle.from_values(
                timestamp.isoformat(), close, close + 1, close - 1, close, 1000 + index
            )
        )
    session = SessionContext("09:15", "15:30", candle_interval_minutes=1)

    def calculate(data: list[Candle]):
        return (
            simple_moving_average(data),
            exponential_moving_average(data),
            weighted_moving_average(data),
            relative_strength_index(data),
            macd(data),
            stochastic_oscillator(data),
            rate_of_change(data),
            average_true_range(data),
            bollinger_bands(data),
            volume_weighted_average_price(data, session_context=session),
            on_balance_volume(data),
            volume_moving_average(data),
            typical_price(data),
            absolute_returns(data),
            percentage_returns(data),
        )

    start = perf_counter()
    outputs = calculate(candles)
    elapsed = perf_counter() - start
    import tracemalloc

    tracemalloc.start()
    try:
        calculate(candles[:1000])
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(outputs) == 15
    assert all(len(output.timestamps) == len(candles) for output in outputs)
    assert elapsed < 10
    assert peak_bytes < 64 * 1024 * 1024
    print(
        f"5,000-candle indicator bundle: {elapsed:.3f}s; "
        f"1,000-candle traced peak: {peak_bytes / (1024 * 1024):.2f} MiB"
    )
