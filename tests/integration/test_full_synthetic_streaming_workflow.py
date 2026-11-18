"""Fresh-database synthetic streaming-to-paper integration test."""

from __future__ import annotations

import json
import socket
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from streamlit.testing.v1 import AppTest

from aegis import decision_journal as journal
from aegis.backtesting import zero_cost_assumptions
from aegis.database import SCHEMA_VERSION, get_connection, get_database_status, initialize_database
from aegis.forward_runtime import (
    DeterministicSyntheticQuoteProvider,
    ForwardRuntimeError,
    ForwardRuntimeService,
    Quote,
)
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_quality import load_calendar_registry
from aegis.paper_trading import (
    RiskLimits,
    create_paper_account,
    get_paper_account_report,
    reconcile_paper_account,
)
from aegis.strategy_lab import (
    StrategyCandle,
    create_sma_crossover_spec,
    generate_sma_crossover_signals,
)

_TZ = ZoneInfo("Asia/Kolkata")
_START = datetime(2026, 1, 5, 9, 15, tzinfo=_TZ)
_APP = Path(__file__).resolve().parents[2] / "app.py"


def _calendar(path: Path):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {
                    "potential_after_missing": 1,
                    "stale_after_missing": 3,
                },
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": "2026-01-05",
                        "coverage_end": "2026-01-05",
                        "source": "SYNTHETIC_TEST_ONLY",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:30"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_calendar_registry(path)


