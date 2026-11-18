"""Isolated, hand-calculated M2-D checks using the real trusted M2-C path."""

import copy
import gc
import inspect
import json
import sqlite3
import urllib.request
import weakref
from collections.abc import Mapping
from contextlib import closing
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from test_portfolio_execution import (
    _DATES,
    _PRICES,
    _all_result_fields,
    _fee_exhaustion_request,
    _request,
    _set_flat_prices,
    _set_volumes,
)
from test_portfolio_execution import (
    execution_setup as _shared_execution_setup,
)

from aegis import backtesting, database, portfolio_accounting, portfolio_execution, strategy_lab
from aegis.database import get_connection
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.portfolio_accounting import (
    _aggregate_execution_batch,
    _PortfolioAccountingIntegrityError,
)
from aegis.portfolio_execution import (
    _PortfolioExecutionIntegrityError,
    _PortfolioSleeveExecutionBatch,
    _qualify_and_execute_portfolio_request,
)

# Reuse the real M2-C temporary-database fixture without importing its tests.
execution_setup = _shared_execution_setup


def _batch(database_path: Path, request=None):
    result = _qualify_and_execute_portfolio_request(
        database_path, request or _request("TCS", "INFY")
    )
    assert type(result) is _PortfolioSleeveExecutionBatch
    return result


def _set_opens(database_path: Path, symbol: str, opens: dict[int, str]) -> None:
    """Change only an isolated fixture's next-open prices, retaining causal close signals."""

    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            "SELECT c.id, c.close_decimal FROM historical_candles AS c "
            "JOIN instruments AS i ON i.id = c.instrument_id "
            "WHERE i.trading_symbol = ? ORDER BY c.candle_timestamp",
            (symbol,),
        ).fetchall()
        for index, value in opens.items():
            close = Decimal(rows[index]["close_decimal"])
            price = Decimal(value)
            connection.execute(
                "UPDATE historical_candles SET open_decimal = ?, high_decimal = ?, low_decimal = ? "
                "WHERE id = ?",
                (value, str(max(close, price) + 2), str(min(close, price) - 2), rows[index]["id"]),
            )
        connection.commit()


def _import_precision_symbol(
    setup: tuple[Path, Path, Path],
    *,
    opening: str = "8." + "0" * 48 + "1",
    closing: str = "8",
    tick: str = "1e-49",
    scale: int = 1,
) -> str:
    """Import actual synthetic CSV evidence, without editing stored candles or master rows."""

    database_path, _, tmp_path = setup
    symbol = "PRECISION"
    register_instrument(
        database_path,
        InstrumentInput(
            "NSE",
            symbol,
            "Synthetic precision regression",
            "EQUITY",
            segment="NSE",
            lot_size=1,
            tick_size=Decimal(tick),
        ),
    )
    rows = ["timestamp,open,high,low,close,volume"]
    for index, (day, price) in enumerate(zip(_DATES, _PRICES, strict=True)):
        timestamp = datetime.combine(day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
        open_text = opening if index == 4 else str(price * scale)
        close_text = closing if index == 4 else str(price * scale)
        with localcontext() as context:
            context.prec = 200
            high = max(Decimal(open_text), Decimal(close_text)) + 2
            low = min(Decimal(open_text), Decimal(close_text)) - 2
        rows.append(f"{timestamp.isoformat()},{open_text},{high},{low},{close_text},1000")
    csv_path = tmp_path / "precision.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    imported = import_ohlcv_csv(database_path, csv_path, "NSE", symbol, "1d")
    assert imported.status == "COMPLETED" and imported.accepted_rows == len(_DATES)
    declare_import_source_use_status(
        database_path,
        imported.import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic precision fixture",
        rationale="Synthetic fixture data only.",
    )
    return symbol


def test_public_csv_precision_realized_pnl_retains_accepted_m1b_summary(execution_setup):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup)
    source = _batch(database_path, _request(symbol, quantity=2))
    execution = source.sleeves[0].result
    assert execution.summary["realized_net_pnl"] == "12"
    with localcontext() as context:
        context.prec = 100
        raw_sum = sum((Decimal(trade["net_pnl"]) for trade in execution.trades), Decimal(0))
    assert raw_sum != Decimal(execution.summary["realized_net_pnl"])
    before = source.to_dict()
    result = _aggregate_execution_batch(source)
    assert result.totals.realized_net_pnl == result.sleeves[0].totals.realized_net_pnl == 12
    assert result.sleeves[0].execution_result is execution
    assert source.to_dict() == before


