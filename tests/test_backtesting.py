"""Comprehensive tests for Phase 4 deterministic backtesting engine."""

import json
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime
from decimal import (
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_UP,
    Context,
    Decimal,
    getcontext,
    localcontext,
)
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis.backtesting import (
    BacktestConfig,
    BacktestError,
    CostAssumptions,
    _buy_and_hold_benchmark,
    _calculate_brokerage,
    _daily_metrics,
    _fees,
    _fill_price,
    _simulate,
    default_cost_assumptions,
    list_backtest_runs,
    list_backtests,
    register_backtest,
    run_backtest,
    zero_cost_assumptions,
)
from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument, update_instrument
from aegis.market_data import (
    SourceUseStatus,
    SourceUseStatusError,
    declare_import_source_use_status,
    import_ohlcv_csv,
    list_recent_imports,
)
from aegis.market_quality import ExchangeCalendar, load_calendar_registry
from aegis.strategy_lab import (
    StrategyCandle,
    StrategyLabError,
    StrategySignal,
    _canonical_hash,
    _validate_candles,
    create_sma_crossover_spec,
    generate_sma_crossover_signals,
    list_strategy_experiments,
    register_experiment,
)

_DAY = date(2026, 1, 5)
_AS_OF = datetime(2026, 1, 5, 9, 25, tzinfo=ZoneInfo("Asia/Kolkata"))


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "backtest_test.db"
    initialize_database(path)
    register_instrument(
        path,
        InstrumentInput(
            "NSE",
            "TCS",
            "Tata Consultancy Services",
            "EQUITY",
            segment="NSE",
            tick_size=Decimal("0.05"),
        ),
    )
    return path


def _calendar_fixture() -> ExchangeCalendar:
    registry_data = {
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
                "source": "Deterministic test fixture",
                "weekdays": [0, 1, 2, 3, 4],
                "sessions": [{"open": "09:15", "close": "09:25"}],
                "holidays": [],
                "special_sessions": [],
            }
        ],
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "exchange_calendars.json"
        path.write_text(json.dumps(registry_data), encoding="utf-8")
        registry = load_calendar_registry(path)
        cal = registry.resolve("NSE", "NSE")
        assert cal is not None
        return cal


def _write_csv(
    path: Path,
    rows: list[tuple[str, int, int, int, int, int]],
    *,
    filename: str = "candles.csv",
) -> Path:
    lines = ["timestamp,open,high,low,close,volume"]
    for ts, o, h, l, c, v in rows:
        lines.append(f"{ts},{o},{h},{l},{c},{v}")
    csv_path = path / filename
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path


def _create_strategy_experiment(
    database_path: Path,
    *,
    exchange: str = "NSE",
    trading_symbol: str = "TCS",
    instrument_type: str = "EQUITY",
    fast_window: int = 2,
    slow_window: int = 3,
) -> int:
    spec = create_sma_crossover_spec(
        exchange=exchange,
        trading_symbol=trading_symbol,
        instrument_type=instrument_type,
        interval="1m",
        fast_window=fast_window,
        slow_window=slow_window,
        hypothesis="Deterministic test hypothesis for backtesting engine.",
    )
    reg = register_experiment(
        database_path,
        spec,
        start_date=_DAY,
        end_date=_DAY,
    )
    return reg.experiment_id


def _create_calendar_file(tmp_path: Path, candle_count: int, name: str) -> Path:
    close_minute = 15 + candle_count
    path = tmp_path / f"{name}_calendars.json"
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
                        "source": "Deterministic test fixture",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": f"09:{close_minute:02d}"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_synthetic_backtest(
    database_path: Path,
    tmp_path: Path,
    closes: list[int],
    *,
    fast_window: int = 2,
    slow_window: int = 3,
    source_use_status: SourceUseStatus = "VERIFIED_PERMITTED",
    name: str = "synthetic",
) -> tuple[int, dict[str, object]]:
    rows = [
        (
            f"2026-01-05T09:{15 + index:02d}:00+05:30",
            close,
            close + 2,
            close - 2,
            close,
            1000,
        )
        for index, close in enumerate(closes)
    ]
    csv_path = _write_csv(tmp_path, rows, filename=f"{name}.csv")
    imported = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
    if source_use_status != "UNVERIFIED":
        declare_import_source_use_status(
            database_path,
            imported.import_id,
            source_use_status,
            declared_by="synthetic test administrator",
            rationale="Synthetic test data with explicit test-only permission.",
        )
    strategy_id = _create_strategy_experiment(
        database_path, fast_window=fast_window, slow_window=slow_window
    )
    registration = register_backtest(
        database_path,
        strategy_id,
        BacktestConfig(
            Decimal(100000),
            1,
            Decimal("0.5"),
            zero_cost_assumptions(),
        ),
    )
    calendar_path = _create_calendar_file(tmp_path, len(closes), name)
    as_of = datetime(
        2026,
        1,
        5,
        9,
        16 + len(closes),
        tzinfo=ZoneInfo("Asia/Kolkata"),
    )
    run_backtest(
        database_path,
        registration.experiment_id,
        calendars=load_calendar_registry(calendar_path),
        as_of=as_of,
    )
    record = list_backtest_runs(database_path, registration.experiment_id)[0]
    return registration.experiment_id, record["result"]


def _rewrite_backtest_configuration(
    database_path: Path,
    experiment_id: int,
    update: Callable[[dict[str, object]], None],
    *,
    refresh_hash: bool = True,
) -> None:
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT configuration_json FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        configuration = json.loads(row["configuration_json"])
        update(configuration)
        stored_hash = _canonical_hash(configuration) if refresh_hash else None
        connection.execute(
            """UPDATE experiments
               SET configuration_json = ?, configuration_sha256 = COALESCE(?, configuration_sha256)
               WHERE id = ?""",
            (json.dumps(configuration), stored_hash, experiment_id),
        )
        connection.commit()


def _decimal_context_state(context: Context) -> tuple[object, ...]:
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        context.flags.copy(),
        context.traps.copy(),
    )


def test_public_run_backtest_is_independent_of_decimal_context(
    database_path: Path, tmp_path: Path
) -> None:
    experiment_id, expected_result = _run_synthetic_backtest(
        database_path, tmp_path, [30, 20, 10, 20, 40, 50], name="decimal_context"
    )
    default_context = getcontext()
    contexts = [
        (default_context.prec, default_context.rounding),
        (3, ROUND_DOWN),
        (5, ROUND_UP),
        (10, ROUND_FLOOR),
        (50, ROUND_HALF_EVEN),
    ]
    calendars = load_calendar_registry(_create_calendar_file(tmp_path, 6, "decimal_context"))
    as_of = datetime(2026, 1, 5, 9, 22, tzinfo=ZoneInfo("Asia/Kolkata"))

    for precision, rounding in contexts:
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            original_state = _decimal_context_state(context)
            run = run_backtest(
                database_path,
                experiment_id,
                calendars=calendars,
                as_of=as_of,
            )
            assert run.status == "COMPLETED", run.reason
            actual_result = list_backtest_runs(database_path, experiment_id)[0]["result"]
            assert actual_result == expected_result
            assert _decimal_context_state(context) == original_state

    empty_calendar_path = tmp_path / "empty_context_calendars.json"
    empty_calendar_path.write_text(
        json.dumps({"schema_version": 1, "calendars": []}), encoding="utf-8"
    )
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        original_state = _decimal_context_state(context)
        blocked_run = run_backtest(
            database_path,
            experiment_id,
            calendars=load_calendar_registry(empty_calendar_path),
            as_of=as_of,
        )
        assert blocked_run.status == "BLOCKED"
        assert _decimal_context_state(context) == original_state


# ---------------------------------------------------------------------------
# 1. Configuration and Validation Tests
# ---------------------------------------------------------------------------


def test_cost_assumptions_validation() -> None:
    valid = default_cost_assumptions()
    assert valid.brokerage_rate == Decimal("0.0003")

    with pytest.raises(
        BacktestError, match="brokerage_rate must be a finite, non-negative Decimal"
    ):
        CostAssumptions(
            brokerage_rate=Decimal("-0.01"),
            brokerage_cap_per_order=Decimal(0),
            exchange_charge_rate=Decimal(0),
            regulatory_charge_rate=Decimal(0),
            transaction_tax_buy_rate=Decimal(0),
            transaction_tax_sell_rate=Decimal(0),
            stamp_duty_buy_rate=Decimal(0),
            gst_rate=Decimal(0),
            spread_basis_points=Decimal(0),
            slippage_basis_points=Decimal(0),
        )

    with pytest.raises(BacktestError, match="gst_rate must be a decimal rate between 0 and 1"):
        CostAssumptions(
            brokerage_rate=Decimal(0),
            brokerage_cap_per_order=Decimal(0),
            exchange_charge_rate=Decimal(0),
            regulatory_charge_rate=Decimal(0),
            transaction_tax_buy_rate=Decimal(0),
            transaction_tax_sell_rate=Decimal(0),
            stamp_duty_buy_rate=Decimal(0),
            gst_rate=Decimal("1.5"),
            spread_basis_points=Decimal(0),
            slippage_basis_points=Decimal(0),
        )


def test_backtest_config_validation() -> None:
    costs = zero_cost_assumptions()
    with pytest.raises(BacktestError, match="starting_cash must be a finite positive Decimal"):
        BacktestConfig(Decimal(0), 10, Decimal("0.1"), costs)

    with pytest.raises(BacktestError, match="representable to INR 0.01"):
        BacktestConfig(Decimal("1000.001"), 10, Decimal("0.1"), costs)

    with pytest.raises(BacktestError, match="order_quantity must be a positive integer"):
        BacktestConfig(Decimal(100000), 0, Decimal("0.1"), costs)

    with pytest.raises(BacktestError, match="order_quantity must be a positive integer"):
        BacktestConfig(Decimal(100000), True, Decimal("0.1"), costs)  # type: ignore[arg-type]

    with pytest.raises(BacktestError, match="max_volume_participation must be a Decimal in"):
        BacktestConfig(Decimal(100000), 10, Decimal(0), costs)

    with pytest.raises(BacktestError, match="max_volume_participation must be a Decimal in"):
        BacktestConfig(Decimal(100000), 10, Decimal("1.5"), costs)


