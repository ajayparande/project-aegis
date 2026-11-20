"""Actual V3 binary mock feed reaches the existing runtime without execution."""

import json
import socket
from contextlib import closing
from datetime import timedelta
from decimal import Decimal

import pytest

import aegis.forward_runtime as runtime_module
import aegis.paper_trading as paper_module
from aegis.database import get_readonly_connection
from aegis.forward_paper_trading import DecisionProposal, ForwardPaperTradingEngine
from aegis.forward_runtime import ForwardRuntimeError, ForwardRuntimeService, Quote
from aegis.paper_trading import get_paper_account_report, reconcile_paper_account
from aegis.providers.upstox_market_stream import ConnectionState, UpstoxMarketStreamAdapter
from tests.test_upstox_market_stream import KEY, START, _frame, _setup_observer, _synchronize


def test_mock_v3_feed_persists_observations_gap_and_restart_without_orders(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Observe-only attempted network access or paper execution")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(runtime_module, "submit_paper_order", forbidden)
    monkeypatch.setattr(paper_module, "submit_paper_order", forbidden)
    monkeypatch.setattr(ForwardPaperTradingEngine, "_route_runtime_proposal", forbidden)
    monkeypatch.setattr(ForwardRuntimeService, "_monitor_position", forbidden)
    database, calendars, instrument, runtime, clock, adapter = _setup_observer(
        tmp_path, monkeypatch, mode="OBSERVE_ONLY"
    )
    _synchronize(adapter, clock)
    results = []
    for index, minute in enumerate((0, 1, 3, 4, 5, 6)):
        at = START + timedelta(minutes=minute)
        received = at + timedelta(milliseconds=5)
        clock.now = at + timedelta(milliseconds=7)
        (result,) = adapter.receive_frame(
            _frame(
                at + timedelta(milliseconds=1),
                event_at=at,
                price=float(Decimal("100.10") + Decimal("0.05") * index),
            ),
            received_at=received,
        )
        results.append(result)
        assert result.runtime_result.accepted and not result.runtime_result.duplicate
        assert result.runtime_result.scheduled_event_ids == ()
        assert result.runtime_result.exit_result is None
        if minute == 3:
            assert result.gap_detected and adapter.state == ConnectionState.GAPPED
            runtime = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
            adapter = UpstoxMarketStreamAdapter(
                runtime, {KEY: instrument}, mode="OBSERVE_ONLY", clock=clock
            )
            _synchronize(adapter, clock)
            (replay,) = adapter.receive_frame(
                _frame(clock.now, event_at=at, price=100.2), received_at=clock.now
            )
            assert replay.runtime_result.duplicate
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted=1").fetchone()[0]
            == 6
        )
        candles = connection.execute(
            "SELECT candle_timestamp,completed_at,close_decimal FROM forward_runtime_candles ORDER BY candle_timestamp"
        ).fetchall()
        assert len(candles) == 5
        assert [r["close_decimal"] for r in candles] == [
            "100.1",
            "100.15",
            "100.2",
            "100.25",
            "100.3",
        ]
        assert not any("03:47:" in r["candle_timestamp"] for r in candles)
        assert candles[1]["candle_timestamp"] == "2026-01-05T03:46:00Z"
        assert candles[1]["completed_at"] == "2026-01-05T03:48:00.005000Z"
        assert connection.execute("SELECT COUNT(*) FROM historical_candles").fetchone()[0] == 5
        for table in (
            "paper_orders",
            "paper_fills",
            "paper_positions",
            "paper_marks",
            "paper_ledger_entries",
            "forward_scheduler_events",
            "forward_execution_authorizations",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM decision_journal_events WHERE state='HOLD'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM decision_journal_health WHERE kind='UPSTOX_V3_OBSERVATION'"
            ).fetchone()[0]
            == 6
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM decision_journal_health WHERE kind='MISSING_OBSERVATION_INTERVAL'"
            ).fetchone()[0]
            == 1
        )
        imports = connection.execute(
            "SELECT import_type,source_use_status,metadata_json FROM data_imports"
        ).fetchall()
        assert len(imports) == 3
        assert all(
            r["import_type"] == "OBSERVED_STREAM" and r["source_use_status"] == "UNVERIFIED"
            for r in imports
        )
        assert all(
            json.loads(r["metadata_json"])["provider"] == "UPSTOX_V3_OBSERVE_ONLY" for r in imports
        )
        initial_quotes = [
            tuple(r) for r in connection.execute("SELECT * FROM forward_quotes ORDER BY sequence")
        ]
    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    assert restored.observation_only
    assert reconcile_paper_account(database, runtime.account_id)["status"] == "RECONCILED"
    report = get_paper_account_report(database, runtime.account_id)
    assert Decimal(report["available_cash"]) == Decimal(10000) and report["positions"] == []
    # Public execution paths cannot be enabled by passing an observe-only runtime
    # into the existing synthetic strategy runner or by constructing a proposal.
    proposal = DecisionProposal("observe-guard", instrument, "HOLD", 0, entry_price=Decimal(100))
    with pytest.raises(ForwardRuntimeError, match="Observation-only"):
        restored.route_event("no-scheduler-event", proposal, None)
    with pytest.raises(ForwardRuntimeError, match="Observation-only"):
        restored.build_mock_proposal("no-scheduler-event", "BUY", 1)
    with pytest.raises(ForwardRuntimeError, match="Observation-only"):
        restored.execution_snapshot(
            instrument, Quote(instrument, START.isoformat(), 1, Decimal(100), 1, "x")
        )
    with pytest.raises(ForwardRuntimeError):
        restored.build_sma_proposal("no-scheduler-event", fast_window=2, slow_window=3)
    with pytest.raises(ForwardRuntimeError, match="Observation-only"):
        restored.schedule_event(instrument, "1m", START.isoformat())
    with closing(get_readonly_connection(database)) as connection:
        assert [
            tuple(r) for r in connection.execute("SELECT * FROM forward_quotes ORDER BY sequence")
        ] == initial_quotes
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_scheduler_events").fetchone()[0] == 0
        )
