"""Synthetic, isolated tests for private M2-C composition and immutable results."""

import copy
import hashlib
import inspect
import json
from collections.abc import Mapping
from contextlib import closing
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis import backtesting, portfolio_execution, portfolio_qualification
from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.portfolio import PortfolioPolicy, PortfolioRequest, SleeveRequest
from aegis.portfolio_execution import (
    _execute_registered_batch,
    _PortfolioExecutionIntegrityError,
    _PortfolioSleeveExecutionBatch,
    _qualify_and_execute_portfolio_request,
)
from aegis.strategy_lab import create_sma_crossover_spec

_DATES = tuple(
    date(2026, 1, 5) + timedelta(days=offset)
    for offset in range(15)
    if (date(2026, 1, 5) + timedelta(days=offset)).weekday() < 5
)[:11]
_PRICES = (10, 10, 10, 12, 8, 7, 13, 14, 5, 15, 20)
_EXECUTION_RESULT_FIELDS = {
    "orders",
    "fills",
    "rejected_or_unfilled_orders",
    "trades",
    "equity_curve",
    "open_position",
    "summary",
    "diagnostics",
    "buy_and_hold_benchmark",
}
_ORDER_BASE_FIELDS = {
    "order_id",
    "order_type",
    "side",
    "signal",
    "signal_candle_timestamp",
    "submitted_at",
}
_ORDER_EXECUTION_FIELDS = {
    "execution_bar_timestamp",
    "execution_time",
    "reference_open",
    "quantity",
    "prior_bar_volume",
    "volume_capacity",
}
_ORDER_PRICE_FIELDS = {
    "fill_price",
    "fees",
    "total_fees",
    "spread_cost",
    "slippage_cost",
    "tick_rounding_cost",
}
_FEE_FIELDS = {
    "brokerage",
    "exchange_charges",
    "regulatory_charges",
    "transaction_tax",
    "stamp_duty",
    "gst",
}
_SUMMARY_FIELDS = {
    "starting_cash",
    "ending_cash",
    "ending_equity",
    "net_liquidation_value",
    "absolute_pnl",
    "net_liquidation_pnl",
    "total_return",
    "net_liquidation_return",
    "realized_net_pnl",
    "unrealized_gross_pnl",
    "unrealized_net_pnl",
    "estimated_liquidation_costs",
    "total_fees",
    "filled_turnover",
    "open_position_quantity",
    "maximum_drawdown",
    "closed_trade_count",
    "winning_trades",
    "losing_trades",
    "gross_profit",
    "gross_loss",
    "net_profit_from_winning_trades",
    "net_loss_from_losing_trades",
    "win_rate",
    "profit_factor",
    "expectancy_per_closed_trade",
    "annualized_daily_volatility",
    "annualized_sharpe_zero_risk_free",
    "daily_returns",
    "open_position",
}
_DIAGNOSTIC_FIELDS = {
    "instrument",
    "interval",
    "execution_configuration",
    "signal_provenance",
    "strategy_implementation_sha256",
    "signals_sha256",
    "dataset_sha256",
    "candle_source_bindings_sha256",
    "source_file_sha256s",
    "source_imports_sha256",
    "calendar_configuration_sha256",
    "effective_calendar_configuration_sha256",
    "expected_schedule_sha256",
    "quality_report_sha256",
    "data_quality",
    "source_imports",
}
_DATA_QUALITY_FIELDS = {
    "status",
    "calendar_status",
    "calendar_confidence",
    "staleness_status",
    "completeness_percentage",
    "expected_candles",
    "actual_candles",
    "as_of",
}
_BENCHMARK_FIELDS = {
    "status",
    "method",
    "quantity",
    "entry_price",
    "exit_price",
    "fees",
    "ending_cash",
    "total_return",
    "equity_curve",
}


@pytest.fixture
def execution_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "m2c-synthetic.db"
    initialize_database(database_path)
    calendar_path = tmp_path / "approved-calendars.json"
    calendar_path.write_text(
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
                        "coverage_start": _DATES[0].isoformat(),
                        "coverage_end": _DATES[-1].isoformat(),
                        "source": "Synthetic test calendar",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "15:30"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(calendar_path))
    for symbol in ("TCS", "INFY"):
        register_instrument(
            database_path,
            InstrumentInput(
                "NSE",
                symbol,
                f"Synthetic {symbol}",
                "EQUITY",
                segment="NSE",
                lot_size=1,
                tick_size=Decimal("0.05"),
            ),
        )
        _import_symbol(database_path, tmp_path, symbol)
    return database_path, calendar_path, tmp_path


def _import_symbol(
    database_path: Path,
    tmp_path: Path,
    symbol: str,
    *,
    prices: tuple[int, ...] = _PRICES,
    volumes: tuple[int, ...] | None = None,
    declare_source_use: bool = True,
) -> None:
    rows = ["timestamp,open,high,low,close,volume"]
    chosen_volumes = volumes or (1000,) * len(prices)
    for trading_day, price, volume in zip(_DATES, prices, chosen_volumes, strict=True):
        timestamp = datetime.combine(
            trading_day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata")
        ).isoformat()
        rows.append(f"{timestamp},{price},{price + 2},{price - 2},{price},{volume}")
    csv_path = tmp_path / f"{symbol}.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    imported = import_ohlcv_csv(database_path, csv_path, "NSE", symbol, "1d")
    assert imported.status == "COMPLETED"
    assert imported.accepted_rows == len(prices)
    if declare_source_use:
        declare_import_source_use_status(
            database_path,
            imported.import_id,
            "VERIFIED_PERMITTED",
            declared_by="synthetic test user",
            rationale="Synthetic test fixture declaration only.",
        )


def _set_volumes(database_path: Path, symbol: str, volumes: tuple[int, ...]) -> None:
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """SELECT c.id FROM historical_candles AS c
               JOIN instruments AS i ON i.id = c.instrument_id
               WHERE i.exchange = 'NSE' AND i.trading_symbol = ?
               ORDER BY c.candle_timestamp""",
            (symbol,),
        ).fetchall()
        assert len(rows) == len(volumes)
        for row, volume in zip(rows, volumes, strict=True):
            connection.execute(
                "UPDATE historical_candles SET volume = ? WHERE id = ?",
                (volume, row["id"]),
            )
        connection.commit()


def _set_flat_prices(database_path: Path, symbol: str) -> None:
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """UPDATE historical_candles
               SET open_decimal = '10', high_decimal = '10',
                   low_decimal = '10', close_decimal = '10'
               WHERE instrument_id = (
                   SELECT id FROM instruments
                   WHERE exchange = 'NSE' AND trading_symbol = ?
               )""",
            (symbol,),
        )
        connection.commit()


def _request(
    *symbols: str,
    capital: str = "1000.00",
    quantity: int = 1,
    end_index: int | None = None,
) -> PortfolioRequest:
    end_date = _DATES[end_index] if end_index is not None else _DATES[-1]
    sleeves = tuple(
        SleeveRequest(
            "NSE",
            symbol,
            create_sma_crossover_spec(
                exchange="NSE",
                trading_symbol=symbol,
                instrument_type="EQUITY",
                interval="1d",
                fast_window=2,
                slow_window=3,
                hypothesis="Synthetic M2-C composition fixture.",
            ),
            quantity,
        )
        for symbol in symbols
    )
    return PortfolioRequest(
        total_capital=Decimal(capital),
        sleeves=sleeves,
        start_date=_DATES[0],
        end_date=end_date,
        as_of=datetime(
            end_date.year, end_date.month, end_date.day, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        ),
        costs=backtesting.zero_cost_assumptions(),
        max_volume_participation=Decimal("0.5"),
        policy=PortfolioPolicy(),
    )