def test_fresh_synthetic_stream_reaches_paper_ledger_and_dashboard(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "isolated" / "streaming.db"
    database.parent.mkdir()
    log_path = database.parent / "streaming.log"
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(database))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(log_path))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    initialize_database(database)
    assert get_database_status(database)["schema_version"] == SCHEMA_VERSION

    calendar_path = database.parent / "calendar.json"
    calendar = _calendar(calendar_path)
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(calendar_path))
    instrument = register_instrument(
        database,
        InstrumentInput(
            "NSE",
            "STREAM",
            "Synthetic streaming fixture",
            "EQUITY",
            segment="NSE",
            tick_size=Decimal("0.05"),
        ),
    )
    risk = RiskLimits(
        max_order_value=Decimal(10000),
        max_position_quantity=10,
        max_instrument_exposure=Decimal(10000),
        max_portfolio_exposure=Decimal(20000),
        max_daily_loss=Decimal(5000),
        max_market_data_age_seconds=60,
    )
    account_id = create_paper_account(
        database,
        name="isolated synthetic stream account",
        initial_cash=Decimal(100000),
        risk_limits=risk,
        costs=zero_cost_assumptions(),
        created_at=_START.isoformat(),
    )
    runtime = ForwardRuntimeService.create(
        database,
        "fresh-synthetic-stream",
        account_id,
        (instrument.id,),
        calendar,
        max_candidates=1,
        max_quote_delay_seconds=60,
        degraded_after_seconds=10,
        stale_after_seconds=30,
        max_execution_price_deviation=Decimal("0.05"),
        created_at=_START.isoformat(),
    )
    provider = DeterministicSyntheticQuoteProvider(
        (instrument.id,),
        start_at=_START,
        base_prices={instrument.id: Decimal(100)},
        price_steps={instrument.id: Decimal("0.05")},
    )
    quotes = list(provider.quotes(7))

    assert runtime.process_quote(quotes[0]).accepted
    entry_event = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    entry = runtime.build_mock_proposal(
        entry_event,
        "BUY",
        1,
        stop_loss=Decimal(99),
        target_price=Decimal(110),
    )
    entry_result = runtime.route_event(
        entry_event,
        entry,
        runtime.execution_snapshot(instrument.id, quotes[1]),
    )
    assert entry_result.status == "FILLED"

    oversell_event = runtime.process_quote(quotes[2]).scheduled_event_ids[0]
    oversell = runtime.build_mock_proposal(oversell_event, "SELL", 2)
    oversell_result = runtime.route_event(
        oversell_event,
        oversell,
        runtime.execution_snapshot(instrument.id, quotes[2]),
    )
    assert oversell_result.status == "REJECTED"
    assert "position" in (oversell_result.reason or "").lower()

    exit_event = runtime.process_quote(quotes[3]).scheduled_event_ids[0]
    exit_proposal = runtime.build_mock_proposal(exit_event, "SELL", 1)
    exit_result = runtime.route_event(
        exit_event,
        exit_proposal,
        runtime.execution_snapshot(instrument.id, quotes[3]),
    )
    assert exit_result.status == "FILLED"

    recovered_event = runtime.process_quote(quotes[4]).scheduled_event_ids[0]
    with closing(get_connection(database)) as connection:
        connection.execute(
            "DELETE FROM forward_scheduler_events WHERE event_id = ?", (recovered_event,)
        )
        connection.commit()
    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendar)
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE event_id = ?",
                (recovered_event,),
            ).fetchone()[0]
            == 1
        )
    recovered_entry = restored.build_mock_proposal(
        recovered_event,
        "BUY",
        1,
        stop_loss=Decimal(99),
        target_price=Decimal("100.25"),
    )
    recovered_entry_result = restored.route_event(
        recovered_event,
        recovered_entry,
        restored.execution_snapshot(instrument.id, quotes[4]),
    )
    assert recovered_entry_result.status == "FILLED"

    with closing(get_connection(database)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ? WHERE id = ?",
            (
                json.dumps(replace(risk, max_order_value=Decimal(1)).to_dict()),
                account_id,
            ),
        )
        connection.commit()
    rejected_exit = restored.process_quote(quotes[5]).exit_result
    assert rejected_exit is not None
    assert rejected_exit.status == "REJECTED"
    marked_report = get_paper_account_report(database, account_id)
    valuation = restored.portfolio_valuation()
    assert Decimal(str(valuation["cash"])) == Decimal(str(marked_report["available_cash"]))
    assert Decimal(str(valuation["market_value"])) == Decimal(str(marked_report["gross_exposure"]))
    assert Decimal(str(valuation["equity"])) == (
        Decimal(str(marked_report["available_cash"]))
        + Decimal(str(marked_report["gross_exposure"]))
    )
    assert quotes[5].received_at is not None
    expected_mark_time = quotes[5].received_at.replace("+00:00", "Z")
    assert isinstance(marked_report["positions"], list)
    assert isinstance(marked_report["reconciliation"], dict)
    assert marked_report["positions"][0]["mark_timestamp"] == expected_mark_time
    assert marked_report["reconciliation"]["status"] == "RECONCILED"

    dashboard = AppTest.from_file(str(_APP)).run(timeout=20)
    assert not dashboard.exception
    runtime_frame = next(
        item.value for item in dashboard.dataframe if "runtime_id" in item.value.columns
    )
    assert {"feed_health", "mark_freshness", "reconciliation_status"}.issubset(
        set(runtime_frame.columns)
    )
    visible_runtime = str(runtime_frame.iloc[0].to_dict())
    assert "LAST_TRADE" in visible_runtime
    assert "RECONCILED" in visible_runtime
    assert not any("submit simulated order" in item.label.lower() for item in dashboard.button)

    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendar)
    with closing(get_connection(database)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ? WHERE id = ?",
            (json.dumps(risk.to_dict()), account_id),
        )
        connection.commit()
    retried_exit = restored.process_quote(quotes[6]).exit_result
    assert retried_exit is not None
    assert retried_exit.status == "FILLED"
    assert reconcile_paper_account(database, account_id) == {
        "status": "RECONCILED",
        "errors": [],
    }

    repeated = restored.route_event(
        exit_event,
        exit_proposal,
        restored.execution_snapshot(instrument.id, quotes[3]),
    )
    assert repeated.duplicate
    report = get_paper_account_report(database, account_id)
    assert report["available_cash"] == "100000.2"
    assert report["positions"] == []
    assert isinstance(report["reconciliation"], dict)
    assert report["reconciliation"]["status"] == "RECONCILED"

    with closing(get_connection(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted = 1").fetchone()[
                0
            ]
            == 7
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM historical_candles WHERE interval = '1m'"
            ).fetchone()[0]
            == 6
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_fills WHERE order_id IN "
                "(SELECT order_id FROM paper_orders WHERE account_id = ?)",
                (account_id,),
            ).fetchone()[0]
            == 4
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ?",
                (account_id,),
            ).fetchone()[0]
            == 6
        )

    assert any(
        "Synthetic Forward Runtime — Read Only" in item.value for item in dashboard.subheader
    )
    assert any("DETERMINISTIC_SYNTHETIC" in str(item.value) for item in dashboard.dataframe)
    assert {item.label: item.value for item in dashboard.metric}["Application Mode"] == (
        "RESEARCH_ONLY"
    )
    assert not any("submit simulated order" in item.label.lower() for item in dashboard.button)
    assert database.is_relative_to(tmp_path)
    assert log_path.is_relative_to(tmp_path)


