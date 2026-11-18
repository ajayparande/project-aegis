"""Private M2-D accounting over exact, coordinator-published M2-C results.

This module performs no I/O, qualification, trading, daily valuation or hypothetical
liquidation. It retains the original immutable execution evidence and exports only
actual accounting. Idle cash is already inside each fixed sleeve; v1 leaves no
initial capital unallocated outside the sleeves.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Context, Decimal, Inexact, localcontext
from typing import Final

from aegis import backtesting, portfolio_execution, strategy_lab
from aegis.portfolio import _paise_value

_ACCOUNTING_VERSION: Final = "M2-D-v2"
_FEE_NAMES: Final = tuple(sorted(portfolio_execution._FEE_FIELDS))
_COUNT_NAMES: Final = (
    "order_count",
    "fill_count",
    "rejected_order_count",
    "unfilled_order_count",
    "trade_count",
)
_AMOUNT_NAMES: Final = (
    "starting_cash",
    "ending_cash",
    "ending_market_value",
    "ending_equity",
    "absolute_pnl",
    "realized_net_pnl",
    "buy_notional",
    "sell_notional",
    "filled_turnover",
    "total_fees",
    "spread_cost",
    "slippage_cost",
    "tick_rounding_cost",
    "cash_rounding_residual",
)


class _PortfolioAccountingIntegrityError(RuntimeError):
    """Raised without an accounting result when source evidence fails reconciliation."""


@dataclass(frozen=True, slots=True)
class _AccountingTotals:
    """Additive measures, validated in their sleeve or portfolio arithmetic domain.

    Rounded M1B measures need not satisfy regrouped, infinite-precision identities.
    Their relationships are checked in M1B operation order in _reconcile_m1b_sleeve;
    portfolio fields are checked as exact sums in _validate_portfolio_totals.
    """

    starting_cash: Decimal
    ending_cash: Decimal
    ending_market_value: Decimal
    ending_equity: Decimal
    absolute_pnl: Decimal
    realized_net_pnl: Decimal
    buy_notional: Decimal
    sell_notional: Decimal
    filled_turnover: Decimal
    total_fees: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    tick_rounding_cost: Decimal
    cash_rounding_residual: Decimal
    order_count: int
    fill_count: int
    rejected_order_count: int
    unfilled_order_count: int
    trade_count: int
    fees: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        for name in _AMOUNT_NAMES:
            value = getattr(self, name)
            if type(value) is not Decimal or not value.is_finite():
                raise _PortfolioAccountingIntegrityError(f"{name} must be a finite Decimal.")
        for name in _COUNT_NAMES:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise _PortfolioAccountingIntegrityError(f"{name} must be a non-negative integer.")
        if type(self.fees) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2 for pair in self.fees
        ):
            raise _PortfolioAccountingIntegrityError("Fee totals must be immutable pairs.")
        if tuple(name for name, _ in self.fees) != _FEE_NAMES or any(
            type(value) is not Decimal or not value.is_finite() or value < 0
            for _, value in self.fees
        ):
            raise _PortfolioAccountingIntegrityError("Fee totals must contain every charge once.")
        if self.starting_cash <= 0 or any(
            getattr(self, name) < 0
            for name in (
                "ending_market_value",
                "buy_notional",
                "sell_notional",
                "filled_turnover",
                "total_fees",
                "spread_cost",
                "slippage_cost",
                "tick_rounding_cost",
            )
        ):
            raise _PortfolioAccountingIntegrityError("Accounting amount has an invalid sign.")
        if (
            self.order_count
            != self.fill_count + self.rejected_order_count + self.unfilled_order_count
        ):
            raise _PortfolioAccountingIntegrityError("Order counts do not reconcile.")

    def to_dict(self) -> dict[str, object]:
        """Return detached canonical decimal text and integer counts."""

        return {
            **{name: backtesting._decimal_text(getattr(self, name)) for name in _AMOUNT_NAMES},
            **{name: getattr(self, name) for name in _COUNT_NAMES},
            "fees": {name: backtesting._decimal_text(value) for name, value in self.fees},
        }


@dataclass(frozen=True, slots=True)
class _ActualPosition:
    """An actual remaining position, without any hypothetical exit information."""

    quantity: int
    entry_price: Decimal
    entry_time: str
    entry_fees: Decimal

    def to_dict(self) -> dict[str, object]:
        """Return a detached position document."""

        return {
            "quantity": self.quantity,
            "entry_price": backtesting._decimal_text(self.entry_price),
            "entry_time": self.entry_time,
            "entry_fees": backtesting._decimal_text(self.entry_fees),
        }


@dataclass(frozen=True, slots=True)
class _SleeveAccounting:
    """Reconciled actual accounting with the unchanged, immutable M1B result retained."""

    sequence: int
    exchange: str
    trading_symbol: str
    allocated_cash: Decimal
    totals: _AccountingTotals
    position: _ActualPosition | None
    source_result_sha256: str
    source_provenance_sha256: str
    execution_result: backtesting.ExecutionResult = field(repr=False)

    def to_dict(self) -> dict[str, object]:
        """Export accounting only; the original M1B evidence remains available in memory."""

        return {
            "sequence": self.sequence,
            "exchange": self.exchange,
            "trading_symbol": self.trading_symbol,
            "allocated_cash": backtesting._decimal_text(self.allocated_cash),
            "totals": self.totals.to_dict(),
            "position": self.position.to_dict() if self.position is not None else None,
            "source_result_sha256": self.source_result_sha256,
            "source_provenance_sha256": self.source_provenance_sha256,
        }


@dataclass(frozen=True, slots=True)
class _PortfolioAccountingResult:
    """Complete M2-D snapshot; no public portfolio-run status or M2-E fields are added."""

    source_batch_sha256: str
    initial_capital: Decimal
    unallocated_initial_cash: Decimal
    sleeves: tuple[_SleeveAccounting, ...]
    totals: _AccountingTotals
    provenance_json: str
    result_sha256: str
    source_batch: portfolio_execution._PortfolioSleeveExecutionBatch = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.sleeves) is not tuple
            or not self.sleeves
            or any(type(sleeve) is not _SleeveAccounting for sleeve in self.sleeves)
        ):
            raise _PortfolioAccountingIntegrityError(
                "Accounting requires immutable sleeve results."
            )
        if type(self.totals) is not _AccountingTotals:
            raise _PortfolioAccountingIntegrityError("Accounting totals must be immutable.")
        _validate_portfolio_totals(self.totals, self.sleeves)
        if self.result_sha256 != strategy_lab._canonical_hash(self._document()):
            raise _PortfolioAccountingIntegrityError(
                "Accounting result fingerprint does not match."
            )

    def _document(self) -> dict[str, object]:
        return _accounting_document(
            self.source_batch_sha256,
            self.initial_capital,
            self.unallocated_initial_cash,
            self.sleeves,
            self.totals,
            self.provenance_json,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a detached accounting serialization, excluding original execution internals."""

        return {**self._document(), "result_sha256": self.result_sha256}


