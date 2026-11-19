"""Offline SMA session integration using existing authenticated runtime fixtures."""

from __future__ import annotations

import json
import socket
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_forward_runtime import _START, _provider, _quote, _setup

from aegis import decision_journal
from aegis.database import get_connection, get_readonly_connection
from aegis.forward_runtime import ForwardRuntimeError, ForwardRuntimeService
from aegis.paper_trading import get_paper_account_report, reconcile_paper_account
from aegis.synthetic_sma_session import SMASessionConfig, run_existing_sma_runtime


@pytest.fixture(autouse=True)
def isolate_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "aegis.log"))
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "runtime_calendar.json"))


def test_actual_sma_session_finances_journal_restart_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warm-up, real HOLD/BUY/SELL, an open-position restart and duplicate delivery."""

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Synthetic session attempted network access")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    database, calendars, (instrument,), runtime = _setup(tmp_path)
    prices = (
        "100",
        "100",
        "100",
        "100",
        "99.90",
        "99.80",
        "100.10",
        "100.10",
        "100.40",
        "100.20",
        "99.90",
        "99.90",
    )
    quotes = tuple(
        _quote(
            instrument,
            (_START + timedelta(minutes=index)).isoformat(),
            index + 1,
            Decimal(price),
            f"sma-session-{index}",
            quantity=2,
        )
        for index, price in enumerate(prices)
    )
    config = SMASessionConfig(buy_quantity=2)
    first = run_existing_sma_runtime(runtime, quotes[:8], config)
    assert first.quotes_accepted == 8
    assert first.account_reconciliation == "RECONCILED"
    assert [(d.requested_action, d.outcome_status) for d in first.decisions][-1] == (
        "BUY",
        "FILLED",
    )
    report = get_paper_account_report(database, runtime.account_id)
    assert report["available_cash"] == "99799.8"
    assert report["positions"][0]["quantity"] == 2
    assert (
        decision_journal.reconcile_journal(database, runtime.runtime_id)["status"] == "RECONCILED"
    )

    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    assert reconcile_paper_account(database, restored.account_id)["status"] == "RECONCILED"
    second = run_existing_sma_runtime(restored, quotes[8:], config)
    assert second.quotes_accepted == 4
    assert second.account_reconciliation == "RECONCILED"
    decisions = first.decisions + second.decisions
    assert len(decisions) == first.scheduled_events + second.scheduled_events == 13
    assert [(d.requested_action, d.outcome_status) for d in decisions if d.order_id] == [
        ("BUY", "FILLED"),
        ("SELL", "FILLED"),
    ]
    assert first.protective_exits == second.protective_exits == 0
    report = get_paper_account_report(database, restored.account_id)
    assert report["positions"] == []
    assert Decimal(str(report["available_cash"])) == Decimal("99999.6")
    assert Decimal(str(report["realized_net_pnl"])) == Decimal("-0.4")
    assert decision_journal.reconcile_journal(database, runtime.runtime_id)["counts"] == {
        "NO_DECISION": 5,
        "HOLD": 6,
        "FILLED": 2,
    }
    assert (
        decision_journal.reconcile_journal(database, runtime.runtime_id)["status"] == "RECONCILED"
    )

    with closing(get_readonly_connection(database)) as connection:
        fills = connection.execute(
            """SELECT o.side, f.quantity, f.fill_price FROM paper_fills f
               JOIN paper_orders o USING(order_id) ORDER BY f.execution_timestamp"""
        ).fetchall()
        assert [(r["side"], r["quantity"], Decimal(r["fill_price"])) for r in fills] == [
            ("BUY", 2, Decimal("100.10")),
            ("SELL", 2, Decimal("99.90")),
        ]
        assert connection.execute("SELECT COUNT(*) FROM paper_ledger_entries").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_execution_authorizations WHERE status = 'CONSUMED'"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM paper_execution_snapshot_uses").fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM decision_journal_audits WHERE stage = 'EXECUTION' AND fill_id IS NOT NULL"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE status = 'PENDING'"
            ).fetchone()[0]
            == 0
        )
        warmups = connection.execute(
            """SELECT s.interval, e.payload_json, s.proposal_json, s.status
               FROM decision_journal_events e
               JOIN forward_scheduler_events s USING(event_id)
               WHERE e.state = 'NO_DECISION'"""
        ).fetchall()
        assert len(warmups) == 5
        assert sorted(row["interval"] for row in warmups) == ["1m"] * 3 + ["5m"] * 2
        for row in warmups:
            payload = json.loads(row["payload_json"])
            assert payload["interval"] == row["interval"]
            assert payload["reason_codes"] == ["SMA_WARMUP"]
            assert (
                payload["original_signal"] is payload["quantity"] is payload["confidence"] is None
            )
            assert row["proposal_json"] is None
            assert row["status"] == "REJECTED"
        before = tuple(
            tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
            for table in (
                "paper_orders",
                "paper_fills",
                "paper_ledger_entries",
                "decision_journal_events",
            )
        )

    # Restart and duplicate delivery do not alter durable decisions or financial effects.
    again = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    duplicate = run_existing_sma_runtime(again, quotes[-1:], config)
    assert duplicate.quotes_accepted == duplicate.scheduled_events == 0
    assert duplicate.decisions == ()
    assert duplicate.account_reconciliation == "RECONCILED"
    with closing(get_readonly_connection(database)) as connection:
        after = tuple(
            tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
            for table in (
                "paper_orders",
                "paper_fills",
                "paper_ledger_entries",
                "decision_journal_events",
            )
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted = 1").fetchone()[
                0
            ]
            == 12
        )
    assert before == after


def test_pending_scheduler_work_blocks_session_before_advancing(tmp_path: Path) -> None:
    database, calendars, instruments, runtime = _setup(tmp_path)
    quotes = tuple(_provider(instruments, count=3))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    with pytest.raises(ForwardRuntimeError, match="Unresolved scheduler event"):
        run_existing_sma_runtime(restored, quotes[2:], SMASessionConfig())
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted = 1").fetchone()[
                0
            ]
            == 2
        )
        assert (
            connection.execute(
                "SELECT status FROM forward_scheduler_events WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            == "PENDING"
        )


def test_warmup_journal_failure_leaves_pending_and_blocks_restart(tmp_path: Path) -> None:
    database, calendars, instruments, runtime = _setup(tmp_path)
    quotes = tuple(_provider(instruments, count=3))
    with closing(get_connection(database)) as connection:
        connection.execute(
            """CREATE TRIGGER fail_warmup BEFORE INSERT ON decision_journal_events
               WHEN NEW.state = 'NO_DECISION'
               BEGIN SELECT RAISE(ABORT, 'WARMUP_JOURNAL_FAILURE'); END"""
        )
        connection.commit()
    with pytest.raises(sqlite3.IntegrityError, match="WARMUP_JOURNAL_FAILURE"):
        run_existing_sma_runtime(runtime, quotes, SMASessionConfig())
    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    with pytest.raises(ForwardRuntimeError, match="Unresolved scheduler event"):
        run_existing_sma_runtime(restored, quotes[2:], SMASessionConfig())
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted = 1").fetchone()[
                0
            ]
            == 2
        )
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM decision_journal_events").fetchone()[0] == 0


def test_missing_provenance_is_not_concealed_as_warmup(tmp_path: Path) -> None:
    database, _, instruments, runtime = _setup(tmp_path)
    quotes = tuple(_provider(instruments, count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    with closing(get_connection(database)) as connection:
        connection.execute("DELETE FROM historical_candles")
        connection.commit()
    with pytest.raises(ForwardRuntimeError, match="historical provenance is missing"):
        runtime.build_sma_proposal(event_id, fast_window=2, slow_window=3, record_warmup=True)
    with closing(get_readonly_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decision_journal_events").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT status FROM forward_scheduler_events WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            == "PENDING"
        )


@pytest.mark.parametrize(
    "settings",
    [
        {"fast_window": True},
        {"fast_window": 1},
        {"slow_window": 2},
        {"buy_quantity": 0},
        {"max_quotes": 10001},
    ],
)
def test_invalid_config_is_rejected(settings: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        SMASessionConfig(**settings)


def test_session_reports_rejected_hold_without_claiming_a_successful_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _, instruments, runtime = _setup(tmp_path)
    snapshot = runtime.execution_snapshot

    def mismatched_snapshot(*args, **kwargs):
        original = snapshot(*args, **kwargs)
        return replace(original, reference_price=original.reference_price + Decimal("0.05"))

    monkeypatch.setattr(runtime, "execution_snapshot", mismatched_snapshot)
    result = run_existing_sma_runtime(runtime, _provider(instruments, count=5), SMASessionConfig())
    assert result.decisions[-1].requested_action == "HOLD"
    assert result.decisions[-1].outcome_status == "REJECTED"
    assert result.decisions[-1].reason
    assert result.account_reconciliation == result.journal_reconciliation == "RECONCILED"
    journal = decision_journal.reconcile_journal(database, runtime.runtime_id)
    assert journal["requested_counts"] == {"NO_DECISION": 3, "HOLD": 1}
    assert journal["counts"] == {"NO_DECISION": 3, "BLOCKED": 1}
    with closing(get_readonly_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_session_cannot_succeed_with_a_missing_terminal_journal_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, calendars, instruments, runtime = _setup(tmp_path)

    def crash(*args, **kwargs):
        raise RuntimeError("SESSION_FINAL_AUDIT_CRASH")

    with monkeypatch.context() as patch:
        patch.setattr(decision_journal, "finish_runtime_decision", crash)
        with pytest.raises(RuntimeError, match="SESSION_FINAL_AUDIT_CRASH"):
            run_existing_sma_runtime(runtime, _provider(instruments, count=5), SMASessionConfig())
    runtime = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    # The scheduler is terminal, but no completed journal outcome may be inferred.
    assert decision_journal.reconcile_journal(database, runtime.runtime_id)["counts"] == {
        "NO_DECISION": 3,
        "PENDING": 1,
    }
    with pytest.raises(RuntimeError, match="Decision journal reconciliation failed: UNRESOLVED"):
        run_existing_sma_runtime(
            runtime, tuple(_provider(instruments, count=6))[5:], SMASessionConfig()
        )
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted=1").fetchone()[0]
            == 5
        )