# E2E-1: compose production services in tests; no alternative execution engine.
_PRICES = (100, 100, 99, 98, 101, 103, 102, 98, 97, 97)


@pytest.fixture
def sma_session(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("External connections prohibited in E2E-1")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    db = tmp_path / "sma-e2e.db"
    initialize_database(db)
    path = tmp_path / "sma-calendar.json"
    _calendar(path)
    config = json.loads(path.read_text())
    config["calendars"][0]["sessions"][0]["close"] = "11:30"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(path))
    calendar = load_calendar_registry(path)
    instrument = register_instrument(
        db,
        InstrumentInput(
            "NSE", "E2ESMA", "Synthetic SMA", "EQUITY", segment="NSE", tick_size=Decimal("0.05")
        ),
    )
    costs = replace(zero_cost_assumptions(), brokerage_rate=Decimal("0.001"))
    account = create_paper_account(
        db,
        name="e2e-sma",
        initial_cash=Decimal(100000),
        risk_limits=RiskLimits(
            max_order_value=Decimal(10000),
            max_position_quantity=10,
            max_instrument_exposure=Decimal(10000),
            max_portfolio_exposure=Decimal(20000),
            max_daily_loss=Decimal(5000),
            max_market_data_age_seconds=60,
        ),
        costs=costs,
        created_at=_START.isoformat(),
    )
    runtime = ForwardRuntimeService.create(
        db,
        "e2e-sma",
        account,
        (instrument.id,),
        calendar,
        max_candidates=1,
        max_quote_delay_seconds=60,
        degraded_after_seconds=10,
        stale_after_seconds=30,
        max_execution_price_deviation=Decimal("0.05"),
        created_at=_START.isoformat(),
    )
    spec = create_sma_crossover_spec(
        exchange="NSE",
        trading_symbol="E2ESMA",
        instrument_type="EQUITY",
        interval="1m",
        fast_window=2,
        slow_window=3,
        hypothesis="Synthetic crossover integration only",
    )
    journal.record_manifest(
        db,
        runtime.runtime_id,
        {
            "scope": "SYNTHETIC_E2E_1",
            "strategy_id": spec.strategy_key,
            "strategy_version": spec.version,
        },
    )
    quotes = [
        Quote(
            instrument.id,
            (_START + timedelta(minutes=n)).isoformat(),
            2 * n + 1,
            Decimal(_PRICES[n - 1] if n else price),
            1000,
            f"e2e-quote-{n}",
            (_START + timedelta(minutes=n)).isoformat(),
        )
        for n, price in enumerate(_PRICES)
    ]
    return db, calendar, runtime, spec, quotes


