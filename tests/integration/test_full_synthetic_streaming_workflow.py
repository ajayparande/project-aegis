"""Fresh-database synthetic streaming-to-paper integration test."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from aegis.backtesting import zero_cost_assumptions
from aegis.database import SCHEMA_VERSION, get_connection, get_database_status, initialize_database
from aegis.forward_runtime import (
    DeterministicSyntheticQuoteProvider,
    ForwardRuntimeService,
)
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_quality import load_calendar_registry
from aegis.paper_trading import (
    RiskLimits,
    create_paper_account,
    get_paper_account_report,
    reconcile_paper_account,
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
    expected_mark_time = quotes[5].received_at.replace("+00:00", "Z")
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