def _qualified_batch(database_path: Path, request: PortfolioRequest):
    result = portfolio_qualification._qualify_portfolio_request(database_path, request)
    assert result.status == "QUALIFIED"
    assert result.qualified_batch is not None
    return result.qualified_batch


def _fee_exhaustion_request(*symbols: str) -> PortfolioRequest:
    costs = backtesting.CostAssumptions(
        **{
            field.name: Decimal(1) if field.name.endswith("_rate") else Decimal(0)
            for field in fields(backtesting.CostAssumptions)
        }
    )
    return replace(_request(*symbols, capital=str(72 * len(symbols))), costs=costs)


def _all_result_fields(result: backtesting.ExecutionResult) -> dict[str, object]:
    return portfolio_execution._execution_result_document(result)


def _assert_decimal_text(value: object) -> None:
    assert type(value) is str
    parsed = Decimal(value)
    assert parsed.is_finite()
    assert backtesting._decimal_text(parsed) == value


def _assert_execution_result_schema(result: backtesting.ExecutionResult) -> None:
    """Check exact output shapes from M1B's constructors and result builders."""

    assert set(_all_result_fields(result)) == _EXECUTION_RESULT_FIELDS

    for order in result.orders:
        status = order["status"]
        if "execution_bar_timestamp" not in order:
            expected = _ORDER_BASE_FIELDS | {"quantity", "status", "reason"}
        else:
            expected = _ORDER_BASE_FIELDS | _ORDER_EXECUTION_FIELDS | {"status", "reason"}
            if status == "FILLED" or "fill_price" in order:
                expected |= _ORDER_PRICE_FIELDS
            _assert_decimal_text(order["reference_open"])
            assert type(order["prior_bar_volume"]) is int
            assert type(order["volume_capacity"]) is int
        assert set(order) == expected
        assert type(order["order_id"]) is int
        assert type(order["order_type"]) is str and order["order_type"] == "MARKET"
        assert type(order["side"]) is str and order["side"] in {"BUY", "SELL"}
        assert type(order["signal"]) is str and order["signal"] in {"ENTER_LONG", "EXIT_LONG"}
        assert type(order["signal_candle_timestamp"]) is str
        assert type(order["submitted_at"]) is str
        assert type(order["quantity"]) is int
        assert type(status) is str
        assert status in {"FILLED", "REJECTED", "UNFILLED_NO_NEXT_BAR"}
        assert (order["reason"] is None) if status == "FILLED" else type(order["reason"]) is str
        if _ORDER_PRICE_FIELDS <= set(order):
            _assert_decimal_text(order["fill_price"])
            _assert_decimal_text(order["total_fees"])
            for name in ("spread_cost", "slippage_cost", "tick_rounding_cost"):
                _assert_decimal_text(order[name])
            assert set(order["fees"]) == _FEE_FIELDS
            for amount in order["fees"].values():
                _assert_decimal_text(amount)

    expected_fill_fields = {
        "order_id",
        "execution_bar_timestamp",
        "execution_time",
        "side",
        "quantity",
        "fill_price",
        "fees",
        "total_fees",
        "cash_before",
        "cash_after",
        "position_quantity_before",
        "position_quantity_after",
    }
    for fill in result.fills:
        assert set(fill) == expected_fill_fields
        assert type(fill["order_id"]) is int
        assert type(fill["quantity"]) is int
        assert type(fill["execution_bar_timestamp"]) is str
        assert type(fill["execution_time"]) is str
        assert type(fill["side"]) is str and fill["side"] in {"BUY", "SELL"}
        assert type(fill["position_quantity_before"]) is str
        assert type(fill["position_quantity_after"]) is str
        for name in ("fill_price", "total_fees", "cash_before", "cash_after"):
            _assert_decimal_text(fill[name])
        assert set(fill["fees"]) == _FEE_FIELDS
        for amount in fill["fees"].values():
            _assert_decimal_text(amount)

    for rejected in result.rejected_or_unfilled_orders:
        assert set(rejected) == {"order_id", "status", "reason"}
        assert type(rejected["order_id"]) is int
        assert type(rejected["status"]) is str
        assert rejected["status"] in {"REJECTED", "UNFILLED_NO_NEXT_BAR"}
        assert type(rejected["reason"]) is str

    expected_trade_fields = {
        "trade_id",
        "entry_order_id",
        "exit_order_id",
        "entry_time",
        "exit_time",
        "quantity",
        "entry_price",
        "exit_price",
        "gross_pnl",
        "entry_fees",
        "exit_fees",
        "net_pnl",
    }
    for trade in result.trades:
        assert set(trade) == expected_trade_fields
        for name in ("trade_id", "entry_order_id", "exit_order_id", "quantity"):
            assert type(trade[name]) is int
        for name in (
            "entry_price",
            "exit_price",
            "gross_pnl",
            "entry_fees",
            "exit_fees",
            "net_pnl",
        ):
            _assert_decimal_text(trade[name])
        assert type(trade["entry_time"]) is str
        assert type(trade["exit_time"]) is str

    for point in result.equity_curve:
        assert set(point) == {
            "timestamp",
            "local_date",
            "cash",
            "position_quantity",
            "mark_price",
            "equity",
        }
        for name in ("timestamp", "local_date", "position_quantity"):
            assert type(point[name]) is str
        for name in ("cash", "mark_price", "equity"):
            _assert_decimal_text(point[name])

    if result.open_position is not None:
        assert set(result.open_position) == {
            "quantity",
            "entry_price",
            "entry_time",
            "entry_fees",
            "mark_price",
            "unrealized_gross_pnl",
            "unrealized_net_pnl",
            "estimated_liquidation_costs",
            "net_liquidation_value",
        }
        assert type(result.open_position["quantity"]) is int
        assert type(result.open_position["entry_time"]) is str
        for name in set(result.open_position) - {"quantity", "entry_time"}:
            _assert_decimal_text(result.open_position[name])

    assert set(result.summary) == _SUMMARY_FIELDS
    count_fields = {
        "open_position_quantity",
        "closed_trade_count",
        "winning_trades",
        "losing_trades",
    }
    nullable_decimal_fields = {
        "win_rate",
        "profit_factor",
        "expectancy_per_closed_trade",
        "annualized_daily_volatility",
        "annualized_sharpe_zero_risk_free",
    }
    for name in (
        _SUMMARY_FIELDS
        - {"open_position", "daily_returns"}
        - count_fields
        - (nullable_decimal_fields)
    ):
        _assert_decimal_text(result.summary[name])
    for name in count_fields:
        assert type(result.summary[name]) is int
    for name in nullable_decimal_fields:
        value = result.summary[name]
        if value is not None:
            _assert_decimal_text(value)
    assert type(result.summary["daily_returns"]) is tuple
    for item in result.summary["daily_returns"]:
        assert set(item) == {"local_date", "return"}
        assert type(item["local_date"]) is str
        _assert_decimal_text(item["return"])
    assert result.summary["open_position"] == result.open_position
    assert result.summary["open_position"] is None or isinstance(
        result.summary["open_position"], Mapping
    )

    assert set(result.diagnostics) == _DIAGNOSTIC_FIELDS
    for name in (
        "strategy_implementation_sha256",
        "signals_sha256",
        "dataset_sha256",
        "candle_source_bindings_sha256",
        "source_imports_sha256",
        "calendar_configuration_sha256",
        "effective_calendar_configuration_sha256",
        "expected_schedule_sha256",
        "quality_report_sha256",
    ):
        value = result.diagnostics[name]
        assert type(value) is str and len(value) == 64
        assert all(character in "0123456789abcdef" for character in value)
    assert type(result.diagnostics["interval"]) is str
    instrument = result.diagnostics["instrument"]
    assert set(instrument) == {
        "instrument_id",
        "exchange",
        "trading_symbol",
        "segment",
        "instrument_type",
        "lot_size",
        "tick_size_decimal",
    }
    for name in ("instrument_id", "lot_size"):
        assert type(instrument[name]) is int
    for name in ("exchange", "trading_symbol", "segment", "instrument_type"):
        assert type(instrument[name]) is str
    _assert_decimal_text(instrument["tick_size_decimal"])
    configuration = result.diagnostics["execution_configuration"]
    assert set(configuration) == {
        "starting_cash",
        "order_quantity",
        "max_volume_participation",
        "costs",
    }
    _assert_decimal_text(configuration["starting_cash"])
    assert type(configuration["order_quantity"]) is int
    _assert_decimal_text(configuration["max_volume_participation"])
    assert set(configuration["costs"]) == {
        "brokerage_rate",
        "brokerage_cap_per_order",
        "exchange_charge_rate",
        "regulatory_charge_rate",
        "transaction_tax_buy_rate",
        "transaction_tax_sell_rate",
        "stamp_duty_buy_rate",
        "gst_rate",
        "spread_basis_points",
        "slippage_basis_points",
    }
    for amount in configuration["costs"].values():
        _assert_decimal_text(amount)
    provenance = result.diagnostics["signal_provenance"]
    assert set(provenance) == {
        "status",
        "strategy_key",
        "name",
        "version",
        "parameters",
        "strategy_spec_sha256",
    }
    for name in ("status", "strategy_key", "name", "version", "strategy_spec_sha256"):
        assert type(provenance[name]) is str
    assert set(provenance["parameters"]) == {"fast_window", "slow_window"}
    assert all(type(value) is int for value in provenance["parameters"].values())
    quality = result.diagnostics["data_quality"]
    assert set(quality) == _DATA_QUALITY_FIELDS
    for name in ("status", "calendar_status", "calendar_confidence", "staleness_status", "as_of"):
        assert type(quality[name]) is str
    if quality["completeness_percentage"] is not None:
        _assert_decimal_text(quality["completeness_percentage"])
    assert quality["expected_candles"] is None or type(quality["expected_candles"]) is int
    assert type(quality["actual_candles"]) is int
    assert type(result.diagnostics["source_file_sha256s"]) is tuple
    assert all(type(value) is str for value in result.diagnostics["source_file_sha256s"])
    assert type(result.diagnostics["source_imports"]) is tuple
    for source_import in result.diagnostics["source_imports"]:
        assert set(source_import) == {
            "import_id",
            "file_sha256",
            "instrument_id",
            "exchange",
            "trading_symbol",
            "interval",
            "import_status",
            "source_use_status",
            "declaration_id",
            "declaration_import_id",
            "declaration_previous_status",
            "declaration_new_status",
            "declared_by",
            "rationale",
            "declared_at",
        }
        for name in (
            "import_id",
            "instrument_id",
            "declaration_id",
            "declaration_import_id",
        ):
            assert type(source_import[name]) is int
        assert type(source_import["file_sha256"]) is str
        for name in set(source_import) - {
            "import_id",
            "instrument_id",
            "declaration_id",
            "declaration_import_id",
        }:
            assert type(source_import[name]) is str

    benchmark = result.buy_and_hold_benchmark
    assert type(benchmark["status"]) is str
    if benchmark["status"] == "COMPLETED":
        assert set(benchmark) == _BENCHMARK_FIELDS
        assert type(benchmark["method"]) is str
        assert type(benchmark["quantity"]) is int
        for name in ("entry_price", "exit_price", "ending_cash", "total_return"):
            _assert_decimal_text(benchmark[name])
        assert set(benchmark["fees"]) == {"entry", "exit", "total"}
        for side in ("entry", "exit"):
            assert set(benchmark["fees"][side]) == _FEE_FIELDS
            for amount in benchmark["fees"][side].values():
                _assert_decimal_text(amount)
        _assert_decimal_text(benchmark["fees"]["total"])
        assert type(benchmark["equity_curve"]) is tuple
        for point in benchmark["equity_curve"]:
            assert set(point) == {"timestamp", "equity"}
            assert type(point["timestamp"]) is str
            _assert_decimal_text(point["equity"])
    else:
        assert benchmark["status"] == "UNAVAILABLE"
        assert set(benchmark) == {"status", "reason"}
        assert type(benchmark["reason"]) is str