def test_precision_turnover_retains_chronological_m1b_summary(execution_setup):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup)
    source = _batch(database_path, _request(symbol))
    execution = source.sleeves[0].result
    assert execution.summary["filled_turnover"] == "50"
    with localcontext() as context:
        context.prec = 100
        raw_turnover = sum(
            (Decimal(fill["fill_price"]) * fill["quantity"] for fill in execution.fills), Decimal(0)
        )
    assert raw_turnover != Decimal(execution.summary["filled_turnover"])
    result = _aggregate_execution_batch(source)
    assert result.totals.filled_turnover == result.sleeves[0].totals.filled_turnover == 50


@pytest.mark.parametrize(
    ("mark", "capital"),
    [
        ("8." + "0" * 48 + "1", "1000"),
        ("2" + "0" * 48, "1000.01"),
    ],
)
def test_precision_ending_equity_preserves_m1b_multiplication_then_addition(
    execution_setup, mark, capital
):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup, opening="8", closing=mark)
    source = _batch(database_path, _request(symbol, capital=capital, end_index=4))
    execution = source.sleeves[0].result
    with localcontext() as context:
        context.prec = 200
        raw_equity = Decimal(execution.summary["ending_cash"]) + Decimal(mark)
        raw_pnl = Decimal(execution.summary["ending_equity"]) - Decimal(capital)
    assert raw_equity != Decimal(execution.summary["ending_equity"])
    if capital == "1000.01":
        assert raw_pnl != Decimal(execution.summary["absolute_pnl"])
    result = _aggregate_execution_batch(source)
    assert result.totals.ending_equity == Decimal(execution.summary["ending_equity"])
    assert result.totals.ending_market_value == Decimal(mark)
    assert result.totals.absolute_pnl == Decimal(execution.summary["absolute_pnl"])
    assert result.sleeves[0].position.quantity == 1


def test_precision_cash_transition_preserves_intermediate_rounding_before_money(execution_setup):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup, opening="8.0051", tick="0.0001")
    with localcontext() as context:
        context.prec = 100
        capital = Decimal("9e46") + Decimal("0.01")
        enlarged_cash = (capital - Decimal("8.0051")).quantize(Decimal(".01"))
    source = _batch(database_path, _request(symbol, capital=str(capital)))
    assert Decimal(source.sleeves[0].result.fills[0]["cash_after"]) != enlarged_cash
    result = _aggregate_execution_batch(source)
    assert result.totals.ending_cash == Decimal(source.sleeves[0].result.summary["ending_cash"])
    assert result.totals.cash_rounding_residual == Decimal("0.005")


def test_precision_fees_and_actual_pnl_preserve_recorded_m1b_summaries(execution_setup):
    database_path, _, _ = execution_setup
    with localcontext() as context:
        context.prec = 100
        capital = Decimal("9e47") + Decimal("0.01")
    symbol = _import_precision_symbol(
        execution_setup, opening=str(8 * 10**47), closing=str(8 * 10**47), scale=10**47
    )
    costs = replace(
        backtesting.zero_cost_assumptions(),
        brokerage_rate=Decimal(".1"),
        transaction_tax_sell_rate=Decimal(".7"),
        stamp_duty_buy_rate=Decimal("1.25e-50"),
    )
    source = _batch(
        database_path, replace(_request(symbol, capital=str(capital), end_index=6), costs=costs)
    )
    execution = source.sleeves[0].result
    with localcontext() as context:
        context.prec = 100
        raw_fees = sum((Decimal(fill["total_fees"]) for fill in execution.fills), Decimal(0))
    assert raw_fees != Decimal(execution.summary["total_fees"])
    result = _aggregate_execution_batch(source)
    for name in ("total_fees", "ending_cash", "ending_equity", "absolute_pnl", "realized_net_pnl"):
        assert getattr(result.totals, name) == Decimal(execution.summary[name])
    with localcontext() as context:
        context.prec = 100
        itemized_sum = sum((value for _, value in result.totals.fees), Decimal(0))
    assert itemized_sum == raw_fees
    assert (
        itemized_sum != result.totals.total_fees
    )  # M1B's chronological total rounded at 50 digits


