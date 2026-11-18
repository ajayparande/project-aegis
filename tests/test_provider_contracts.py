from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aegis.providers.contracts import HistoricalCandle


def make_candle(**changes):
    values = {
        "timestamp": datetime(2026, 11, 17, tzinfo=UTC),
        "open": Decimal(100),
        "high": Decimal(105),
        "low": Decimal(99),
        "close": Decimal(102),
        "volume": 0,
    }
    values.update(changes)
    return HistoricalCandle(**values)


def test_valid_zero_volume():
    assert make_candle().volume == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"timestamp": datetime(2026, 11, 17)},
        {"open": Decimal("NaN")},
        {"close": Decimal("Infinity")},
        {"high": Decimal(101)},
        {"low": Decimal(103)},
        {"volume": True},
        {"volume": -1},
    ],
)
def test_reject_invalid_candle(changes):
    with pytest.raises(ValueError):
        make_candle(**changes)