def _assert_recursively_immutable(value: object) -> None:
    if isinstance(value, Mapping):
        if value:
            first_key = next(iter(value))
            with pytest.raises(TypeError):
                value[first_key] = value[first_key]  # type: ignore[index]
        for item in value.values():
            _assert_recursively_immutable(item)
    elif type(value) is tuple:
        if value:
            with pytest.raises(TypeError):
                value[0] = value[0]  # type: ignore[index]
        for item in value:
            _assert_recursively_immutable(item)
    elif type(value) is list:
        pytest.fail("A retained M1B result contains a mutable list.")


def _mutate_exported_value(value: object) -> None:
    if isinstance(value, dict):
        value["__tampered__"] = "export-only"
        if value:
            _mutate_exported_value(next(iter(value.values())))
    elif isinstance(value, list):
        value.append({"__tampered__": "export-only"})
        if value:
            _mutate_exported_value(value[0])


def test_private_orchestration_reuses_m1b_and_preserves_every_order_branch(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    original = backtesting._execute_qualified_request
    observed_tokens: list[object] = []

    def observe(request, *, qualification_token):
        observed_tokens.append(qualification_token)
        return original(request, qualification_token=qualification_token)

    monkeypatch.setattr(backtesting, "_execute_qualified_request", observe)
    result = _qualify_and_execute_portfolio_request(database_path, _request("TCS"))

    assert type(result) is _PortfolioSleeveExecutionBatch
    assert [sleeve.sequence for sleeve in result.sleeves] == [0]
    assert observed_tokens == [backtesting._QUALIFICATION_TOKEN]
    execution = result.sleeves[0].result
    assert [order["status"] for order in execution.orders] == [
        "FILLED",
        "FILLED",
        "FILLED",
        "FILLED",
        "UNFILLED_NO_NEXT_BAR",
    ]
    assert [order["side"] for order in execution.orders] == [
        "BUY",
        "SELL",
        "BUY",
        "SELL",
        "BUY",
    ]
    assert len(execution.fills) == 4
    assert len(execution.trades) == 2
    assert execution.open_position is None
    assert execution.buy_and_hold_benchmark["status"] == "COMPLETED"


def test_open_position_and_unavailable_benchmark_are_valid_m1b_branches(execution_setup) -> None:
    database_path, _, _ = execution_setup
    open_result = _qualify_and_execute_portfolio_request(
        database_path, _request("TCS", end_index=4)
    )
    assert type(open_result) is _PortfolioSleeveExecutionBatch
    open_execution = open_result.sleeves[0].result
    assert open_execution.open_position is not None
    assert open_execution.open_position["quantity"] == 1
    assert open_execution.summary["open_position"] == open_execution.open_position

    unaffordable = _qualify_and_execute_portfolio_request(
        database_path, _request("INFY", capital="1.00")
    )
    assert type(unaffordable) is _PortfolioSleeveExecutionBatch
    execution = unaffordable.sleeves[0].result
    assert execution.buy_and_hold_benchmark["status"] == "UNAVAILABLE"
    assert execution.orders[0]["status"] == "REJECTED"
    assert "fill_price" in execution.orders[0]


def test_volume_and_position_rejections_are_preserved(execution_setup) -> None:
    database_path, _, _ = execution_setup
    volumes = [1000] * len(_DATES)
    volumes[5] = 0
    _set_volumes(database_path, "TCS", tuple(volumes))
    result = _qualify_and_execute_portfolio_request(database_path, _request("TCS", end_index=7))
    assert type(result) is _PortfolioSleeveExecutionBatch
    execution = result.sleeves[0].result
    assert [order["status"] for order in execution.orders] == [
        "FILLED",
        "REJECTED",
        "REJECTED",
    ]
    assert execution.orders[1]["side"] == "SELL"
    assert "fill_price" not in execution.orders[1]
    assert execution.orders[2]["reason"] == "A long position is already open."
    assert execution.open_position is not None


def test_insufficient_cash_and_no_position_exit_rejections_are_valid(execution_setup) -> None:
    database_path, _, _ = execution_setup
    result = _qualify_and_execute_portfolio_request(
        database_path,
        _request("TCS", capital="100.00", quantity=20),
    )
    assert type(result) is _PortfolioSleeveExecutionBatch
    execution = result.sleeves[0].result
    assert execution.orders[0]["status"] == "REJECTED"
    assert execution.orders[0]["reason"] == "Insufficient cash for fill and charges."
    assert "fill_price" in execution.orders[0]
    assert execution.orders[1]["reason"] == "No open long position exists."
    assert execution.buy_and_hold_benchmark["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("scenario", ["zero", "normal", "stressed", "fee_exhaustion"])
def test_cash_sign_and_nonzero_cost_scenarios_preserve_direct_m1b_parity(
    execution_setup, scenario: str
) -> None:
    database_path, calendar_path, _ = execution_setup
    request = _request("TCS")
    if scenario == "fee_exhaustion":
        request = _fee_exhaustion_request("TCS")
    elif scenario in {"normal", "stressed"}:
        costs = backtesting.default_cost_assumptions()
        if scenario == "stressed":
            costs = replace(
                costs, spread_basis_points=Decimal(20), slippage_basis_points=Decimal(50)
            )
        request = replace(request, costs=costs)
    # Match the public direct loader's research description so strategy hashes and
    # all diagnostics can be compared exactly, without discarding evidence fields.
    sleeve = request.sleeves[0]
    request = replace(
        request,
        sleeves=(
            replace(
                sleeve,
                strategy=replace(
                    sleeve.strategy,
                    hypothesis="Deterministic direct-execution baseline for qualified candles.",
                ),
            ),
        ),
    )
    direct = backtesting.execute_one_instrument(
        database_path,
        exchange="NSE",
        trading_symbol="TCS",
        interval="1d",
        start_date=request.start_date,
        end_date=request.end_date,
        fast_window=sleeve.strategy.fast_window,
        slow_window=sleeve.strategy.slow_window,
        config=backtesting.BacktestConfig(
            starting_cash=request.total_capital,
            order_quantity=sleeve.order_quantity,
            max_volume_participation=request.max_volume_participation,
            costs=request.costs,
        ),
        calendar_config_path=calendar_path,
        as_of=request.as_of,
    )
    result = _qualify_and_execute_portfolio_request(database_path, request)
    assert type(result) is _PortfolioSleeveExecutionBatch
    composed = result.sleeves[0].result
    assert _all_result_fields(composed) == _all_result_fields(direct)
    assert composed is not direct
    if scenario == "fee_exhaustion":
        sell = next(fill for fill in composed.fills if fill["side"] == "SELL")
        cash_after = Decimal(sell["cash_after"])
        assert cash_after < 0
        assert cash_after == (
            Decimal(sell["cash_before"])
            + Decimal(sell["fill_price"]) * sell["quantity"]
            - Decimal(sell["total_fees"])
        )
        assert composed.summary["ending_cash"] == sell["cash_after"]
        later_orders = [order for order in composed.orders if order["order_id"] > sell["order_id"]]
        assert all(order["status"] != "FILLED" for order in later_orders)
        assert any(
            order["side"] == "BUY"
            and order["status"] == "REJECTED"
            and order["reason"] == "Insufficient cash for fill and charges."
            for order in later_orders
        )
    else:
        assert all(Decimal(fill["cash_after"]) >= 0 for fill in composed.fills)


@pytest.mark.parametrize("cash_field", ["cash_before", "cash_after"])
@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "-Infinity", "--1", "-01", "-1.00", "-1e0", "-79", 1, True, None],
)
def test_invalid_or_inconsistent_signed_cash_rejects_later_sleeve_without_publication(
    execution_setup, monkeypatch: pytest.MonkeyPatch, cash_field: str, value: object
) -> None:
    database_path, _, _ = execution_setup
    original = backtesting._execute_qualified_request
    calls = 0

    def tamper_second(request, *, qualification_token):
        nonlocal calls
        calls += 1
        result = original(request, qualification_token=qualification_token)
        if calls == 2:
            values = _all_result_fields(result)
            sell = next(fill for fill in values["fills"] if fill["side"] == "SELL")
            sell[cash_field] = value
            return backtesting.ExecutionResult(**values)
        return result

    monkeypatch.setattr(backtesting, "_execute_qualified_request", tamper_second)
    published = None
    with pytest.raises(_PortfolioExecutionIntegrityError):
        published = _qualify_and_execute_portfolio_request(
            database_path, _fee_exhaustion_request("TCS", "INFY")
        )
    assert published is None
    assert calls == 2


