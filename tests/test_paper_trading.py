"""Focused simulated-only paper ledger, risk, and recovery tests."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal, getcontext, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis.backtesting import zero_cost_assumptions
from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.market_quality import analyze_dataset, load_calendar_registry
from aegis.paper_trading import (
    ExecutionSnapshot,
    PaperOrderRequest,
    PaperRiskHaltReason,
    PaperTradingError,
    RiskLimits,
    create_paper_account,
    get_paper_account_report,
    halt_blocks_order,
    reconcile_paper_account,
    submit_paper_order,
)

_LOCAL = ZoneInfo("Asia/Kolkata")
_DAY = date(2026, 1, 5)


def _risk(**changes) -> RiskLimits:
    values = {
        "max_order_value": Decimal(100000),
        "max_position_quantity": 100,
        "max_instrument_exposure": Decimal(100000),
        "max_portfolio_exposure": Decimal(100000),
        "max_daily_loss": Decimal(10000),
        "max_market_data_age_seconds": 60,
    }
    values.update(changes)
    return RiskLimits(**values)


def _calendar(path: Path):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {"potential_after_missing": 1, "stale_after_missing": 3},
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": _DAY.isoformat(),
                        "coverage_end": _DAY.isoformat(),
                        "source": "Synthetic isolated test calendar",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:20"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_calendar_registry(path)


def _import_candle(database_path: Path, tmp_path: Path, minute: int, price: int):
    timestamp = datetime(2026, 1, 5, 9, minute, tzinfo=_LOCAL).isoformat()
    csv_path = tmp_path / f"candle_{minute}.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        f"{timestamp},{price},{price + 1},{price - 1},{price},1000\n",
        encoding="utf-8",
    )
    result = import_ohlcv_csv(database_path, csv_path, "NSE", "TEST", "1m")
    assert result.status == "COMPLETED"
    declare_import_source_use_status(
        database_path,
        result.import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic test fixture",
        rationale="Synthetic-only test evidence; never market-performance evidence.",
    )
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE data_imports SET completed_at = ? WHERE id = ?",
            (timestamp, result.import_id),
        )
        connection.commit()
    return result.import_id, timestamp


@pytest.fixture
def setup(tmp_path: Path):
    database_path = tmp_path / "paper_test.db"
    initialize_database(database_path)
    instrument = register_instrument(
        database_path,
        InstrumentInput(
            "NSE",
            "TEST",
            "Synthetic Test Equity",
            "EQUITY",
            segment="NSE",
            tick_size=Decimal("0.05"),
        ),
    )
    imports = [_import_candle(database_path, tmp_path, 15, 100)]
    imports.append(_import_candle(database_path, tmp_path, 16, 100))
    calendar_path = tmp_path / "calendar.json"
    calendars = _calendar(calendar_path)
    account_id = create_paper_account(
        database_path,
        name="isolated test paper account",
        initial_cash=Decimal("1000.00"),
        risk_limits=_risk(),
        costs=zero_cost_assumptions(),
        created_at=datetime(2026, 1, 5, 9, 15, tzinfo=_LOCAL).isoformat(),
    )
    return database_path, tmp_path, instrument.id, imports, calendars, account_id


def _snapshot(database_path, instrument_id, import_id, timestamp, as_of, calendars, price):
    parsed = datetime.fromisoformat(as_of)
    report = analyze_dataset(
        database_path,
        "NSE",
        "TEST",
        "1m",
        _DAY,
        _DAY,
        as_of=parsed,
        calendars=calendars,
    )
    return ExecutionSnapshot(
        instrument_id=instrument_id,
        interval="1m",
        timestamp=timestamp,
        as_of=as_of,
        reference_price=Decimal(price),
        price_field="execution_quote",
        available_quantity=100,
        import_id=import_id,
        quality=report,
    )


def _buy(setup, *, key="buy-1", quantity=2, source_use_status="VERIFIED_PERMITTED"):
    database_path, _, instrument_id, imports, calendars, account_id = setup
    with closing(get_connection(database_path)) as connection:
        order_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
        )
        existing = connection.execute(
            "SELECT 1 FROM paper_orders WHERE account_id = ? AND idempotency_key = ?",
            (account_id, key),
        ).fetchone()
    if existing is not None:
        import_id = imports[1][0]
        ts = "2026-01-05T09:17:00+05:30"
        as_of = ts
    else:
        candle_minute = 17 + order_count
        import_id, _ = _import_candle(database_path, setup[1], candle_minute, 100)
        execution_minute = candle_minute + 1
        ts = f"2026-01-05T09:{execution_minute:02d}:00+05:30"
        as_of = ts
    if source_use_status != "VERIFIED_PERMITTED":
        with closing(get_connection(database_path)) as connection:
            connection.execute(
                "UPDATE data_imports SET source_use_status = ? WHERE id = ?",
                (source_use_status, import_id),
            )
            connection.commit()
    snapshot = _snapshot(
        database_path,
        instrument_id,
        import_id,
        ts,
        as_of,
        calendars,
        "100",
    )
    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        quantity,
        datetime(2026, 1, 5, 9, 15, tzinfo=_LOCAL).isoformat(),
        datetime(2026, 1, 5, 9, 15, 30, tzinfo=_LOCAL).isoformat(),
    )
    return submit_paper_order(database_path, account_id, key, request, snapshot)


def _sell(setup, *, key="sell-reduce", quantity=1, minute=17, price="100"):
    database_path, tmp_path, instrument_id, _, calendars, account_id = setup
    with closing(get_connection(database_path)) as connection:
        order_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
        )
    candle_minute = max(minute, 17 + order_count)
    for missing_minute in range(17, candle_minute):
        candle_time = f"2026-01-05T03:{missing_minute + 30:02d}:00Z"
        with closing(get_connection(database_path)) as connection:
            exists = connection.execute(
                """SELECT 1 FROM historical_candles
                   WHERE instrument_id = ? AND interval = '1m' AND candle_timestamp = ?""",
                (instrument_id, candle_time),
            ).fetchone()
        if exists is None:
            _import_candle(database_path, tmp_path, missing_minute, int(price))
    import_id, _ = _import_candle(database_path, tmp_path, candle_minute, int(price))
    execution_minute = candle_minute + 1
    timestamp = f"2026-01-05T09:{execution_minute:02d}:00+05:30"
    snapshot = _snapshot(
        database_path,
        instrument_id,
        import_id,
        timestamp,
        timestamp,
        calendars,
        price,
    )
    request = PaperOrderRequest(
        instrument_id,
        "SELL",
        quantity,
        "2026-01-05T09:15:00+05:30",
        f"2026-01-05T09:{minute:02d}:30+05:30",
    )
    return submit_paper_order(database_path, account_id, key, request, snapshot)


def _set_risk(setup, **changes):
    database_path, _, _, _, _, account_id = setup
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ? WHERE id = ?",
            (json.dumps(_risk(**changes).to_dict()), account_id),
        )
        connection.commit()


def test_paper_account_requires_explicit_safe_risk_configuration(tmp_path: Path) -> None:
    database_path = tmp_path / "risk.db"
    initialize_database(database_path)
    with pytest.raises(PaperTradingError, match="max_order_value"):
        RiskLimits(Decimal(0), 1, Decimal(1), Decimal(1), Decimal(1), 0)
    with pytest.raises(PaperTradingError, match="RiskLimits"):
        create_paper_account(
            database_path,
            name="missing risk",
            initial_cash=Decimal(100),
            risk_limits=None,  # type: ignore[arg-type]
            costs=zero_cost_assumptions(),
            created_at="2026-01-05T09:15:00+05:30",
        )


def test_malformed_persisted_risk_configuration_cannot_submit(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = '{}' WHERE id = ?", (account_id,)
        )
        connection.commit()
    with pytest.raises((KeyError, PaperTradingError)):
        _buy(setup, key="malformed-risk")
    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_buy_sell_accounting_idempotency_and_restart_reconciliation(setup) -> None:
    database_path, tmp_path, instrument_id, _imports, calendars, account_id = setup
    result = _buy(setup)
    duplicate = _buy(setup)
    assert result.status == "FILLED"
    assert result.order_id == duplicate.order_id
    assert duplicate.duplicate is True

    sell_import_id, _sell_candle_timestamp = _import_candle(database_path, tmp_path, 18, 110)
    sell_snapshot = _snapshot(
        database_path,
        instrument_id,
        sell_import_id,
        "2026-01-05T09:19:00+05:30",
        datetime(2026, 1, 5, 9, 19, tzinfo=_LOCAL).isoformat(),
        calendars,
        "110",
    )
    sell = PaperOrderRequest(
        instrument_id,
        "SELL",
        2,
        datetime(2026, 1, 5, 9, 16, tzinfo=_LOCAL).isoformat(),
        datetime(2026, 1, 5, 9, 16, 30, tzinfo=_LOCAL).isoformat(),
    )
    sell_result = submit_paper_order(database_path, account_id, "sell-1", sell, sell_snapshot)
    assert sell_result.status == "FILLED"
    assert reconcile_paper_account(database_path, account_id) == {
        "status": "RECONCILED",
        "errors": [],
    }

    report = get_paper_account_report(database_path, account_id)
    assert report["simulated"] is True
    assert report["available_cash"] == "1020"
    assert report["realized_net_pnl"] == "20"
    assert report["positions"] == []
    assert report["label"].startswith("SIMULATED")

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM paper_order_events").fetchone()[0] == 4


def test_filled_execution_snapshot_is_single_use_across_distinct_orders(setup) -> None:
    database_path, _, instrument_id, _, calendars, account_id = setup
    result = _buy(setup, key="snapshot-first")
    assert result.status == "FILLED"
    with closing(get_connection(database_path)) as connection:
        used = connection.execute(
            """SELECT u.import_id, o.execution_timestamp
               FROM paper_execution_snapshot_uses u
               JOIN paper_orders o ON o.order_id = u.order_id
               WHERE u.order_id = ?""",
            (result.order_id,),
        ).fetchone()
    snapshot = _snapshot(
        database_path,
        instrument_id,
        used["import_id"],
        used["execution_timestamp"],
        used["execution_timestamp"],
        calendars,
        "100",
    )
    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        2,
        "2026-01-05T09:15:00+05:30",
        "2026-01-05T09:15:30+05:30",
    )

    reused = submit_paper_order(database_path, account_id, "snapshot-second", request, snapshot)

    assert reused.status == "REJECTED"
    assert "already been consumed" in (reused.reason or "")


def test_integrity_halt_blocks_phase8_sell_reduction(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    assert _buy(setup, key="integrity-position").status == "FILLED"
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """UPDATE paper_accounts SET halted = 1, halt_class = 'INTEGRITY',
               halt_reason = 'Checksum integrity halt.' WHERE id = ?""",
            (account_id,),
        )
        connection.commit()

    result = _sell(setup, key="integrity-sell")

    assert result.status == "REJECTED"
    assert result.reason == "Checksum integrity halt."
    assert get_paper_account_report(database_path, account_id)["positions"][0]["quantity"] == 2


def test_additional_buy_counts_existing_position_once_in_exposure_and_cash(setup) -> None:
    first = _buy(setup, quantity=2)
    additional = _buy(setup, key="add-to-position", quantity=2)

    assert first.status == "FILLED"
    assert additional.status == "FILLED"
    report = get_paper_account_report(setup[0], setup[5])
    assert report["positions"] == [
        {
            "exchange": "NSE",
            "symbol": "TEST",
            "quantity": 4,
            "average_entry_price": "100",
            "mark_price": "100",
            "mark_timestamp": "2026-01-05T03:49:00Z",
            "realized_net_pnl": "0",
            "unrealized_gross_pnl": "0",
        }
    ]
    assert report["available_cash"] == "600"
    assert report["gross_exposure"] == "400"
    assert report["equity_before_estimated_liquidation_costs"] == "1000"


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "expected_reason"),
    [
        ("max_instrument_exposure", Decimal(300), "instrument exposure"),
        ("max_portfolio_exposure", Decimal(300), "portfolio exposure"),
    ],
)
def test_additional_buy_obeys_instrument_and_portfolio_exposure_limits(
    setup, limit_name, limit_value, expected_reason
) -> None:
    _set_risk(setup, **{limit_name: limit_value})
    assert _buy(setup, quantity=2).status == "FILLED"

    result = _buy(setup, key=f"exposure-{limit_name}", quantity=2)

    assert result.status == "REJECTED"
    assert expected_reason in (result.reason or "").lower()
    report = get_paper_account_report(setup[0], setup[5])
    assert report["positions"][0]["quantity"] == 2
    assert report["available_cash"] == "800"


def test_daily_loss_projection_for_additional_buy_uses_full_new_position(setup) -> None:
    _set_risk(setup, max_daily_loss=Decimal(50))
    assert _buy(setup, quantity=2).status == "FILLED"
    database_path, _, instrument_id, imports, calendars, account_id = setup
    snapshot = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:18:00+05:30",
        "2026-01-05T09:18:00+05:30",
        calendars,
        "80",
    )
    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        1,
        "2026-01-05T09:15:00+05:30",
        "2026-01-05T09:15:30+05:30",
    )

    result = submit_paper_order(database_path, account_id, "daily-add-on", request, snapshot)

    assert result.status == "FILLED"
    report = get_paper_account_report(database_path, account_id, report_date=_DAY)
    assert report["positions"][0]["quantity"] == 3
    assert report["available_cash"] == "720"
    assert report["gross_exposure"] == "240"
    assert report["daily_report"]["net_equity_change_before_estimated_liquidation_costs"] == "-40"


def test_spread_slippage_tick_rounding_and_fees_are_recorded(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    costs = {
        "brokerage_rate": "0.001",
        "brokerage_cap_per_order": "20",
        "exchange_charge_rate": "0",
        "regulatory_charge_rate": "0",
        "transaction_tax_buy_rate": "0",
        "transaction_tax_sell_rate": "0",
        "stamp_duty_buy_rate": "0",
        "gst_rate": "0",
        "spread_basis_points": "2",
        "slippage_basis_points": "5",
    }
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET cost_assumptions_json = ? WHERE id = ?",
            (json.dumps(costs), account_id),
        )
        connection.commit()
    result = _buy(setup)
    assert result.status == "FILLED"
    report = get_paper_account_report(database_path, account_id)
    assert report["available_cash"] == "799.6"
    order = report["orders"][0]
    assert order["simulated_fill_price"] == "100.1"
    assert order["simulated_fees"]["brokerage"] == "0.2"
    assert order["simulated_execution_costs"] == {
        "adverse_tick_rounding_cost": "0.08",
        "slippage_cost": "0.1",
        "spread_cost": "0.02",
    }


def test_idempotency_key_conflict_does_not_create_another_order(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    _buy(setup)
    request = PaperOrderRequest(
        setup[2],
        "BUY",
        3,
        "2026-01-05T09:15:00+05:30",
        "2026-01-05T09:15:30+05:30",
    )
    snapshot = _snapshot(
        database_path,
        setup[2],
        setup[3][1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:17:00+05:30",
        setup[4],
        "100",
    )
    with pytest.raises(PaperTradingError, match="reused"):
        submit_paper_order(database_path, account_id, "buy-1", request, snapshot)


def test_risk_limit_rejects_and_halts_new_entries(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ? WHERE id = ?",
            (
                json.dumps(_risk(max_order_value=Decimal(100)).to_dict()),
                account_id,
            ),
        )
        connection.commit()
    result = _buy(setup, key="oversize", quantity=2)
    assert result.status == "REJECTED"
    assert "Maximum order value" in (result.reason or "")
    report = get_paper_account_report(database_path, account_id)
    assert report["status"] == "HALTED"
    assert report["available_cash"] == "1000"
    assert report["positions"] == []


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "message"),
    [
        ("max_position_quantity", 1, "position quantity"),
        ("max_instrument_exposure", Decimal(100), "instrument exposure"),
        ("max_portfolio_exposure", Decimal(100), "portfolio exposure"),
    ],
)
def test_position_and_exposure_limits_reject_and_halt(
    setup, limit_name, limit_value, message
) -> None:
    database_path, _, _, _, _, account_id = setup
    limits = _risk(**{limit_name: limit_value})
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ? WHERE id = ?",
            (json.dumps(limits.to_dict()), account_id),
        )
        connection.commit()
    result = _buy(setup, key=f"{limit_name}-breach", quantity=2)
    assert result.status == "REJECTED"
    assert message in (result.reason or "").lower()
    assert get_paper_account_report(database_path, account_id)["status"] == "HALTED"


def test_daily_loss_from_cost_assumptions_halts_entry(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ?, cost_assumptions_json = ? WHERE id = ?",
            (
                json.dumps(_risk(max_daily_loss=Decimal("0.01")).to_dict()),
                json.dumps(
                    {
                        **zero_cost_assumptions().to_dict(),
                        "slippage_basis_points": "10",
                    }
                ),
                account_id,
            ),
        )
        connection.commit()
    result = _buy(setup, key="daily-loss", quantity=2)
    assert result.status == "REJECTED"
    assert "daily loss" in (result.reason or "").lower()
    assert get_paper_account_report(database_path, account_id)["status"] == "HALTED"


def test_unverified_source_fails_closed(setup) -> None:
    denied = _buy(setup, key="unverified", source_use_status="UNVERIFIED")
    assert denied.status == "REJECTED"
    assert "permitted" in (denied.reason or "").lower()


def test_stale_snapshot_fails_closed(setup) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        1,
        "2026-01-05T09:15:00+05:30",
        "2026-01-05T09:15:30+05:30",
    )
    stale = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:19:00+05:30",
        calendars,
        "100",
    )
    result = submit_paper_order(database_path, account_id, "stale", request, stale)
    assert result.status == "REJECTED"
    assert "stale" in (result.reason or "").lower()


def test_sell_cannot_create_a_short_position(setup) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    snapshot = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:17:00+05:30",
        calendars,
        "100",
    )
    request = PaperOrderRequest(
        instrument_id,
        "SELL",
        1,
        "2026-01-05T09:15:00+05:30",
        "2026-01-05T09:15:30+05:30",
    )
    result = submit_paper_order(database_path, account_id, "short", request, snapshot)
    assert result.status == "REJECTED"
    assert "long-only" in (result.reason or "").lower()
    assert reconcile_paper_account(database_path, account_id)["status"] == "RECONCILED"


def test_unknown_instrument_is_persisted_as_auditable_rejection(setup) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    snapshot = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:17:00+05:30",
        calendars,
        "100",
    )
    request = PaperOrderRequest(
        999,
        "BUY",
        1,
        "2026-01-05T09:15:00+05:30",
        "2026-01-05T09:15:30+05:30",
    )
    rejected = submit_paper_order(
        database_path, account_id, "unknown-instrument", request, snapshot
    )
    assert rejected.status == "REJECTED"
    assert "unavailable" in (rejected.reason or "").lower()
    report = get_paper_account_report(database_path, account_id)
    assert report["orders"][0]["requested_instrument_id"] == 999
    assert report["orders"][0]["simulated_fill_price"] is None


def test_execution_snapshot_cannot_precede_order_submission(setup) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    snapshot = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:17:00+05:30",
        calendars,
        "100",
    )
    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        1,
        "2026-01-05T09:15:00+05:30",
        "2026-01-05T09:17:30+05:30",
    )
    result = submit_paper_order(database_path, account_id, "future-snapshot", request, snapshot)
    assert result.status == "REJECTED"
    assert "predates order submission" in (result.reason or "").lower()
    assert get_paper_account_report(database_path, account_id)["available_cash"] == "1000"


@pytest.mark.parametrize(
    ("calendar_status", "calendar_confidence"),
    [("PARTIAL", "CONFIRMED"), ("FULL", "CONFIGURED")],
)
def test_incomplete_or_unconfirmed_calendar_cannot_authorize_paper_order(
    setup, calendar_status: str, calendar_confidence: str
) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    snapshot = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:17:00+05:30",
        calendars,
        "100",
    )
    bad_report = replace(
        snapshot.quality,
        calendar_status=calendar_status,
        calendar_confidence=calendar_confidence,
    )
    invalid = ExecutionSnapshot(
        snapshot.instrument_id,
        snapshot.interval,
        snapshot.timestamp,
        snapshot.as_of,
        snapshot.reference_price,
        snapshot.price_field,
        snapshot.available_quantity,
        snapshot.import_id,
        bad_report,
    )
    request = PaperOrderRequest(
        instrument_id, "BUY", 1, "2026-01-05T09:15:00+05:30", "2026-01-05T09:15:30+05:30"
    )
    result = submit_paper_order(database_path, account_id, "bad-quality", request, invalid)
    assert result.status == "REJECTED"
    assert "calendar" in (result.reason or "").lower()
    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_marks").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT halted FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == 1
        )


def test_insufficient_execution_capacity_rejects_without_partial_fill(setup) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    snapshot = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:17:00+05:30",
        calendars,
        "100",
    )
    snapshot = ExecutionSnapshot(
        snapshot.instrument_id,
        snapshot.interval,
        snapshot.timestamp,
        snapshot.as_of,
        snapshot.reference_price,
        snapshot.price_field,
        1,
        snapshot.import_id,
        snapshot.quality,
    )
    request = PaperOrderRequest(
        instrument_id, "BUY", 2, "2026-01-05T09:15:00+05:30", "2026-01-05T09:15:30+05:30"
    )
    result = submit_paper_order(database_path, account_id, "capacity", request, snapshot)
    assert result.status == "REJECTED"
    assert "capacity" in (result.reason or "")
    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_reconciliation_detects_tampered_position_and_halts(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    _buy(setup)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_positions SET quantity = 99 WHERE account_id = ?", (account_id,)
        )
        connection.commit()
    result = reconcile_paper_account(database_path, account_id)
    assert result["status"] == "FAILED"
    assert result["errors"]
    report = get_paper_account_report(database_path, account_id)
    assert report["reconciliation"]["status"] == "FAILED"


def test_reconciliation_rejects_ledger_entry_linked_to_another_orders_fill(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    assert _buy(setup, key="first-fill").status == "FILLED"
    assert _buy(setup, key="second-fill").status == "FILLED"
    with closing(get_connection(database_path)) as connection:
        orders = connection.execute(
            "SELECT order_id FROM paper_orders ORDER BY created_at, order_id"
        ).fetchall()
        fills = connection.execute("SELECT fill_id FROM paper_fills ORDER BY order_id").fetchall()
        connection.execute(
            "UPDATE paper_ledger_entries SET fill_id = ? WHERE order_id = ?",
            (fills[1]["fill_id"], orders[0]["order_id"]),
        )
        connection.commit()

    result = reconcile_paper_account(database_path, account_id)

    assert result["status"] == "FAILED"
    assert any("belonging to their order" in error for error in result["errors"])
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT fill_id FROM paper_ledger_entries WHERE order_id = ?",
                (orders[0]["order_id"],),
            ).fetchone()[0]
            == fills[1]["fill_id"]
        )
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT halted FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == 1
        )


def test_reconciliation_rejects_order_fill_quantity_mismatch_without_repair(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    filled = _buy(setup, quantity=2)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_fills SET quantity = 3 WHERE order_id = ?", (filled.order_id,)
        )
        connection.commit()

    result = reconcile_paper_account(database_path, account_id)

    assert result["status"] == "FAILED"
    assert any("Order and fill fields disagree" in error for error in result["errors"])
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT quantity FROM paper_fills WHERE order_id = ?", (filled.order_id,)
            ).fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_ledger_entries WHERE order_id = ?", (filled.order_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT halted FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == 1
        )


def test_reconciliation_rejects_missing_ledger_entry_without_repair(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    filled = _buy(setup)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "DELETE FROM paper_ledger_entries WHERE order_id = ?", (filled.order_id,)
        )
        connection.commit()

    result = reconcile_paper_account(database_path, account_id)

    assert result["status"] == "FAILED"
    assert any("exactly one fill and ledger entry" in error for error in result["errors"])
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE order_id = ?", (filled.order_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_fills WHERE order_id = ?", (filled.order_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_ledger_entries WHERE order_id = ?", (filled.order_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT halted FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == 1
        )


def test_risk_halt_allows_only_risk_reducing_sell_and_stays_halted(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    assert _buy(setup).status == "FILLED"
    _set_risk(setup, max_order_value=Decimal(100))
    assert _buy(setup, key="risk-halt", quantity=2).status == "REJECTED"

    assert _sell(setup, key="sell-after-risk-halt").status == "FILLED"
    report = get_paper_account_report(database_path, account_id)
    assert report["status"] == "HALTED"
    assert report["positions"][0]["quantity"] == 1
    assert _buy(setup, key="buy-after-risk-halt", quantity=1).status == "REJECTED"


def test_risk_halt_rejects_sell_larger_than_held_position(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    assert _buy(setup).status == "FILLED"
    _set_risk(setup, max_order_value=Decimal(100))
    assert _buy(setup, key="risk-halt", quantity=2).status == "REJECTED"

    result = _sell(setup, key="oversell-while-halted", quantity=3)

    assert result.status == "REJECTED"
    assert "long-only" in (result.reason or "").lower()
    report = get_paper_account_report(database_path, account_id)
    assert report["status"] == "HALTED"
    assert report["positions"][0]["quantity"] == 2


@pytest.mark.parametrize(
    ("halted", "halt_class", "halt_reason", "side", "blocked"),
    [
        (0, "NONE", None, "BUY", False),
        (1, "RISK", PaperRiskHaltReason.MAXIMUM_DAILY_LOSS.value, "SELL", False),
        (1, "RISK", PaperRiskHaltReason.MAXIMUM_DAILY_LOSS.value, "BUY", True),
        (1, "RISK", "unrecognized reason", "SELL", True),
        (1, "RISK", None, "SELL", True),
        (1, "INTEGRITY", PaperRiskHaltReason.MAXIMUM_DAILY_LOSS.value, "SELL", True),
        (1, "NONE", None, "SELL", True),
        (1, "UNCLASSIFIED", "Reconciliation failure.", "SELL", True),
        (1, "UNKNOWN", "Daily loss limit.", "SELL", True),
        (0, "RISK", PaperRiskHaltReason.MAXIMUM_DAILY_LOSS.value, "SELL", True),
    ],
)
def test_halt_class_and_reason_must_be_consistent(
    halted: int, halt_class: str, halt_reason: str | None, side: str, blocked: bool
) -> None:
    assert halt_blocks_order(halted, halt_class, halt_reason, side) is blocked


@pytest.mark.parametrize(
    ("halt_class", "halt_reason"),
    [
        ("RISK", "unrecognized reason"),
        ("RISK", None),
        ("INTEGRITY", "Maximum daily loss breached."),
        ("NONE", None),
    ],
)
def test_inconsistent_persisted_halt_blocks_buy_and_sell(
    setup, halt_class: str, halt_reason: str | None
) -> None:
    database_path, _, _, _, _, account_id = setup
    assert _buy(setup).status == "FILLED"
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """UPDATE paper_accounts SET halted = 1, halt_class = ?, halt_reason = ?
               WHERE id = ?""",
            (halt_class, halt_reason, account_id),
        )
        connection.commit()

    sell = _sell(setup, key=f"inconsistent-halt-sell-{halt_class}")
    buy = _buy(setup, key=f"inconsistent-halt-buy-{halt_class}")

    assert sell.status == "REJECTED"
    assert buy.status == "REJECTED"
    with closing(get_connection(database_path)) as connection:
        account = connection.execute(
            "SELECT halted, halt_class, halt_reason FROM paper_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        assert account["halted"] == 1
        assert account["halt_class"] == halt_class
        assert account["halt_reason"] == halt_reason
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_ledger_entries").fetchone()[0] == 1


def test_daily_loss_halt_allows_valid_exit_but_blocks_new_buy(setup) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    _set_risk(setup, max_daily_loss=Decimal(1))
    assert _buy(setup).status == "FILLED"
    snapshot = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:17:00+05:30",
        calendars,
        "80",
    )
    request = PaperOrderRequest(
        instrument_id, "BUY", 1, "2026-01-05T09:15:00+05:30", "2026-01-05T09:15:30+05:30"
    )
    assert submit_paper_order(
        database_path, account_id, "daily-halt", request, snapshot
    ).status == ("REJECTED")

    assert _sell(setup, key="sell-after-daily-halt", price="80").status == "FILLED"
    report = get_paper_account_report(database_path, account_id)
    assert report["status"] == "HALTED"
    assert report["positions"][0]["quantity"] == 1
    assert _buy(setup, key="buy-after-daily-halt", quantity=1).status == "REJECTED"


def test_stale_data_halt_allows_sell_only_with_new_fresh_evidence(setup) -> None:
    database_path, _, instrument_id, imports, calendars, account_id = setup
    assert _buy(setup).status == "FILLED"
    stale = _snapshot(
        database_path,
        instrument_id,
        imports[1][0],
        "2026-01-05T09:17:00+05:30",
        "2026-01-05T09:19:00+05:30",
        calendars,
        "100",
    )
    request = PaperOrderRequest(
        instrument_id, "BUY", 1, "2026-01-05T09:15:00+05:30", "2026-01-05T09:15:30+05:30"
    )
    assert submit_paper_order(database_path, account_id, "stale-halt", request, stale).status == (
        "REJECTED"
    )

    sell = _sell(setup, key="fresh-sell-after-stale-halt", minute=18)
    assert sell.status == "FILLED", sell.reason
    report = get_paper_account_report(database_path, account_id)
    assert report["status"] == "HALTED"
    assert report["positions"][0]["quantity"] == 1
    assert _buy(setup, key="buy-after-stale-halt", quantity=1).status == "REJECTED"


def test_reconciliation_halt_blocks_buy_and_sell_without_repair(setup) -> None:
    database_path, _, _, _, _, account_id = setup
    assert _buy(setup).status == "FILLED"
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE paper_positions SET quantity = 99 WHERE account_id = ?", (account_id,)
        )
        connection.commit()
    assert reconcile_paper_account(database_path, account_id)["status"] == "FAILED"

    sell = _sell(setup, key="sell-during-reconciliation-halt")
    buy = _buy(setup, key="buy-during-reconciliation-halt", quantity=1)

    assert sell.status == "REJECTED"
    assert "reconciliation" in (sell.reason or "").lower()
    assert buy.status == "REJECTED"
    report = get_paper_account_report(database_path, account_id)
    assert report["status"] == "HALTED"
    assert report["positions"][0]["quantity"] == 99


def test_failed_fill_transaction_rolls_back_order_fill_cash_and_position(
    setup, monkeypatch
) -> None:
    import aegis.paper_trading as paper

    database_path, _, _, _, _, account_id = setup

    def fail(*args, **kwargs):
        raise RuntimeError("injected ledger projection failure")

    monkeypatch.setattr(paper, "_apply_position_fill", fail)
    with pytest.raises(RuntimeError, match="injected"):
        _buy(setup)
    with closing(get_connection(database_path)) as connection:
        for table in (
            "paper_orders",
            "paper_fills",
            "paper_ledger_entries",
            "paper_positions",
            "paper_marks",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT halted FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == 0
        )


def test_public_accounting_is_independent_of_ambient_decimal_context(setup) -> None:
    before = (getcontext().prec, getcontext().rounding, dict(getcontext().traps))
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_DOWN
        _buy(setup, quantity=2)
        report = get_paper_account_report(setup[0], setup[5])
        assert report["available_cash"] == "800"
    after = (getcontext().prec, getcontext().rounding, dict(getcontext().traps))
    assert after == before