def test_multi_sleeve_precision_totals_sum_accepted_summaries_exactly(execution_setup):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup)
    source = _batch(database_path, _request(symbol, "TCS"))
    with localcontext() as context:
        context.prec = 100
        expected = sum(
            (Decimal(s.result.summary["realized_net_pnl"]) for s in source.sleeves), Decimal(0)
        )
    with localcontext() as context:
        context.prec = 50
        rounded = sum(
            (Decimal(s.result.summary["realized_net_pnl"]) for s in source.sleeves), Decimal(0)
        )
    assert expected != rounded
    before = source.to_dict()
    result = _aggregate_execution_batch(source)
    assert result.totals.realized_net_pnl == expected
    for name in (
        "realized_net_pnl",
        "total_fees",
        "filled_turnover",
        "ending_cash",
        "ending_equity",
        "absolute_pnl",
    ):
        with localcontext() as context:
            context.prec = 100
            total = sum((Decimal(s.result.summary[name]) for s in source.sleeves), Decimal(0))
        assert getattr(result.totals, name) == total
    assert source.to_dict() == before
    assert result.to_dict() == _aggregate_execution_batch(source).to_dict()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("realized_net_pnl", "incorrect", "Realized P&L"),
        ("filled_turnover", "incorrect", "Actual turnover"),
        ("ending_cash", "incorrect", "Ending cash"),
        ("ending_equity", "incorrect", "Ending equity"),
        ("absolute_pnl", "incorrect", "Actual P&L"),
        ("total_fees", "incorrect", "Summary fees"),
        ("realized_net_pnl", "NaN", "finite"),
        ("filled_turnover", "Infinity", "finite"),
        ("ending_equity", "-Infinity", "finite"),
    ],
)
def test_precision_reconciliations_still_reject_corrupt_later_sleeve(
    execution_setup,
    name: str,
    value: str,
    message: str,
):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup)
    source = _batch(database_path, _request("TCS", symbol, quantity=2))
    _, qualification_json = portfolio_execution._read_execution_batch_evidence(source)
    qualified = json.loads(qualification_json)
    values = _all_result_fields(source.sleeves[1].result)
    if value == "incorrect":
        with localcontext() as context:
            context.prec = 100
            value = backtesting._decimal_text(Decimal(values["summary"][name]) + 1)
    values["summary"][name] = value
    malformed = backtesting.ExecutionResult(**values)
    candidate = copy.copy(source.sleeves[1])
    object.__setattr__(candidate, "result", malformed)
    # Exercise the actual numeric comparison, independently of the identity guard.
    with pytest.raises(
        (_PortfolioAccountingIntegrityError, _PortfolioExecutionIntegrityError), match=message
    ):
        portfolio_accounting._reconcile_sleeve(
            candidate,
            qualified["allocation"]["sleeves"][1],
            qualified["universe_manifest"]["members"][1],
        )
    object.__setattr__(source.sleeves[1], "result", malformed)
    published = None
    with pytest.raises(_PortfolioExecutionIntegrityError):
        published = _aggregate_execution_batch(source)
    assert published is None


@pytest.mark.parametrize(
    "name",
    [
        "realized_net_pnl",
        "filled_turnover",
        "total_fees",
        "ending_cash",
        "ending_equity",
        "absolute_pnl",
        "cash_rounding_residual",
    ],
)
def test_portfolio_precision_measures_cannot_diverge_from_accepted_sleeve_sums(
    execution_setup, name
):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup)
    result = _aggregate_execution_batch(_batch(database_path, _request(symbol, "TCS")))
    with localcontext() as context:
        context.prec = 100
        incorrect = getattr(result.totals, name) + 1
    totals = replace(result.totals, **{name: incorrect})
    with pytest.raises(_PortfolioAccountingIntegrityError, match="exact sum"):
        replace(result, totals=totals)


@pytest.mark.parametrize("cash_after", ["984.001", "984.01", "NaN"])
def test_precision_cash_residual_is_observed_not_a_balancing_adjustment(
    execution_setup, cash_after
):
    database_path, _, _ = execution_setup
    symbol = _import_precision_symbol(execution_setup)
    source = _batch(database_path, _request(symbol, quantity=2))
    _, qualification_json = portfolio_execution._read_execution_batch_evidence(source)
    qualified = json.loads(qualification_json)
    values = _all_result_fields(source.sleeves[0].result)
    values["fills"][0]["cash_after"] = cash_after
    candidate = copy.copy(source.sleeves[0])
    object.__setattr__(candidate, "result", backtesting.ExecutionResult(**values))
    with pytest.raises(
        (_PortfolioAccountingIntegrityError, _PortfolioExecutionIntegrityError),
        match="cash transition|finite",
    ):
        portfolio_accounting._reconcile_sleeve(
            candidate,
            qualified["allocation"]["sleeves"][0],
            qualified["universe_manifest"]["members"][0],
        )


