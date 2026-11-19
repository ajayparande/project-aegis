"""P10-P4 database-backed SMA proposal integration tests.

These tests use an isolated SQLite database and synthetic runtime
fixtures. No broker, credentials, or live orders are involved.
"""

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aegis.forward_paper_trading import DecisionAction
from aegis.forward_runtime import (
    ForwardRuntimeError,
    ForwardRuntimeService,
)
from aegis.forward_sma import decide_completed_sma
from aegis.strategy_lab import StrategyCandle


def _candle(index: int, close: str) -> StrategyCandle:
    timestamp = (
        datetime(2026, 1, 5, 9, 15, tzinfo=UTC)
        + timedelta(minutes=index)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")

    price = Decimal(close)
    return StrategyCandle(
        timestamp=timestamp,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=10,
    )


def test_runtime_exposes_sma_proposal_builder():
    method = ForwardRuntimeService.build_sma_proposal
    parameters = inspect.signature(method).parameters

    assert "event_id" in parameters
    assert "fast_window" in parameters
    assert "slow_window" in parameters
    assert "buy_quantity" in parameters


@pytest.mark.parametrize(
    ("fast", "slow"),
    [
        (0, 3),
        (1, 3),
        (3, 3),
        (4, 3),
        (True, 3),
        (2, False),
    ],
)
def test_invalid_windows_rejected_before_database_access(
    fast, slow, tmp_path
):
    service = object.__new__(ForwardRuntimeService)

    with pytest.raises(ForwardRuntimeError):
        service.build_sma_proposal(
            "synthetic-event",
            fast_window=fast,
            slow_window=slow,
        )


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_invalid_buy_quantity_rejected_before_database_access(
    quantity, tmp_path
):
    service = object.__new__(ForwardRuntimeService)

    with pytest.raises(ForwardRuntimeError):
        service.build_sma_proposal(
            "synthetic-event",
            fast_window=2,
            slow_window=3,
            buy_quantity=quantity,
        )


def test_sma_signal_remains_execution_free():
    candles = tuple(
        _candle(index, str(price))
        for index, price in enumerate(
            [10, 10, 10, 10, 9, 8, 11, 13]
        )
    )

    result = decide_completed_sma(
        candles,
        fast_window=2,
        slow_window=3,
    )

    assert result.action in {"BUY", "SELL", "HOLD"}
    assert result.quantity == 0


def test_decision_actions_are_supported():
    assert DecisionAction.BUY.value == "BUY"
    assert DecisionAction.SELL.value == "SELL"
    assert DecisionAction.HOLD.value == "HOLD"


def test_missing_scheduler_event_fails_closed(monkeypatch, tmp_path):
    """An unknown event must not produce a proposal."""
    import sqlite3

    from aegis import forward_runtime

    database_path = tmp_path / "isolated_sma.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE forward_scheduler_events (
                event_id TEXT PRIMARY KEY,
                runtime_id TEXT NOT NULL
            )"""
        )

    service = object.__new__(ForwardRuntimeService)
    service.database_path = database_path
    service.runtime_id = "isolated-runtime"

    monkeypatch.setattr(
        ForwardRuntimeService,
        "_ensure_runtime_ready",
        lambda self: None,
    )
    monkeypatch.setattr(
        ForwardRuntimeService,
        "_ensure_scheduler_recovered",
        lambda self: None,
    )

    def isolated_connection(path):
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(
        forward_runtime,
        "get_connection",
        isolated_connection,
    )

    with pytest.raises(
        ForwardRuntimeError,
        match="Scheduler event does not exist",
    ):
        service.build_sma_proposal(
            "missing-event",
            fast_window=2,
            slow_window=3,
        )