def _exact_sum(values: Iterable[Decimal]) -> Decimal:
    """Sum finite Decimal inputs without ambient-context rounding or a money quantize."""

    items = tuple(values)
    if not items:
        return Decimal(0)
    # Align the smallest exponent through the largest integral part, plus carries.
    minimum_exponent = min(int(value.as_tuple().exponent) for value in items)
    largest_adjusted = max(value.adjusted() for value in items)
    with localcontext() as context:
        context.prec = max(50, largest_adjusted - minimum_exponent + 1 + len(str(len(items))))
        context.traps[Inexact] = True
        return sum(items, Decimal(0))


def _decimal(value: object, label: str) -> Decimal:
    return portfolio_execution._require_decimal(value, label)


def _record(value: object, label: str) -> Mapping[str, object]:
    return portfolio_execution._require_mapping(value, label)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _PortfolioAccountingIntegrityError(message)


def _reconcile_sleeve(
    sleeve: portfolio_execution._PortfolioSleeveExecution,
    allocation: Mapping[str, object],
    member: Mapping[str, object],
) -> _SleeveAccounting:
    """Use M1B's 50-digit, half-up operations, isolated from the caller's context."""

    with localcontext(Context(prec=50, rounding=ROUND_HALF_UP)):
        return _reconcile_m1b_sleeve(sleeve, allocation, member)


