"""Isolated regression tests for forward financial causal ordering.

Synthetic SQLite only. No broker, credentials, or live orders.
"""

from __future__ import annotations

import sqlite3

import pytest

from aegis.paper_trading import (
    PaperTradingError,
    _assert_forward_causal_order,
)

ACCOUNT = 1
INSTRUMENT = 10
RUNTIME = "runtime-a"
EARLY = "2026-01-05T09:16:00+05:30"
LATER = "2026-01-05T09:18:00+05:30"


@pytest.fixture
def connection():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE forward_scheduler_events (
            event_id TEXT PRIMARY KEY,
            runtime_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            decision_timestamp TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE paper_orders (
            order_id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            requested_instrument_id INTEGER NOT NULL,
            signal_timestamp TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE paper_fills (
            fill_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            execution_timestamp TEXT NOT NULL
        );
        """
    )
    yield db
    db.close()


def event(
    db,
    event_id,
    timestamp,
    status="PENDING",
    runtime=RUNTIME,
    instrument=INSTRUMENT,
):
    db.execute(
        """
        INSERT INTO forward_scheduler_events
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, runtime, instrument, timestamp, status),
    )


def fill(
    db,
    order_id,
    signal_timestamp,
    execution_timestamp,
    account=ACCOUNT,
    instrument=INSTRUMENT,
):
    db.execute(
        """
        INSERT INTO paper_orders
        VALUES (?, ?, ?, ?, 'FILLED')
        """,
        (order_id, account, instrument, signal_timestamp),
    )
    db.execute(
        "INSERT INTO paper_fills VALUES (?, ?, ?)",
        (order_id + "-fill", order_id, execution_timestamp),
    )


def check(db, event_id="early", account=ACCOUNT):
    _assert_forward_causal_order(db, account, event_id, INSTRUMENT)


@pytest.mark.parametrize("status", ["HOLD", "FILLED", "REJECTED"])
def test_later_terminal_event_blocks_older_event(connection, status):
    event(connection, "early", EARLY)
    event(connection, "later", LATER, status)

    with pytest.raises(PaperTradingError, match="terminal scheduler decision"):
        check(connection)


def test_later_pending_event_does_not_block_recovery(connection):
    event(connection, "early", EARLY)
    event(connection, "later", LATER, "PENDING")

    check(connection)


def test_later_terminal_event_from_other_runtime_is_not_same_runtime(
    connection,
):
    event(connection, "early", EARLY)
    event(connection, "other", LATER, "FILLED", "runtime-b")

    check(connection)


def test_later_fill_from_other_runtime_blocks_shared_account(
    connection,
):
    event(connection, "early", EARLY)
    fill(connection, "other-order", LATER, LATER)

    with pytest.raises(PaperTradingError, match="paper fill"):
        check(connection)


def test_fill_from_another_account_does_not_block(connection):
    event(connection, "early", EARLY)
    fill(
        connection,
        "different-account",
        LATER,
        LATER,
        account=ACCOUNT + 1,
    )

    check(connection)


def test_fill_from_another_instrument_does_not_block(connection):
    event(connection, "early", EARLY)
    fill(
        connection,
        "different-instrument",
        LATER,
        LATER,
        instrument=INSTRUMENT + 1,
    )

    check(connection)


def test_earlier_completed_fill_does_not_block_later_decision(
    connection,
):
    event(connection, "early", LATER)
    fill(
        connection,
        "previous-buy",
        EARLY,
        "2026-01-05T09:17:00+05:30",
    )

    check(connection)


def test_execution_after_decision_blocks_stale_position_context(
    connection,
):
    event(connection, "early", LATER)
    fill(
        connection,
        "late-execution",
        EARLY,
        "2026-01-05T09:19:00+05:30",
    )

    with pytest.raises(PaperTradingError, match="paper fill"):
        check(connection)


def test_timestamp_offsets_are_compared_as_instants(connection):
    event(connection, "early", "2026-01-05T03:46:00Z")
    event(
        connection,
        "later",
        "2026-01-05T09:17:00+05:30",
        "FILLED",
    )

    with pytest.raises(PaperTradingError):
        check(connection)


def test_missing_event_fails_closed(connection):
    with pytest.raises(PaperTradingError, match="persisted scheduler event"):
        check(connection)


def test_causal_rejection_does_not_write_financial_rows(connection):
    event(connection, "early", EARLY)
    event(connection, "later", LATER, "FILLED")

    connection.commit()
    connection.execute("BEGIN IMMEDIATE")

    with pytest.raises(PaperTradingError):
        check(connection)

    assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0

    connection.rollback()