# ---------------------------------------------------------------------------
# 2. Financial Precision & Adverse Execution Math Tests
# ---------------------------------------------------------------------------


def test_calculate_brokerage_modes() -> None:
    # 1. Rate with cap: min(turnover * rate, cap)
    c1 = CostAssumptions(
        brokerage_rate=Decimal("0.0003"),
        brokerage_cap_per_order=Decimal("20.00"),
        exchange_charge_rate=Decimal(0),
        regulatory_charge_rate=Decimal(0),
        transaction_tax_buy_rate=Decimal(0),
        transaction_tax_sell_rate=Decimal(0),
        stamp_duty_buy_rate=Decimal(0),
        gst_rate=Decimal(0),
        spread_basis_points=Decimal(0),
        slippage_basis_points=Decimal(0),
    )
    # 50,000 turnover * 0.0003 = 15.00 < 20.00 -> 15.00
    assert _calculate_brokerage(Decimal(50000), c1) == Decimal("15.0000")
    # 100,000 turnover * 0.0003 = 30.00 > 20.00 -> 20.00
    assert _calculate_brokerage(Decimal(100000), c1) == Decimal("20.00")

    # 2. Rate only, cap = 0 (uncapped)
    c2 = CostAssumptions(
        brokerage_rate=Decimal("0.0003"),
        brokerage_cap_per_order=Decimal(0),
        exchange_charge_rate=Decimal(0),
        regulatory_charge_rate=Decimal(0),
        transaction_tax_buy_rate=Decimal(0),
        transaction_tax_sell_rate=Decimal(0),
        stamp_duty_buy_rate=Decimal(0),
        gst_rate=Decimal(0),
        spread_basis_points=Decimal(0),
        slippage_basis_points=Decimal(0),
    )
    assert _calculate_brokerage(Decimal(100000), c2) == Decimal("30.0000")

    # 3. A cap without a brokerage rate does not become a flat fee.
    c3 = CostAssumptions(
        brokerage_rate=Decimal(0),
        brokerage_cap_per_order=Decimal("20.00"),
        exchange_charge_rate=Decimal(0),
        regulatory_charge_rate=Decimal(0),
        transaction_tax_buy_rate=Decimal(0),
        transaction_tax_sell_rate=Decimal(0),
        stamp_duty_buy_rate=Decimal(0),
        gst_rate=Decimal(0),
        spread_basis_points=Decimal(0),
        slippage_basis_points=Decimal(0),
    )
    assert _calculate_brokerage(Decimal(100000), c3) == Decimal(0)

    # 4. Zero brokerage
    c4 = zero_cost_assumptions()
    assert _calculate_brokerage(Decimal(100000), c4) == Decimal(0)


def test_fill_price_adverse_execution_and_tick_rounding() -> None:
    # 10 bps spread (half-spread = 5 bps = 0.0005)
    # 10 bps slippage (= 0.0010)
    # total adverse rate = 0.0015
    costs = CostAssumptions(
        brokerage_rate=Decimal(0),
        brokerage_cap_per_order=Decimal(0),
        exchange_charge_rate=Decimal(0),
        regulatory_charge_rate=Decimal(0),
        transaction_tax_buy_rate=Decimal(0),
        transaction_tax_sell_rate=Decimal(0),
        stamp_duty_buy_rate=Decimal(0),
        gst_rate=Decimal(0),
        spread_basis_points=Decimal("10.0"),
        slippage_basis_points=Decimal("10.0"),
    )
    tick_size = Decimal("0.05")

    # Reference open = 100.00
    # Buy raw = 100.00 * (1 + 0.0015) = 100.15
    # Rounded CEILING to 0.05 -> 100.15
    fill, spread, slip, tick_cost = _fill_price(Decimal("100.00"), "BUY", tick_size, costs)
    assert fill == Decimal("100.15")
    assert spread == Decimal("0.05")
    assert slip == Decimal("0.10")

    # Sell raw = 100.00 * (1 - 0.0015) = 99.85
    # Rounded FLOOR to 0.05 -> 99.85
    fill, spread, slip, tick_cost = _fill_price(Decimal("100.00"), "SELL", tick_size, costs)
    assert fill == Decimal("99.85")

    # Test tick rounding when raw price does not land on tick
    # Reference open = 100.02
    # Buy raw = 100.02 + 0.05001 + 0.10002 = 100.17003
    # Ceiling to 0.05 -> 100.20
    fill, _, _, tick_cost = _fill_price(Decimal("100.02"), "BUY", tick_size, costs)
    assert fill == Decimal("100.20")
    assert tick_cost > 0

    # Sell raw = 100.02 - 0.05001 - 0.10002 = 99.86997
    # Floor to 0.05 -> 99.85
    fill, _, _, tick_cost = _fill_price(Decimal("100.02"), "SELL", tick_size, costs)
    assert fill == Decimal("99.85")
    assert tick_cost > 0


def test_fill_price_rejects_invalid_reference_and_tick() -> None:
    costs = zero_cost_assumptions()
    with pytest.raises(BacktestError, match="reference price"):
        _fill_price(Decimal(0), "BUY", Decimal("0.05"), costs)
    with pytest.raises(BacktestError, match="tick_size"):
        _fill_price(Decimal(100), "BUY", Decimal(0), costs)


def test_fees_calculation_reconciles_exactly() -> None:
    costs = CostAssumptions(
        brokerage_rate=Decimal("0.0003"),
        brokerage_cap_per_order=Decimal("20.00"),
        exchange_charge_rate=Decimal("0.0000345"),
        regulatory_charge_rate=Decimal("0.000001"),
        transaction_tax_buy_rate=Decimal("0.001"),
        transaction_tax_sell_rate=Decimal("0.001"),
        stamp_duty_buy_rate=Decimal("0.00015"),
        gst_rate=Decimal("0.18"),
        spread_basis_points=Decimal(0),
        slippage_basis_points=Decimal(0),
    )
    # Price = 3000.00, Quantity = 10 -> Turnover = 30,000.00
    buy_fees = _fees(Decimal("3000.00"), 10, "BUY", costs)
    # Brokerage: 30000 * 0.0003 = 9.00 (< 20.00) -> 9.00
    assert buy_fees["brokerage"] == Decimal("9.00")
    # Exchange: 30000 * 0.0000345 = 1.035 -> 1.04
    assert buy_fees["exchange_charges"] == Decimal("1.04")
    # Regulatory: 30000 * 0.000001 = 0.03 -> 0.03
    assert buy_fees["regulatory_charges"] == Decimal("0.03")
    # STT: 30000 * 0.001 = 30.00
    assert buy_fees["transaction_tax"] == Decimal("30.00")
    # Stamp duty: 30000 * 0.00015 = 4.50
    assert buy_fees["stamp_duty"] == Decimal("4.50")
    # GST: (9.00 + 1.035 + 0.03) * 0.18 = 10.065 * 0.18 = 1.8117 -> 1.81
    assert buy_fees["gst"] == Decimal("1.81")

    # On SELL: stamp duty is 0
    sell_fees = _fees(Decimal("3000.00"), 10, "SELL", costs)
    assert sell_fees["stamp_duty"] == Decimal("0.00")


def test_daily_metrics_report_daily_returns_and_trade_aggregates() -> None:
    metrics = _daily_metrics(
        [
            {"local_date": "2026-01-05", "equity": "110"},
            {"local_date": "2026-01-06", "equity": "99"},
        ],
        Decimal(100),
        [
            {"gross_pnl": "20", "net_pnl": "15"},
            {"gross_pnl": "-10", "net_pnl": "-15"},
        ],
    )
    assert metrics["daily_returns"] == [
        {"local_date": "2026-01-05", "return": "0.1"},
        {"local_date": "2026-01-06", "return": "-0.1"},
    ]
    assert metrics["winning_trades"] == 1
    assert metrics["losing_trades"] == 1
    assert metrics["gross_profit"] == "20"
    assert metrics["gross_loss"] == "10"
    assert metrics["profit_factor"] == "1"
    assert metrics["win_rate"] == "0.5"


def test_daily_metrics_boundary_cases_keep_undefined_values_explicit() -> None:
    no_trades = _daily_metrics(
        [{"local_date": "2026-01-05", "equity": "100"}],
        Decimal(100),
        [],
    )
    assert no_trades["closed_trade_count"] == 0
    assert no_trades["win_rate"] is None
    assert no_trades["profit_factor"] is None
    assert no_trades["expectancy_per_closed_trade"] is None
    assert no_trades["annualized_daily_volatility"] is None
    assert no_trades["annualized_sharpe_zero_risk_free"] is None

    only_winners = _daily_metrics(
        [
            {"local_date": "2026-01-05", "equity": "110"},
            {"local_date": "2026-01-06", "equity": "121"},
        ],
        Decimal(100),
        [{"gross_pnl": "10", "net_pnl": "8"}],
    )
    assert only_winners["winning_trades"] == 1
    assert only_winners["losing_trades"] == 0
    assert only_winners["profit_factor"] is None
    assert only_winners["gross_loss"] == "0"
    assert only_winners["annualized_daily_volatility"] == "0"
    assert only_winners["annualized_sharpe_zero_risk_free"] is None

    only_loser_and_breakeven = _daily_metrics(
        [
            {"local_date": "2026-01-05", "equity": "100"},
            {"local_date": "2026-01-06", "equity": "90"},
        ],
        Decimal(100),
        [
            {"gross_pnl": "-10", "net_pnl": "-12"},
            {"gross_pnl": "0", "net_pnl": "0"},
        ],
    )
    assert only_loser_and_breakeven["winning_trades"] == 0
    assert only_loser_and_breakeven["losing_trades"] == 1
    assert only_loser_and_breakeven["win_rate"] == "0"
    assert only_loser_and_breakeven["profit_factor"] == "0"
    assert only_loser_and_breakeven["gross_profit"] == "0"
    assert only_loser_and_breakeven["gross_loss"] == "10"