def _reconcile_m1b_sleeve(
    sleeve: portfolio_execution._PortfolioSleeveExecution,
    allocation: Mapping[str, object],
    member: Mapping[str, object],
) -> _SleeveAccounting:
    """Validate recorded accounting in the operation order used by M1B _simulate.

    No order decisions, prices or fees are generated. Fee totals and turnover follow
    chronological fills, and realized P&L follows closed-trade order. Grouped charge
    and side-notional breakdowns sum their recorded components exactly; they do not
    replace M1B's separately rounded summary measures.
    """

    result = sleeve.result
    allocated = _decimal(allocation["cash"], "allocation.cash")
    identity = (sleeve.exchange, sleeve.trading_symbol)
    _require(
        identity
        == (allocation["exchange"], allocation["trading_symbol"])
        == (member["exchange"], member["trading_symbol"])
        and sleeve.sequence == member["sequence"],
        "Execution, allocation and manifest instrument bindings differ.",
    )
    summary = result.summary
    _require(
        _decimal(summary["starting_cash"], "starting_cash") == allocated,
        "Executed sleeve cash differs from its allocation.",
    )
    instrument = _record(result.diagnostics["instrument"], "instrument")
    configuration = _record(result.diagnostics["execution_configuration"], "configuration")
    _require(
        (instrument["exchange"], instrument["trading_symbol"]) == identity
        and instrument["instrument_id"] == member["instrument_id"]
        and configuration["starting_cash"] == backtesting._decimal_text(allocated)
        and configuration["order_quantity"] == member["order_quantity"],
        "M1B execution configuration or instrument binding differs from the manifest.",
    )
    orders = result.orders
    order_ids = [order["order_id"] for order in orders]
    _require(len(set(order_ids)) == len(order_ids), "Duplicate order records.")
    fills = result.fills
    fill_ids = [fill["order_id"] for fill in fills]
    _require(len(set(fill_ids)) == len(fill_ids), "Duplicate fill records.")
    filled_orders = {order["order_id"]: order for order in orders if order["status"] == "FILLED"}
    _require(set(fill_ids) == set(filled_orders), "Orders and fills do not correspond exactly.")
    expected_rejections = tuple(
        {"order_id": order["order_id"], "status": order["status"], "reason": order["reason"]}
        for order in orders
        if order["status"] != "FILLED"
    )
    _require(
        result.rejected_or_unfilled_orders == expected_rejections,
        "Rejected or unfilled order evidence is incomplete.",
    )
    buys: list[Decimal] = []
    sells: list[Decimal] = []
    fill_turnovers: list[Decimal] = []
    fill_fee_totals: list[Decimal] = []
    fee_items: dict[str, list[Decimal]] = {name: [] for name in _FEE_NAMES}
    impacts: dict[str, list[Decimal]] = {
        "spread_cost": [],
        "slippage_cost": [],
        "tick_rounding_cost": [],
    }
    cash = allocated
    quantity = 0
    rounding_residuals: list[Decimal] = []
    for fill in fills:
        order = filled_orders[fill["order_id"]]
        for name in ("side", "quantity", "fill_price", "fees", "total_fees"):
            _require(fill[name] == order[name], "Fill evidence differs from its filled order.")
        _require(
            _decimal(fill["cash_before"], "cash_before") == cash
            and fill["position_quantity_before"] == str(quantity),
            "Cash or positions moved between sleeve fills.",
        )
        cash_after = _decimal(fill["cash_after"], "cash_after")
        quantity_after = portfolio_execution._require_integer_text(
            fill["position_quantity_after"], "position_quantity_after", minimum=0
        )
        fill_quantity = portfolio_execution._require_int(fill["quantity"], "quantity", minimum=1)
        notional = _decimal(fill["fill_price"], "fill_price") * fill_quantity
        _require(fill["side"] in ("BUY", "SELL"), "Fill side is invalid.")
        _require(
            (quantity == 0 and quantity_after == fill_quantity)
            if fill["side"] == "BUY"
            else (quantity == fill_quantity and quantity_after == 0),
            "Filled position transition does not conserve quantity.",
        )
        (buys if fill["side"] == "BUY" else sells).append(notional)
        fill_turnovers.append(notional)
        fees = _record(fill["fees"], "fees")
        _require(set(fees) == set(_FEE_NAMES), "Fill omits itemized fees.")
        fill_fees = _decimal(fill["total_fees"], "total_fees")
        _require(
            backtesting._fee_total({name: _decimal(value, name) for name, value in fees.items()})
            == fill_fees,
            "Fill fees do not reconcile.",
        )
        fill_fee_totals.append(fill_fees)
        # Keep both intermediate +/- operations before M1B's money quantization.
        unrounded_cash = (
            cash - notional - fill_fees if fill["side"] == "BUY" else cash + notional - fill_fees
        )
        # Reuse M1B's cash quantization solely to validate recorded accounting.
        # No price, fee, fill decision, or cash correction is calculated here.
        _require(
            cash_after == backtesting._money(unrounded_cash),
            "Fill cash transition differs from M1B cash rounding.",
        )
        rounding_residuals.append(cash_after - unrounded_cash)
        cash, quantity = cash_after, quantity_after
        for name in _FEE_NAMES:
            fee_items[name].append(_decimal(fees[name], name))
        for name, amounts in impacts.items():
            amounts.append(_decimal(order[name], name))
    fee_totals = tuple((name, _exact_sum(fee_items[name])) for name in _FEE_NAMES)
    total_fees = _decimal(summary["total_fees"], "summary.total_fees")
    _require(
        total_fees == sum(fill_fee_totals, Decimal(0)),
        "Summary fees differ from actual fills.",
    )
    trade_ids = [trade["trade_id"] for trade in result.trades]
    _require(len(set(trade_ids)) == len(trade_ids), "Duplicate trade records.")
    realized = _decimal(summary["realized_net_pnl"], "realized_net_pnl")
    _require(
        realized
        == sum((_decimal(trade["net_pnl"], "trade.net_pnl") for trade in result.trades), Decimal(0))
        and len(result.trades) == summary["closed_trade_count"],
        "Realized P&L or closed trade count differs from actual trades.",
    )
    last = result.equity_curve[-1]
    _require(
        cash
        == _decimal(last["cash"], "last.cash")
        == _decimal(summary["ending_cash"], "ending_cash")
        and str(quantity) == last["position_quantity"]
        and quantity == summary["open_position_quantity"],
        "Ending cash or position does not match the actual execution.",
    )
    market_value = _decimal(last["mark_price"], "mark_price") * quantity
    ending_equity = _decimal(summary["ending_equity"], "ending_equity")
    _require(
        ending_equity == _decimal(last["equity"], "last.equity") == cash + market_value,
        "Ending equity differs from the actual last mark.",
    )
    buy_notional, sell_notional = _exact_sum(buys), _exact_sum(sells)
    filled_turnover = _decimal(summary["filled_turnover"], "filled_turnover")
    _require(
        sum(fill_turnovers, Decimal(0)) == filled_turnover,
        "Actual turnover differs from filled notional.",
    )
    # This reports the rounding already performed by M1B; it never repairs cash.
    residual = _exact_sum(rounding_residuals)
    absolute_pnl = _decimal(summary["absolute_pnl"], "absolute_pnl")
    _require(
        absolute_pnl == ending_equity - allocated,
        "Actual P&L differs from M1B ending equity less starting cash.",
    )
    position = result.open_position
    actual_position = None
    if position is not None:
        _require(
            position["quantity"] == quantity and quantity > 0,
            "Remaining position differs from the execution quantity.",
        )
        actual_position = _ActualPosition(
            quantity=quantity,
            entry_price=_decimal(position["entry_price"], "entry_price"),
            entry_time=portfolio_execution._require_text(position["entry_time"], "entry_time"),
            entry_fees=_decimal(position["entry_fees"], "entry_fees"),
        )
    else:
        _require(quantity == 0, "An actual remaining position is missing.")
    totals = _AccountingTotals(
        starting_cash=allocated,
        ending_cash=cash,
        ending_market_value=market_value,
        ending_equity=ending_equity,
        absolute_pnl=absolute_pnl,
        realized_net_pnl=realized,
        buy_notional=buy_notional,
        sell_notional=sell_notional,
        filled_turnover=filled_turnover,
        total_fees=total_fees,
        spread_cost=_exact_sum(impacts["spread_cost"]),
        slippage_cost=_exact_sum(impacts["slippage_cost"]),
        tick_rounding_cost=_exact_sum(impacts["tick_rounding_cost"]),
        cash_rounding_residual=residual,
        order_count=len(orders),
        fill_count=len(fills),
        rejected_order_count=sum(order["status"] == "REJECTED" for order in orders),
        unfilled_order_count=sum(order["status"] == "UNFILLED_NO_NEXT_BAR" for order in orders),
        trade_count=len(result.trades),
        fees=fee_totals,
    )
    return _SleeveAccounting(
        sequence=sleeve.sequence,
        exchange=sleeve.exchange,
        trading_symbol=sleeve.trading_symbol,
        allocated_cash=allocated,
        totals=totals,
        position=actual_position,
        source_result_sha256=sleeve.result_sha256,
        source_provenance_sha256=sleeve.provenance_sha256,
        execution_result=result,
    )


