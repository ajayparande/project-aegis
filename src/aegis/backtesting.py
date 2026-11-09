"""Deterministic, research-only backtesting for registered Strategy Lab experiments."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
    localcontext,
)
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from aegis.database import get_connection
from aegis.market_quality import (
    CalendarRegistry,
    ExchangeCalendar,
    analyze_dataset,
    calculate_expected_candles,
)
from aegis.strategy_lab import (
    StrategyCandle,
    StrategySignal,
    StrategySpec,
    _canonical_hash,
    _canonical_json,
    _effective_calendar_hash,
    _gate_reason,
    _load_completed_candles,
    _parse_spec,
    _sha256_file,
    _source_revision,
    _validate_candles,
    _validate_experiment_configuration,
    _validate_legacy_strategy_reference,
    generate_sma_crossover_signals,
)

BACKTEST_SCHEMA_VERSION = 1
_MONEY_QUANTUM = Decimal("0.01")
_BASIS_POINT_DENOMINATOR = Decimal(10000)


class BacktestError(ValueError):
    """Raised when a backtest configuration or execution input is invalid."""


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    """User-supplied charges; rates are applied to turnover on the documented side.

    A zero brokerage cap means uncapped percentage brokerage. A positive cap limits
    percentage brokerage per order; it is not a flat charge when the rate is zero.
    """

    brokerage_rate: Decimal
    brokerage_cap_per_order: Decimal
    exchange_charge_rate: Decimal
    regulatory_charge_rate: Decimal
    transaction_tax_buy_rate: Decimal
    transaction_tax_sell_rate: Decimal
    stamp_duty_buy_rate: Decimal
    gst_rate: Decimal
    spread_basis_points: Decimal
    slippage_basis_points: Decimal

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise BacktestError(f"{name} must be a finite, non-negative Decimal.")
            if name.endswith("_rate") and value > 1:
                raise BacktestError(f"{name} must be a decimal rate between 0 and 1.")

    def to_dict(self) -> dict[str, str]:
        return {name: _decimal_text(value) for name, value in asdict(self).items()}


def default_cost_assumptions() -> CostAssumptions:
    """Standard Indian cash equity delivery/intraday baseline assumption template.

    Note: Rates are user-supplied assumptions for research simulation.
    No broker or exchange fee schedule is bundled or claimed to be official.
    """
    return CostAssumptions(
        brokerage_rate=Decimal("0.0003"),
        brokerage_cap_per_order=Decimal("20.00"),
        exchange_charge_rate=Decimal("0.0000345"),
        regulatory_charge_rate=Decimal("0.000001"),
        transaction_tax_buy_rate=Decimal("0.001"),
        transaction_tax_sell_rate=Decimal("0.001"),
        stamp_duty_buy_rate=Decimal("0.00015"),
        gst_rate=Decimal("0.18"),
        spread_basis_points=Decimal("2.0"),
        slippage_basis_points=Decimal("5.0"),
    )


def zero_cost_assumptions() -> CostAssumptions:
    """Frictionless cost assumptions for testing baseline theoretical alpha."""
    return CostAssumptions(
        brokerage_rate=Decimal(0),
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


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Frozen portfolio sizing, volume participation, and cost assumptions."""

    starting_cash: Decimal
    order_quantity: int
    max_volume_participation: Decimal
    costs: CostAssumptions

    def __post_init__(self) -> None:
        if (
            not isinstance(self.starting_cash, Decimal)
            or not self.starting_cash.is_finite()
            or self.starting_cash <= 0
        ):
            raise BacktestError("starting_cash must be a finite positive Decimal.")
        exponent = self.starting_cash.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -2:
            sub_paisa_digits = -2 - exponent
            digits = self.starting_cash.as_tuple().digits
            if any(digit != 0 for digit in digits[max(0, len(digits) - sub_paisa_digits) :]):
                raise BacktestError("starting_cash must be representable to INR 0.01.")
        if (
            isinstance(self.order_quantity, bool)
            or not isinstance(self.order_quantity, int)
            or self.order_quantity < 1
        ):
            raise BacktestError("order_quantity must be a positive integer.")
        if (
            not isinstance(self.max_volume_participation, Decimal)
            or not self.max_volume_participation.is_finite()
            or not Decimal(0) < self.max_volume_participation <= Decimal(1)
        ):
            raise BacktestError("max_volume_participation must be a Decimal in (0, 1].")
        if not isinstance(self.costs, CostAssumptions):
            raise BacktestError("costs must be CostAssumptions.")

    def to_dict(self) -> dict[str, object]:
        return {
            "starting_cash": _decimal_text(self.starting_cash),
            "order_quantity": self.order_quantity,
            "max_volume_participation": _decimal_text(self.max_volume_participation),
            "costs": self.costs.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BacktestRegistration:
    """Identifier and display name for a registered backtest configuration."""

    experiment_id: int
    name: str


@dataclass(frozen=True, slots=True)
class BacktestRunResult:
    """Persisted outcome of a chronological simulated portfolio run."""

    run_id: int
    status: Literal["COMPLETED", "BLOCKED"]
    reason: str | None


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _decimal_from_json(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise BacktestError(f"{label} must be stored as decimal text.")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BacktestError(f"{label} is not a valid decimal value.") from exc
    if not result.is_finite():
        raise BacktestError(f"{label} must be finite.")
    return result


def _config_from_document(document: object) -> BacktestConfig:
    if not isinstance(document, dict):
        raise BacktestError("Backtest configuration must be an object.")
    costs_document = document.get("costs")
    if not isinstance(costs_document, dict):
        raise BacktestError("Backtest cost assumptions are missing.")
    costs = CostAssumptions(
        **{
            name: _decimal_from_json(costs_document.get(name), name)
            for name in CostAssumptions.__dataclass_fields__
        }
    )
    try:
        quantity = document["order_quantity"]
    except KeyError as exc:
        raise BacktestError("Backtest order quantity is missing.") from exc
    return BacktestConfig(
        starting_cash=_decimal_from_json(document.get("starting_cash"), "starting_cash"),
        order_quantity=quantity,  # type: ignore[arg-type]
        max_volume_participation=_decimal_from_json(
            document.get("max_volume_participation"), "max_volume_participation"
        ),
        costs=costs,
    )


@contextmanager
def _write_transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = get_connection(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _readonly_connection(database_path: Path) -> sqlite3.Connection:
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _load_strategy_experiment(
    connection: sqlite3.Connection, strategy_experiment_id: int
) -> tuple[sqlite3.Row, dict[str, object], date, date, str, StrategySpec]:
    row = connection.execute(
        """SELECT id, strategy_id, hypothesis_id, configuration_json, configuration_sha256,
                  configuration_hash_origin
           FROM experiments WHERE id = ? AND strategy_id IS NOT NULL""",
        (strategy_experiment_id,),
    ).fetchone()
    if row is None:
        raise BacktestError(f"Strategy Lab experiment {strategy_experiment_id} was not found.")
    try:
        document = json.loads(row["configuration_json"])
        spec = _parse_spec(document["strategy_spec"])
        document, start_date, end_date, spec_hash = _validate_experiment_configuration(
            document, spec
        )
    except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, BacktestError):
            raise
        raise BacktestError("Linked Strategy Lab experiment has invalid configuration.") from exc
    configuration_hash = _canonical_hash(document)
    stored_hash = row["configuration_sha256"]
    if stored_hash is not None and stored_hash != configuration_hash:
        raise BacktestError("Linked Strategy Lab experiment configuration failed integrity check.")
    _validate_legacy_strategy_reference(connection, int(row["strategy_id"]), spec)
    snapshot = {
        "strategy_experiment_id": int(row["id"]),
        "strategy_id": int(row["strategy_id"]),
        "hypothesis_id": int(row["hypothesis_id"]) if row["hypothesis_id"] is not None else None,
        "strategy_spec": json.loads(_canonical_json(spec.to_dict())),
        "strategy_spec_sha256": spec_hash,
        "strategy_configuration_sha256": configuration_hash,
        "strategy_configuration_hash_origin": row["configuration_hash_origin"]
        or ("FIRST_RUN_BACKFILL" if stored_hash is None else "REGISTRATION"),
        "exchange": spec.exchange,
        "trading_symbol": spec.trading_symbol,
        "interval": spec.interval,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    return row, snapshot, start_date, end_date, spec_hash, spec


def register_backtest(
    database_path: Path,
    strategy_experiment_id: int,
    config: BacktestConfig,
) -> BacktestRegistration:
    """Register a frozen backtest config linked relationally to its strategy and hypothesis."""

    if (
        isinstance(strategy_experiment_id, bool)
        or not isinstance(strategy_experiment_id, int)
        or strategy_experiment_id < 1
    ):
        raise BacktestError("strategy_experiment_id must be a positive integer.")
    with _write_transaction(database_path) as connection:
        _, strategy_snapshot, start_date, end_date, _, spec = _load_strategy_experiment(
            connection, strategy_experiment_id
        )
        instrument = connection.execute(
            """SELECT instrument_type, lot_size, is_active FROM instruments
               WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE""",
            (spec.exchange, spec.trading_symbol),
        ).fetchone()
        if instrument is None:
            raise BacktestError("Strategy instrument is no longer registered.")
        if not instrument["is_active"]:
            raise BacktestError("Backtests require an active registered instrument.")
        if instrument["instrument_type"].upper() != spec.instrument_type:
            raise BacktestError("Strategy instrument type no longer matches the Instrument Master.")
        if int(instrument["lot_size"]) < 1:
            raise BacktestError("Registered instrument lot_size must be positive.")
        if spec.exchange.upper() != "NSE" or spec.instrument_type.upper() != "EQUITY":
            raise BacktestError(
                f"Phase 4 backtesting execution is restricted to NSE cash equities (EQUITY). "
                f"Unsupported instrument: {spec.exchange}:{spec.trading_symbol} ({spec.instrument_type})."
            )
        if config.order_quantity % int(instrument["lot_size"]) != 0:
            raise BacktestError("order_quantity must be a multiple of the registered lot size.")
        configuration = {
            "backtest_schema_version": BACKTEST_SCHEMA_VERSION,
            "experiment_type": "BACKTEST",
            "strategy_snapshot": strategy_snapshot,
            "backtest_config": config.to_dict(),
            "methodology": {
                "signal_timing": "COMPLETED_CANDLE_CLOSE",
                "fill_timing": "NEXT_ELIGIBLE_CANDLE_OPEN",
                "order_type": "MARKET",
                "position_policy": "LONG_ONLY_SINGLE_POSITION",
                "volume_policy": "ALL_OR_NONE_PRIOR_BAR_PROXY",
                "benchmark": "SAME_INSTRUMENT_BUY_AND_HOLD",
            },
        }
        config_sha256 = _canonical_hash(configuration)
        name = (
            f"Backtest — {spec.exchange}:{spec.trading_symbol} {spec.interval} "
            f"{start_date} to {end_date}"
        )
        cursor = connection.execute(
            """INSERT INTO experiments
               (name, strategy_id, hypothesis_id, configuration_json, status,
                configuration_sha256, configuration_hash_origin)
               VALUES (?, ?, ?, ?, 'BACKTEST_REGISTERED', ?, 'REGISTRATION')""",
            (
                name,
                strategy_snapshot["strategy_id"],
                strategy_snapshot["hypothesis_id"],
                _canonical_json(configuration),
                config_sha256,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a backtest experiment identifier.")
        return BacktestRegistration(int(cursor.lastrowid), name)


def list_backtests(database_path: Path) -> list[dict[str, object]]:
    """List registered backtests without mixing them into the Strategy Lab list."""

    with closing(_readonly_connection(database_path)) as connection:
        rows = connection.execute(
            """SELECT id, name, status, strategy_id, hypothesis_id, configuration_json,
                      configuration_sha256, created_at
               FROM experiments
               WHERE status = 'BACKTEST_REGISTERED' OR strategy_id IS NULL
               ORDER BY id DESC"""
        ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            experiment_id = int(row["id"])
            is_registered = row["status"] == "BACKTEST_REGISTERED"
            try:
                configuration = json.loads(row["configuration_json"])
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                if not is_registered:
                    continue
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has malformed configuration JSON."
                ) from exc
            if not is_registered and (
                not isinstance(configuration, dict)
                or configuration.get("experiment_type") != "BACKTEST"
            ):
                continue
            if (
                not isinstance(configuration, dict)
                or configuration.get("experiment_type") != "BACKTEST"
                or type(configuration.get("backtest_schema_version")) is not int
                or configuration.get("backtest_schema_version") != BACKTEST_SCHEMA_VERSION
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an unsupported or malformed schema."
                )

            stored_hash = row["configuration_sha256"]
            if stored_hash is None:
                if is_registered:
                    raise BacktestError(
                        f"Backtest experiment {experiment_id} has a missing configuration SHA-256."
                    )
                # Preserve the pre-status legacy listing path, which had no hash column contract.
            elif (
                not isinstance(stored_hash, str)
                or len(stored_hash) != 64
                or any(character not in "0123456789abcdef" for character in stored_hash)
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has a malformed configuration SHA-256."
                )
            elif _canonical_hash(configuration) != stored_hash:
                raise BacktestError(
                    f"Backtest experiment {experiment_id} configuration hash does not match "
                    "its stored configuration."
                )

            strategy = configuration.get("strategy_snapshot")
            required_snapshot_fields = {
                "strategy_experiment_id",
                "strategy_id",
                "hypothesis_id",
                "strategy_spec",
                "strategy_spec_sha256",
                "strategy_configuration_sha256",
                "strategy_configuration_hash_origin",
                "exchange",
                "trading_symbol",
                "interval",
                "start_date",
                "end_date",
            }
            if not isinstance(strategy, dict) or not required_snapshot_fields.issubset(strategy):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has a missing or malformed "
                    "strategy snapshot."
                )

            strategy_experiment_id = strategy["strategy_experiment_id"]
            if (
                isinstance(strategy_experiment_id, bool)
                or not isinstance(strategy_experiment_id, int)
                or strategy_experiment_id < 1
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid strategy experiment "
                    "ID type or value."
                )
            strategy_id = strategy["strategy_id"]
            if isinstance(strategy_id, bool) or not isinstance(strategy_id, int) or strategy_id < 1:
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid strategy ID type or value."
                )
            hypothesis_id = strategy["hypothesis_id"]
            if hypothesis_id is not None and (
                isinstance(hypothesis_id, bool)
                or not isinstance(hypothesis_id, int)
                or hypothesis_id < 1
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid hypothesis ID type "
                    "or value."
                )

            try:
                snapshot_spec = _parse_spec(strategy["strategy_spec"])
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid strategy "
                    f"specification: {exc}"
                ) from exc
            if _canonical_json(snapshot_spec.to_dict()) != _canonical_json(
                strategy["strategy_spec"]
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} strategy specification is not "
                    "in its canonical stored form."
                )
            spec_hash = strategy["strategy_spec_sha256"]
            if (
                not isinstance(spec_hash, str)
                or len(spec_hash) != 64
                or any(character not in "0123456789abcdef" for character in spec_hash)
                or spec_hash != _canonical_hash(snapshot_spec.to_dict())
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid strategy "
                    "specification SHA-256."
                )
            strategy_configuration_hash = strategy["strategy_configuration_sha256"]
            if (
                not isinstance(strategy_configuration_hash, str)
                or len(strategy_configuration_hash) != 64
                or any(
                    character not in "0123456789abcdef" for character in strategy_configuration_hash
                )
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid strategy "
                    "configuration SHA-256."
                )
            if (
                not isinstance(strategy["strategy_configuration_hash_origin"], str)
                or not strategy["strategy_configuration_hash_origin"]
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid strategy "
                    "configuration hash origin."
                )
            for field in ("exchange", "trading_symbol", "interval", "start_date", "end_date"):
                if not isinstance(strategy[field], str) or not strategy[field]:
                    raise BacktestError(
                        f"Backtest experiment {experiment_id} has a missing or invalid "
                        f"strategy snapshot field {field}."
                    )

            try:
                _config_from_document(configuration.get("backtest_config"))
            except (BacktestError, TypeError, ValueError) as exc:
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid backtest "
                    f"configuration: {exc}"
                ) from exc
            methodology = configuration.get("methodology")
            if (
                not isinstance(methodology, dict)
                or methodology.get("signal_timing") != "COMPLETED_CANDLE_CLOSE"
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has malformed or unsupported methodology."
                )

            if row["strategy_id"] is None and is_registered:
                raise BacktestError(
                    f"Backtest experiment {experiment_id} is missing its relational strategy "
                    "reference."
                )
            try:
                _, expected_snapshot, _, _, _, _ = _load_strategy_experiment(
                    connection, strategy_experiment_id
                )
            except BacktestError as exc:
                raise BacktestError(
                    f"Backtest experiment {experiment_id} has an invalid strategy reference: {exc}"
                ) from exc
            if strategy != expected_snapshot:
                raise BacktestError(
                    f"Backtest experiment {experiment_id} strategy snapshot does not match "
                    "the referenced Strategy Lab experiment."
                )
            if is_registered and (
                row["strategy_id"] != strategy_id or row["hypothesis_id"] != hypothesis_id
            ):
                raise BacktestError(
                    f"Backtest experiment {experiment_id} relational strategy/hypothesis "
                    "references do not match its snapshot."
                )

            result.append(
                {
                    "id": experiment_id,
                    "name": row["name"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "strategy_experiment_id": strategy_experiment_id,
                    "exchange": strategy["exchange"],
                    "trading_symbol": strategy["trading_symbol"],
                    "interval": strategy["interval"],
                    "start_date": strategy["start_date"],
                    "end_date": strategy["end_date"],
                }
            )
    return result


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _calculate_brokerage(turnover: Decimal, costs: CostAssumptions) -> Decimal:
    brokerage = turnover * costs.brokerage_rate
    if costs.brokerage_cap_per_order > 0:
        brokerage = min(brokerage, costs.brokerage_cap_per_order)
    return brokerage


def _fees(
    price: Decimal, quantity: int, side: Literal["BUY", "SELL"], costs: CostAssumptions
) -> dict[str, Decimal]:
    turnover = price * quantity
    brokerage = _money(_calculate_brokerage(turnover, costs))
    exchange = _money(turnover * costs.exchange_charge_rate)
    regulatory = _money(turnover * costs.regulatory_charge_rate)
    tax = turnover * (
        costs.transaction_tax_buy_rate if side == "BUY" else costs.transaction_tax_sell_rate
    )
    stamp_duty = turnover * costs.stamp_duty_buy_rate if side == "BUY" else Decimal(0)
    gst = (brokerage + exchange + regulatory) * costs.gst_rate
    return {
        "brokerage": brokerage,
        "exchange_charges": exchange,
        "regulatory_charges": regulatory,
        "transaction_tax": _money(tax),
        "stamp_duty": _money(stamp_duty),
        "gst": _money(gst),
    }


def _fee_total(fees: dict[str, Decimal]) -> Decimal:
    return sum(fees.values(), Decimal(0))


def _fill_price(
    reference: Decimal,
    side: Literal["BUY", "SELL"],
    tick_size: Decimal,
    costs: CostAssumptions,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if not reference.is_finite() or reference <= 0:
        raise BacktestError("reference price must be finite and greater than zero.")
    if not tick_size.is_finite() or tick_size <= 0:
        raise BacktestError("tick_size must be finite and greater than zero.")
    if side not in ("BUY", "SELL"):
        raise BacktestError("side must be BUY or SELL.")
    half_spread_rate = costs.spread_basis_points / Decimal(2) / _BASIS_POINT_DENOMINATOR
    slippage_rate = costs.slippage_basis_points / _BASIS_POINT_DENOMINATOR
    spread_per_unit = reference * half_spread_rate
    slippage_per_unit = reference * slippage_rate
    raw_price = reference + spread_per_unit + slippage_per_unit
    rounding = ROUND_CEILING
    if side == "SELL":
        raw_price = reference - spread_per_unit - slippage_per_unit
        rounding = ROUND_FLOOR
    ticks = (raw_price / tick_size).to_integral_value(rounding=rounding)
    price = ticks * tick_size
    if price <= 0:
        raise BacktestError(f"Adverse execution resulted in a non-positive fill price: {price}.")
    tick_cost = (price - raw_price) if side == "BUY" else (raw_price - price)
    return price, spread_per_unit, slippage_per_unit, tick_cost


def _execution_time(candle: StrategyCandle, interval: str, calendar: ExchangeCalendar) -> str:
    parsed = datetime.fromisoformat(candle.timestamp)
    if interval != "1d":
        return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    timezone = ZoneInfo(calendar.timezone_name)
    local_day = parsed.astimezone(timezone).date()
    special = calendar.special_sessions.get(local_day)
    sessions = special.sessions if special is not None else calendar.sessions
    if not sessions:
        raise BacktestError("Daily bar has no configured market session for its execution time.")
    market_open = datetime.combine(local_day, min(item.opens for item in sessions), tzinfo=timezone)
    return market_open.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _daily_metrics(
    equity_curve: list[dict[str, str]],
    initial_cash: Decimal,
    trades: list[dict[str, object]],
) -> dict[str, object]:
    daily_equity: dict[str, Decimal] = {}
    for point in equity_curve:
        day = point["local_date"]
        daily_equity[day] = _decimal_from_json(point["equity"], "equity")
    returns: list[Decimal] = []
    daily_returns: list[dict[str, str]] = []
    previous = initial_cash
    for day, value in daily_equity.items():
        daily_return = value / previous - Decimal(1)
        returns.append(daily_return)
        daily_returns.append({"local_date": day, "return": _decimal_text(daily_return)})
        previous = value
    volatility: Decimal | None = None
    sharpe: Decimal | None = None
    if len(returns) >= 2:
        mean = sum(returns, Decimal(0)) / len(returns)
        variance = sum(((item - mean) ** 2 for item in returns), Decimal(0)) / (len(returns) - 1)
        volatility = variance.sqrt() * Decimal(252).sqrt()
        if volatility != 0:
            sharpe = mean * Decimal(252).sqrt() / (variance.sqrt())

    gross_trade_pnls = [
        _decimal_from_json(str(trade["gross_pnl"]), "gross_pnl") for trade in trades
    ]
    net_trade_pnls = [_decimal_from_json(str(trade["net_pnl"]), "net_pnl") for trade in trades]
    winners = [value for value in net_trade_pnls if value > 0]
    losers = [value for value in net_trade_pnls if value < 0]
    gross_profit = sum((value for value in gross_trade_pnls if value > 0), Decimal(0))
    gross_loss = -sum((value for value in gross_trade_pnls if value < 0), Decimal(0))
    net_profit = sum(winners, Decimal(0))
    net_loss = -sum(losers, Decimal(0))
    profit_factor = net_profit / net_loss if net_loss else None
    expectancy = sum(net_trade_pnls, Decimal(0)) / len(net_trade_pnls) if net_trade_pnls else None
    return {
        "closed_trade_count": len(net_trade_pnls),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "gross_profit": _decimal_text(gross_profit),
        "gross_loss": _decimal_text(gross_loss),
        "net_profit_from_winning_trades": _decimal_text(net_profit),
        "net_loss_from_losing_trades": _decimal_text(net_loss),
        "win_rate": (
            _decimal_text(Decimal(len(winners)) / len(net_trade_pnls)) if net_trade_pnls else None
        ),
        "profit_factor": _decimal_text(profit_factor) if profit_factor is not None else None,
        "expectancy_per_closed_trade": (
            _decimal_text(expectancy) if expectancy is not None else None
        ),
        "annualized_daily_volatility": (
            _decimal_text(volatility) if volatility is not None else None
        ),
        "annualized_sharpe_zero_risk_free": _decimal_text(sharpe) if sharpe is not None else None,
        "daily_returns": daily_returns,
    }


def _simulate(
    candles: list[StrategyCandle],
    signals: tuple[StrategySignal, ...],
    *,
    config: BacktestConfig,
    tick_size: Decimal,
    interval: str,
    calendar: ExchangeCalendar,
    expected_by_timestamp: dict[str, datetime],
) -> dict[str, object]:
    """Execute a strictly causal simulation: execute pending order at open, MTM at close, queue at close."""

    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        cash = _money(config.starting_cash)
        position_quantity = 0
        entry_price: Decimal | None = None
        entry_fees = Decimal(0)
        entry_time: str | None = None
        entry_order_id: int | None = None
        orders: list[dict[str, object]] = []
        trades: list[dict[str, object]] = []
        equity_curve: list[dict[str, str]] = []
        total_fees = Decimal(0)
        total_turnover = Decimal(0)
        signals_by_timestamp = {signal.timestamp: signal for signal in signals}
        pending_order: dict[str, object] | None = None

        for index, candle in enumerate(candles):
            # Step 1: Execute any pending order from the previous candle at this candle's open.
            if pending_order is not None:
                side: Literal["BUY", "SELL"] = pending_order["side"]  # type: ignore[assignment]
                quantity = config.order_quantity if side == "BUY" else position_quantity

                # Volume participation & historical liquidity proxy:
                # The pending order was generated at candle[index - 1] close.
                # When evaluating execution at candle[index] open, the execution candle's
                # full volume is NOT yet known in reality (preventing hindsight look-ahead).
                # We use the previous completed candle's volume (candles[index - 1].volume)
                # as the known historical liquidity proxy.
                assert index >= 1, "A pending order cannot exist on the first candle."
                prior_candle = candles[index - 1]
                capacity = int(
                    (
                        Decimal(prior_candle.volume) * config.max_volume_participation
                    ).to_integral_value(rounding=ROUND_FLOOR)
                )
                pending_order["execution_bar_timestamp"] = candle.timestamp
                pending_order["execution_time"] = _execution_time(candle, interval, calendar)
                pending_order["reference_open"] = _decimal_text(candle.open)
                pending_order["quantity"] = quantity
                pending_order["prior_bar_volume"] = prior_candle.volume
                pending_order["volume_capacity"] = capacity

                if quantity > capacity:
                    pending_order.update(
                        status="REJECTED",
                        reason=(
                            "Order quantity exceeds configured volume participation limit based on "
                            "the prior completed bar's volume (historical liquidity proxy)."
                        ),
                    )
                else:
                    fill, spread_per_unit, slippage_per_unit, tick_per_unit = _fill_price(
                        candle.open, side, tick_size, config.costs
                    )
                    fees = _fees(fill, quantity, side, config.costs)
                    fee_total = _fee_total(fees)
                    turnover = fill * quantity
                    pending_order.update(
                        fill_price=_decimal_text(fill),
                        fees={key: _decimal_text(value) for key, value in fees.items()},
                        total_fees=_decimal_text(fee_total),
                        spread_cost=_decimal_text(spread_per_unit * quantity),
                        slippage_cost=_decimal_text(slippage_per_unit * quantity),
                        tick_rounding_cost=_decimal_text(tick_per_unit * quantity),
                    )
                    if side == "BUY" and cash < turnover + fee_total:
                        pending_order.update(
                            status="REJECTED",
                            reason="Insufficient cash for fill and charges.",
                        )
                    elif side == "BUY":
                        cash = _money(cash - turnover - fee_total)
                        position_quantity = quantity
                        entry_price = fill
                        entry_fees = fee_total
                        entry_time = str(pending_order["execution_time"])
                        entry_order_id = int(str(pending_order["order_id"]))
                        pending_order.update(status="FILLED", reason=None)
                        total_fees += fee_total
                        total_turnover += turnover
                    else:
                        cash = _money(cash + turnover - fee_total)
                        gross_pnl = (fill - (entry_price or Decimal(0))) * quantity
                        net_pnl = gross_pnl - entry_fees - fee_total
                        trades.append(
                            {
                                "trade_id": len(trades) + 1,
                                "entry_order_id": entry_order_id,
                                "exit_order_id": int(str(pending_order["order_id"])),
                                "entry_time": entry_time,
                                "exit_time": pending_order["execution_time"],
                                "quantity": quantity,
                                "entry_price": _decimal_text(entry_price or Decimal(0)),
                                "exit_price": _decimal_text(fill),
                                "gross_pnl": _decimal_text(gross_pnl),
                                "entry_fees": _decimal_text(entry_fees),
                                "exit_fees": _decimal_text(fee_total),
                                "net_pnl": _decimal_text(net_pnl),
                            }
                        )
                        position_quantity = 0
                        entry_price = None
                        entry_fees = Decimal(0)
                        entry_time = None
                        entry_order_id = None
                        pending_order.update(status="FILLED", reason=None)
                        total_fees += fee_total
                        total_turnover += turnover
                orders.append(pending_order)
                pending_order = None

            # Step 2: Mark to market at candle close based on position held during this bar.
            mark_to_market = candle.close * position_quantity
            equity = cash + mark_to_market
            local_date = (
                datetime.fromisoformat(candle.timestamp)
                .astimezone(ZoneInfo(calendar.timezone_name))
                .date()
            )
            equity_curve.append(
                {
                    "timestamp": candle.timestamp,
                    "local_date": local_date.isoformat(),
                    "cash": _decimal_text(cash),
                    "position_quantity": str(position_quantity),
                    "mark_price": _decimal_text(candle.close),
                    "equity": _decimal_text(equity),
                }
            )

            # Step 3: Evaluate signal emitted at candle close and queue order for next bar.
            signal = signals_by_timestamp.get(candle.timestamp)
            if signal is not None:
                order_id = len(orders) + 1
                side = "BUY" if signal.action == "ENTER_LONG" else "SELL"
                if side == "BUY" and position_quantity > 0:
                    orders.append(
                        {
                            "order_id": order_id,
                            "order_type": "MARKET",
                            "side": side,
                            "signal": signal.action,
                            "signal_candle_timestamp": signal.timestamp,
                            "submitted_at": expected_by_timestamp[signal.timestamp].isoformat(),
                            "quantity": config.order_quantity,
                            "status": "REJECTED",
                            "reason": "A long position is already open.",
                        }
                    )
                elif side == "SELL" and position_quantity == 0:
                    orders.append(
                        {
                            "order_id": order_id,
                            "order_type": "MARKET",
                            "side": side,
                            "signal": signal.action,
                            "signal_candle_timestamp": signal.timestamp,
                            "submitted_at": expected_by_timestamp[signal.timestamp].isoformat(),
                            "quantity": 0,
                            "status": "REJECTED",
                            "reason": "No open long position exists.",
                        }
                    )
                elif index + 1 >= len(candles):
                    orders.append(
                        {
                            "order_id": order_id,
                            "order_type": "MARKET",
                            "side": side,
                            "signal": signal.action,
                            "signal_candle_timestamp": signal.timestamp,
                            "submitted_at": expected_by_timestamp[signal.timestamp].isoformat(),
                            "quantity": (
                                config.order_quantity if side == "BUY" else position_quantity
                            ),
                            "status": "UNFILLED_NO_NEXT_BAR",
                            "reason": (
                                "No next eligible candle exists in the registered date range."
                            ),
                        }
                    )
                else:
                    pending_order = {
                        "order_id": order_id,
                        "order_type": "MARKET",
                        "side": side,
                        "signal": signal.action,
                        "signal_candle_timestamp": signal.timestamp,
                        "submitted_at": expected_by_timestamp[signal.timestamp].isoformat(),
                    }

        peak = config.starting_cash
        max_drawdown = Decimal(0)
        for point in equity_curve:
            equity = _decimal_from_json(point["equity"], "equity")
            peak = max(peak, equity)
            if peak:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)
        final_equity = (
            _decimal_from_json(equity_curve[-1]["equity"], "equity")
            if equity_curve
            else config.starting_cash
        )

        # Open position liquidation assessment at dataset end
        last_candle = candles[-1] if candles else None
        open_position: dict[str, object] | None = None
        unrealized_gross_pnl = Decimal(0)
        unrealized_net_pnl = Decimal(0)
        estimated_liquidation_costs = Decimal(0)

        if position_quantity > 0 and entry_price is not None and last_candle is not None:
            mark_price = last_candle.close
            unrealized_gross_pnl = (mark_price - entry_price) * position_quantity

            # Estimate liquidation costs at last candle close (adverse exit price + charges)
            exit_fill, _, _, _ = _fill_price(mark_price, "SELL", tick_size, config.costs)
            exit_adverse_impact = (mark_price - exit_fill) * position_quantity
            exit_fees = _fees(exit_fill, position_quantity, "SELL", config.costs)
            exit_fee_total = _fee_total(exit_fees)
            estimated_liquidation_costs = _money(exit_adverse_impact + exit_fee_total)
            unrealized_net_pnl = unrealized_gross_pnl - entry_fees - estimated_liquidation_costs

            open_position = {
                "quantity": position_quantity,
                "entry_price": _decimal_text(entry_price),
                "entry_time": entry_time,
                "entry_fees": _decimal_text(entry_fees),
                "mark_price": _decimal_text(mark_price),
                "unrealized_gross_pnl": _decimal_text(unrealized_gross_pnl),
                "unrealized_net_pnl": _decimal_text(unrealized_net_pnl),
                "estimated_liquidation_costs": _decimal_text(estimated_liquidation_costs),
                "net_liquidation_value": _decimal_text(final_equity - estimated_liquidation_costs),
            }

        net_liquidation_value = final_equity - estimated_liquidation_costs
        realized_net_pnl = sum(
            (_decimal_from_json(str(trade["net_pnl"]), "net_pnl") for trade in trades),
            Decimal(0),
        )

        return {
            "orders": orders,
            "trades": trades,
            "equity_curve": equity_curve,
            "open_position": open_position,
            "summary": {
                "starting_cash": _decimal_text(config.starting_cash),
                "ending_cash": _decimal_text(cash),
                "ending_equity": _decimal_text(final_equity),
                "net_liquidation_value": _decimal_text(net_liquidation_value),
                "absolute_pnl": _decimal_text(final_equity - config.starting_cash),
                "net_liquidation_pnl": _decimal_text(net_liquidation_value - config.starting_cash),
                "total_return": _decimal_text(final_equity / config.starting_cash - Decimal(1)),
                "net_liquidation_return": _decimal_text(
                    net_liquidation_value / config.starting_cash - Decimal(1)
                ),
                "realized_net_pnl": _decimal_text(realized_net_pnl),
                "unrealized_gross_pnl": _decimal_text(unrealized_gross_pnl),
                "unrealized_net_pnl": _decimal_text(unrealized_net_pnl),
                "estimated_liquidation_costs": _decimal_text(estimated_liquidation_costs),
                "total_fees": _decimal_text(total_fees),
                "filled_turnover": _decimal_text(total_turnover),
                "open_position_quantity": position_quantity,
                "maximum_drawdown": _decimal_text(max_drawdown),
                "open_position": open_position,
                **_daily_metrics(equity_curve, config.starting_cash, trades),
            },
        }


def _buy_and_hold_benchmark(
    candles: list[StrategyCandle],
    *,
    config: BacktestConfig,
    tick_size: Decimal,
) -> dict[str, object]:
    if not candles:
        return {"status": "UNAVAILABLE", "reason": "No candles are available."}
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        first, last = candles[0], candles[-1]
        buy, _, _, _ = _fill_price(first.open, "BUY", tick_size, config.costs)
        sell, _, _, _ = _fill_price(last.close, "SELL", tick_size, config.costs)
        buy_turnover = buy * config.order_quantity
        sell_turnover = sell * config.order_quantity
        buy_fees = _fees(buy, config.order_quantity, "BUY", config.costs)
        sell_fees = _fees(sell, config.order_quantity, "SELL", config.costs)
        total_fees = _fee_total(buy_fees) + _fee_total(sell_fees)
        if config.starting_cash < buy_turnover + _fee_total(buy_fees):
            return {
                "status": "UNAVAILABLE",
                "reason": "The fixed benchmark quantity is not affordable at the initial bar.",
            }
        cash_after_buy = config.starting_cash - buy_turnover - _fee_total(buy_fees)
        ending_cash = cash_after_buy + sell_turnover - _fee_total(sell_fees)
        benchmark_curve: list[dict[str, str]] = []
        for candle in candles:
            mtm = candle.close * config.order_quantity
            eq = ending_cash if candle is last else (cash_after_buy + mtm)
            benchmark_curve.append(
                {
                    "timestamp": candle.timestamp,
                    "equity": _decimal_text(eq),
                }
            )
        return {
            "status": "COMPLETED",
            "method": (
                "Same-instrument fixed-quantity buy-and-hold, entering first bar open and "
                "exiting final bar close."
            ),
            "quantity": config.order_quantity,
            "entry_price": _decimal_text(buy),
            "exit_price": _decimal_text(sell),
            "fees": {
                "entry": {key: _decimal_text(value) for key, value in buy_fees.items()},
                "exit": {key: _decimal_text(value) for key, value in sell_fees.items()},
                "total": _decimal_text(total_fees),
            },
            "ending_cash": _decimal_text(ending_cash),
            "total_return": _decimal_text(ending_cash / config.starting_cash - Decimal(1)),
            "equity_curve": benchmark_curve,
        }


def _finish_run(
    database_path: Path, run_id: int, *, status: str, result: dict[str, object], error: str | None
) -> None:
    with _write_transaction(database_path) as connection:
        cursor = connection.execute(
            """UPDATE runs SET status = ?, completed_at = CURRENT_TIMESTAMP,
               result_json = ?, error_message = ? WHERE id = ? AND status = 'RUNNING'""",
            (status, _canonical_json(result), error, run_id),
        )
        if cursor.rowcount != 1:
            raise BacktestError(f"Backtest run {run_id} is no longer RUNNING.")


def _blocked(
    database_path: Path,
    run_id: int,
    reason: str,
    configuration_hash: str,
    *,
    provenance: dict[str, object] | None = None,
) -> BacktestRunResult:
    result: dict[str, object] = {
        "result_schema_version": 1,
        "status": "BLOCKED",
        "provenance_status": "PARTIAL_BLOCKED",
        "backtest_configuration_sha256": configuration_hash,
        "reason": reason,
    }
    if provenance:
        result.update(provenance)
    _finish_run(
        database_path,
        run_id,
        status="BLOCKED",
        result=result,
        error=reason,
    )
    return BacktestRunResult(run_id, "BLOCKED", reason)


def run_backtest(
    database_path: Path,
    backtest_experiment_id: int,
    *,
    calendars: CalendarRegistry,
    as_of: datetime,
) -> BacktestRunResult:
    """Run a registered backtest only against complete, confirmed, fresh local data."""

    if isinstance(backtest_experiment_id, bool) or not isinstance(backtest_experiment_id, int):
        raise BacktestError("backtest_experiment_id must be an integer.")
    if backtest_experiment_id < 1:
        raise BacktestError("backtest_experiment_id must be positive.")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise BacktestError("as_of must be timezone-aware.")
    with _write_transaction(database_path) as connection:
        row = connection.execute(
            """SELECT id, strategy_id, hypothesis_id, configuration_json, configuration_sha256
               FROM experiments WHERE id = ?""",
            (backtest_experiment_id,),
        ).fetchone()
        if row is None:
            raise BacktestError(f"Backtest experiment {backtest_experiment_id} was not found.")
        try:
            configuration = json.loads(row["configuration_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise BacktestError("Backtest configuration is malformed.") from exc
        if (
            not isinstance(configuration, dict)
            or configuration.get("backtest_schema_version") != BACKTEST_SCHEMA_VERSION
        ):
            raise BacktestError("Experiment is not a supported backtest configuration.")
        actual_hash = _canonical_hash(configuration)
        if actual_hash != row["configuration_sha256"]:
            raise BacktestError("Backtest configuration integrity check failed.")
        try:
            config = _config_from_document(configuration["backtest_config"])
            strategy_snapshot = configuration["strategy_snapshot"]
            if not isinstance(strategy_snapshot, dict):
                raise BacktestError("Backtest strategy snapshot is malformed.")
            strategy_experiment_id = int(strategy_snapshot["strategy_experiment_id"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            if isinstance(exc, BacktestError):
                raise
            raise BacktestError("Backtest experiment configuration is malformed.") from exc
        strategy_row, current_snapshot, start_date, end_date, expected_spec_hash, _ = (
            _load_strategy_experiment(connection, strategy_experiment_id)
        )
        del strategy_row
        if current_snapshot != strategy_snapshot:
            raise BacktestError(
                "Linked Strategy Lab experiment changed after backtest registration."
            )
        if (
            row["strategy_id"] != current_snapshot["strategy_id"]
            or row["hypothesis_id"] != current_snapshot["hypothesis_id"]
        ):
            raise BacktestError(
                "Backtest relational source links do not match the stored strategy snapshot."
            )
        if configuration.get("methodology", {}).get("signal_timing") != "COMPLETED_CANDLE_CLOSE":
            raise BacktestError("Backtest signal timing is unsupported.")
        cursor = connection.execute(
            "INSERT INTO runs (experiment_id, status) VALUES (?, 'RUNNING')",
            (backtest_experiment_id,),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a backtest run identifier.")
        run_id = int(cursor.lastrowid)

    try:
        spec = _parse_spec(strategy_snapshot["strategy_spec"])
        if spec.exchange.upper() != "NSE" or spec.instrument_type.upper() != "EQUITY":
            return _blocked(
                database_path,
                run_id,
                f"Phase 4 backtesting execution is restricted to NSE cash equities (EQUITY). "
                f"Unsupported instrument: {spec.exchange}:{spec.trading_symbol} ({spec.instrument_type}).",
                actual_hash,
            )
        with closing(_readonly_connection(database_path)) as connection:
            instrument = connection.execute(
                """SELECT id, exchange, trading_symbol, segment, tick_size,
                          tick_size_decimal, lot_size, is_active, instrument_type
                   FROM instruments
                   WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE""",
                (spec.exchange, spec.trading_symbol),
            ).fetchone()
        if instrument is None:
            return _blocked(
                database_path, run_id, "Strategy instrument is not registered.", actual_hash
            )
        if not instrument["is_active"]:
            return _blocked(
                database_path, run_id, "Strategy instrument is no longer active.", actual_hash
            )
        if instrument["instrument_type"].upper() != spec.instrument_type:
            return _blocked(
                database_path,
                run_id,
                "Strategy instrument type no longer matches the Instrument Master.",
                actual_hash,
            )
        if int(instrument["lot_size"]) < 1 or config.order_quantity % int(instrument["lot_size"]):
            return _blocked(
                database_path,
                run_id,
                "Backtest order quantity no longer matches the registered instrument lot size.",
                actual_hash,
            )
        if instrument["tick_size"] is not None or instrument["tick_size_decimal"] is None:
            return _blocked(
                database_path,
                run_id,
                "An authoritative decimal tick size is required for backtest fill rounding.",
                actual_hash,
            )
        tick_size = _decimal_from_json(instrument["tick_size_decimal"], "tick_size_decimal")
        instrument_snapshot: dict[str, object] = {
            "instrument_id": int(instrument["id"]),
            "exchange": str(instrument["exchange"]),
            "trading_symbol": str(instrument["trading_symbol"]),
            "segment": instrument["segment"],
            "instrument_type": str(instrument["instrument_type"]),
            "lot_size": int(instrument["lot_size"]),
            "tick_size_decimal": _decimal_text(tick_size),
        }
        instrument_snapshot_sha256 = _canonical_hash(instrument_snapshot)
        report = analyze_dataset(
            database_path,
            spec.exchange,
            spec.trading_symbol,
            spec.interval,
            start_date,
            end_date,
            as_of=as_of,
            calendars=calendars,
        )
        reason = _gate_reason(report)
        if reason is not None:
            return _blocked(database_path, run_id, reason, actual_hash)
        calendar = calendars.resolve(spec.exchange, report.segment)
        if calendar is None:
            return _blocked(
                database_path,
                run_id,
                "No exchange/segment calendar is configured.",
                actual_hash,
            )
        scheduled, completed, _, uncovered = calculate_expected_candles(
            calendar, spec.interval, start_date, end_date, as_of=as_of
        )
        if uncovered or len(scheduled) != len(completed):
            return _blocked(
                database_path,
                run_id,
                "The requested backtest range includes uncovered or incomplete candles.",
                actual_hash,
            )
        expected_by_timestamp = {item.timestamp: item.completes_at for item in completed}
        completed_timestamps = set(expected_by_timestamp)
        candles, dataset_hash, source_hashes, source_imports = _load_completed_candles(
            database_path,
            exchange=spec.exchange,
            trading_symbol=spec.trading_symbol,
            interval=spec.interval,
            start_date=start_date,
            end_date=end_date,
            timezone_name=calendar.timezone_name,
            completed_timestamps=completed_timestamps,
        )
        _validate_candles(candles)
        if len(candles) != len(completed_timestamps):
            return _blocked(
                database_path,
                run_id,
                "Dataset changed or lacks a completed expected candle.",
                actual_hash,
            )
        prohibited_imports = [
            item for item in source_imports if item["source_use_status"] == "PROHIBITED"
        ]
        if prohibited_imports:
            return _blocked(
                database_path,
                run_id,
                "Backtest data includes source imports marked PROHIBITED: "
                + ", ".join(str(item["import_id"]) for item in prohibited_imports),
                actual_hash,
                provenance={"source_imports": list(source_imports)},
            )
        unverified_imports = [
            item for item in source_imports if item["source_use_status"] != "VERIFIED_PERMITTED"
        ]
        if unverified_imports:
            return _blocked(
                database_path,
                run_id,
                "Backtest data includes source imports not declared VERIFIED_PERMITTED: "
                + ", ".join(str(item["import_id"]) for item in unverified_imports),
                actual_hash,
                provenance={"source_imports": list(source_imports)},
            )
        required_history = spec.minimum_history_candles
        if len(candles) < required_history:
            return _blocked(
                database_path,
                run_id,
                f"Insufficient strategy history for {spec.name} version {spec.version}: "
                f"requires {required_history} candles but only {len(candles)} eligible "
                "completed candles are available.",
                actual_hash,
                provenance={"source_imports": list(source_imports)},
            )
        signals = generate_sma_crossover_signals(
            candles, fast_window=spec.fast_window, slow_window=spec.slow_window
        )
        with localcontext() as context:
            context.prec = 50
            context.rounding = ROUND_HALF_UP
            output = _simulate(
                candles,
                signals,
                config=config,
                tick_size=tick_size,
                interval=spec.interval,
                calendar=calendar,
                expected_by_timestamp=expected_by_timestamp,
            )
            benchmark = _buy_and_hold_benchmark(candles, config=config, tick_size=tick_size)
        summary = output["summary"]
        if not isinstance(summary, dict):
            raise TypeError("Backtest simulation returned an invalid summary.")
        benchmark_return: Decimal | None = None
        relative_performance: Decimal | None = None
        if benchmark.get("status") == "COMPLETED":
            benchmark_return = _decimal_from_json(
                benchmark.get("total_return"), "benchmark.total_return"
            )
            net_liquidation_return = _decimal_from_json(
                summary["net_liquidation_return"], "net_liquidation_return"
            )
            with localcontext() as context:
                context.prec = 50
                context.rounding = ROUND_HALF_UP
                relative_performance = net_liquidation_return - benchmark_return
        summary["benchmark_total_return"] = (
            _decimal_text(benchmark_return) if benchmark_return is not None else None
        )
        summary["relative_performance_vs_same_instrument_buy_and_hold"] = (
            _decimal_text(relative_performance) if relative_performance is not None else None
        )
        metadata = {
            "provenance_status": "COMPLETE",
            "backtest_configuration_sha256": actual_hash,
            "instrument_snapshot": instrument_snapshot,
            "instrument_snapshot_sha256": instrument_snapshot_sha256,
            "source_imports": list(source_imports),
            "strategy_experiment_id": strategy_experiment_id,
            "strategy_configuration_sha256": strategy_snapshot["strategy_configuration_sha256"],
            "strategy_spec_sha256": expected_spec_hash,
            "strategy_implementation_sha256": _sha256_file(
                Path(__file__).with_name("strategy_lab.py")
            ),
            "backtesting_implementation_sha256": _sha256_file(Path(__file__)),
            "market_quality_implementation_sha256": _sha256_file(
                Path(__file__).with_name("market_quality.py")
            ),
            "market_data_ingestion_implementation_sha256": _sha256_file(
                Path(__file__).with_name("market_data.py")
            ),
            "calendar_configuration_sha256": calendars.configuration_sha256,
            "effective_calendar_configuration_sha256": _effective_calendar_hash(
                calendar, calendars
            ),
            "dataset_sha256": dataset_hash,
            "source_file_sha256s": list(source_hashes),
            "source_revision": _source_revision(),
            "as_of": as_of.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "exchange": spec.exchange,
            "trading_symbol": spec.trading_symbol,
            "interval": spec.interval,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "calendar_confidence": report.calendar_confidence,
            "calendar_source": report.calendar_source,
            "quality_status": report.overall_status,
            "cost_assumptions": config.costs.to_dict(),
            "cost_methodology": {
                "brokerage": (
                    "Per-order turnover times brokerage_rate, limited by "
                    "brokerage_cap_per_order when the cap is positive; zero cap means uncapped."
                ),
                "exchange_charges": "Turnover-based on BUY and SELL orders.",
                "regulatory_charges": "Turnover-based on BUY and SELL orders.",
                "transaction_tax": "Side-specific transaction_tax_buy_rate or transaction_tax_sell_rate.",
                "stamp_duty": "Turnover-based on BUY orders only.",
                "gst": "gst_rate applied to rounded brokerage, exchange, and regulatory charges.",
                "fee_rounding": "Each charge is rounded to INR 0.01 using ROUND_HALF_UP.",
                "relative_performance": (
                    "Strategy net-liquidation return minus same-instrument buy-and-hold "
                    "return; reported as a decimal return difference."
                ),
            },
            "cost_rates_are_user_supplied": True,
            "no_broker_or_exchange_fee_schedule_is_bundled": True,
            "supported_execution_scope": "NSE cash equities (EQUITY) only.",
            "annualization_assumption": "252 trading sessions; zero risk-free rate; daily marks.",
            "metric_methodology": {
                "absolute_pnl": "Ending marked-to-market equity minus starting cash.",
                "net_liquidation_pnl": (
                    "Ending marked-to-market equity less estimated liquidation costs, "
                    "minus starting cash."
                ),
                "realized_net_pnl": "Sum of completed trade P&L after entry and exit charges.",
                "unrealized_net_pnl": (
                    "Open-position gross P&L less entry charges and estimated exit impact "
                    "and charges."
                ),
                "win_rate": (
                    "Closed trades with positive net P&L divided by all closed trades; "
                    "undefined when there are no closed trades."
                ),
                "gross_profit_and_loss": (
                    "Sums of positive and absolute negative completed-trade P&L before fees."
                ),
                "profit_factor": (
                    "Sum of positive net closed-trade P&L divided by absolute negative net "
                    "closed-trade P&L; undefined when there are no net losses."
                ),
                "expectancy_per_closed_trade": (
                    "Mean net P&L per completed trade; undefined when there are no trades."
                ),
                "maximum_drawdown": (
                    "Largest peak-to-trough decline as a fraction of prior peak across "
                    "recorded equity marks."
                ),
                "annualized_daily_volatility": (
                    "Sample standard deviation of exchange-local daily returns times "
                    "square root of 252; undefined with fewer than two observations."
                ),
                "annualized_sharpe_zero_risk_free": (
                    "Mean daily return divided by sample standard deviation times square "
                    "root of 252, assuming zero risk-free rate; undefined with fewer than "
                    "two observations or zero variance."
                ),
            },
            "execution_model": configuration["methodology"],
        }
        result_document = {
            "result_schema_version": 1,
            "status": "COMPLETED",
            "metadata": metadata,
            "summary": output["summary"],
            "benchmark": benchmark,
            "open_position": output["open_position"],
            "orders": output["orders"],
            "trades": output["trades"],
            "equity_curve": output["equity_curve"],
        }
        _finish_run(database_path, run_id, status="COMPLETED", result=result_document, error=None)
        return BacktestRunResult(run_id, "COMPLETED", None)
    except Exception as exc:
        _finish_run(
            database_path,
            run_id,
            status="FAILED",
            result={
                "result_schema_version": 1,
                "status": "FAILED",
                "provenance_status": "PARTIAL_FAILED",
                "backtest_configuration_sha256": actual_hash,
                "error_type": type(exc).__name__,
            },
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def list_backtest_runs(database_path: Path, backtest_experiment_id: int) -> list[dict[str, object]]:
    """Read persisted backtest run summaries and outputs."""

    with closing(_readonly_connection(database_path)) as connection:
        rows = connection.execute(
            """SELECT id, started_at, completed_at, status, result_json, error_message
               FROM runs WHERE experiment_id = ? ORDER BY id DESC""",
            (backtest_experiment_id,),
        ).fetchall()
    results: list[dict[str, object]] = []
    for row in rows:
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        if isinstance(result, dict) and result:
            metadata = result.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                result["metadata"] = metadata
            missing_provenance = [
                field
                for field in ("instrument_snapshot", "source_imports")
                if field not in metadata
            ]
            if missing_provenance:
                metadata["provenance_status"] = "LEGACY_INCOMPLETE"
                metadata["legacy_missing_provenance_fields"] = missing_provenance
        results.append(
            {
                "id": int(row["id"]),
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "status": row["status"],
                "result": result,
                "error_message": row["error_message"],
            }
        )
    return results