def test_strategy_signals_are_invariant_to_future_candle_changes() -> None:
    closes = [30, 20, 10, 20, 40, 50, 5]
    candles = [
        StrategyCandle(
            f"2026-01-05T03:{45 + index:02d}:00Z",
            Decimal(close),
            Decimal(close + 2),
            Decimal(close - 2),
            Decimal(close),
            1000,
        )
        for index, close in enumerate(closes)
    ]
    decision_index = 4
    prefix_signals = generate_sma_crossover_signals(
        candles[: decision_index + 1], fast_window=2, slow_window=3
    )
    changed_future = candles[: decision_index + 1] + [
        StrategyCandle(
            candle.timestamp,
            Decimal(90),
            Decimal(92),
            Decimal(88),
            Decimal(90),
            1_000_000,
        )
        for candle in candles[decision_index + 1 :]
    ]
    full_signals = generate_sma_crossover_signals(changed_future, fast_window=2, slow_window=3)
    assert tuple(
        signal for signal in full_signals if signal.timestamp <= candles[decision_index].timestamp
    ) == (prefix_signals)


def test_simulation_is_independent_of_callers_decimal_context() -> None:
    candles = [
        StrategyCandle(
            f"2026-01-05T03:{15 + index:02d}:00Z",
            price,
            price + Decimal(1),
            price - Decimal(1),
            price,
            1000,
        )
        for index, price in enumerate(
            (Decimal("100.10"), Decimal("100.10"), Decimal("100.10"), Decimal("100.10"))
        )
    ]
    signals = (
        StrategySignal(
            candles[0].timestamp, "ENTER_LONG", candles[0].close, Decimal(1), Decimal(2)
        ),
        StrategySignal(candles[2].timestamp, "EXIT_LONG", candles[2].close, Decimal(2), Decimal(1)),
    )
    expected_by_timestamp = {
        candle.timestamp: datetime(2026, 1, 5, 3, 16 + index)
        for index, candle in enumerate(candles)
    }
    config = BacktestConfig(
        Decimal(100000),
        1,
        Decimal("0.5"),
        default_cost_assumptions(),
    )
    before = getcontext().copy()
    baseline = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=_calendar_fixture(),
        expected_by_timestamp=expected_by_timestamp,
    )
    with localcontext() as caller_context:
        caller_context.prec = 3
        caller_context.rounding = ROUND_DOWN
        constrained = _simulate(
            candles,
            signals,
            config=config,
            tick_size=Decimal("0.05"),
            interval="1m",
            calendar=_calendar_fixture(),
            expected_by_timestamp=expected_by_timestamp,
        )
        assert caller_context.prec == 3
        assert caller_context.rounding == ROUND_DOWN
    assert constrained == baseline
    with localcontext() as caller_context:
        caller_context.prec = 5
        caller_context.rounding = ROUND_UP
        rounded_up = _simulate(
            candles,
            signals,
            config=config,
            tick_size=Decimal("0.05"),
            interval="1m",
            calendar=_calendar_fixture(),
            expected_by_timestamp=expected_by_timestamp,
        )
        assert caller_context.prec == 5
        assert caller_context.rounding == ROUND_UP
    assert rounded_up == baseline
    after = getcontext()
    assert after.prec == before.prec
    assert after.rounding == before.rounding
    assert after.Emin == before.Emin
    assert after.Emax == before.Emax


def test_zero_missing_and_negative_volume_fail_closed() -> None:
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z",
            Decimal(100),
            Decimal(101),
            Decimal(99),
            Decimal(100),
            0,
        ),
        StrategyCandle(
            "2026-01-05T03:16:00Z",
            Decimal(100),
            Decimal(101),
            Decimal(99),
            Decimal(100),
            1_000_000,
        ),
    ]
    signals = (
        StrategySignal(
            candles[0].timestamp, "ENTER_LONG", Decimal(100), Decimal(101), Decimal(100)
        ),
    )
    config = BacktestConfig(
        Decimal(100000),
        1,
        Decimal("0.5"),
        zero_cost_assumptions(),
    )
    result = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=_calendar_fixture(),
        expected_by_timestamp={
            "2026-01-05T03:15:00Z": datetime(2026, 1, 5, 3, 16),
            "2026-01-05T03:16:00Z": datetime(2026, 1, 5, 3, 17),
        },
    )
    assert result["orders"][0]["status"] == "REJECTED"
    assert result["orders"][0]["volume_capacity"] == 0
    assert result["equity_curve"][1]["cash"] == "100000"
    assert result["equity_curve"][1]["position_quantity"] == "0"

    for bad_volume in (-1, None):
        invalid = [
            StrategyCandle(
                "2026-01-05T03:15:00Z",
                Decimal(100),
                Decimal(101),
                Decimal(99),
                Decimal(100),
                bad_volume,  # type: ignore[arg-type]
            )
        ]
        with pytest.raises(StrategyLabError, match="volume must be a non-negative integer"):
            _validate_candles(invalid)


# ---------------------------------------------------------------------------
# 3. Causal Execution Timeline & Volume Liquidity Proxy Tests
# ---------------------------------------------------------------------------


def test_causal_execution_timeline_and_holding_updates() -> None:
    """Verify strictly causal sequence:
    - Candle T close: signal generated; no change in cash or holdings; MTM uses pre-existing position.
    - Candle T+1 open: pending order evaluated against open and filled. Cash and holding updated.
    - Candle T+1 close: MTM uses updated position and Close(T+1).
    """
    cal = _calendar_fixture()
    expected_by_timestamp = {
        f"2026-01-05T03:{15 + i:02d}:00Z": datetime(2026, 1, 5, 3, 16 + i) for i in range(5)
    }
    candles = [
        StrategyCandle(
            timestamp=f"2026-01-05T03:{15 + i:02d}:00Z",
            open=Decimal("100.00"),
            high=Decimal("105.00"),
            low=Decimal("95.00"),
            close=Decimal("100.00") + Decimal(i * 10),
            volume=1000,
        )
        for i in range(4)
    ]
    # Candle 0: Close 100
    # Candle 1: Close 110 -> Signal emitted at Candle 1 close
    # Candle 2: Open 100, Close 120 -> Order filled at Candle 2 open
    # Candle 3: Open 100, Close 130
    signals = (
        StrategySignal(
            timestamp="2026-01-05T03:16:00Z",  # Candle 1 close
            action="ENTER_LONG",
            close=Decimal("110.00"),
            fast_sma=Decimal("110.00"),
            slow_sma=Decimal("100.00"),
        ),
    )
    config = BacktestConfig(
        starting_cash=Decimal("100000.00"),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )

    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp=expected_by_timestamp,
    )
    eq_curve = sim["equity_curve"]
    assert isinstance(eq_curve, list)

    # Candle 0 (03:15): Cash 100,000, Position 0, Equity 100,000
    assert eq_curve[0]["cash"] == "100000"
    assert eq_curve[0]["position_quantity"] == "0"
    assert eq_curve[0]["equity"] == "100000"

    # Candle 1 (03:16): Signal generated at close.
    # CRITICAL: Cash must still be 100,000, position 0, equity 100,000 at Candle 1 close!
    assert eq_curve[1]["cash"] == "100000"
    assert eq_curve[1]["position_quantity"] == "0"
    assert eq_curve[1]["equity"] == "100000"

    # Candle 2 (03:17): Order filled at Candle 2 open (price 100.00, qty 10, turnover 1,000).
    # Cash becomes 100,000 - 1,000 = 99,000.
    # Position becomes 10.
    # At Candle 2 close: Close is 120.00. MTM = 10 * 120 = 1,200.
    # Equity at Candle 2 close = 99,000 + 1,200 = 100,200.
    assert eq_curve[2]["cash"] == "99000"
    assert eq_curve[2]["position_quantity"] == "10"
    assert eq_curve[2]["equity"] == "100200"

    # Orders check:
    orders = sim["orders"]
    assert isinstance(orders, list)
    assert len(orders) == 1
    assert orders[0]["status"] == "FILLED"
    assert orders[0]["signal_candle_timestamp"] == "2026-01-05T03:16:00Z"
    assert orders[0]["execution_bar_timestamp"] == "2026-01-05T03:17:00Z"
    assert orders[0]["reference_open"] == "100"
    assert orders[0]["fill_price"] == "100"


def test_volume_liquidity_proxy_uses_prior_completed_bar_not_execution_bar() -> None:
    """Verify that volume capacity check strictly uses candles[index - 1].volume,
    preventing hindsight lookahead into candle[index]'s volume.
    """
    cal = _calendar_fixture()
    expected_by_timestamp = {
        f"2026-01-05T03:{15 + i:02d}:00Z": datetime(2026, 1, 5, 3, 16 + i) for i in range(5)
    }
    # Candle 0: volume = 50. Max participation = 0.10 -> capacity = 5.
    # Candle 1: volume = 1,000,000 (huge volume!).
    # If order is submitted at Candle 0 close, evaluating at Candle 1 open:
    # Liquidity proxy is Candle 0 volume (50) -> capacity = 5.
    # If order quantity is 10, it must be REJECTED despite Candle 1 having 1,000,000 volume!
    candles = [
        StrategyCandle(
            timestamp="2026-01-05T03:15:00Z",
            open=Decimal(100),
            high=Decimal(105),
            low=Decimal(95),
            close=Decimal(100),
            volume=50,  # Prior bar volume
        ),
        StrategyCandle(
            timestamp="2026-01-05T03:16:00Z",
            open=Decimal(100),
            high=Decimal(105),
            low=Decimal(95),
            close=Decimal(105),
            volume=1000000,  # Execution bar volume (hindsight!)
        ),
    ]
    signals = (
        StrategySignal(
            timestamp="2026-01-05T03:15:00Z",
            action="ENTER_LONG",
            close=Decimal(100),
            fast_sma=Decimal(100),
            slow_sma=Decimal(95),
        ),
    )
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,  # 10 > 50 * 0.10 = 5
        max_volume_participation=Decimal("0.10"),
        costs=zero_cost_assumptions(),
    )
    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp=expected_by_timestamp,
    )
    orders = sim["orders"]
    assert isinstance(orders, list)
    assert len(orders) == 1
    assert orders[0]["status"] == "REJECTED"
    assert "prior completed bar's volume" in str(orders[0]["reason"])
    assert orders[0]["volume_capacity"] == 5
    assert orders[0]["prior_bar_volume"] == 50

    # Portfolio state preserved
    eq_curve = sim["equity_curve"]
    assert isinstance(eq_curve, list)
    assert eq_curve[1]["cash"] == "100000"
    assert eq_curve[1]["position_quantity"] == "0"