def _validate_portfolio_totals(
    totals: _AccountingTotals, sleeves: tuple[_SleeveAccounting, ...]
) -> None:
    """Require exact sums of accepted sleeve measures, without regrouping M1B operations."""

    for sleeve in sleeves:
        for name in (
            "starting_cash",
            "ending_cash",
            "ending_equity",
            "absolute_pnl",
            "realized_net_pnl",
            "total_fees",
            "filled_turnover",
        ):
            _require(
                getattr(sleeve.totals, name)
                == _decimal(sleeve.execution_result.summary[name], name),
                f"Accepted sleeve summary {name} changed during aggregation.",
            )
    for name in _AMOUNT_NAMES:
        _require(
            getattr(totals, name) == _exact_sum(getattr(sleeve.totals, name) for sleeve in sleeves),
            f"Portfolio {name} does not equal the exact sum of accepted sleeves.",
        )
    for name in _COUNT_NAMES:
        _require(
            getattr(totals, name) == sum(getattr(sleeve.totals, name) for sleeve in sleeves),
            f"Portfolio {name} does not equal its sleeve counts.",
        )
    for name, value in totals.fees:
        _require(
            value == _exact_sum(dict(sleeve.totals.fees)[name] for sleeve in sleeves),
            f"Portfolio {name} does not equal its itemized sleeve charges.",
        )