def test_exact_m1b_schemas_cover_order_and_benchmark_branches(execution_setup) -> None:
    database_path, _, _ = execution_setup
    filled_and_final_bar = (
        _qualify_and_execute_portfolio_request(database_path, _request("TCS")).sleeves[0].result
    )
    _assert_execution_result_schema(filled_and_final_bar)
    assert {order["status"] for order in filled_and_final_bar.orders} == {
        "FILLED",
        "UNFILLED_NO_NEXT_BAR",
    }

    open_position = (
        _qualify_and_execute_portfolio_request(database_path, _request("TCS", end_index=4))
        .sleeves[0]
        .result
    )
    _assert_execution_result_schema(open_position)
    assert open_position.open_position is not None

    volumes = [1000] * len(_DATES)
    volumes[5] = 0
    _set_volumes(database_path, "TCS", tuple(volumes))
    volume_rejected = (
        _qualify_and_execute_portfolio_request(database_path, _request("TCS", end_index=7))
        .sleeves[0]
        .result
    )
    _assert_execution_result_schema(volume_rejected)
    assert any(
        order["status"] == "REJECTED" and "prior_bar_volume" in order and "fill_price" not in order
        for order in volume_rejected.orders
    )
    assert any(
        order["status"] == "REJECTED" and "execution_bar_timestamp" not in order
        for order in volume_rejected.orders
    )

    insufficient_cash = (
        _qualify_and_execute_portfolio_request(
            database_path, _request("TCS", capital="100.00", quantity=20)
        )
        .sleeves[0]
        .result
    )
    _assert_execution_result_schema(insufficient_cash)
    assert any(
        order["status"] == "REJECTED"
        and order["reason"] == "Insufficient cash for fill and charges."
        and "fill_price" in order
        for order in insufficient_cash.orders
    )
    assert any(
        order["reason"] == "No open long position exists." for order in insufficient_cash.orders
    )

    _set_flat_prices(database_path, "INFY")
    no_signals = (
        _qualify_and_execute_portfolio_request(database_path, _request("INFY")).sleeves[0].result
    )
    _assert_execution_result_schema(no_signals)
    assert no_signals.orders == ()

    unavailable_benchmark = (
        _qualify_and_execute_portfolio_request(database_path, _request("INFY", capital="1.00"))
        .sleeves[0]
        .result
    )
    _assert_execution_result_schema(unavailable_benchmark)
    assert unavailable_benchmark.buy_and_hold_benchmark["status"] == "UNAVAILABLE"