def _sma_proposal(session, event_id):
    db, _, runtime, spec, _ = session
    with closing(get_connection(db)) as conn:
        event = conn.execute(
            "SELECT * FROM forward_scheduler_events WHERE event_id=?", (event_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT * FROM forward_runtime_candles WHERE runtime_id=? AND instrument_id=? AND interval='1m' AND completed_at<=? ORDER BY candle_timestamp",
            (runtime.runtime_id, event["instrument_id"], event["decision_timestamp"]),
        ).fetchall()
    evidence = runtime.prepare_evidence(
        event["instrument_id"],
        "1m",
        event["decision_timestamp"],
        event_id=event_id,
        proposal_id=event["proposal_id"],
    )
    if (
        evidence.quality.overall_status != "COMPLETE"
        or evidence.quality.calendar_confidence != "CONFIRMED"
    ):
        raise ForwardRuntimeError("Unqualified SMA decision opportunity")
    if len(rows) < spec.minimum_history_candles:
        journal.append_decision(
            db,
            journal.DecisionEvent(
                run_id=runtime.runtime_id,
                event_id=event_id,
                instrument_id=event["instrument_id"],
                strategy_id=spec.strategy_key,
                strategy_version=spec.version,
                interval=spec.interval,
                decision_at=event["decision_timestamp"],
                information_cutoff=event["decision_timestamp"],
                state="NO_DECISION",
                reason_codes=("INSUFFICIENT_HISTORY",),
                evidence_references=(
                    f"scheduler:{event_id}",
                    f"import:{evidence.source_import_id}",
                    f"candle:{event['candle_timestamp']}",
                ),
                source_import_id=evidence.source_import_id,
                source_use_status="VERIFIED_PERMITTED",
                calendar_confidence=evidence.quality.calendar_confidence,
                calendar_fingerprint=runtime.calendars.configuration_sha256,
                paper_account_id=runtime.account_id,
            ),
        )
        return None, len(rows)
    candles = [
        StrategyCandle(
            r["candle_timestamp"],
            *(
                Decimal(r[key])
                for key in ("open_decimal", "high_decimal", "low_decimal", "close_decimal")
            ),
            r["volume"],
        )
        for r in rows
    ]
    signals = generate_sma_crossover_signals(
        candles, fast_window=spec.fast_window, slow_window=spec.slow_window
    )
    action = "HOLD"
    if signals and signals[-1].timestamp == candles[-1].timestamp:
        action = {"ENTER_LONG": "BUY", "EXIT_LONG": "SELL"}[signals[-1].action]
    proposal = runtime.build_mock_proposal(event_id, action, 0 if action == "HOLD" else 2)
    return replace(
        proposal,
        source=f"{spec.strategy_key}:{spec.version}",
        rationale="Deterministic SMA over qualified completed synthetic candles",
    ), len(candles)


def _process_minute(session, n):
    _, _, runtime, _, quotes = session
    exit_result = None
    if n:
        close = Quote(
            quotes[n].instrument_id,
            (_START + timedelta(minutes=n, seconds=-30)).isoformat(),
            2 * n,
            Decimal(_PRICES[n - 1]),
            1000,
            f"close-{n - 1}",
        )
        exit_result = runtime.process_quote(close).exit_result
    result = runtime.process_quote(quotes[n])
    return replace(result, exit_result=exit_result or result.exit_result)


def _entry_ready(session):
    _, _, runtime, _, quotes = session
    for n, quote in enumerate(quotes[:6]):
        result = _process_minute(session, n)
        if n:
            event = result.scheduled_event_ids[0]
            proposal, history = _sma_proposal(session, event)
            if proposal is None:
                assert history < session[3].minimum_history_candles
                continue
            if proposal.action.value == "BUY":
                assert history == 5
                return event, proposal, runtime.execution_snapshot(quote.instrument_id, quote)
            assert proposal.action.value == "HOLD"
            assert (
                runtime.route_event(
                    event, proposal, runtime.execution_snapshot(quote.instrument_id, quote)
                ).status
                == "HOLD"
            )
    raise AssertionError("Synthetic sequence must produce SMA BUY")


def _count(db, table):
    with closing(get_connection(db)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_e2e1_sma_lifecycle_accounting_replay_and_horizons(sma_session, tmp_path):
    db, calendar, runtime, _, quotes = sma_session
    entry_event, proposal, snapshot = _entry_ready(sma_session)
    assert _count(db, "paper_orders") == 0
    buy = runtime.route_event(entry_event, proposal, snapshot)
    assert buy.status == "FILLED", buy.reason
    # Independent arithmetic: 2*101 + round(202*0.001, paise) = 202.20.
    buy_notional = Decimal(2) * Decimal(101)
    buy_fee = (buy_notional * Decimal("0.001")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    after_buy = get_paper_account_report(db, runtime.account_id)
    assert Decimal(after_buy["available_cash"]) == Decimal(100000) - buy_notional - buy_fee
    assert after_buy["positions"][0]["quantity"] == 2
    assert runtime.process_quote(quotes[5]).duplicate
    assert (
        runtime.schedule_event(
            quotes[5].instrument_id,
            "1m",
            (_START + timedelta(minutes=4))
            .astimezone(ZoneInfo("UTC"))
            .isoformat()
            .replace("+00:00", "Z"),
        )
        == entry_event
    )
    assert runtime.route_event(entry_event, proposal, snapshot).duplicate
    runtime = ForwardRuntimeService.open(db, runtime.runtime_id, calendar)
    assert runtime.route_event(entry_event, proposal, snapshot).duplicate
    session = (db, calendar, runtime, sma_session[3], quotes)
    sequence = ["BUY"]
    sell = None
    for quote in quotes[6:9]:
        event = _process_minute(session, quotes.index(quote)).scheduled_event_ids[0]
        decision, _ = _sma_proposal(session, event)
        sequence.append(decision.action.value)
        result = runtime.route_event(
            event, decision, runtime.execution_snapshot(quote.instrument_id, quote)
        )
        assert result.status == ("HOLD" if decision.action.value == "HOLD" else "FILLED"), (
            result.reason
        )
        if decision.action.value == "SELL":
            sell = result
    assert sequence == ["BUY", "HOLD", "HOLD", "SELL"]
    assert sell is not None
    sell_notional = Decimal(2) * Decimal(98)
    sell_fee = (sell_notional * Decimal("0.001")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pnl = sell_notional - sell_fee - buy_notional - buy_fee
    final = get_paper_account_report(db, runtime.account_id)
    assert Decimal(final["realized_net_pnl"]) == pnl == Decimal("-6.40")
    assert Decimal(final["available_cash"]) == Decimal("99993.60")
    assert Decimal(final["equity_before_estimated_liquidation_costs"]) == Decimal("99993.60")
    assert final["positions"] == []
    assert reconcile_paper_account(db, runtime.account_id)["status"] == "RECONCILED"
    assert journal.reconcile_journal(db, runtime.runtime_id)["status"] == "RECONCILED"
    records = journal.list_decisions(db, runtime.runtime_id)
    assert [r["decision"]["state"] for r in records] == [
        "NO_DECISION",
        "NO_DECISION",
        "NO_DECISION",
        "HOLD",
        "BUY",
        "HOLD",
        "HOLD",
        "SELL",
    ]
    assert len(records) == 8 and _count(db, "paper_orders") == _count(db, "paper_fills") == 2
    assert len({r["decision_id"] for r in records}) == 8
    executions = [a for r in records for a in r["audits"] if a["stage"] == "EXECUTION"]
    assert {a["order_id"] for a in executions} == {buy.order_id, sell.order_id}
    assert all(
        a["fill_id"] and a["snapshot_fingerprint"] and a["quote_event_id"] for a in executions
    )
    assert [Decimal(a["execution_reference_price"]) for a in executions] == [
        Decimal(101),
        Decimal(98),
    ]
    assert all(
        r["decision"]["reference_price"] is None and r["decision"]["candle_completed_at"] is None
        for r in records
    )
    with closing(get_connection(db)) as conn:
        fills = conn.execute(
            "SELECT quantity, fill_price, fees_json FROM paper_fills ORDER BY execution_timestamp"
        ).fetchall()
        assert [Decimal(f["fill_price"]) for f in fills] == [Decimal(101), Decimal(98)]
        assert [Decimal(json.loads(f["fees_json"])["brokerage"]) for f in fills] == [
            buy_fee,
            sell_fee,
        ]
        assert all(f["quantity"] == 2 for f in fills)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE decision_journal_events SET state='SELL'")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM forward_execution_authorizations WHERE status='CONSUMED'"
            ).fetchone()[0]
            == 2
        )
    original = json.dumps(records, sort_keys=True)
    identity = next(r["decision_id"] for r in records if r["decision"]["state"] == "BUY")
    for horizon in (5, 15, 60):
        with pytest.raises(journal.JournalError, match="not elapsed|cannot be resolved"):
            journal.evaluate_outcome(
                db, identity, horizon_minutes=horizon, evaluated_at="2026-01-05T03:51:00Z"
            )
    # Future observations arrive through the same runtime, without retrospective orders.
    for n in range(9, 70):
        runtime.process_quote(
            Quote(
                quotes[0].instrument_id,
                (_START + timedelta(minutes=n)).isoformat(),
                2 * n + 1,
                Decimal(98),
                1000,
                f"future-{n}",
            )
        )
    for horizon in (5, 15, 60):
        outcome = journal.evaluate_outcome(
            db, identity, horizon_minutes=horizon, evaluated_at="2026-01-05T05:00:00Z"
        )
        assert (
            journal.evaluate_outcome(
                db, identity, horizon_minutes=horizon, evaluated_at="2026-01-05T05:00:00Z"
            )
            == outcome
        )
        with closing(get_connection(db)) as conn:
            payload = json.loads(
                conn.execute(
                    "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?",
                    (outcome,),
                ).fetchone()[0]
            )
        assert payload["coverage_status"] == "UNAVAILABLE" and payload["observed_return"] is None
    assert _count(db, "decision_journal_outcomes") == 3
    assert json.dumps(journal.list_decisions(db, runtime.runtime_id), sort_keys=True) == original
    (tmp_path / "e2e1-evidence.json").write_text(
        json.dumps(
            {
                "sequence": sequence,
                "buy": buy.order_id,
                "sell": sell.order_id,
                "decision_ids": [r["decision_id"] for r in records],
                "starting_cash": "100000",
                "buy_price": "101",
                "sell_price": "98",
                "buy_fee": str(buy_fee),
                "sell_fee": str(sell_fee),
                "pnl": str(pnl),
                "ending_cash": final["available_cash"],
                "reconciliation": "RECONCILED",
            },
            indent=2,
        )
    )


@pytest.mark.parametrize("failure", ["journal", "rollback", "stale", "snapshot", "expired", "risk"])
def test_e2e1_buy_failures_preserve_financial_boundary(sma_session, failure):
    db, _, runtime, _, _quotes = sma_session
    event, proposal, snapshot = _entry_ready(sma_session)
    with closing(get_connection(db)) as conn:
        if failure == "journal":
            conn.execute(
                "CREATE TRIGGER e2e_failure BEFORE INSERT ON decision_journal_events WHEN NEW.state='BUY' BEGIN SELECT RAISE(ABORT,'synthetic journal failure'); END"
            )
        if failure == "rollback":
            conn.execute(
                "CREATE TRIGGER e2e_failure BEFORE INSERT ON paper_fills BEGIN SELECT RAISE(ABORT,'synthetic interrupted fill'); END"
            )
        if failure == "risk":
            limits = json.loads(
                conn.execute("SELECT risk_limits_json FROM paper_accounts").fetchone()[0]
            )
            limits["max_order_value"] = "50"
            conn.execute("UPDATE paper_accounts SET risk_limits_json=?", (json.dumps(limits),))
        conn.commit()
    if failure == "stale":
        late = replace(
            _quotes[5],
            timestamp=(_START + timedelta(minutes=5, seconds=2)).isoformat(),
            received_at=(_START + timedelta(minutes=5, seconds=13)).isoformat(),
            sequence=_quotes[5].sequence + 1,
            event_id="delayed-execution-quote",
        )
        assert runtime.process_quote(late).accepted
        snapshot = runtime.execution_snapshot(late.instrument_id, late)
    if failure == "snapshot":
        snapshot = replace(snapshot, reference_price=Decimal(104))
    if failure == "expired":
        proposal = replace(
            proposal, expires_at=(_START + timedelta(minutes=5, microseconds=1)).isoformat()
        )
        fresh = replace(
            _quotes[5],
            timestamp=(_START + timedelta(minutes=5, seconds=2)).isoformat(),
            received_at=(_START + timedelta(minutes=5, seconds=2)).isoformat(),
            sequence=_quotes[5].sequence + 1,
            event_id="expired-execution-quote",
        )
        assert runtime.process_quote(fresh).accepted
        snapshot = runtime.execution_snapshot(fresh.instrument_id, fresh)
    if failure == "rollback":
        with pytest.raises(sqlite3.IntegrityError):
            runtime.route_event(event, proposal, snapshot)
    else:
        result = runtime.route_event(event, proposal, snapshot)
        assert result.status == "REJECTED", result
        if failure in {"stale", "expired"}:
            assert "expired" in result.reason.lower()
    if failure in {"journal", "rollback"}:
        assert _count(db, "paper_orders") == 0
    if failure == "stale":
        runtime.monitor_feed_health((_START + timedelta(minutes=8)).isoformat())
        assert any(
            r["decision"]["state"] == "NO_DECISION"
            for r in journal.list_decisions(db, runtime.runtime_id)
        )
    assert (
        _count(db, "paper_fills")
        == _count(db, "paper_positions")
        == _count(db, "paper_ledger_entries")
        == 0
    )
    with closing(get_connection(db)) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM forward_execution_authorizations WHERE status='CONSUMED'"
            ).fetchone()[0]
            == 0
        )
        if failure == "rollback":
            conn.execute("DROP TRIGGER e2e_failure")
            conn.commit()
    if failure == "rollback":
        assert runtime.route_event(event, proposal, snapshot).status == "FILLED"
        assert runtime.route_event(event, proposal, snapshot).duplicate
        assert _count(db, "paper_fills") == 1
    if failure in {"risk", "stale", "snapshot", "expired"}:
        assert any(
            a["status"] == "BLOCKED"
            for r in journal.list_decisions(db, runtime.runtime_id)
            for a in r["audits"]
        )


