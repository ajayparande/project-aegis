"""Isolated synthetic integration of research and simulated paper services.

All records are generated for this test in pytest's temporary directory. The
test's source-use declaration applies only to its own synthetic CSV fixture.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from aegis.backtesting import (
    BacktestConfig,
    list_backtest_runs,
    register_backtest,
    run_backtest,
    zero_cost_assumptions,
)
from aegis.database import SCHEMA_VERSION, get_connection, get_database_status, initialize_database
from aegis.instruments import InstrumentInput, get_instrument, list_instruments, register_instrument
from aegis.market_data import (
    declare_import_source_use_status,
    import_ohlcv_csv,
    list_recent_imports,
)
from aegis.market_quality import analyze_dataset, load_calendar_registry
from aegis.paper_trading import (
    ExecutionSnapshot,
    PaperOrderRequest,
    RiskLimits,
    create_paper_account,
    get_paper_account_report,
    reconcile_paper_account,
    submit_paper_order,
)
from aegis.strategy_lab import (
    create_sma_crossover_spec,
    register_experiment,
    run_strategy_experiment,
)

_DAY = date(2026, 1, 5)
_TZ = ZoneInfo("Asia/Kolkata")
_CLOSES = (30, 20, 10, 20, 40, 5, 4)
_APP = Path(__file__).resolve().parents[2] / "app.py"


def _calendar(path: Path):
    """Write a one-session fixture explicitly identified as non-authoritative."""
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
                        "coverage_start": _DAY.isoformat(),
                        "coverage_end": _DAY.isoformat(),
                        "source": "SYNTHETIC_TEST_ONLY; not an NSE calendar source",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:22"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_calendar_registry(path)


def _write_candles(path: Path) -> Path:
    rows = ["timestamp,open,high,low,close,volume"]
    for index, close in enumerate(_CLOSES):
        minute = 15 + index
        rows.append(
            f"2026-01-05T09:{minute:02d}:00+05:30,{close},{close + 2},{close - 2},{close},1000"
        )
    csv_path = path / "synthetic_aegis_candles.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


def _persisted_signals(database: Path, run_id: int) -> dict[str, object]:
    """Read-only retrieval because Strategy Lab currently has no run getter."""
    with closing(get_connection(database)) as connection:
        row = connection.execute("SELECT result_json FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row is not None
    result = json.loads(row["result_json"])
    return result


def test_full_synthetic_workflow_stays_isolated_and_reconciles(tmp_path: Path, monkeypatch) -> None:
    """Exercise the public research-to-paper path without routing or real data."""
    database = tmp_path / "integration" / "aegis_synthetic.db"
    database.parent.mkdir()
    log_path = database.parent / "aegis_synthetic.log"
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(database))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(log_path))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")

    # A: Fresh current schema and stable instrument identity.
    initialize_database(database)
    assert get_database_status(database)["schema_version"] == SCHEMA_VERSION
    instrument = register_instrument(
        database,
        InstrumentInput(
            "NSE",
            "SYNTH",
            "Synthetic Test Equity",
            "EQUITY",
            segment="NSE",
            tick_size=Decimal("0.05"),
        ),
    )
    assert get_instrument(database, "NSE", "SYNTH") == instrument
    assert list_instruments(database, exchange="NSE") == [instrument]

    # B: Import only generated candles; the repeated-file import is idempotent.
    csv_path = _write_candles(database.parent)
    imported = import_ohlcv_csv(database, csv_path, "NSE", "SYNTH", "1m")
    assert imported.status == "COMPLETED"
    assert imported.accepted_rows == len(_CLOSES)
    duplicate_import = import_ohlcv_csv(database, csv_path, "NSE", "SYNTH", "1m")
    assert duplicate_import.accepted_rows == 0
    assert duplicate_import.duplicate_rows == len(_CLOSES)
    declare_import_source_use_status(
        database,
        imported.import_id,
        "VERIFIED_PERMITTED",
        declared_by="isolated synthetic integration test",
        rationale="Test-generated synthetic candles; declaration is limited to this fixture.",
    )
    with closing(get_connection(database)) as connection:
        connection.execute(
            "UPDATE data_imports SET completed_at = ? WHERE id = ?",
            ("2026-01-05T09:22:00+05:30", imported.import_id),
        )
        source_status = connection.execute(
            "SELECT source_use_status FROM data_imports WHERE id = ?", (imported.import_id,)
        ).fetchone()[0]
        connection.commit()
    assert source_status == "VERIFIED_PERMITTED"

    # C: Synthetic calendar is an isolated execution fixture, not operational evidence.
    calendar_path = database.parent / "synthetic_calendar.json"
    calendars = _calendar(calendar_path)
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(calendar_path))
    quality = analyze_dataset(
        database,
        "NSE",
        "SYNTH",
        "1m",
        _DAY,
        _DAY,
        as_of=datetime(2026, 1, 5, 9, 22, tzinfo=_TZ),
        calendars=calendars,
    )
    assert quality.overall_status == "COMPLETE"
    assert quality.calendar_status == "FULL"
    assert quality.actual_candles == len(_CLOSES)
    assert quality.missing_timestamps == ()
    assert quality.last_candle_timestamp == "2026-01-05T03:51:00Z"

    # D: Strategy Lab produces and persists informational, completed-close signals.
    spec = create_sma_crossover_spec(
        exchange="NSE",
        trading_symbol="SYNTH",
        instrument_type="EQUITY",
        interval="1m",
        fast_window=2,
        slow_window=3,
        hypothesis="Synthetic integration fixture tests deterministic crossover plumbing.",
    )
    strategy = register_experiment(database, spec, start_date=_DAY, end_date=_DAY)
    as_of = datetime(2026, 1, 5, 9, 22, tzinfo=_TZ)
    strategy_run = run_strategy_experiment(
        database, strategy.experiment_id, calendars=calendars, as_of=as_of
    )
    assert strategy_run.status == "COMPLETED"
    assert [(item.action, item.timestamp) for item in strategy_run.signals] == [
        ("ENTER_LONG", "2026-01-05T03:49:00Z"),
        ("EXIT_LONG", "2026-01-05T03:51:00Z"),
    ]
    persisted = _persisted_signals(database, strategy_run.run_id)
    assert [(item["action"], item["timestamp"]) for item in persisted["signals"]] == [
        ("ENTER_LONG", "2026-01-05T03:49:00Z"),
        ("EXIT_LONG", "2026-01-05T03:51:00Z"),
    ]
    assert persisted["metadata"]["signals_are_execution_orders"] is False
    assert persisted["metadata"]["provenance_status"] == "COMPLETE"
    repeat_run = run_strategy_experiment(
        database, strategy.experiment_id, calendars=calendars, as_of=as_of
    )
    assert repeat_run.signals == strategy_run.signals
    assert repeat_run.dataset_sha256 == strategy_run.dataset_sha256

    # E: Linked backtest and public persisted report retrieval.
    backtest = register_backtest(
        database,
        strategy.experiment_id,
        BacktestConfig(Decimal(10000), 2, Decimal("0.5"), zero_cost_assumptions()),
    )
    backtest_run = run_backtest(database, backtest.experiment_id, calendars=calendars, as_of=as_of)
    assert backtest_run.status == "COMPLETED", backtest_run.reason
    reports = list_backtest_runs(database, backtest.experiment_id)
    assert reports and reports[0]["status"] == "COMPLETED"
    backtest_result = reports[0]["result"]
    assert backtest_result["metadata"]["source_imports"][0]["import_id"] == imported.import_id
    assert backtest_result["metadata"]["source_imports"][0]["source_use_status"] == (
        "VERIFIED_PERMITTED"
    )
    assert backtest_result["orders"][0]["signal_candle_timestamp"] == "2026-01-05T03:49:00Z"
    assert backtest_result["orders"][0]["execution_bar_timestamp"] == "2026-01-05T03:50:00Z"
    assert backtest_result["orders"][1]["status"] == "UNFILLED_NO_NEXT_BAR"
    assert backtest_result["summary"]["open_position_quantity"] == 2
    assert backtest_result["summary"]["ending_equity"] == "9998"

    # F/G: Test-side explicit signal bridge; paper fills use separate synthetic quotes.
    entry = next(item for item in strategy_run.signals if item.action == "ENTER_LONG")
    exit_signal = next(item for item in strategy_run.signals if item.action == "EXIT_LONG")
    assert entry.timestamp < exit_signal.timestamp
    limits = RiskLimits(
        max_order_value=Decimal(1000),
        max_position_quantity=10,
        max_instrument_exposure=Decimal(1000),
        max_portfolio_exposure=Decimal(1000),
        max_daily_loss=Decimal(1000),
        max_market_data_age_seconds=300,
    )
    account = create_paper_account(
        database,
        name="synthetic integration only",
        initial_cash=Decimal(10000),
        risk_limits=limits,
        costs=zero_cost_assumptions(),
        created_at="2026-01-05T09:15:00+05:30",
    )

    def snapshot(timestamp: str, quote: str) -> ExecutionSnapshot:
        quote_time = datetime.fromisoformat(timestamp)
        execution_quality = analyze_dataset(
            database,
            "NSE",
            "SYNTH",
            "1m",
            _DAY,
            _DAY,
            as_of=quote_time,
            calendars=calendars,
            source_import_id=imported.import_id,
        )
        return ExecutionSnapshot(
            instrument_id=instrument.id,
            interval="1m",
            timestamp=timestamp,
            as_of=timestamp,
            reference_price=Decimal(quote),
            price_field="execution_quote",
            available_quantity=100,
            import_id=imported.import_id,
            quality=execution_quality,
        )

    buy_request = PaperOrderRequest(
        instrument.id,
        "BUY",
        2,
        entry.timestamp.replace("Z", "+00:00"),
        "2026-01-05T09:22:05+05:30",
    )
    buy_snapshot = snapshot("2026-01-05T09:22:10+05:30", "40.00")
    buy_result = submit_paper_order(database, account, "synthetic-entry", buy_request, buy_snapshot)
    assert buy_result.status == "FILLED", buy_result.reason
    assert not buy_result.duplicate
    # On restart, the same request is idempotent and creates no second fill.
    initialize_database(database)
    assert get_instrument(database, "NSE", "SYNTH") == instrument
    duplicate_buy = submit_paper_order(
        database, account, "synthetic-entry", buy_request, buy_snapshot
    )
    assert duplicate_buy.order_id == buy_result.order_id
    assert duplicate_buy.duplicate

    sell_request = PaperOrderRequest(
        instrument.id,
        "SELL",
        2,
        exit_signal.timestamp.replace("Z", "+00:00"),
        "2026-01-05T09:22:15+05:30",
    )
    sell_snapshot = snapshot("2026-01-05T09:22:20+05:30", "39.00")
    sell_result = submit_paper_order(
        database, account, "synthetic-exit", sell_request, sell_snapshot
    )
    assert sell_result.status == "FILLED"
    report = get_paper_account_report(database, account)
    assert report["simulated"] is True
    assert report["available_cash"] == "9998"
    assert report["realized_net_pnl"] == "-2"
    assert report["positions"] == []
    assert reconcile_paper_account(database, account) == {"status": "RECONCILED", "errors": []}

    with closing(get_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM paper_ledger_entries").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2

    # H: Corrupt only a copy; reconciliation must fail closed without repairing it.
    corrupt_database = database.parent / "synthetic_corruption_copy.db"
    shutil.copyfile(database, corrupt_database)
    with closing(get_connection(corrupt_database)) as connection:
        connection.execute(
            "UPDATE paper_realized_pnl SET realized_net_pnl = '99' WHERE account_id = ?",
            (account,),
        )
        connection.commit()
    corrupted = reconcile_paper_account(corrupt_database, account)
    assert corrupted["status"] == "FAILED"
    assert corrupted["errors"]
    with closing(get_connection(corrupt_database)) as connection:
        assert (
            connection.execute(
                "SELECT realized_net_pnl FROM paper_realized_pnl WHERE account_id = ?", (account,)
            ).fetchone()[0]
            == "99"
        )
        assert (
            connection.execute(
                "SELECT halted FROM paper_accounts WHERE id = ?", (account,)
            ).fetchone()[0]
            == 1
        )

    # I: AppTest runs against the same isolated DB/log and exposes read-only reporting.
    dashboard = AppTest.from_file(str(_APP)).run(timeout=20)
    assert not dashboard.exception
    assert {item.label: item.value for item in dashboard.metric}[
        "Application Mode"
    ] == "RESEARCH_ONLY"
    assert {item.label: item.value for item in dashboard.metric}["Historical Candles"] == "7"
    assert any("Paper Simulation Reports" in item.value for item in dashboard.subheader)
    assert any("SIMULATED" in item.value for item in dashboard.warning)
    assert "Simulated available cash" in {item.label for item in dashboard.metric}
    assert not any("submit simulated order" in item.label.lower() for item in dashboard.button)

    # J: Cross-service identity/provenance and artifact isolation.
    assert get_instrument(database, "NSE", "SYNTH").id == instrument.id  # type: ignore[union-attr]
    assert list_recent_imports(database)
    assert strategy.experiment_id > 0 and backtest.experiment_id > 0
    assert database.is_relative_to(tmp_path) and log_path.is_relative_to(tmp_path)
    assert os.environ["AEGIS_MODE"] == "RESEARCH_ONLY"
    assert log_path.parent == database.parent