def test_multi_sleeve_execution_is_ordered_deterministic_and_serialization_is_detached(
    execution_setup,
) -> None:
    database_path, _, _ = execution_setup
    request = _request("INFY", "TCS")
    first = _qualify_and_execute_portfolio_request(database_path, request)
    second = _qualify_and_execute_portfolio_request(database_path, request)

    assert type(first) is _PortfolioSleeveExecutionBatch
    assert type(second) is _PortfolioSleeveExecutionBatch
    assert [item.trading_symbol for item in first.sleeves] == ["INFY", "TCS"]
    assert [item.result_sha256 for item in first.sleeves] == [
        item.result_sha256 for item in second.sleeves
    ]
    assert first.to_dict() == second.to_dict()
    assert [_all_result_fields(item.result) for item in first.sleeves] == [
        _all_result_fields(item.result) for item in second.sleeves
    ]
    serialized = first.to_dict()
    serialized["sleeves"][0]["execution_result"]["orders"][0]["status"] = "TAMPERED"
    assert first.sleeves[0].result.orders[0]["status"] == "FILLED"
    assert first.sleeves[0].result_sha256 == second.sleeves[0].result_sha256


def test_result_hash_includes_all_provenance_and_has_no_run_clock_field(
    execution_setup,
) -> None:
    database_path, _, _ = execution_setup
    request = _request("TCS")
    first = _qualify_and_execute_portfolio_request(database_path, request)
    second = _qualify_and_execute_portfolio_request(database_path, request)
    assert type(first) is _PortfolioSleeveExecutionBatch
    assert type(second) is _PortfolioSleeveExecutionBatch
    first_sleeve = first.sleeves[0]
    second_sleeve = second.sleeves[0]

    assert first_sleeve.result_sha256 == second_sleeve.result_sha256
    assert first_sleeve.provenance_sha256 == second_sleeve.provenance_sha256
    assert first_sleeve.provenance_json == second_sleeve.provenance_json
    assert portfolio_execution._execution_result_sha256(first_sleeve.result) == (
        first_sleeve.result_sha256
    )
    serialized = _all_result_fields(first_sleeve.result)
    assert set(serialized) == _EXECUTION_RESULT_FIELDS
    diagnostics = serialized["diagnostics"]
    assert "as_of" in diagnostics["data_quality"]
    assert (
        diagnostics["data_quality"]["as_of"]
        == (first_sleeve.result.diagnostics["data_quality"]["as_of"])
    )
    assert not {
        "created_at",
        "executed_at",
        "generated_at",
        "runtime_timestamp",
    } & set(diagnostics)
    assert (
        first_sleeve.result.diagnostics["source_imports"]
        == (second_sleeve.result.diagnostics["source_imports"])
    )

    changed = _all_result_fields(first_sleeve.result)
    changed["diagnostics"]["dataset_sha256"] = "f" * 64
    altered = backtesting.ExecutionResult(**changed)
    assert portfolio_execution._execution_result_sha256(altered) != (first_sleeve.result_sha256)