@pytest.mark.parametrize("invalid", ["source", "calendar"])
def test_e2e1_invalid_evidence_cannot_reach_sma_buy(sma_session, invalid):
    db, _, runtime, _, _ = sma_session
    event, proposal, snapshot = _entry_ready(sma_session)
    if invalid == "source":
        with closing(get_connection(db)) as conn:
            conn.execute(
                "UPDATE data_imports SET source_use_status='PROHIBITED' WHERE import_type='SYNTHETIC_STREAM'"
            )
            conn.commit()
    else:
        path = runtime.calendars.configuration_path
        config = json.loads(path.read_text())
        config["calendars"][0]["confidence"] = "CONFIGURED"
        path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        _sma_proposal(sma_session, event)
    assert runtime.route_event(event, proposal, snapshot).status == "REJECTED"
    assert _count(db, "paper_fills") == 0
    with closing(get_connection(db)) as conn:
        assert (
            conn.execute(
                "SELECT status FROM forward_scheduler_events WHERE event_id=?", (event,)
            ).fetchone()[0]
            == "REJECTED"
        )
    assert any(
        a["status"] == "BLOCKED"
        for r in journal.list_decisions(db, runtime.runtime_id)
        for a in r["audits"]
    )
    assert runtime.route_event(event, proposal, snapshot).duplicate


