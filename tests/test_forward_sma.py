"""Tests for pure P10-P4 SMA decisions."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aegis.forward_sma import decide_completed_sma
from aegis.strategy_lab import StrategyCandle


def candles_from_closes(values: list[int]) -> tuple[StrategyCandle, ...]:
    start = datetime(2026, 1, 5, 9, 15, tzinfo=UTC)
    result = []

    for index, value in enumerate(values):
        price = Decimal(value)
        result.append(
            StrategyCandle(
                timestamp=(start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=100,
            )
        )

    return tuple(result)


def test_buy_on_latest_bullish_crossover() -> None:
    candles = candles_from_closes([10, 9, 8, 12])

    result = decide_completed_sma(candles, fast_window=2, slow_window=3)

    assert result.action == "BUY"
    assert result.quantity == 0
    assert result.reference_price == Decimal(12)


def test_sell_on_latest_bearish_crossover() -> None:
    candles = candles_from_closes([10, 9, 8, 12, 7, 6])

    result = decide_completed_sma(candles, fast_window=2, slow_window=3)

    assert result.action == "SELL"
    assert result.quantity == 0


def test_hold_when_no_latest_crossover() -> None:
    candles = candles_from_closes([10, 9, 8, 12, 13])

    result = decide_completed_sma(candles, fast_window=2, slow_window=3)

    assert result.action == "HOLD"
    assert result.quantity == 0


def test_insufficient_history_fails_closed() -> None:
    candles = candles_from_closes([10, 9, 8])

    with pytest.raises(ValueError, match="Insufficient"):
        decide_completed_sma(candles, fast_window=2, slow_window=3)


@pytest.mark.parametrize(
    ("fast", "slow"),
    [(1, 3), (3, 3), (4, 3), (True, 3), (2, False)],
)
def test_invalid_windows_rejected(fast: int, slow: int) -> None:
    candles = candles_from_closes([10, 9, 8, 12])

    with pytest.raises(ValueError):
        decide_completed_sma(candles, fast_window=fast, slow_window=slow)