def test_source_import_adaptation_is_detached_and_calendar_is_shared(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    observed_requests = []
    qualifications = []
    original = backtesting._execute_qualified_request
    original_qualify = portfolio_qualification._qualify_portfolio_request

    def observe(request, *, qualification_token):
        observed_requests.append(request)
        return original(request, qualification_token=qualification_token)

    def observe_qualification(database, request):
        qualification = original_qualify(database, request)
        qualifications.append(qualification)
        return qualification

    monkeypatch.setattr(backtesting, "_execute_qualified_request", observe)
    monkeypatch.setattr(
        portfolio_qualification, "_qualify_portfolio_request", observe_qualification
    )
    batch = _qualify_and_execute_portfolio_request(database_path, _request("TCS", "INFY"))

    assert type(batch) is _PortfolioSleeveExecutionBatch
    assert len(observed_requests) == 2
    assert observed_requests[0].calendar is observed_requests[1].calendar
    qualified_batch = qualifications[0].qualified_batch
    assert qualified_batch is not None
    for request, sleeve in zip(
        observed_requests,
        qualified_batch.sleeves,
        strict=True,
    ):
        original_imports = tuple(dict(value) for value in sleeve.source_imports)
        adapted = request.eligibility.source_imports
        assert all(type(value) is dict for value in adapted)
        assert all(
            value is not source
            for value, source in zip(adapted, sleeve.source_imports, strict=True)
        )
        assert tuple(dict(value) for value in sleeve.source_imports) == original_imports


def test_paise_remainder_is_bound_to_frozen_manifest_order(execution_setup) -> None:
    database_path, _, _ = execution_setup
    result = _qualify_and_execute_portfolio_request(
        database_path, _request("INFY", "TCS", capital="100.01")
    )
    assert type(result) is _PortfolioSleeveExecutionBatch
    assert [item.trading_symbol for item in result.sleeves] == ["INFY", "TCS"]
    assert [
        item.result.diagnostics["execution_configuration"]["starting_cash"]
        for item in result.sleeves
    ] == ["50.01", "50"]


def test_blocked_qualification_is_returned_unchanged_without_m1b(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, tmp_path = execution_setup
    no_declaration_database = tmp_path / "blocked.db"
    initialize_database(no_declaration_database)
    register_instrument(
        no_declaration_database,
        InstrumentInput(
            "NSE", "TCS", "Synthetic TCS", "EQUITY", segment="NSE", tick_size=Decimal("0.05")
        ),
    )
    _import_symbol(no_declaration_database, tmp_path, "TCS", declare_source_use=False)
    blocked = portfolio_qualification._qualify_portfolio_request(
        no_declaration_database, _request("TCS")
    )
    assert blocked.status == "BLOCKED"
    monkeypatch.setattr(
        portfolio_qualification,
        "_qualify_portfolio_request",
        lambda *_args, **_kwargs: blocked,
    )

    monkeypatch.setattr(
        backtesting,
        "_execute_qualified_request",
        lambda *args, **kwargs: pytest.fail("Blocked qualification must not execute M1B."),
    )
    returned = _qualify_and_execute_portfolio_request(no_declaration_database, _request("TCS"))
    assert returned is blocked
    assert returned.status == "BLOCKED"
    assert returned.qualified_batch is None


def test_exact_registered_batch_is_required_and_no_external_result_argument_exists(
    execution_setup,
) -> None:
    database_path, _, _ = execution_setup
    batch = _qualified_batch(database_path, _request("TCS"))
    copied = copy.copy(batch)
    with pytest.raises(ValueError, match="exact coordinator-registered"):
        _execute_registered_batch(copied)
    assert tuple(inspect.signature(_qualify_and_execute_portfolio_request).parameters) == (
        "database_path",
        "request",
    )
    assert tuple(inspect.signature(_execute_registered_batch).parameters) == ("batch",)


def test_mutated_registered_batch_provenance_is_rejected(execution_setup) -> None:
    database_path, _, _ = execution_setup
    batch = _qualified_batch(database_path, _request("TCS"))
    object.__setattr__(batch, "fingerprint_sha256", "0" * 64)
    with pytest.raises(_PortfolioExecutionIntegrityError, match="contents no longer match"):
        _execute_registered_batch(batch)


def test_calendar_byte_change_blocks_before_signal_generation(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, calendar_path, _ = execution_setup
    batch = _qualified_batch(database_path, _request("TCS"))
    calendar_path.write_text(
        calendar_path.read_text(encoding="utf-8").replace(
            "Synthetic test calendar", "Modified calendar"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        backtesting,
        "generate_sma_crossover_signals",
        lambda *args, **kwargs: pytest.fail("Calendar mismatch must precede signals."),
    )
    with pytest.raises(_PortfolioExecutionIntegrityError, match="calendar bytes"):
        _execute_registered_batch(batch)


def test_calendar_change_during_sleeve_execution_prevents_publication(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, calendar_path, tmp_path = execution_setup
    alternate_path = tmp_path / "redirected-calendar.json"
    alternate_path.write_bytes(calendar_path.read_bytes())
    batch = _qualified_batch(database_path, _request("TCS"))
    original = backtesting._execute_qualified_request

    def change_after_execution(request, *, qualification_token):
        result = original(request, qualification_token=qualification_token)
        calendar_path.write_text(
            calendar_path.read_text(encoding="utf-8").replace(
                "Synthetic test calendar", "Changed during M2-C"
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(alternate_path))
        return result

    monkeypatch.setattr(backtesting, "_execute_qualified_request", change_after_execution)
    published = None
    with pytest.raises(backtesting.BacktestError, match="changed"):
        published = _execute_registered_batch(batch)
    assert published is None


def test_calendar_path_is_captured_once_and_used_for_initial_and_final_checks(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, calendar_path, tmp_path = execution_setup
    alternate_path = tmp_path / "alternate-calendar.json"
    alternate_path.write_bytes(calendar_path.read_bytes())
    batch = _qualified_batch(database_path, _request("TCS"))
    original_get_settings = portfolio_execution.get_settings
    original_load = portfolio_execution.market_quality.load_calendar_registry
    settings_paths: list[Path] = []
    loaded_paths: list[Path] = []

    changed = False

    def settings_source():
        nonlocal changed
        settings = original_get_settings()
        selected_path = alternate_path if changed else settings.calendar_config_path
        settings_paths.append(selected_path.resolve())
        return replace(settings, calendar_config_path=selected_path)

    def capture_load(path: Path):
        loaded_paths.append(path.resolve())
        return original_load(path)

    monkeypatch.setattr(portfolio_execution.market_quality, "load_calendar_registry", capture_load)
    monkeypatch.setattr(portfolio_execution, "get_settings", settings_source)
    original_executor = backtesting._execute_qualified_request

    def change_settings_after_execution(request, *, qualification_token):
        nonlocal changed
        result = original_executor(request, qualification_token=qualification_token)
        changed = True
        return result

    monkeypatch.setattr(backtesting, "_execute_qualified_request", change_settings_after_execution)

    result = _execute_registered_batch(batch)

    assert type(result) is _PortfolioSleeveExecutionBatch
    assert settings_paths == [calendar_path.resolve()]
    assert loaded_paths == [calendar_path.resolve()] * 3


def test_environment_calendar_change_during_execution_cannot_redirect_final_check(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, calendar_path, tmp_path = execution_setup
    alternate_path = tmp_path / "environment-calendar.json"
    alternate_path.write_bytes(calendar_path.read_bytes())
    batch = _qualified_batch(database_path, _request("TCS"))
    original = backtesting._execute_qualified_request
    original_load = portfolio_execution.market_quality.load_calendar_registry
    loaded_paths: list[Path] = []

    def capture_load(path: Path):
        loaded_paths.append(path.resolve())
        return original_load(path)

    def change_environment_after_execution(request, *, qualification_token):
        result = original(request, qualification_token=qualification_token)
        monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(alternate_path))
        return result

    monkeypatch.setattr(portfolio_execution.market_quality, "load_calendar_registry", capture_load)
    monkeypatch.setattr(
        backtesting, "_execute_qualified_request", change_environment_after_execution
    )
    result = _execute_registered_batch(batch)

    assert type(result) is _PortfolioSleeveExecutionBatch
    assert (
        result.calendar_configuration_sha256
        == hashlib.sha256(calendar_path.read_bytes()).hexdigest()
    )
    assert loaded_paths == [calendar_path.resolve()] * 3


def test_calendar_path_mismatch_at_orchestration_entry_blocks_execution(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, calendar_path, tmp_path = execution_setup
    alternate_path = tmp_path / "unapproved-calendar.json"
    alternate_path.write_bytes(calendar_path.read_bytes())
    batch = _qualified_batch(database_path, _request("TCS"))
    original_get_settings = portfolio_execution.get_settings
    settings_calls = 0

    def select_alternate_path():
        nonlocal settings_calls
        settings_calls += 1
        return replace(
            original_get_settings(),
            calendar_config_path=alternate_path,
        )

    monkeypatch.setattr(portfolio_execution, "get_settings", select_alternate_path)
    monkeypatch.setattr(
        backtesting,
        "_execute_qualified_request",
        lambda *args, **kwargs: pytest.fail("A mismatched calendar path must block execution."),
    )

    with pytest.raises(_PortfolioExecutionIntegrityError, match="not the application-approved"):
        _execute_registered_batch(batch)

    assert settings_calls == 1


@pytest.mark.parametrize("changed_fingerprint", ["effective_calendar", "expected_schedule"])
def test_final_calendar_verification_rechecks_sleeve_fingerprints(
    execution_setup, monkeypatch: pytest.MonkeyPatch, changed_fingerprint: str
) -> None:
    database_path, _, _ = execution_setup
    batch = _qualified_batch(database_path, _request("TCS"))
    execution_finished = False
    original_executor = backtesting._execute_qualified_request
    original_effective_hash = portfolio_execution.strategy_lab._effective_calendar_hash
    original_schedule = portfolio_execution.market_quality.calculate_expected_candles

    def observe_executor(request, *, qualification_token):
        nonlocal execution_finished
        result = original_executor(request, qualification_token=qualification_token)
        execution_finished = True
        return result

    def alter_effective_hash(calendar, registry):
        if execution_finished and changed_fingerprint == "effective_calendar":
            return "0" * 64
        return original_effective_hash(calendar, registry)

    def alter_schedule(calendar, interval, start_date, end_date, *, as_of):
        result = original_schedule(calendar, interval, start_date, end_date, as_of=as_of)
        if execution_finished and changed_fingerprint == "expected_schedule":
            scheduled, completed, expected_days, uncovered_days = result
            changed_first = replace(
                completed[0],
                completes_at=completed[0].completes_at + timedelta(seconds=1),
            )
            return scheduled, (changed_first, *completed[1:]), expected_days, uncovered_days
        return result

    monkeypatch.setattr(backtesting, "_execute_qualified_request", observe_executor)
    monkeypatch.setattr(
        portfolio_execution.strategy_lab, "_effective_calendar_hash", alter_effective_hash
    )
    monkeypatch.setattr(
        portfolio_execution.market_quality, "calculate_expected_candles", alter_schedule
    )

    expected_error = (
        "Effective calendar configuration differs"
        if changed_fingerprint == "effective_calendar"
        else "Expected candle completion mapping differs"
    )
    with pytest.raises(_PortfolioExecutionIntegrityError, match=expected_error):
        _execute_registered_batch(batch)


def test_nested_calendar_registry_mappings_reject_mutation(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    original_loader = portfolio_execution._load_bound_calendar_registry
    original_executor = backtesting._execute_qualified_request
    registries = []

    def capture_registry(provenance, approved_path: Path):
        registry = original_loader(provenance, approved_path)
        registries.append(registry)
        return registry

    def inspect_calendar(request, *, qualification_token):
        with pytest.raises(TypeError):
            request.calendar.holidays[_DATES[0]] = "synthetic mutation"
        with pytest.raises(TypeError):
            request.calendar.special_sessions[_DATES[0]] = object()
        return original_executor(request, qualification_token=qualification_token)

    monkeypatch.setattr(portfolio_execution, "_load_bound_calendar_registry", capture_registry)
    monkeypatch.setattr(backtesting, "_execute_qualified_request", inspect_calendar)
    result = _qualify_and_execute_portfolio_request(database_path, _request("TCS"))

    assert type(result) is _PortfolioSleeveExecutionBatch
    assert len(registries) == 1


def test_all_sleeves_use_the_same_loaded_calendar_object(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    batch = _qualified_batch(database_path, _request("TCS", "INFY"))
    original_loader = portfolio_execution._load_bound_calendar_registry
    original_executor = backtesting._execute_qualified_request
    registries = []
    calendars = []

    def capture_registry(provenance, approved_path: Path):
        registry = original_loader(provenance, approved_path)
        registries.append(registry)
        return registry

    def observe_calendar(request, *, qualification_token):
        calendars.append(request.calendar)
        return original_executor(request, qualification_token=qualification_token)

    monkeypatch.setattr(portfolio_execution, "_load_bound_calendar_registry", capture_registry)
    monkeypatch.setattr(backtesting, "_execute_qualified_request", observe_calendar)
    result = _execute_registered_batch(batch)

    assert type(result) is _PortfolioSleeveExecutionBatch
    assert len(registries) == 1
    assert len(calendars) == 2
    assert calendars[0] is calendars[1]
    assert calendars[0] is registries[0].resolve("NSE", "NSE")


def test_m2c_fingerprint_and_m2b_provenance_are_fresh_and_bound(
    execution_setup,
) -> None:
    database_path, _, _ = execution_setup
    qualified = _qualified_batch(database_path, _request("TCS"))
    batch_provenance = qualified.provenance_document()
    m2b_fingerprints = portfolio_execution._implementation_fingerprints_from_batch(batch_provenance)

    result = _execute_registered_batch(qualified)

    assert type(result) is _PortfolioSleeveExecutionBatch
    fingerprints = dict(result.implementation_fingerprints)
    assert set(fingerprints) == set(m2b_fingerprints) | {"portfolio_execution"}
    assert {name: fingerprints[name] for name in m2b_fingerprints} == m2b_fingerprints
    implementation_path = Path(portfolio_execution.__file__).resolve()
    assert (
        fingerprints["portfolio_execution"]
        == hashlib.sha256(implementation_path.read_bytes()).hexdigest()
    )


def test_m2c_source_change_during_execution_prevents_publication(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    qualified = _qualified_batch(database_path, _request("TCS"))
    implementation_path = Path(portfolio_execution.__file__).resolve()
    original_read_bytes = Path.read_bytes
    changed = False
    observed_versions: list[bool] = []

    def source_bytes(path: Path) -> bytes:
        nonlocal changed
        content = original_read_bytes(path)
        if path.resolve() == implementation_path:
            observed_versions.append(changed)
            return content + (b"\nsynthetic source change" if changed else b"")
        return content

    monkeypatch.setattr(Path, "read_bytes", source_bytes)
    original_executor = backtesting._execute_qualified_request

    def change_after_execution(request, *, qualification_token):
        nonlocal changed
        result = original_executor(request, qualification_token=qualification_token)
        changed = True
        return result

    monkeypatch.setattr(backtesting, "_execute_qualified_request", change_after_execution)
    published = None
    with pytest.raises(_PortfolioExecutionIntegrityError, match="implementation changed"):
        published = _execute_registered_batch(qualified)

    assert published is None
    assert False in observed_versions
    assert True in observed_versions


@pytest.mark.parametrize(
    "tamper",
    (
        "orders",
        "fills",
        "rejections",
        "trades",
        "equity_curve",
        "open_position",
        "summary",
        "diagnostics",
        "benchmark",
    ),
)
def test_all_nine_execution_result_fields_reject_schema_or_tampering(
    execution_setup, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    database_path, _, _ = execution_setup
    request = (
        _request("TCS", end_index=7)
        if tamper in {"rejections", "open_position"}
        else _request("TCS")
    )
    if tamper == "rejections":
        volumes = [1000] * len(_DATES)
        volumes[5] = 0
        _set_volumes(database_path, "TCS", tuple(volumes))
        request = _request("TCS", end_index=7)
    if tamper == "open_position":
        request = _request("TCS", end_index=4)
    original = backtesting._execute_qualified_request

    def substitute(request_value, *, qualification_token):
        result = original(request_value, qualification_token=qualification_token)
        values = _all_result_fields(result)
        if tamper == "orders":
            values["orders"][0]["unexpected"] = "extra"
        elif tamper == "fills":
            del values["fills"][0]["cash_before"]
        elif tamper == "rejections":
            values["rejected_or_unfilled_orders"][0]["status"] = "FILLED"
        elif tamper == "trades":
            values["trades"][0]["net_pnl"] = "999999"
        elif tamper == "equity_curve":
            values["equity_curve"][0]["equity"] = "999999"
        elif tamper == "open_position":
            values["open_position"]["quantity"] = 99
        elif tamper == "summary":
            values["summary"]["total_return"] = 1.5
        elif tamper == "diagnostics":
            values["diagnostics"]["dataset_sha256"] = "0" * 64
        else:
            values["buy_and_hold_benchmark"]["extra"] = "tampered"
        return backtesting.ExecutionResult(**values)

    monkeypatch.setattr(backtesting, "_execute_qualified_request", substitute)
    with pytest.raises(_PortfolioExecutionIntegrityError):
        _qualify_and_execute_portfolio_request(database_path, request)


@pytest.mark.parametrize(
    "tamper",
    (
        "missing_order_key",
        "unexpected_order_key",
        "boolean_quantity",
        "non_finite_equity",
        "noncanonical_decimal",
        "invalid_order_status",
        "invalid_benchmark_status",
        "malformed_nullable_summary",
    ),
)
def test_result_schema_rejects_malformed_values_and_status_combinations(
    execution_setup, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    database_path, _, _ = execution_setup
    original = backtesting._execute_qualified_request

    def malformed(request, *, qualification_token):
        result = original(request, qualification_token=qualification_token)
        values = _all_result_fields(result)
        if tamper == "missing_order_key":
            del values["orders"][0]["submitted_at"]
        elif tamper == "unexpected_order_key":
            values["orders"][0]["unexpected"] = "not emitted by M1B"
        elif tamper == "boolean_quantity":
            values["orders"][0]["quantity"] = True
        elif tamper == "non_finite_equity":
            values["equity_curve"][0]["equity"] = float("nan")
        elif tamper == "noncanonical_decimal":
            values["equity_curve"][0]["equity"] = "01.00"
        elif tamper == "invalid_order_status":
            values["orders"][0]["status"] = "UNFILLED_NO_NEXT_BAR"
        elif tamper == "invalid_benchmark_status":
            values["buy_and_hold_benchmark"]["status"] = "PENDING"
        else:
            values["summary"]["win_rate"] = "NaN"
        return backtesting.ExecutionResult(**values)

    monkeypatch.setattr(backtesting, "_execute_qualified_request", malformed)
    with pytest.raises(_PortfolioExecutionIntegrityError):
        _qualify_and_execute_portfolio_request(database_path, _request("TCS"))


@pytest.mark.parametrize(
    "mismatch",
    ("execution_inputs", "generated_signals", "request_provenance", "batch_source_provenance"),
)
def test_structurally_valid_result_with_mismatched_execution_binding_is_rejected(
    execution_setup, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    database_path, _, _ = execution_setup
    original = backtesting._execute_qualified_request

    def substitute(request, *, qualification_token):
        result = original(request, qualification_token=qualification_token)
        values = _all_result_fields(result)
        diagnostics = values["diagnostics"]
        if mismatch == "execution_inputs":
            diagnostics["execution_configuration"]["starting_cash"] = backtesting._decimal_text(
                request.config.starting_cash + Decimal("1.00")
            )
        elif mismatch == "generated_signals":
            diagnostics["signals_sha256"] = "a" * 64
        elif mismatch == "request_provenance":
            diagnostics["signal_provenance"]["name"] = "Different but well-formed strategy"
        else:
            diagnostics["source_imports"][0]["declared_by"] = "different synthetic declaration"
            diagnostics["source_imports_sha256"] = portfolio_execution.strategy_lab._canonical_hash(
                diagnostics["source_imports"]
            )
        return backtesting.ExecutionResult(**values)

    monkeypatch.setattr(backtesting, "_execute_qualified_request", substitute)
    with pytest.raises(_PortfolioExecutionIntegrityError):
        _qualify_and_execute_portfolio_request(database_path, _request("TCS"))


def test_prior_identical_execution_result_is_revalidated_and_detached(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    request = _request("TCS")
    original = backtesting._execute_qualified_request
    raw_results: list[backtesting.ExecutionResult] = []

    def retain_result(m1b_request, *, qualification_token):
        result = original(m1b_request, qualification_token=qualification_token)
        raw_results.append(result)
        return result

    monkeypatch.setattr(backtesting, "_execute_qualified_request", retain_result)
    first = _qualify_and_execute_portfolio_request(database_path, request)
    assert type(first) is _PortfolioSleeveExecutionBatch
    assert len(raw_results) == 1
    assert first.sleeves[0].result is not raw_results[0]

    monkeypatch.setattr(
        backtesting,
        "_execute_qualified_request",
        lambda *_args, **_kwargs: raw_results[0],
    )
    second = _qualify_and_execute_portfolio_request(database_path, request)
    assert type(second) is _PortfolioSleeveExecutionBatch
    assert second.sleeves[0].result is not raw_results[0]
    assert second.sleeves[0].result_sha256 == first.sleeves[0].result_sha256
    assert portfolio_execution._execution_result_sha256(raw_results[0]) == (
        first.sleeves[0].result_sha256
    )


def test_result_provenance_binds_exact_registered_batch_request_signals_and_configuration(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    original = portfolio_qualification._qualify_portfolio_request
    qualifications = []

    def observe_qualification(database, request):
        qualification = original(database, request)
        qualifications.append(qualification)
        return qualification

    monkeypatch.setattr(
        portfolio_qualification, "_qualify_portfolio_request", observe_qualification
    )
    result = _qualify_and_execute_portfolio_request(database_path, _request("TCS"))

    assert type(result) is _PortfolioSleeveExecutionBatch
    qualified_batch = qualifications[0].qualified_batch
    assert qualified_batch is not None
    sleeve = result.sleeves[0]
    provenance = json.loads(sleeve.provenance_json)
    assert provenance["batch_fingerprint_sha256"] == qualified_batch.fingerprint_sha256
    assert provenance["request_sha256"] == result.request_sha256
    assert provenance["manifest_sha256"] == result.manifest_sha256
    assert provenance["signals_sha256"] == sleeve.result.diagnostics["signals_sha256"]
    assert (
        provenance["execution_configuration"]
        == (sleeve.result.diagnostics["execution_configuration"])
    )
    assert provenance["execution_result_sha256"] == sleeve.result_sha256
    assert (
        provenance["implementation_source_sha256"]["portfolio_execution"]
        == dict(result.implementation_fingerprints)["portfolio_execution"]
    )


def test_wrong_execution_result_type_and_cross_sleeve_substitution_fail(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    original = backtesting._execute_qualified_request
    saved: list[backtesting.ExecutionResult] = []
    calls = 0

    def substitute_second(request, *, qualification_token):
        nonlocal calls
        calls += 1
        result = original(request, qualification_token=qualification_token)
        if calls == 1:
            saved.append(result)
            return result
        return saved[0]

    monkeypatch.setattr(backtesting, "_execute_qualified_request", substitute_second)
    with pytest.raises(_PortfolioExecutionIntegrityError, match="diagnostic instrument"):
        _qualify_and_execute_portfolio_request(database_path, _request("TCS", "INFY"))

    monkeypatch.setattr(backtesting, "_execute_qualified_request", lambda *a, **k: object())
    with pytest.raises(_PortfolioExecutionIntegrityError, match="substituted result type"):
        _qualify_and_execute_portfolio_request(database_path, _request("TCS"))


def test_later_sleeve_exception_publishes_no_partial_result(
    execution_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = execution_setup
    original = backtesting._execute_qualified_request
    calls = 0

    def fail_second(request, *, qualification_token):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Synthetic later-sleeve failure.")
        return original(request, qualification_token=qualification_token)

    monkeypatch.setattr(backtesting, "_execute_qualified_request", fail_second)
    published = None
    with pytest.raises(RuntimeError, match="later-sleeve failure"):
        published = _qualify_and_execute_portfolio_request(database_path, _request("TCS", "INFY"))
    assert published is None
    assert calls == 2


def test_execution_result_snapshot_is_deeply_detached_and_hashes_after_validation(
    execution_setup,
) -> None:
    database_path, _, _ = execution_setup
    result = _qualify_and_execute_portfolio_request(database_path, _request("TCS"))
    assert type(result) is _PortfolioSleeveExecutionBatch
    sleeve = result.sleeves[0]
    exported = sleeve.to_dict()
    before_hash = sleeve.result_sha256
    exported["execution_result"]["orders"][0]["status"] = "REJECTED"
    exported["provenance"]["execution_result_sha256"] = "0" * 64
    assert sleeve.result.orders[0]["status"] == "FILLED"
    assert sleeve.result_sha256 == before_hash
    with pytest.raises(TypeError):
        sleeve.result.orders[0]["status"] = "REJECTED"


def test_all_nine_result_fields_are_deeply_immutable_and_exports_are_detached(
    execution_setup,
) -> None:
    database_path, _, _ = execution_setup
    complete = _qualify_and_execute_portfolio_request(database_path, _request("TCS"))
    open_position = _qualify_and_execute_portfolio_request(
        database_path, _request("TCS", end_index=4)
    )
    assert type(complete) is _PortfolioSleeveExecutionBatch
    assert type(open_position) is _PortfolioSleeveExecutionBatch
    assert {field.name for field in fields(backtesting.ExecutionResult)} == (
        _EXECUTION_RESULT_FIELDS
    )

    for field_name in _EXECUTION_RESULT_FIELDS:
        sleeve = open_position.sleeves[0] if field_name == "open_position" else complete.sleeves[0]
        value = getattr(sleeve.result, field_name)
        assert value is not None
        _assert_recursively_immutable(value)

        before_document = _all_result_fields(sleeve.result)
        before_hash = sleeve.result_sha256
        exported = sleeve.to_dict()["execution_result"][field_name]
        _mutate_exported_value(exported)
        assert _all_result_fields(sleeve.result) == before_document
        assert sleeve.result_sha256 == before_hash