def test_e2e1_protective_sell_repairs_audit_without_reexecution(sma_session):
    db, calendar, runtime, _, _quotes = sma_session
    event, proposal, snapshot = _entry_ready(sma_session)
    proposal = replace(proposal, stop_loss=Decimal(100))
    assert runtime.route_event(event, proposal, snapshot).status == "FILLED"
    with closing(get_connection(db)) as conn:
        conn.execute(
            "CREATE TRIGGER e2e_exit_failure BEFORE INSERT ON decision_journal_audits WHEN NEW.stage='EXECUTION' BEGIN SELECT RAISE(ABORT,'synthetic supplemental failure'); END"
        )
        conn.commit()
    _process_minute(sma_session, 6)
    _process_minute(sma_session, 7)
    exit_result = _process_minute(sma_session, 8).exit_result
    assert exit_result is not None and exit_result.status == "FILLED"
    assert _count(db, "paper_positions") == 0 and _count(db, "paper_fills") == 2
    assert journal.reconcile_journal(db, runtime.runtime_id)["status"] == "UNRESOLVED"
    with closing(get_connection(db)) as conn:
        conn.execute("DROP TRIGGER e2e_exit_failure")
        conn.commit()
    for _ in range(2):
        runtime = ForwardRuntimeService.open(db, runtime.runtime_id, calendar)
    assert _count(db, "paper_fills") == _count(db, "paper_orders") == 2
    assert journal.reconcile_journal(db, runtime.runtime_id)["status"] == "RECONCILED"
    assert reconcile_paper_account(db, runtime.account_id)["status"] == "RECONCILED"