def test_exit_volume_rejection_preserves_position() -> None:
    """Verify that if an exit order exceeds volume capacity, it is rejected and position is preserved."""
    cal = _calendar_fixture()
    expected_by_timestamp = {
        f"2026-01-05T03:{15 + i:02d}:00Z": datetime(2026, 1, 5, 3, 16 + i) for i in range(5)
    }
    # Candle 0: volume 1000 -> capacity 100
    # Candle 1: order fills -> position 10. Volume drops to 20 -> capacity 2!
    # Candle 2: exit signal at Candle 2 close.
    # Candle 3: exit order evaluated against Candle 2 volume (20) -> capacity 2 < quantity 10 -> REJECTED!
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(100), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:16:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(105), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:17:00Z", Decimal(105), Decimal(105), Decimal(95), Decimal(95), 20
        ),
        StrategyCandle(
            "2026-01-05T03:18:00Z", Decimal(95), Decimal(100), Decimal(90), Decimal(95), 500
        ),
    ]
    signals = (
        StrategySignal(
            "2026-01-05T03:15:00Z", "ENTER_LONG", Decimal(100), Decimal(100), Decimal(95)
        ),
        StrategySignal("2026-01-05T03:17:00Z", "EXIT_LONG", Decimal(95), Decimal(95), Decimal(100)),
    )
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,
        max_volume_participation=Decimal("0.10"),  # capacity at candle 2 is 20 * 0.10 = 2
        costs=zero_cost_assumptions(),
    )
    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp=expected_by_timestamp,
    )
    orders = sim["orders"]
    assert isinstance(orders, list)
    assert len(orders) == 2
    # Buy filled
    assert orders[0]["status"] == "FILLED"
    # Exit rejected due to volume
    assert orders[1]["status"] == "REJECTED"
    assert orders[1]["volume_capacity"] == 2
    # Long position preserved!
    eq_curve = sim["equity_curve"]
    assert isinstance(eq_curve, list)
    assert eq_curve[3]["position_quantity"] == "10"


def test_insufficient_cash_rejects_order_and_preserves_cash() -> None:
    cal = _calendar_fixture()
    expected_by_timestamp = {
        "2026-01-05T03:15:00Z": datetime(2026, 1, 5, 3, 16),
        "2026-01-05T03:16:00Z": datetime(2026, 1, 5, 3, 17),
    }
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(100), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:16:00Z", Decimal(500), Decimal(505), Decimal(495), Decimal(500), 1000
        ),
    ]
    signals = (
        StrategySignal(
            "2026-01-05T03:15:00Z", "ENTER_LONG", Decimal(100), Decimal(100), Decimal(95)
        ),
    )
    # Starting cash = 1,000. But 10 * 500 = 5,000 required!
    config = BacktestConfig(
        starting_cash=Decimal(1000),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp=expected_by_timestamp,
    )
    orders = sim["orders"]
    assert isinstance(orders, list)
    assert orders[0]["status"] == "REJECTED"
    assert "Insufficient cash" in str(orders[0]["reason"])
    # Cash preserved
    assert sim["summary"]["ending_cash"] == "1000"
    assert sim["summary"]["open_position_quantity"] == 0


def test_signal_on_final_candle_is_unfilled_no_next_bar() -> None:
    cal = _calendar_fixture()
    expected_by_timestamp = {
        "2026-01-05T03:15:00Z": datetime(2026, 1, 5, 3, 16),
    }
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(100), 1000
        ),
    ]
    signals = (
        StrategySignal(
            "2026-01-05T03:15:00Z", "ENTER_LONG", Decimal(100), Decimal(100), Decimal(95)
        ),
    )
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp=expected_by_timestamp,
    )
    orders = sim["orders"]
    assert isinstance(orders, list)
    assert orders[0]["status"] == "UNFILLED_NO_NEXT_BAR"


def test_final_exit_signal_stays_unfilled_and_preserves_position() -> None:
    cal = _calendar_fixture()
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(100), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:16:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(100), 1000
        ),
    ]
    signals = (
        StrategySignal(
            "2026-01-05T03:15:00Z", "ENTER_LONG", Decimal(100), Decimal(100), Decimal(95)
        ),
        StrategySignal(
            "2026-01-05T03:16:00Z", "EXIT_LONG", Decimal(100), Decimal(95), Decimal(100)
        ),
    )
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp={
            "2026-01-05T03:15:00Z": datetime(2026, 1, 5, 3, 16),
            "2026-01-05T03:16:00Z": datetime(2026, 1, 5, 3, 17),
        },
    )
    assert [order["status"] for order in sim["orders"]] == ["FILLED", "UNFILLED_NO_NEXT_BAR"]
    assert sim["equity_curve"][-1]["position_quantity"] == "10"
    assert sim["summary"]["open_position_quantity"] == 10
    assert sim["trades"] == []


# ---------------------------------------------------------------------------
# 4. Open Position Reporting & Net Liquidation Value Tests
# ---------------------------------------------------------------------------


def test_open_position_at_end_of_dataset_is_not_silently_liquidated() -> None:
    """Verify that when a backtest ends with an open position:
    - It is not liquidated in trades.
    - open_position dict is populated with mark price and estimated adverse liquidation costs.
    - net_liquidation_value == ending_equity - estimated_liquidation_costs.
    """
    cal = _calendar_fixture()
    expected_by_timestamp = {
        f"2026-01-05T03:{15 + i:02d}:00Z": datetime(2026, 1, 5, 3, 16 + i) for i in range(5)
    }
    # Enter at Candle 1 open (price 100).
    # Candle 2 close is 150.
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(100), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:16:00Z", Decimal(100), Decimal(120), Decimal(98), Decimal(120), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:17:00Z", Decimal(120), Decimal(155), Decimal(115), Decimal(150), 1000
        ),
    ]
    signals = (
        StrategySignal(
            "2026-01-05T03:15:00Z", "ENTER_LONG", Decimal(100), Decimal(100), Decimal(95)
        ),
    )
    costs = CostAssumptions(
        brokerage_rate=Decimal("0.0003"),
        brokerage_cap_per_order=Decimal("20.00"),
        exchange_charge_rate=Decimal("0.0000345"),
        regulatory_charge_rate=Decimal("0.000001"),
        transaction_tax_buy_rate=Decimal("0.001"),
        transaction_tax_sell_rate=Decimal("0.001"),
        stamp_duty_buy_rate=Decimal("0.00015"),
        gst_rate=Decimal("0.18"),
        spread_basis_points=Decimal("10.0"),  # 5 bps half-spread
        slippage_basis_points=Decimal("10.0"),  # 10 bps slippage
    )
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=costs,
    )
    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp=expected_by_timestamp,
    )
    summary = sim["summary"]
    assert isinstance(summary, dict)
    assert summary["open_position_quantity"] == 10
    assert summary["realized_net_pnl"] == "0"
    assert len(sim["trades"]) == 0  # No closed trades!
    assert summary["closed_trade_count"] == 0
    assert summary["win_rate"] is None
    assert summary["profit_factor"] is None
    assert summary["expectancy_per_closed_trade"] is None

    open_pos = summary["open_position"]
    assert isinstance(open_pos, dict)
    assert open_pos["quantity"] == 10
    assert open_pos["mark_price"] == "150"
    assert Decimal(str(open_pos["unrealized_gross_pnl"])) > 0
    assert Decimal(str(open_pos["estimated_liquidation_costs"])) > 0

    # Net liquidation value reconciliation
    ending_equity = Decimal(str(summary["ending_equity"]))
    liq_costs = Decimal(str(summary["estimated_liquidation_costs"]))
    net_liq_val = Decimal(str(summary["net_liquidation_value"]))
    assert net_liq_val == ending_equity - liq_costs


def test_maximum_drawdown_includes_open_position_mark_to_market() -> None:
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z", Decimal(100), Decimal(101), Decimal(99), Decimal(100), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:16:00Z", Decimal(100), Decimal(101), Decimal(79), Decimal(80), 1000
        ),
    ]
    signals = (
        StrategySignal(
            candles[0].timestamp, "ENTER_LONG", candles[0].close, Decimal(101), Decimal(99)
        ),
    )
    expected_by_timestamp = {
        candle.timestamp: datetime(2026, 1, 5, 3, 16 + index)
        for index, candle in enumerate(candles)
    }
    sim = _simulate(
        candles,
        signals,
        config=BacktestConfig(
            Decimal(100000),
            1,
            Decimal("0.5"),
            zero_cost_assumptions(),
        ),
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=_calendar_fixture(),
        expected_by_timestamp=expected_by_timestamp,
    )

    assert sim["summary"]["maximum_drawdown"] == "0.0002"
    assert sim["summary"]["ending_equity"] == "99980"
    assert sim["summary"]["open_position_quantity"] == 1


# ---------------------------------------------------------------------------
# 5. Buy-and-Hold Benchmark Tests
# ---------------------------------------------------------------------------