def _accounting_document(
    source_sha256: str,
    initial_capital: Decimal,
    unallocated: Decimal,
    sleeves: tuple[_SleeveAccounting, ...],
    totals: _AccountingTotals,
    provenance_json: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "accounting_version": _ACCOUNTING_VERSION,
        "source_batch_sha256": source_sha256,
        "initial_capital": backtesting._decimal_text(initial_capital),
        "unallocated_initial_cash": backtesting._decimal_text(unallocated),
        "sleeves": [sleeve.to_dict() for sleeve in sleeves],
        "totals": totals.to_dict(),
        "provenance": json.loads(provenance_json),
    }


def _aggregate_execution_batch(
    batch: portfolio_execution._PortfolioSleeveExecutionBatch,
) -> _PortfolioAccountingResult:
    """Reconcile an exact published M2-C batch and publish all accounting or raise.

    There is deliberately no public input API, database path, caller-supplied
    allocation, idle-cash amount, execution evidence, or qualification token.
    """

    source_sha256, qualification_json = portfolio_execution._read_execution_batch_evidence(batch)
    provenance = json.loads(qualification_json)
    request = _record(provenance["request"], "request")
    allocation = _record(provenance["allocation"], "allocation")
    manifest = _record(provenance["universe_manifest"], "manifest")
    requests = request["sleeves"]
    allocations = allocation["sleeves"]
    members = manifest["members"]
    _require(
        type(requests) is list and type(allocations) is list and type(members) is list,
        "Source request, manifest and allocation must contain ordered members.",
    )
    # JSON objects above are validated by the trusted M2-C handoff, then detached.
    assert (
        isinstance(requests, list) and isinstance(allocations, list) and isinstance(members, list)
    )
    _require(
        len(batch.sleeves) == len(requests) == len(allocations) == len(members) > 0,
        "Every requested sleeve must appear exactly once.",
    )
    identities = [(sleeve.exchange, sleeve.trading_symbol) for sleeve in batch.sleeves]
    _require(len(set(identities)) == len(identities), "Duplicate executed sleeve instruments.")
    initial_capital = _decimal(request["total_capital"], "total_capital")
    _require(
        initial_capital == _decimal(allocation["total_capital"], "allocation.total_capital"),
        "Source initial capital and allocation differ.",
    )
    total_paise = _paise_value(initial_capital, "initial_capital")
    base, remainder = divmod(total_paise, len(members))
    completed: list[_SleeveAccounting] = []
    for index, (sleeve, intent, cash, member) in enumerate(
        zip(batch.sleeves, requests, allocations, members, strict=True)
    ):
        intent_record = _record(intent, "sleeve intent")
        member_record = _record(member, "manifest member")
        cash_record = _record(cash, "allocation member")
        _require(
            sleeve.sequence == index
            and (sleeve.exchange, sleeve.trading_symbol)
            == (intent_record["exchange"], intent_record["trading_symbol"]),
            "Sleeve execution order or request identity differs from the manifest.",
        )
        allocated = _decimal(cash_record["cash"], "allocation.cash")
        _require(
            _paise_value(allocated, "allocated_cash") == base + (1 if index < remainder else 0),
            "Sleeve allocation violates the frozen-order paise remainder rule.",
        )
        completed.append(_reconcile_sleeve(sleeve, cash_record, member_record))
    sleeves = tuple(completed)
    allocated_total = _exact_sum(sleeve.allocated_cash for sleeve in sleeves)
    # All v1 capital is allocated. Unused sleeve cash is not counted again here.
    unallocated = Decimal(0)
    _require(
        initial_capital == _exact_sum((allocated_total, unallocated)),
        "Initial capital is not conserved.",
    )

    def amount(name: str) -> Decimal:
        return _exact_sum(getattr(sleeve.totals, name) for sleeve in sleeves)

    def count(name: str) -> int:
        return sum(getattr(sleeve.totals, name) for sleeve in sleeves)

    totals = _AccountingTotals(
        starting_cash=amount("starting_cash"),
        ending_cash=amount("ending_cash"),
        ending_market_value=amount("ending_market_value"),
        ending_equity=amount("ending_equity"),
        absolute_pnl=amount("absolute_pnl"),
        realized_net_pnl=amount("realized_net_pnl"),
        buy_notional=amount("buy_notional"),
        sell_notional=amount("sell_notional"),
        filled_turnover=amount("filled_turnover"),
        total_fees=amount("total_fees"),
        spread_cost=amount("spread_cost"),
        slippage_cost=amount("slippage_cost"),
        tick_rounding_cost=amount("tick_rounding_cost"),
        cash_rounding_residual=amount("cash_rounding_residual"),
        order_count=count("order_count"),
        fill_count=count("fill_count"),
        rejected_order_count=count("rejected_order_count"),
        unfilled_order_count=count("unfilled_order_count"),
        trade_count=count("trade_count"),
        fees=tuple(
            (name, _exact_sum(dict(sleeve.totals.fees)[name] for sleeve in sleeves))
            for name in _FEE_NAMES
        ),
    )
    _require(totals.starting_cash == initial_capital, "Portfolio capital differs from sleeve cash.")
    source_provenance = strategy_lab._canonical_json(
        {
            "accounting_version": _ACCOUNTING_VERSION,
            "source_batch_sha256": source_sha256,
            "qualification_fingerprint_sha256": batch.batch_fingerprint_sha256,
            "request_sha256": batch.request_sha256,
            "manifest_sha256": batch.manifest_sha256,
            "allocation_sha256": strategy_lab._canonical_hash(allocation),
            "calendar_configuration_sha256": batch.calendar_configuration_sha256,
            "calendar_registry_sha256": batch.calendar_registry_sha256,
            "source_implementation_fingerprints": dict(batch.implementation_fingerprints),
            "sleeve_result_sha256s": [sleeve.source_result_sha256 for sleeve in sleeves],
            "sleeve_provenance_sha256s": [sleeve.source_provenance_sha256 for sleeve in sleeves],
        }
    )
    _require(
        portfolio_execution._read_execution_batch_evidence(batch)
        == (source_sha256, qualification_json),
        "Execution evidence changed during aggregation.",
    )
    document = _accounting_document(
        source_sha256,
        initial_capital,
        unallocated,
        sleeves,
        totals,
        source_provenance,
    )
    return _PortfolioAccountingResult(
        source_batch_sha256=source_sha256,
        initial_capital=initial_capital,
        unallocated_initial_cash=unallocated,
        sleeves=sleeves,
        totals=totals,
        provenance_json=source_provenance,
        result_sha256=strategy_lab._canonical_hash(document),
        source_batch=batch,
    )