@pytest.mark.parametrize("history", [1, 2, 3])
def test_e2e1_insufficient_history_never_emits_action(sma_session, history, monkeypatch):
    db, calendar, runtime, spec, quotes = sma_session
    runtime.process_quote(quotes[0])

    def forbidden(*args, **kwargs):
        raise AssertionError("Warm-up must not evaluate SMA, construct a proposal or reach Phase 8")

    for n in range(1, history + 1):
        event = _process_minute(sma_session, n).scheduled_event_ids[0]
        with monkeypatch.context() as guard:
            guard.setattr(runtime, "build_mock_proposal", forbidden)
            guard.setattr(runtime, "route_event", forbidden)
            guard.setitem(globals(), "generate_sma_crossover_signals", forbidden)
            proposal, count = _sma_proposal(sma_session, event)
            assert count < spec.minimum_history_candles and proposal is None
        records = journal.list_decisions(db, runtime.runtime_id)
        assert len(records) == n
        original = records[-1]
        decision = original["decision"]
        assert decision["state"] == "NO_DECISION"
        assert decision["event_id"] == event and decision["decision_at"] == (
            _START + timedelta(minutes=n)
        ).astimezone(ZoneInfo("UTC")).isoformat(timespec="microseconds").replace("+00:00", "Z")
        assert decision["reason_codes"] == ["INSUFFICIENT_HISTORY"]
        assert decision["quantity"] is decision["confidence"] is decision["proposal_id"] is None
        assert f"scheduler:{event}" in decision["evidence_references"]
        with closing(get_connection(db)) as conn:
            row = conn.execute(
                "SELECT * FROM forward_scheduler_events WHERE event_id=?", (event,)
            ).fetchone()
            assert (
                row["proposal_binding_state"] == "UNBOUND"
                and row["proposal_json"] is None
                and row["proposal_sha256"] is None
            )
            candle = row["candle_timestamp"]
        runtime = ForwardRuntimeService.open(db, runtime.runtime_id, calendar)
        sma_session = (db, calendar, runtime, spec, quotes)
        assert runtime.schedule_event(quotes[n].instrument_id, "1m", candle) == event
        assert _sma_proposal(sma_session, event) == (None, n)
        assert journal.list_decisions(db, runtime.runtime_id) == records
    assert (
        _count(db, "paper_orders")
        == _count(db, "paper_fills")
        == _count(db, "forward_execution_authorizations")
        == 0
    )