def test_buy_and_hold_benchmark_happy_path_and_unaffordable() -> None:
    candles = [
        StrategyCandle(
            "2026-01-05T03:15:00Z", Decimal(100), Decimal(105), Decimal(95), Decimal(105), 1000
        ),
        StrategyCandle(
            "2026-01-05T03:16:00Z", Decimal(105), Decimal(120), Decimal(100), Decimal(120), 1000
        ),
    ]
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    bm = _buy_and_hold_benchmark(candles, config=config, tick_size=Decimal("0.05"))
    assert bm["status"] == "COMPLETED"
    assert bm["quantity"] == 10
    assert bm["entry_price"] == "100"
    assert bm["exit_price"] == "120"
    # Turnover: buy 1000, sell 1200 -> profit 200 -> ending cash 100,200
    assert bm["ending_cash"] == "100200"
    assert bm["total_return"] == "0.002"
    assert len(bm["equity_curve"]) == 2

    flat = _buy_and_hold_benchmark(
        [
            StrategyCandle(
                candle.timestamp,
                Decimal(100),
                Decimal(105),
                Decimal(95),
                Decimal(100),
                candle.volume,
            )
            for candle in candles
        ],
        config=config,
        tick_size=Decimal("0.05"),
    )
    assert flat["status"] == "COMPLETED"
    assert flat["total_return"] == "0"

    # Unaffordable benchmark
    poor_config = BacktestConfig(
        starting_cash=Decimal(500),  # Not enough for 10 * 100 = 1000
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    bm_poor = _buy_and_hold_benchmark(candles, config=poor_config, tick_size=Decimal("0.05"))
    assert bm_poor["status"] == "UNAVAILABLE"
    assert "not affordable" in str(bm_poor["reason"])


# ---------------------------------------------------------------------------
# 6. Instrument Eligibility & Registration Tests
# ---------------------------------------------------------------------------


def test_register_backtest_enforces_nse_cash_equities_only(database_path: Path) -> None:
    # 1. Register a BSE instrument and an INDEX instrument in instruments table
    register_instrument(
        database_path,
        InstrumentInput("BSE", "INFY", "Infosys Limited", "EQUITY", segment="BSE"),
    )
    register_instrument(
        database_path,
        InstrumentInput("NSE", "NIFTY 50", "Nifty 50 Index", "INDEX", segment="NSE"),
    )

    # Strategy experiment for BSE
    bse_exp_id = _create_strategy_experiment(database_path, exchange="BSE", trading_symbol="INFY")
    # Strategy experiment for INDEX
    index_exp_id = _create_strategy_experiment(
        database_path, exchange="NSE", trading_symbol="NIFTY 50", instrument_type="INDEX"
    )
    # Strategy experiment for NSE EQUITY
    nse_exp_id = _create_strategy_experiment(database_path, exchange="NSE", trading_symbol="TCS")

    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )

    # BSE equity must be rejected
    with pytest.raises(BacktestError, match="restricted to NSE cash equities"):
        register_backtest(database_path, bse_exp_id, config)

    # INDEX must be rejected
    with pytest.raises(BacktestError, match="restricted to NSE cash equities"):
        register_backtest(database_path, index_exp_id, config)

    # NSE EQUITY succeeds!
    reg = register_backtest(database_path, nse_exp_id, config)
    assert reg.experiment_id > 0
    assert "Backtest — NSE:TCS" in reg.name

    # Check listing
    backtests = list_backtests(database_path)
    assert len(backtests) == 1
    assert backtests[0]["trading_symbol"] == "TCS"


def test_register_backtest_checks_lot_size(database_path: Path) -> None:
    nse_exp_id = _create_strategy_experiment(database_path, exchange="NSE", trading_symbol="TCS")
    # Lot size for TCS is 1. Order quantity 10 is fine.
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    reg = register_backtest(database_path, nse_exp_id, config)
    assert reg.experiment_id > 0


@pytest.mark.parametrize(
    ("configuration_json", "diagnostic"),
    [
        ("{malformed", "malformed configuration JSON"),
        (
            json.dumps({"experiment_type": "BACKTEST", "strategy_snapshot": {}}),
            "unsupported or malformed schema",
        ),
        (
            json.dumps({"experiment_type": "BACKTEST", "backtest_schema_version": 999}),
            "unsupported or malformed schema",
        ),
        (
            json.dumps({"experiment_type": "BACKTEST", "backtest_schema_version": True}),
            "unsupported or malformed schema",
        ),
        (
            json.dumps({"experiment_type": "BACKTEST", "backtest_schema_version": 1}),
            "missing or malformed strategy snapshot",
        ),
        (
            json.dumps(
                {
                    "experiment_type": "BACKTEST",
                    "backtest_schema_version": 1,
                    "strategy_snapshot": {"exchange": "NSE"},
                }
            ),
            "missing or malformed strategy snapshot",
        ),
        (
            json.dumps(
                {
                    "experiment_type": "BACKTEST",
                    "backtest_schema_version": 1,
                    "strategy_snapshot": [],
                }
            ),
            "missing or malformed strategy snapshot",
        ),
    ],
)
def test_list_backtests_reports_malformed_registered_records(
    database_path: Path,
    configuration_json: str,
    diagnostic: str,
) -> None:
    strategy_experiment_id = _create_strategy_experiment(database_path)
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    valid_registration = register_backtest(database_path, strategy_experiment_id, config)
    malformed_registration = register_backtest(database_path, strategy_experiment_id, config)
    try:
        malformed_configuration = json.loads(configuration_json)
    except json.JSONDecodeError:
        malformed_hash = "malformed"
    else:
        malformed_hash = _canonical_hash(malformed_configuration)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """UPDATE experiments
               SET configuration_json = ?, configuration_sha256 = ? WHERE id = ?""",
            (configuration_json, malformed_hash, malformed_registration.experiment_id),
        )
        connection.commit()

    assert len(list_strategy_experiments(database_path)) == 1
    with pytest.raises(
        BacktestError,
        match=rf"Backtest experiment {malformed_registration.experiment_id} .*{diagnostic}",
    ):
        list_backtests(database_path)
    assert valid_registration.experiment_id < malformed_registration.experiment_id


def _create_registered_backtest(database_path: Path) -> tuple[int, int]:
    strategy_experiment_id = _create_strategy_experiment(database_path)
    registration = register_backtest(
        database_path,
        strategy_experiment_id,
        BacktestConfig(
            Decimal(100000),
            1,
            Decimal("0.5"),
            zero_cost_assumptions(),
        ),
    )
    return strategy_experiment_id, registration.experiment_id


def test_list_backtests_accepts_multiple_valid_canonical_registrations(
    database_path: Path,
) -> None:
    strategy_experiment_id = _create_strategy_experiment(database_path)
    config = BacktestConfig(
        Decimal(100000),
        1,
        Decimal("0.5"),
        zero_cost_assumptions(),
    )
    first = register_backtest(database_path, strategy_experiment_id, config)
    second = register_backtest(database_path, strategy_experiment_id, config)

    listed = list_backtests(database_path)

    assert [item["id"] for item in listed] == [second.experiment_id, first.experiment_id]
    assert all(item["strategy_experiment_id"] == strategy_experiment_id for item in listed)
    with closing(get_connection(database_path)) as connection:
        for registration in (first, second):
            row = connection.execute(
                """SELECT configuration_json, configuration_sha256
                   FROM experiments WHERE id = ?""",
                (registration.experiment_id,),
            ).fetchone()
            assert row["configuration_sha256"] == _canonical_hash(
                json.loads(row["configuration_json"])
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy_experiment_id", "not-an-integer"),
        ("strategy_experiment_id", True),
        ("strategy_experiment_id", 1.5),
        ("strategy_id", "2"),
        ("strategy_id", False),
        ("strategy_id", 2.0),
        ("hypothesis_id", "1"),
        ("hypothesis_id", True),
    ],
)
def test_list_backtests_rejects_invalid_snapshot_identifier_types(
    database_path: Path,
    field: str,
    value: object,
) -> None:
    _, experiment_id = _create_registered_backtest(database_path)
    _rewrite_backtest_configuration(
        database_path,
        experiment_id,
        lambda configuration: configuration["strategy_snapshot"].__setitem__(field, value),
    )

    with pytest.raises(BacktestError, match=rf"Backtest experiment {experiment_id} .*ID type"):
        list_backtests(database_path)


def test_list_backtests_rejects_missing_required_snapshot_value(database_path: Path) -> None:
    _, experiment_id = _create_registered_backtest(database_path)
    _rewrite_backtest_configuration(
        database_path,
        experiment_id,
        lambda configuration: configuration["strategy_snapshot"].pop("strategy_spec_sha256"),
    )

    with pytest.raises(
        BacktestError,
        match=rf"Backtest experiment {experiment_id} has a missing or malformed strategy snapshot",
    ):
        list_backtests(database_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", 7),
        ("version", 1),
        ("fast_window", True),
        ("strategy_key", "unsupported_strategy"),
    ],
)
def test_list_backtests_rejects_invalid_nested_strategy_specification(
    database_path: Path,
    field: str,
    value: object,
) -> None:
    _, experiment_id = _create_registered_backtest(database_path)

    def update(configuration: dict[str, object]) -> None:
        strategy = configuration["strategy_snapshot"]
        spec = strategy["strategy_spec"]
        spec[field] = value
        if field == "version":
            strategy["strategy_spec_sha256"] = _canonical_hash(spec)

    _rewrite_backtest_configuration(database_path, experiment_id, update)

    with pytest.raises(
        BacktestError,
        match=rf"Backtest experiment {experiment_id} has an invalid strategy specification",
    ):
        list_backtests(database_path)


def test_list_backtests_rejects_noncanonical_nested_strategy_specification(
    database_path: Path,
) -> None:
    _, experiment_id = _create_registered_backtest(database_path)
    _rewrite_backtest_configuration(
        database_path,
        experiment_id,
        lambda configuration: configuration["strategy_snapshot"].__setitem__("strategy_spec", []),
    )

    with pytest.raises(
        BacktestError,
        match=rf"Backtest experiment {experiment_id} has an invalid strategy specification",
    ):
        list_backtests(database_path)