def test_one_sleeve_hand_calculated_accounting_and_original_evidence(execution_setup):
    database_path, _, _ = execution_setup
    source = _batch(database_path, _request("TCS"))
    before = source.to_dict()
    result = _aggregate_execution_batch(source)
    assert result.source_batch is source
    assert result.sleeves[0].execution_result is source.sleeves[0].result
    assert result.initial_capital == result.totals.starting_cash == Decimal(1000)
    assert result.unallocated_initial_cash == 0
    totals = result.totals
    # Buy 8, sell 13; buy 14, sell 15. Final entry has no next bar.
    assert totals.buy_notional == 22
    assert totals.sell_notional == 28
    assert totals.ending_cash == totals.ending_equity == 1006
    assert totals.realized_net_pnl == totals.absolute_pnl == 6
    assert totals.total_fees == totals.cash_rounding_residual == 0
    assert (totals.order_count, totals.fill_count, totals.trade_count) == (5, 4, 2)
    assert (totals.rejected_order_count, totals.unfilled_order_count) == (0, 1)
    assert result.sleeves[0].position is None
    assert source.to_dict() == before


def test_equal_sleeves_every_aggregate_is_the_exact_sum(execution_setup):
    database_path, _, _ = execution_setup
    result = _aggregate_execution_batch(_batch(database_path))
    assert [s.allocated_cash for s in result.sleeves] == [Decimal(500), Decimal(500)]
    for name in (*portfolio_accounting._AMOUNT_NAMES, *portfolio_accounting._COUNT_NAMES):
        assert getattr(result.totals, name) == sum(getattr(s.totals, name) for s in result.sleeves)
    assert result.totals.ending_cash == 1012
    assert result.totals.absolute_pnl == result.totals.realized_net_pnl == 12
    assert result.totals.trade_count == 4


@pytest.mark.parametrize("symbols", [("INFY", "TCS"), ("TCS", "INFY")])
def test_paise_remainder_and_idle_cash_are_preserved_in_manifest_order(execution_setup, symbols):
    database_path, _, _ = execution_setup
    _set_flat_prices(database_path, "TCS")
    _set_flat_prices(database_path, "INFY")
    result = _aggregate_execution_batch(_batch(database_path, _request(*symbols, capital="100.07")))
    assert [s.trading_symbol for s in result.sleeves] == list(symbols)
    assert [s.allocated_cash for s in result.sleeves] == [Decimal("50.04"), Decimal("50.03")]
    assert result.initial_capital == sum(s.allocated_cash for s in result.sleeves)
    assert result.unallocated_initial_cash == 0  # idle cash is already in the sleeves
    assert result.totals.ending_cash == Decimal("100.07")
    assert result.totals.order_count == result.totals.fill_count == 0
    assert result.totals.absolute_pnl == 0


def test_unused_sleeve_cash_does_not_fund_an_unaffordable_sleeve(execution_setup):
    database_path, _, _ = execution_setup
    request = _request("TCS", "INFY", capital="100")
    request = replace(
        request, sleeves=(replace(request.sleeves[0], order_quantity=100), request.sleeves[1])
    )
    result = _aggregate_execution_batch(_batch(database_path, request))
    assert result.sleeves[0].totals.fill_count == 0
    assert result.sleeves[0].totals.ending_cash == 50
    assert result.sleeves[1].totals.ending_cash == 56
    assert result.totals.ending_cash == 106
    assert result.totals.rejected_order_count > 0


def test_volume_rejections_and_unfilled_counts_are_preserved(execution_setup):
    database_path, _, _ = execution_setup
    _set_volumes(database_path, "TCS", (0,) * len(_DATES))
    result = _aggregate_execution_batch(_batch(database_path, _request("TCS")))
    assert result.totals.fill_count == result.totals.trade_count == 0
    assert (
        result.totals.order_count
        == result.totals.rejected_order_count + result.totals.unfilled_order_count
    )
    assert result.totals.rejected_order_count > 0
    assert result.totals.ending_cash == 1000


