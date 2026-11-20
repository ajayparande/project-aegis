"""P10-P4 database-backed SMA proposal integration tests.

These tests use an isolated SQLite database and synthetic runtime
fixtures. No broker, credentials, or live orders are involved.
"""

import inspect
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aegis.database import get_readonly_connection
from aegis.forward_paper_trading import DecisionAction
from aegis.forward_runtime import (
    ForwardRuntimeError,
    ForwardRuntimeService,
)
from aegis.forward_sma import decide_completed_sma
from aegis.strategy_lab import StrategyCandle
from tests.test_forward_runtime import _setup as runtime_setup


def _database_dump(database_path):
    with closing(get_readonly_connection(database_path)) as connection:
        return tuple(connection.iterdump())


def _candle(index: int, close: str) -> StrategyCandle:
    timestamp = (
        (datetime(2026, 1, 5, 9, 15, tzinfo=UTC) + timedelta(minutes=index))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

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
def test_invalid_windows_rejected_before_database_access(fast, slow):
    service = object.__new__(ForwardRuntimeService)

    with pytest.raises(ForwardRuntimeError, match="Require 2 <= fast_window < slow_window"):
        service.build_sma_proposal(
            "synthetic-event",
            fast_window=fast,
            slow_window=slow,
        )


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_invalid_buy_quantity_rejected_before_database_access(quantity):
    service = object.__new__(ForwardRuntimeService)

    with pytest.raises(ForwardRuntimeError, match="buy_quantity must be a positive integer"):
        service.build_sma_proposal(
            "synthetic-event",
            fast_window=2,
            slow_window=3,
            buy_quantity=quantity,
        )


def test_sma_signal_remains_execution_free():
    candles = tuple(
        _candle(index, str(price)) for index, price in enumerate([10, 10, 10, 10, 9, 8, 11, 13])
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


def test_missing_scheduler_event_fails_closed(tmp_path):
    """An unknown event must not produce a proposal."""
    _, _, _, service = runtime_setup(tmp_path)

    with pytest.raises(
        ForwardRuntimeError,
        match="Scheduler event does not exist",
    ):
        service.build_sma_proposal(
            "missing-event",
            fast_window=2,
            slow_window=3,
        )


def test_forged_service_cannot_bypass_persisted_scheduling_only(tmp_path):
    database_path, _, _, runtime = runtime_setup(tmp_path, scheduling_only=True)
    forged = object.__new__(ForwardRuntimeService)
    forged.database_path = database_path
    forged.runtime_id = runtime.runtime_id
    before = _database_dump(database_path)

    with pytest.raises(ForwardRuntimeError, match="Scheduling-only runtime"):
        forged.build_sma_proposal(
            "missing-event",
            fast_window=2,
            slow_window=3,
        )

    assert _database_dump(database_path) == before


def test_forged_scheduling_only_configuration_fails_closed(tmp_path):
    database_path, _, _, runtime = runtime_setup(tmp_path, scheduling_only=True)
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            "SELECT config_json FROM forward_runtimes WHERE runtime_id = ?",
            (runtime.runtime_id,),
        ).fetchone()
        config = json.loads(row[0])
        config["scheduling_only"] = False
        connection.execute(
            "UPDATE forward_runtimes SET config_json = ? WHERE runtime_id = ?",
            (json.dumps(config, sort_keys=True, separators=(",", ":")), runtime.runtime_id),
        )
        connection.commit()

    before = _database_dump(database_path)
    with pytest.raises(ForwardRuntimeError, match="Runtime configuration is corrupted"):
        runtime.build_sma_proposal(
            "missing-event",
            fast_window=2,
            slow_window=3,
        )
    assert _database_dump(database_path) == before