def test_list_backtests_rejects_nonexistent_strategy_experiment(database_path: Path) -> None:
    _, experiment_id = _create_registered_backtest(database_path)
    _rewrite_backtest_configuration(
        database_path,
        experiment_id,
        lambda configuration: configuration["strategy_snapshot"].__setitem__(
            "strategy_experiment_id", 999999
        ),
    )

    with pytest.raises(
        BacktestError,
        match=rf"Backtest experiment {experiment_id} has an invalid strategy reference",
    ):
        list_backtests(database_path)


@pytest.mark.parametrize(
    ("mutation", "diagnostic"),
    [
        (
            lambda strategy: strategy.__setitem__("strategy_id", strategy["strategy_id"] + 1),
            "strategy snapshot does not match the referenced Strategy Lab experiment",
        ),
        (
            lambda strategy: strategy.__setitem__("exchange", "BSE"),
            "strategy snapshot does not match the referenced Strategy Lab experiment",
        ),
        (
            lambda strategy: (
                strategy["strategy_spec"].__setitem__("version", "2.0.0"),
                strategy.__setitem__(
                    "strategy_spec_sha256",
                    _canonical_hash(
                        {
                            **strategy["strategy_spec"],
                            "version": "2.0.0",
                        }
                    ),
                ),
            ),
            "strategy snapshot does not match the referenced Strategy Lab experiment",
        ),
    ],
)
def test_list_backtests_rejects_snapshot_identity_or_version_mismatch(
    database_path: Path,
    mutation: Callable[[dict[str, object]], object],
    diagnostic: str,
) -> None:
    _, experiment_id = _create_registered_backtest(database_path)
    _rewrite_backtest_configuration(
        database_path,
        experiment_id,
        lambda configuration: mutation(configuration["strategy_snapshot"]),
    )

    with pytest.raises(
        BacktestError,
        match=rf"Backtest experiment {experiment_id} {diagnostic}",
    ):
        list_backtests(database_path)


@pytest.mark.parametrize(
    ("stored_hash", "diagnostic"),
    [
        (None, "missing configuration SHA-256"),
        ("not-a-hash", "malformed configuration SHA-256"),
        ("a" * 64, "configuration hash does not match"),
    ],
)
def test_list_backtests_rejects_missing_malformed_or_incorrect_configuration_hash(
    database_path: Path,
    stored_hash: str | None,
    diagnostic: str,
) -> None:
    _, experiment_id = _create_registered_backtest(database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE experiments SET configuration_sha256 = ? WHERE id = ?",
            (stored_hash, experiment_id),
        )
        connection.commit()

    with pytest.raises(BacktestError, match=rf"Backtest experiment {experiment_id} .*{diagnostic}"):
        list_backtests(database_path)


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("strategy_spec_sha256", "bad", "invalid strategy specification SHA-256"),
        ("strategy_configuration_sha256", "bad", "invalid strategy configuration SHA-256"),
        ("strategy_configuration_hash_origin", "", "invalid strategy configuration hash origin"),
        ("exchange", "", "missing or invalid strategy snapshot field exchange"),
        ("trading_symbol", None, "missing or invalid strategy snapshot field trading_symbol"),
    ],
)
def test_list_backtests_rejects_invalid_snapshot_metadata(
    database_path: Path,
    field: str,
    value: object,
    diagnostic: str,
) -> None:
    _, experiment_id = _create_registered_backtest(database_path)
    _rewrite_backtest_configuration(
        database_path,
        experiment_id,
        lambda configuration: configuration["strategy_snapshot"].__setitem__(field, value),
    )

    with pytest.raises(BacktestError, match=rf"Backtest experiment {experiment_id} .*{diagnostic}"):
        list_backtests(database_path)


def test_list_backtests_preserves_unrelated_null_strategy_record_handling(
    database_path: Path,
) -> None:
    _, valid_experiment_id = _create_registered_backtest(database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """INSERT INTO experiments (name, strategy_id, configuration_json, status)
               VALUES ('Unrelated legacy record', NULL, '{malformed', 'CREATED')"""
        )
        connection.commit()

    assert [row["id"] for row in list_backtests(database_path)] == [valid_experiment_id]


def test_list_backtests_preserves_legacy_typed_null_strategy_record(
    database_path: Path,
) -> None:
    _, registered_experiment_id = _create_registered_backtest(database_path)
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            """SELECT name, configuration_json FROM experiments WHERE id = ?""",
            (registered_experiment_id,),
        ).fetchone()
        cursor = connection.execute(
            """INSERT INTO experiments (name, strategy_id, hypothesis_id,
                       configuration_json, status)
               VALUES (?, NULL, NULL, ?, 'CREATED')""",
            (f"Legacy {row['name']}", row["configuration_json"]),
        )
        legacy_experiment_id = int(cursor.lastrowid)
        connection.commit()

    listed_ids = [row["id"] for row in list_backtests(database_path)]
    assert listed_ids == [legacy_experiment_id, registered_experiment_id]


def test_list_backtests_mixed_valid_and_malformed_records_fails_all_or_nothing(
    database_path: Path,
) -> None:
    _, valid_experiment_id = _create_registered_backtest(database_path)
    _, malformed_experiment_id = _create_registered_backtest(database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE experiments SET configuration_sha256 = ? WHERE id = ?",
            ("f" * 64, malformed_experiment_id),
        )
        connection.commit()

    with pytest.raises(
        BacktestError,
        match=rf"Backtest experiment {malformed_experiment_id} configuration hash does not match",
    ):
        list_backtests(database_path)
    assert valid_experiment_id < malformed_experiment_id
    assert len(list_strategy_experiments(database_path)) == 2