def test_transaction_costs_are_itemized_once_without_double_subtraction(execution_setup):
    database_path, _, _ = execution_setup
    costs = replace(
        backtesting.zero_cost_assumptions(),
        brokerage_rate=Decimal(".01"),
        exchange_charge_rate=Decimal(".02"),
        regulatory_charge_rate=Decimal(".03"),
        transaction_tax_buy_rate=Decimal(".04"),
        transaction_tax_sell_rate=Decimal(".04"),
        stamp_duty_buy_rate=Decimal(".05"),
        gst_rate=Decimal(".18"),
    )
    request = replace(_request("TCS", "INFY"), costs=costs)
    result = _aggregate_execution_batch(_batch(database_path, request))
    # Per sleeve fees: 1.29 + 1.44 + 2.25 + 1.66 = 6.64.
    assert result.totals.total_fees == Decimal("13.28")
    assert result.totals.ending_cash == result.totals.ending_equity == Decimal("998.72")
    assert result.totals.realized_net_pnl == result.totals.absolute_pnl == Decimal("-1.28")
    assert dict(result.totals.fees) == {
        "brokerage": Decimal(1),
        "exchange_charges": Decimal(2),
        "regulatory_charges": Decimal(3),
        "transaction_tax": Decimal(4),
        "stamp_duty": Decimal("2.2"),
        "gst": Decimal("1.08"),
    }


def test_execution_impact_costs_are_reported_without_additional_cash_debits(execution_setup):
    database_path, _, _ = execution_setup
    request = replace(
        _request("TCS"),
        costs=replace(
            backtesting.default_cost_assumptions(),
            spread_basis_points=Decimal(20),
            slippage_basis_points=Decimal(50),
        ),
    )
    source = _batch(database_path, request)
    result = _aggregate_execution_batch(source)
    for name in ("spread_cost", "slippage_cost", "tick_rounding_cost"):
        assert getattr(result.totals, name) > 0
        assert getattr(result.totals, name) == sum(
            Decimal(order[name])
            for order in source.sleeves[0].result.orders
            if order["status"] == "FILLED"
        )
    assert result.totals.ending_cash == Decimal(source.sleeves[0].result.summary["ending_cash"])


@pytest.mark.parametrize(
    ("opens", "expected"),
    [
        ({}, "6"),
        ({4: "10", 6: "10", 7: "10", 9: "10"}, "0"),
        ({4: "12", 6: "8", 7: "14", 9: "5"}, "-13"),
    ],
)
def test_positive_zero_and_negative_realized_pnl(execution_setup, opens, expected):
    database_path, _, _ = execution_setup
    _set_opens(database_path, "TCS", opens)
    result = _aggregate_execution_batch(_batch(database_path, _request("TCS")))
    assert result.totals.realized_net_pnl == result.totals.absolute_pnl == Decimal(expected)


def test_fee_exhaustion_preserves_valid_negative_cash_and_later_rejections(execution_setup):
    database_path, _, _ = execution_setup
    source = _batch(database_path, _fee_exhaustion_request("TCS", "INFY"))
    result = _aggregate_execution_batch(source)
    # Each sleeve: 72 - 8 - 64 = 0; sell: 0 + 13 - 91 = -78.
    assert result.initial_capital == 144
    assert [s.totals.ending_cash for s in result.sleeves] == [Decimal(-78), Decimal(-78)]
    assert result.totals.ending_cash == result.totals.ending_equity == -156
    assert result.totals.total_fees == 310
    assert result.totals.realized_net_pnl == result.totals.absolute_pnl == -300
    assert result.totals.rejected_order_count > 0
    assert result.totals.cash_rounding_residual == 0


def test_sub_paise_m1b_cash_rounding_is_explicit_and_never_repaired(execution_setup):
    database_path, _, _ = execution_setup
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE instruments SET tick_size_decimal = ? WHERE trading_symbol = ?",
            ("0.005", "TCS"),
        )
        connection.commit()
    _set_opens(database_path, "TCS", {4: "8.005"})
    source = _batch(database_path, _request("TCS"))
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = context.traps[Rounded] = True
        result = _aggregate_execution_batch(source)
    assert result.totals.cash_rounding_residual == Decimal("0.005")
    assert result.totals.realized_net_pnl == Decimal("5.995")
    assert result.totals.absolute_pnl == 6
    assert result.totals.ending_cash == 1006
    assert result.sleeves[0].execution_result is source.sleeves[0].result