@pytest.mark.parametrize("history", [4, 6])
def test_e2e1_qualified_no_crossover_remains_hold(sma_session, history):
    db, _, runtime, spec, quotes = sma_session
    runtime.process_quote(quotes[0])
    for n in range(1, history + 1):
        event = _process_minute(sma_session, n).scheduled_event_ids[0]
    proposal, count = _sma_proposal(sma_session, event)
    assert count == history >= spec.minimum_history_candles
    assert proposal is not None and proposal.action.value == "HOLD"
    assert (
        runtime.route_event(
            event,
            proposal,
            runtime.execution_snapshot(quotes[history].instrument_id, quotes[history]),
        ).status
        == "HOLD"
    )
    assert journal.list_decisions(db, runtime.runtime_id)[0]["decision"]["state"] == "HOLD"
    assert _count(db, "paper_orders") == 0


def test_e2e1_incomplete_candle_cannot_supply_warmup_crossover(sma_session):
    db, _, runtime, _, quotes = sma_session
    runtime.process_quote(quotes[0])
    for n in range(1, 4):
        event = _process_minute(sma_session, n).scheduled_event_ids[0]
    incomplete = replace(
        quotes[3],
        timestamp=(_START + timedelta(minutes=3, seconds=45)).isoformat(),
        received_at=(_START + timedelta(minutes=3, seconds=45)).isoformat(),
        sequence=8,
        price=Decimal(200),
        event_id="incomplete-crossover",
    )
    assert runtime.process_quote(incomplete).accepted
    apparent = [
        StrategyCandle(
            (_START + timedelta(minutes=n))
            .astimezone(ZoneInfo("UTC"))
            .isoformat()
            .replace("+00:00", "Z"),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            1000,
        )
        for n, price in enumerate((100, 100, 99, 200))
    ]
    assert (
        generate_sma_crossover_signals(apparent, fast_window=2, slow_window=3)[-1].action
        == "ENTER_LONG"
    )
    assert _sma_proposal(sma_session, event) == (None, 3)
    assert journal.list_decisions(db, runtime.runtime_id)[0]["decision"]["state"] == "NO_DECISION"
    assert _count(db, "paper_orders") == _count(db, "forward_execution_authorizations") == 0