def test_inactive_instrument_blocks_later_run(database_path: Path, tmp_path: Path) -> None:
    exp_id = _create_strategy_experiment(database_path)
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    registration = register_backtest(database_path, exp_id, config)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("UPDATE instruments SET is_active = 0 WHERE trading_symbol = 'TCS'")
        connection.commit()

    calendar_path = tmp_path / "empty_calendars.json"
    calendar_path.write_text(json.dumps({"schema_version": 1, "calendars": []}), encoding="utf-8")
    result = run_backtest(
        database_path,
        registration.experiment_id,
        calendars=load_calendar_registry(calendar_path),
        as_of=_AS_OF,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "Strategy instrument is no longer active."


def test_register_backtest_rejects_inactive_instrument(database_path: Path) -> None:
    exp_id = _create_strategy_experiment(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("UPDATE instruments SET is_active = 0 WHERE trading_symbol = 'TCS'")
        connection.commit()

    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    with pytest.raises(BacktestError, match="active registered instrument"):
        register_backtest(database_path, exp_id, config)


def test_backtest_run_rejects_broken_relational_source_link(database_path: Path) -> None:
    exp_id = _create_strategy_experiment(database_path)
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    registration = register_backtest(database_path, exp_id, config)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            "UPDATE experiments SET strategy_id = NULL WHERE id = ?",
            (registration.experiment_id,),
        )
        connection.commit()

    with pytest.raises(BacktestError, match="relational source links"):
        run_backtest(
            database_path,
            registration.experiment_id,
            calendars=_calendar_fixture(),
            as_of=_AS_OF,
        )


# ---------------------------------------------------------------------------
# 7. End-to-End Execution, Fail-Closed Gates, and Provenance Tests
# ---------------------------------------------------------------------------


def test_run_backtest_fails_closed_when_data_gate_blocks(
    database_path: Path, tmp_path: Path
) -> None:
    """When calendar is unconfirmed or data is missing, run_backtest records BLOCKED status."""
    nse_exp_id = _create_strategy_experiment(database_path, exchange="NSE", trading_symbol="TCS")
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    reg = register_backtest(database_path, nse_exp_id, config)

    # Using default unconfirmed/empty exchange calendar
    cal_path = tmp_path / "empty_calendars.json"
    cal_path.write_text(json.dumps({"schema_version": 1, "calendars": []}), encoding="utf-8")
    registry = load_calendar_registry(cal_path)

    run_res = run_backtest(
        database_path,
        reg.experiment_id,
        calendars=registry,
        as_of=_AS_OF,
    )
    assert run_res.status == "BLOCKED"
    assert run_res.reason is not None

    # Check runs record in database
    runs = list_backtest_runs(database_path, reg.experiment_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "BLOCKED"
    assert runs[0]["result"]["provenance_status"] == "PARTIAL_BLOCKED"


def test_run_backtest_end_to_end_with_confirmed_calendar_and_complete_data(
    database_path: Path, tmp_path: Path
) -> None:
    """Full end-to-end backtest run with confirmed calendar, complete candles, trades, and provenance."""
    # 1. Setup confirmed calendar for 09:15 - 09:21 (6 minutes = 6 candles)
    cal_file = tmp_path / "exchange_calendars.json"
    cal_file.write_text(
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
                        "source": "Deterministic test fixture",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:21"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calendars = load_calendar_registry(cal_file)

    # 2. Import 6 completed candles
    # Fast window = 2, slow window = 3
    # Candle 0 (09:15): Close 30
    # Candle 1 (09:16): Close 20
    # Candle 2 (09:17): Close 10 -> Fast 15 <= Slow 20
    # Candle 3 (09:18): Close 20 -> Fast 15 <= Slow 16.67
    # Candle 4 (09:19): Close 40 -> Fast 30 > Slow 23.33 -> ENTER_LONG at Candle 4 close!
    # Candle 5 (09:20): Open 42 -> Filled at open! Close 50 -> Dataset ends with open position.
    csv_rows = [
        ("2026-01-05T09:15:00+05:30", 30, 32, 28, 30, 1000),
        ("2026-01-05T09:16:00+05:30", 21, 25, 19, 20, 1000),
        ("2026-01-05T09:17:00+05:30", 19, 22, 9, 10, 1000),
        ("2026-01-05T09:18:00+05:30", 11, 22, 10, 20, 1000),
        ("2026-01-05T09:19:00+05:30", 21, 42, 20, 40, 1000),
        ("2026-01-05T09:20:00+05:30", 42, 52, 40, 50, 1000),
    ]
    csv_path = _write_csv(tmp_path, csv_rows)
    imported = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
    declare_import_source_use_status(
        database_path,
        imported.import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic test administrator",
        rationale="Synthetic fixture explicitly permitted for this isolated test.",
    )

    # 3. Create Strategy experiment & Backtest experiment
    exp_id = _create_strategy_experiment(database_path, fast_window=2, slow_window=3)
    config = BacktestConfig(
        starting_cash=Decimal("100000.00"),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=default_cost_assumptions(),
    )
    reg = register_backtest(database_path, exp_id, config)

    # 4. Run Backtest
    as_of = datetime(2026, 1, 5, 9, 22, tzinfo=ZoneInfo("Asia/Kolkata"))
    run_res = run_backtest(
        database_path,
        reg.experiment_id,
        calendars=calendars,
        as_of=as_of,
    )
    assert run_res.status == "COMPLETED", run_res.reason
    assert run_res.reason is None

    # 5. Verify results and provenance in database
    runs = list_backtest_runs(database_path, reg.experiment_id)
    assert len(runs) == 1
    result = runs[0]["result"]
    assert result["status"] == "COMPLETED"

    # Metadata & Provenance
    meta = result["metadata"]
    assert meta["provenance_status"] == "COMPLETE"
    assert meta["instrument_snapshot"]["instrument_id"] > 0
    assert meta["instrument_snapshot"]["tick_size_decimal"] == "0.05"
    assert meta["instrument_snapshot_sha256"]
    assert len(meta["source_imports"]) == 1
    assert meta["source_imports"][0]["source_use_status"] == "VERIFIED_PERMITTED"
    assert meta["source_imports"][0]["declared_by"] == "synthetic test administrator"
    assert meta["strategy_experiment_id"] == exp_id
    assert meta["backtesting_implementation_sha256"] is not None
    assert meta["dataset_sha256"] is not None
    assert meta["calendar_confidence"] == "CONFIRMED"
    assert meta["supported_execution_scope"] == "NSE cash equities (EQUITY) only."

    # Summary
    summary = result["summary"]
    assert summary["open_position_quantity"] == 10
    assert Decimal(str(summary["total_return"])) > 0
    assert Decimal(str(summary["net_liquidation_value"])) > 0
    assert summary["benchmark_total_return"] == result["benchmark"]["total_return"]
    assert summary["relative_performance_vs_same_instrument_buy_and_hold"] is not None

    # Open position
    open_pos = result["open_position"]
    assert open_pos["quantity"] == 10
    assert open_pos["mark_price"] == "50"

    # Orders & Benchmark
    assert len(result["orders"]) == 1
    assert result["orders"][0]["status"] == "FILLED"
    assert result["benchmark"]["status"] == "COMPLETED"
    assert len(result["benchmark"]["equity_curve"]) == 6

    first_result = result
    run_backtest(
        database_path,
        reg.experiment_id,
        calendars=calendars,
        as_of=as_of,
    )
    repeated_result = list_backtest_runs(database_path, reg.experiment_id)[0]["result"]
    assert repeated_result == first_result

    update_instrument(database_path, "NSE", "TCS", {"tick_size": Decimal("0.10")})
    run_backtest(
        database_path,
        reg.experiment_id,
        calendars=calendars,
        as_of=as_of,
    )
    results_after_tick_change = list_backtest_runs(database_path, reg.experiment_id)
    latest_result = results_after_tick_change[0]["result"]
    assert latest_result["metadata"]["instrument_snapshot"]["tick_size_decimal"] == "0.1"
    assert latest_result["orders"][0]["fill_price"] == "42.1"
    assert results_after_tick_change[1]["result"] == first_result

    declare_import_source_use_status(
        database_path,
        imported.import_id,
        "UNVERIFIED",
        declared_by="synthetic test administrator",
        rationale="Test that a later status change does not rewrite completed run provenance.",
    )
    persisted_completed = list_backtest_runs(database_path, reg.experiment_id)[1]["result"]
    assert persisted_completed["metadata"]["source_imports"][0]["source_use_status"] == (
        "VERIFIED_PERMITTED"
    )


def test_tampered_backtest_configuration_fails_integrity(database_path: Path) -> None:
    exp_id = _create_strategy_experiment(database_path)
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    reg = register_backtest(database_path, exp_id, config)

    # Tamper with the configuration_json in SQLite
    with closing(sqlite3.connect(database_path)) as conn:
        conn.execute(
            "UPDATE experiments SET configuration_json = ? WHERE id = ?",
            ('{"tampered": true}', reg.experiment_id),
        )
        conn.commit()

    with pytest.raises(BacktestError, match="integrity check failed|is malformed|not a supported"):
        run_backtest(
            database_path,
            reg.experiment_id,
            calendars=_calendar_fixture(),  # type: ignore[arg-type]
            as_of=_AS_OF,
        )


def test_run_backtest_blocks_without_authoritative_decimal_tick_size(
    database_path: Path, tmp_path: Path
) -> None:
    """If an instrument has null tick_size_decimal or non-null legacy tick_size, fail closed."""
    # Register an instrument with tick_size = None
    register_instrument(
        database_path,
        InstrumentInput("NSE", "NOTICK", "No Tick Instrument", "EQUITY", segment="NSE"),
    )
    exp_id = _create_strategy_experiment(database_path, exchange="NSE", trading_symbol="NOTICK")
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=1,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    reg = register_backtest(database_path, exp_id, config)

    # Setup calendar & 1 candle
    cal_file = tmp_path / "exchange_calendars.json"
    cal_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": _DAY.isoformat(),
                        "coverage_end": _DAY.isoformat(),
                        "source": "Deterministic fixture",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:16"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calendars = load_calendar_registry(cal_file)
    csv_rows = [("2026-01-05T09:15:00+05:30", 100, 105, 95, 100, 1000)]
    csv_path = _write_csv(tmp_path, csv_rows)
    import_ohlcv_csv(database_path, csv_path, "NSE", "NOTICK", "1m")

    run_res = run_backtest(
        database_path,
        reg.experiment_id,
        calendars=calendars,
        as_of=datetime(2026, 1, 5, 9, 17, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    assert run_res.status == "BLOCKED"
    assert "authoritative decimal tick size" in str(run_res.reason)


def test_duplicate_signal_rejections_and_closed_trade_round_trip() -> None:
    """Verify round trip execution, trade PnL reconciliation, and signal rejections."""
    cal = _calendar_fixture()
    expected_by_timestamp = {
        f"2026-01-05T03:{15 + i:02d}:00Z": datetime(2026, 1, 5, 3, 16 + i) for i in range(10)
    }
    candles = [
        StrategyCandle(
            f"2026-01-05T03:{15 + i:02d}:00Z",
            Decimal(100),
            Decimal(110),
            Decimal(90),
            Decimal(100),
            1000,
        )
        for i in range(5)
    ]
    # Candle 0: sell signal when not long -> rejected!
    # Candle 1: buy signal -> pending order
    # Candle 2: filled at open. At Candle 2 close: another buy signal -> rejected (already long)!
    # Candle 3: exit signal -> pending order
    # Candle 4: exit filled at open. Position is 0!
    signals = (
        StrategySignal(
            "2026-01-05T03:15:00Z", "EXIT_LONG", Decimal(100), Decimal(90), Decimal(100)
        ),
        StrategySignal(
            "2026-01-05T03:16:00Z", "ENTER_LONG", Decimal(100), Decimal(110), Decimal(100)
        ),
        StrategySignal(
            "2026-01-05T03:17:00Z", "ENTER_LONG", Decimal(100), Decimal(110), Decimal(100)
        ),
        StrategySignal(
            "2026-01-05T03:18:00Z", "EXIT_LONG", Decimal(100), Decimal(90), Decimal(100)
        ),
    )
    config = BacktestConfig(
        starting_cash=Decimal(100000),
        order_quantity=10,
        max_volume_participation=Decimal("0.5"),
        costs=zero_cost_assumptions(),
    )
    sim = _simulate(
        candles,
        signals,
        config=config,
        tick_size=Decimal("0.05"),
        interval="1m",
        calendar=cal,
        expected_by_timestamp=expected_by_timestamp,
    )
    orders = sim["orders"]
    assert isinstance(orders, list)
    # Order 0: EXIT_LONG rejected at submission (no position)
    assert orders[0]["status"] == "REJECTED"
    assert "No open long position exists" in str(orders[0]["reason"])

    # Order 1: ENTER_LONG submitted at Candle 1 close, filled at Candle 2 open
    assert orders[1]["status"] == "FILLED"

    # Order 2: ENTER_LONG rejected at Candle 2 close (already long)
    assert orders[2]["status"] == "REJECTED"
    assert "already open" in str(orders[2]["reason"])

    # Order 3: EXIT_LONG submitted at Candle 3 close, filled at Candle 4 open
    assert orders[3]["status"] == "FILLED"

    # Closed trades
    trades = sim["trades"]
    assert isinstance(trades, list)
    assert len(trades) == 1
    trade = trades[0]
    assert trade["quantity"] == 10
    assert trade["entry_price"] == "100"
    assert trade["exit_price"] == "100"
    assert trade["gross_pnl"] == "0"
    assert trade["net_pnl"] == "0"
    assert sim["summary"]["open_position_quantity"] == 0
    assert sim["summary"]["closed_trade_count"] == 1


def test_backtest_blocks_when_strategy_history_is_below_required_minimum(
    database_path: Path, tmp_path: Path
) -> None:
    experiment_id, result = _run_synthetic_backtest(
        database_path,
        tmp_path,
        [30, 20, 10],
        slow_window=3,
        name="below_minimum",
    )
    assert result["status"] == "BLOCKED"
    assert "requires 4 candles but only 3" in result["reason"]
    assert "Simple moving-average crossover" in result["reason"]
    assert "version 1.0.0" in result["reason"]
    assert list_backtest_runs(database_path, experiment_id)[0]["status"] == "BLOCKED"


def test_backtest_accepts_exact_and_above_minimum_strategy_history(
    database_path: Path, tmp_path: Path
) -> None:
    exact_id, exact = _run_synthetic_backtest(
        database_path,
        tmp_path,
        [20, 20, 20, 20],
        slow_window=3,
        name="exact_minimum",
    )
    assert list_backtest_runs(database_path, exact_id)[0]["status"] == "COMPLETED"
    assert exact["orders"] == []
    assert exact["trades"] == []

    above_id, above = _run_synthetic_backtest(
        database_path,
        tmp_path,
        [30, 20, 10, 20, 40, 50],
        slow_window=3,
        name="above_minimum",
    )
    assert list_backtest_runs(database_path, above_id)[0]["status"] == "COMPLETED"
    assert above["orders"][0]["status"] == "FILLED"
    assert above["open_position"]["quantity"] == 1


def test_history_requirement_comes_from_registered_strategy_configuration(
    database_path: Path, tmp_path: Path
) -> None:
    experiment_id, result = _run_synthetic_backtest(
        database_path,
        tmp_path,
        [30, 20, 10, 20, 40],
        fast_window=3,
        slow_window=5,
        name="different_lookback",
    )
    assert result["status"] == "BLOCKED"
    assert "requires 6 candles but only 5" in result["reason"]
    assert list_backtest_runs(database_path, experiment_id)[0]["status"] == "BLOCKED"


def test_sufficient_history_with_no_signals_is_a_valid_no_trade_run(
    database_path: Path, tmp_path: Path
) -> None:
    experiment_id, result = _run_synthetic_backtest(
        database_path,
        tmp_path,
        [20, 20, 20, 20, 20, 20],
        name="sufficient_no_signals",
    )
    assert result["status"] == "COMPLETED"
    assert result["orders"] == []
    assert result["trades"] == []
    assert result["summary"]["closed_trade_count"] == 0
    assert list_backtest_runs(database_path, experiment_id)[0]["status"] == "COMPLETED"


def test_strategy_listing_excludes_backtests_but_preserves_relational_links(
    database_path: Path,
) -> None:
    strategy_experiment_id = _create_strategy_experiment(database_path)
    with closing(get_connection(database_path)) as connection:
        original_strategy_id = connection.execute(
            "SELECT strategy_id FROM experiments WHERE id = ?",
            (strategy_experiment_id,),
        ).fetchone()["strategy_id"]
    registration = register_backtest(
        database_path,
        strategy_experiment_id,
        BacktestConfig(
            Decimal(100000),
            1,
            Decimal("0.5"),
            zero_cost_assumptions(),
        ),
    )
    listed = list_strategy_experiments(database_path)
    assert [item["id"] for item in listed] == [strategy_experiment_id]
    with closing(get_connection(database_path)) as connection:
        linked = connection.execute(
            "SELECT strategy_id FROM experiments WHERE id = ?",
            (registration.experiment_id,),
        ).fetchone()
    assert linked["strategy_id"] == original_strategy_id


def test_strategy_listing_still_raises_for_malformed_strategy_and_reads_legacy_records(
    database_path: Path,
) -> None:
    malformed_id = _create_strategy_experiment(database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE experiments SET configuration_json = ? WHERE id = ?",
            ('{"malformed": true}', malformed_id),
        )
        connection.commit()
    with pytest.raises(StrategyLabError, match=f"Experiment {malformed_id} has malformed"):
        list_strategy_experiments(database_path)

    legacy_id = _create_strategy_experiment(database_path, trading_symbol="TCS")
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT configuration_json FROM experiments WHERE id = ?", (legacy_id,)
        ).fetchone()
        legacy_configuration = json.loads(row["configuration_json"])
        legacy_configuration["configuration_schema_version"] = 1
        legacy_configuration.pop("strategy_identity")
        connection.execute(
            "UPDATE experiments SET configuration_json = ? WHERE id = ?",
            (json.dumps(legacy_configuration), legacy_id),
        )
        connection.commit()
    # The intentionally malformed record still fails closed; the legacy entry remains parseable.
    with closing(get_connection(database_path)) as connection:
        connection.execute("DELETE FROM experiments WHERE id = ?", (malformed_id,))
        connection.commit()
    assert [item["id"] for item in list_strategy_experiments(database_path)] == [legacy_id]


@pytest.mark.parametrize(
    ("source_status", "expected_reason"),
    [
        ("UNVERIFIED", "not declared VERIFIED_PERMITTED"),
        ("PROHIBITED", "marked PROHIBITED"),
    ],
)
def test_backtest_fails_closed_for_unverified_and_prohibited_imports(
    database_path: Path,
    tmp_path: Path,
    source_status: SourceUseStatus,
    expected_reason: str,
) -> None:
    experiment_id, result = _run_synthetic_backtest(
        database_path,
        tmp_path,
        [30, 20, 10, 20, 40, 50],
        source_use_status=source_status,
        name=f"permission_{source_status.lower()}",
    )
    assert result["status"] == "BLOCKED"
    assert expected_reason in result["reason"]
    assert result["source_imports"][0]["source_use_status"] == source_status
    assert list_backtest_runs(database_path, experiment_id)[0]["status"] == "BLOCKED"


def test_mixed_source_dataset_requires_every_contributing_import_to_be_permitted(
    database_path: Path, tmp_path: Path
) -> None:
    calendar_path = _create_calendar_file(tmp_path, 6, "mixed_source")
    first_rows = [
        (f"2026-01-05T09:{15 + index:02d}:00+05:30", price, price + 2, price - 2, price, 1000)
        for index, price in enumerate((30, 20, 10))
    ]
    later_rows = [
        (f"2026-01-05T09:{18 + index:02d}:00+05:30", price, price + 2, price - 2, price, 1000)
        for index, price in enumerate((20, 40, 50))
    ]
    first_import = import_ohlcv_csv(
        database_path,
        _write_csv(tmp_path, first_rows, filename="first_source.csv"),
        "NSE",
        "TCS",
        "1m",
    )
    declare_import_source_use_status(
        database_path,
        first_import.import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic test administrator",
        rationale="First synthetic fixture permitted for this test.",
    )
    second_import = import_ohlcv_csv(
        database_path,
        _write_csv(tmp_path, later_rows, filename="second_source.csv"),
        "NSE",
        "TCS",
        "1m",
    )
    assert second_import.import_id != first_import.import_id
    assert list_recent_imports(database_path)[0]["source_use_status"] == "UNVERIFIED"
    strategy_experiment_id = _create_strategy_experiment(database_path)
    registration = register_backtest(
        database_path,
        strategy_experiment_id,
        BacktestConfig(
            Decimal(100000),
            1,
            Decimal("0.5"),
            zero_cost_assumptions(),
        ),
    )
    run_backtest(
        database_path,
        registration.experiment_id,
        calendars=load_calendar_registry(calendar_path),
        as_of=datetime(2026, 1, 5, 9, 22, tzinfo=ZoneInfo("Asia/Kolkata")),
    )
    result = list_backtest_runs(database_path, registration.experiment_id)[0]["result"]
    assert result["status"] == "BLOCKED"
    assert {item["source_use_status"] for item in result["source_imports"]} == {
        "VERIFIED_PERMITTED",
        "UNVERIFIED",
    }


def test_source_use_declaration_requires_explicit_auditable_user_action(
    database_path: Path, tmp_path: Path
) -> None:
    csv_path = _write_csv(
        tmp_path,
        [("2026-01-05T09:15:00+05:30", 100, 102, 98, 100, 1000)],
    )
    imported = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
    assert list_recent_imports(database_path)[0]["source_use_status"] == "UNVERIFIED"
    with pytest.raises(SourceUseStatusError, match="declared_by"):
        declare_import_source_use_status(
            database_path,
            imported.import_id,
            "VERIFIED_PERMITTED",
            declared_by=" ",
            rationale="Explicit synthetic test declaration.",
        )
    with pytest.raises(SourceUseStatusError, match="rationale"):
        declare_import_source_use_status(
            database_path,
            imported.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test administrator",
            rationale=" ",
        )
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT source_use_status FROM data_imports WHERE id = ?",
                (imported.import_id,),
            ).fetchone()["source_use_status"]
            == "UNVERIFIED"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM import_source_use_history WHERE import_id = ?",
                (imported.import_id,),
            ).fetchone()[0]
            == 0
        )


def test_legacy_backtest_provenance_is_marked_incomplete_without_fabrication(
    database_path: Path,
) -> None:
    experiment_id = _create_strategy_experiment(database_path)
    registration = register_backtest(
        database_path,
        experiment_id,
        BacktestConfig(
            Decimal(100000),
            1,
            Decimal("0.5"),
            zero_cost_assumptions(),
        ),
    )
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """INSERT INTO runs (experiment_id, status, result_json)
               VALUES (?, 'COMPLETED', ?)""",
            (
                registration.experiment_id,
                json.dumps(
                    {
                        "status": "COMPLETED",
                        "metadata": {"provenance_status": "COMPLETE"},
                    }
                ),
            ),
        )
        connection.execute(
            """INSERT INTO runs (experiment_id, status, result_json)
               VALUES (?, 'COMPLETED', ?)""",
            (registration.experiment_id, json.dumps({"status": "COMPLETED"})),
        )
        connection.commit()
    legacy_runs = list_backtest_runs(database_path, registration.experiment_id)
    legacy_without_metadata = legacy_runs[0]["result"]
    assert legacy_without_metadata["metadata"]["provenance_status"] == "LEGACY_INCOMPLETE"
    assert legacy_without_metadata["metadata"]["legacy_missing_provenance_fields"] == [
        "instrument_snapshot",
        "source_imports",
    ]
    assert "metadata" in legacy_without_metadata

    legacy = legacy_runs[1]["result"]
    assert legacy["metadata"]["provenance_status"] == "LEGACY_INCOMPLETE"
    assert legacy["metadata"]["legacy_missing_provenance_fields"] == [
        "instrument_snapshot",
        "source_imports",
    ]
    assert "instrument_snapshot" not in legacy["metadata"]