def test_remaining_position_uses_actual_equity_without_hypothetical_liquidation(execution_setup):
    database_path, _, _ = execution_setup
    request = replace(_request("TCS", end_index=4), costs=backtesting.default_cost_assumptions())
    source = _batch(database_path, request)
    result = _aggregate_execution_batch(source)
    assert result.sleeves[0].position.quantity == 1
    assert result.totals.trade_count == 0
    assert result.totals.realized_net_pnl == 0
    assert result.totals.ending_market_value == 8
    assert result.totals.ending_equity == Decimal(source.sleeves[0].result.summary["ending_equity"])
    assert result.totals.total_fees == sum(
        Decimal(f["total_fees"]) for f in source.sleeves[0].result.fills
    )
    exported = result.to_dict()

    def no_valuation_fields(value):
        if isinstance(value, dict):
            assert not set(value) & {
                "daily_valuations",
                "valuations",
                "equity_curve",
                "daily_returns",
                "net_liquidation_value",
                "net_liquidation_pnl",
                "estimated_liquidation_costs",
                "hypothetical_terminal_liquidation",
                "unrealized_net_pnl",
            }
            for item in value.values():
                no_valuation_fields(item)
        elif isinstance(value, list):
            for item in value:
                no_valuation_fields(item)

    no_valuation_fields(exported)


def test_repeat_execution_and_decimal_context_produce_identical_results(execution_setup):
    database_path, _, _ = execution_setup
    first_source = _batch(database_path)
    second_source = _batch(database_path)
    first = _aggregate_execution_batch(first_source)
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        second = _aggregate_execution_batch(second_source)
    assert first.to_dict() == second.to_dict()
    assert first.result_sha256 == second.result_sha256
    assert first.source_batch_sha256 == strategy_lab._canonical_hash(first_source.to_dict())
    doc = first.to_dict()
    fingerprint = doc.pop("result_sha256")
    assert fingerprint == strategy_lab._canonical_hash(doc)
    assert doc["provenance"]["source_batch_sha256"] == first.source_batch_sha256
    assert doc["provenance"]["request_sha256"] == first_source.request_sha256


def _assert_immutable(value):
    if is_dataclass(value):
        assert value.__dataclass_params__.frozen
        for item in fields(value):
            _assert_immutable(getattr(value, item.name))
    elif isinstance(value, Mapping):
        assert type(value).__name__ == "mappingproxy"
        for item in value.values():
            _assert_immutable(item)
    elif isinstance(value, tuple):
        for item in value:
            _assert_immutable(item)
    else:
        assert type(value) in (str, int, Decimal, bool, type(None))


def test_all_result_data_is_deeply_immutable_and_exports_are_detached(execution_setup):
    database_path, _, _ = execution_setup
    source = _batch(database_path)
    result = _aggregate_execution_batch(source)
    _assert_immutable(result)
    before = result.to_dict()
    exported = result.to_dict()
    exported["sleeves"][0]["totals"]["ending_cash"] = "999999"
    exported["totals"]["fees"]["brokerage"] = "999999"
    exported["provenance"]["sleeve_result_sha256s"].append("tamper")
    assert result.to_dict() == before
    with pytest.raises(FrozenInstanceError):
        result.initial_capital = Decimal(1)
    with pytest.raises(FrozenInstanceError):
        result.sleeves[0].totals.ending_cash = Decimal(1)
    with pytest.raises(TypeError):
        result.sleeves[0].execution_result.fills[0]["cash_after"] = "tamper"


@pytest.mark.parametrize("kind", ["copy", "replace", "wrong_type", "blocked"])
def test_only_exact_coordinator_published_batches_are_accepted(execution_setup, kind):
    database_path, _, _ = execution_setup
    source = _batch(database_path)
    candidate = {
        "copy": lambda: copy.copy(source),
        "replace": lambda: replace(source),
        "wrong_type": lambda: object(),
        "blocked": lambda: None,
    }[kind]()
    with pytest.raises(_PortfolioExecutionIntegrityError):
        _aggregate_execution_batch(candidate)
    assert tuple(inspect.signature(_aggregate_execution_batch).parameters) == ("batch",)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "duplicate",
        "unexpected",
        "reordered",
        "allocation",
        "fill",
        "fees",
        "cash",
        "cross_sleeve_transfer",
        "duplicate_fill",
        "duplicate_order",
        "duplicate_trade",
        "missing_trade",
        "inconsistent_position",
        "result_hash",
        "provenance_hash",
        "request_hash",
        "manifest_hash",
        "calendar_hash",
        "implementation_hash",
        "source_provenance",
        "nan",
        "infinity",
        "noncanonical_decimal",
        "wrong_decimal_type",
    ],
)
def test_tampered_evidence_is_rejected_without_successful_publication(execution_setup, kind):
    database_path, _, _ = execution_setup
    source = _batch(database_path)
    first, second = source.sleeves
    if kind == "missing":
        object.__setattr__(source, "sleeves", (first,))
    elif kind == "duplicate":
        object.__setattr__(source, "sleeves", (first, first))
    elif kind == "reordered":
        object.__setattr__(source, "sleeves", (second, first))
    elif kind == "unexpected":
        object.__setattr__(second, "trading_symbol", "UNEXPECTED")
    elif kind in {"request_hash", "manifest_hash", "calendar_hash", "implementation_hash"}:
        name = {
            "request_hash": "request_sha256",
            "manifest_hash": "manifest_sha256",
            "calendar_hash": "calendar_configuration_sha256",
        }.get(kind)
        if name is not None:
            object.__setattr__(source, name, "0" * 64)
        else:
            object.__setattr__(source, "implementation_fingerprints", (("different", "0" * 64),))
    elif kind in {"result_hash", "provenance_hash"}:
        object.__setattr__(
            second, "result_sha256" if kind == "result_hash" else "provenance_sha256", "0" * 64
        )
    elif kind in {"allocation", "source_provenance"}:
        doc = json.loads(second.provenance_json)
        if kind == "allocation":
            doc["execution_configuration"]["starting_cash"] = "501"
        else:
            doc["dataset_sha256"] = "0" * 64
        object.__setattr__(second, "provenance_json", strategy_lab._canonical_json(doc))
        object.__setattr__(second, "provenance_sha256", strategy_lab._canonical_hash(doc))
    else:
        values = _all_result_fields(second.result)
        if kind == "fill":
            values["fills"][0]["fill_price"] = "9"
        elif kind == "fees":
            values["summary"]["total_fees"] = "1"
        elif kind in {"cash", "cross_sleeve_transfer"}:
            values["summary"]["ending_cash"] = "505"
            if kind == "cross_sleeve_transfer":
                other = _all_result_fields(first.result)
                other["summary"]["ending_cash"] = "507"
                object.__setattr__(first, "result", backtesting.ExecutionResult(**other))
        elif kind.startswith("duplicate_"):
            key = {
                "duplicate_fill": "fills",
                "duplicate_order": "orders",
                "duplicate_trade": "trades",
            }[kind]
            values[key].append(copy.deepcopy(values[key][0]))
        elif kind == "missing_trade":
            values["trades"] = []
        elif kind == "inconsistent_position":
            values["summary"]["open_position_quantity"] = 1
        else:
            values["fills"][0]["cash_after"] = {
                "nan": "NaN",
                "infinity": "Infinity",
                "noncanonical_decimal": "492.00",
                "wrong_decimal_type": 492,
            }[kind]
        object.__setattr__(second, "result", backtesting.ExecutionResult(**values))
        # Even recomputing the exposed content hash cannot mint a trusted batch.
        object.__setattr__(
            second, "result_sha256", portfolio_execution._execution_result_sha256(second.result)
        )
    published = None
    with pytest.raises((_PortfolioExecutionIntegrityError, TypeError, ValueError)):
        published = _aggregate_execution_batch(source)
    assert published is None


def test_later_accounting_failure_and_mid_aggregation_mutation_publish_no_partial_result(
    execution_setup,
    monkeypatch,
):
    database_path, _, _ = execution_setup
    source = _batch(database_path)
    original = portfolio_accounting._reconcile_sleeve
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise _PortfolioAccountingIntegrityError("Synthetic second-sleeve failure")
        return original(*args)

    monkeypatch.setattr(portfolio_accounting, "_reconcile_sleeve", fail_second)
    published = None
    with pytest.raises(_PortfolioAccountingIntegrityError, match="second-sleeve"):
        published = _aggregate_execution_batch(source)
    assert calls == 2 and published is None

    def change_after_reconciliation(*args):
        result = original(*args)
        object.__setattr__(source, "request_sha256", "0" * 64)
        return result

    monkeypatch.setattr(portfolio_accounting, "_reconcile_sleeve", change_after_reconciliation)
    with pytest.raises(_PortfolioExecutionIntegrityError):
        published = _aggregate_execution_batch(source)
    assert published is None


@pytest.mark.parametrize("kind", ["mutable_result", "duplicate_fingerprints"])
def test_handoff_rejects_structural_changes_with_identical_json_values(execution_setup, kind):
    database_path, _, _ = execution_setup
    source = _batch(database_path)
    original_document = source.to_dict()
    if kind == "mutable_result":
        result = source.sleeves[0].result
        object.__setattr__(result, "summary", dict(result.summary))
    else:
        object.__setattr__(
            source,
            "implementation_fingerprints",
            (*source.implementation_fingerprints, source.implementation_fingerprints[0]),
        )
    assert source.to_dict() == original_document
    with pytest.raises(_PortfolioExecutionIntegrityError):
        _aggregate_execution_batch(source)


def test_reconciliation_rejects_balancing_cash_as_a_fake_rounding_residual(execution_setup):
    database_path, _, _ = execution_setup
    source = _batch(database_path, _request("TCS"))
    _, qualification_json = portfolio_execution._read_execution_batch_evidence(source)
    qualification = json.loads(qualification_json)
    sleeve = source.sleeves[0]
    values = _all_result_fields(sleeve.result)
    # A non-paise cash error must not be excused as an observed rounding residual.
    values["fills"][0]["cash_after"] = "992.001"
    object.__setattr__(sleeve, "result", backtesting.ExecutionResult(**values))
    with pytest.raises(_PortfolioAccountingIntegrityError, match="cash transition"):
        portfolio_accounting._reconcile_sleeve(
            sleeve,
            qualification["allocation"]["sleeves"][0],
            qualification["universe_manifest"]["members"][0],
        )
    with pytest.raises(_PortfolioExecutionIntegrityError):
        _aggregate_execution_batch(source)


def test_aggregation_performs_no_io_qualification_execution_or_liquidation(
    execution_setup, monkeypatch
):
    database_path, _, _ = execution_setup
    source = _batch(database_path)

    def forbidden(*args, **kwargs):
        pytest.fail("M2-D must aggregate existing evidence without I/O or further execution.")

    for module, name in (
        (database, "get_connection"),
        (database, "get_readonly_connection"),
        (database, "initialize_database"),
        (sqlite3, "connect"),
        (backtesting, "_readonly_connection"),
        (backtesting, "_qualify_one_instrument"),
        (backtesting, "_execute_qualified_request"),
        (backtesting, "_simulate"),
        (backtesting, "_buy_and_hold_benchmark"),
        (backtesting, "_fees"),
        (backtesting, "_fill_price"),
        (backtesting, "generate_sma_crossover_signals"),
        (portfolio_execution, "_load_bound_calendar_registry"),
        (portfolio_execution, "_capture_approved_calendar_path"),
        (portfolio_execution.portfolio_qualification, "_qualify_portfolio_request"),
        (Path, "read_bytes"),
        (Path, "read_text"),
        (Path, "write_bytes"),
        (Path, "write_text"),
        (Path, "open"),
        (urllib.request, "urlopen"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    result = _aggregate_execution_batch(source)
    assert result.totals.ending_cash == 1012
    assert result.to_dict()["totals"]["ending_cash"] == "1012"


def test_accounting_source_has_no_phase8_broker_or_persistence_dependencies():
    import ast

    tree = ast.parse(inspect.getsource(portfolio_accounting))
    imported = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = {
        "paper_trading",
        "forward_paper_trading",
        "database",
        "sqlite3",
        "requests",
        "kiteconnect",
        "zerodha",
    }
    assert not any(set(name.split(".")) & forbidden for name in imported)


def test_published_registration_lives_with_result_and_releases_after_last_reference(
    execution_setup,
):
    database_path, _, _ = execution_setup
    source = _batch(database_path)
    batch_id = id(source)
    reference = weakref.ref(source)
    result = _aggregate_execution_batch(source)
    del source
    gc.collect()
    assert reference() is result.source_batch
    assert _aggregate_execution_batch(result.source_batch).to_dict() == result.to_dict()
    del result
    gc.collect()
    assert reference() is None
    assert batch_id not in portfolio_execution._PUBLISHED_EXECUTION_BATCHES
