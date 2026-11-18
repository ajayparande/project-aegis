"""Focused tests for immutable M2 portfolio requests and cash allocation."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

import pytest

from aegis.backtesting import zero_cost_assumptions
from aegis.portfolio import (
    MAX_ORDER_QUANTITY,
    CapitalAllocation,
    PortfolioError,
    PortfolioPolicy,
    PortfolioRequest,
    SleeveCashAllocation,
    SleeveRequest,
    _decimal_text,
    allocate_equal_cash,
)
from aegis.strategy_lab import StrategySpec, create_sma_crossover_spec

_START = date(2026, 1, 1)
_END = date(2026, 1, 31)
_AS_OF = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def _strategy(
    trading_symbol: str = "TCS",
    *,
    exchange: str = "NSE",
    interval: str = "1d",
) -> StrategySpec:
    return create_sma_crossover_spec(
        exchange=exchange,
        trading_symbol=trading_symbol,
        instrument_type="EQUITY",
        interval=interval,
        fast_window=2,
        slow_window=3,
        hypothesis="A deterministic portfolio contract test fixture.",
    )


def _sleeve(
    trading_symbol: str = "TCS",
    *,
    exchange: str = "NSE",
    quantity: object = 1,
    strategy: StrategySpec | None = None,
) -> SleeveRequest:
    return SleeveRequest(
        exchange=exchange,
        trading_symbol=trading_symbol,
        strategy=strategy or _strategy(trading_symbol, exchange=exchange),
        order_quantity=quantity,  # type: ignore[arg-type]
    )


def _request(
    *,
    total_capital: object = Decimal("100.07"),
    sleeves: object = None,
    start_date: date = _START,
    end_date: date = _END,
    as_of: object = _AS_OF,
    max_volume_participation: object = Decimal("0.5"),
    costs=None,
    policy: object = None,
) -> PortfolioRequest:
    request_sleeves = (_sleeve(),) if sleeves is None else sleeves
    return PortfolioRequest(
        total_capital=total_capital,  # type: ignore[arg-type]
        sleeves=request_sleeves,  # type: ignore[arg-type]
        start_date=start_date,
        end_date=end_date,
        as_of=as_of,  # type: ignore[arg-type]
        costs=costs or zero_cost_assumptions(),
        max_volume_participation=max_volume_participation,  # type: ignore[arg-type]
        policy=policy or PortfolioPolicy(),  # type: ignore[arg-type]
    )


def test_policy_and_request_accept_approved_daily_nse_contract() -> None:
    policy = PortfolioPolicy()
    request = _request(sleeves=(_sleeve("tcs"), _sleeve("infy")))

    assert policy.to_dict()["allocation_method"] == "EQUAL_FIXED_CASH"
    assert policy.to_dict()["valuation_frequency"] == "DAILY"
    assert request.sleeves[0].trading_symbol == "TCS"
    assert request.sleeves[1].trading_symbol == "INFY"
    assert request.sleeves[0].strategy.interval == "1d"


@pytest.mark.parametrize(
    ("field", "unsupported"),
    [
        ("policy_version", "m2-v2"),
        ("allocation_method", "PROPORTIONAL"),
        ("remainder_rule", "RANDOM"),
        ("unused_cash_policy", "PORTFOLIO_POOL"),
        ("cross_sleeve_policy", "ALLOW_BORROWING"),
        ("valuation_frequency", "INTRADAY"),
        ("missing_expected_session_price", "CARRY"),
        ("confirmed_holiday_mark_policy", "NO_CARRY"),
        ("terminal_liquidation_policy", "ACTUAL_FILL"),
    ],
)
def test_policy_rejects_unsupported_values(field: str, unsupported: str) -> None:
    with pytest.raises(PortfolioError):
        PortfolioPolicy(**{field: unsupported})


def test_sleeve_request_rejects_strategy_identity_mismatch() -> None:
    with pytest.raises(PortfolioError, match="match its StrategySpec"):
        SleeveRequest("NSE", "INFY", _strategy("TCS"), 1)


def test_sleeve_request_rejects_non_daily_or_non_equity_strategies() -> None:
    with pytest.raises(PortfolioError, match="daily"):
        SleeveRequest("NSE", "TCS", _strategy(interval="1m"), 1)
    with pytest.raises(PortfolioError, match="NSE cash EQUITY"):
        SleeveRequest(
            "NSE",
            "NIFTY",
            create_sma_crossover_spec(
                exchange="NSE",
                trading_symbol="NIFTY",
                instrument_type="INDEX",
                interval="1d",
                fast_window=2,
                slow_window=3,
                hypothesis="A deterministic portfolio contract test fixture.",
            ),
            1,
        )


def test_sleeve_request_rejects_noncanonical_exchange_and_mutable_strategy_collections() -> None:
    with pytest.raises(PortfolioError, match="NSE"):
        SleeveRequest("BSE", "TCS", _strategy("TCS", exchange="BSE"), 1)

    mutable_spec = replace(_strategy(), evidence_references=["mutable"])
    with pytest.raises(PortfolioError, match="immutable scalar or tuple"):
        SleeveRequest("NSE", "TCS", mutable_spec, 1)


def test_sleeve_request_rejects_mutable_strategy_field_values() -> None:
    class MutableText(str):
        pass

    class MutableTuple(tuple):
        pass

    mutable_text = MutableText("mutable rationale")
    mutable_text.state = []
    mutable_item = MutableText("mutable limitation")
    mutable_item.state = []
    mutable_tuple = MutableTuple(("mutable reference",))
    mutable_tuple.state = []

    with pytest.raises(PortfolioError, match="immutable scalar or tuple"):
        SleeveRequest("NSE", "TCS", replace(_strategy(), rationale=mutable_text), 1)
    with pytest.raises(PortfolioError, match="immutable scalar or tuple"):
        SleeveRequest(
            "NSE",
            "TCS",
            replace(_strategy(), evidence_references=mutable_tuple),
            1,
        )
    with pytest.raises(PortfolioError, match="immutable scalar or tuple"):
        SleeveRequest(
            "NSE",
            "TCS",
            replace(_strategy(), known_limitations=(mutable_item,)),
            1,
        )


def test_sleeve_request_rejects_mutable_integer_strategy_field() -> None:
    class MutableInt(int):
        pass

    mutable_window = MutableInt(2)
    mutable_window.state = []

    with pytest.raises(PortfolioError, match="immutable scalar or tuple"):
        SleeveRequest(
            "NSE",
            "TCS",
            replace(_strategy(), fast_window=mutable_window),
            1,
        )


@pytest.mark.parametrize(
    "quantity",
    [0, -1, True, 1.5, "1", MAX_ORDER_QUANTITY + 1],
)
def test_sleeve_request_rejects_invalid_or_excessive_quantities(quantity: object) -> None:
    with pytest.raises(PortfolioError, match="order_quantity"):
        _sleeve(quantity=quantity)


def test_sleeve_request_accepts_signed_64_bit_maximum() -> None:
    assert _sleeve(quantity=MAX_ORDER_QUANTITY).order_quantity == MAX_ORDER_QUANTITY


@pytest.mark.parametrize(
    "capital",
    [
        100.07,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal(0),
        Decimal("-0.01"),
        Decimal("1.001"),
    ],
)
def test_request_rejects_invalid_or_sub_paisa_capital(capital: object) -> None:
    with pytest.raises(PortfolioError, match="total_capital"):
        _request(total_capital=capital)


@pytest.mark.parametrize(
    "capital",
    [Decimal("1E+1000000000"), Decimal("1E-1000000000")],
)
def test_request_rejects_extreme_decimal_exponents(capital: Decimal) -> None:
    with pytest.raises(PortfolioError, match="representation limit"):
        _request(total_capital=capital)


@pytest.mark.parametrize(
    "value",
    [Decimal("1E+1000000000"), Decimal("1E-1000000000")],
)
def test_decimal_serialization_rejects_extreme_exponents(value: Decimal) -> None:
    with pytest.raises(PortfolioError, match="representation limit"):
        _decimal_text(value)


def test_request_rejects_extreme_cost_decimal_before_serialization() -> None:
    costs = replace(
        zero_cost_assumptions(),
        brokerage_rate=Decimal("1E-1000000000"),
    )

    with pytest.raises(PortfolioError, match="costs.brokerage_rate"):
        _request(costs=costs)


def test_scientific_decimal_values_allocate_and_serialize_exactly() -> None:
    costs = replace(
        zero_cost_assumptions(),
        brokerage_rate=Decimal("3E-4"),
    )
    request = _request(
        total_capital=Decimal("1.0007E+2"),
        sleeves=(_sleeve("TCS"), _sleeve("INFY"), _sleeve("WIPRO")),
        max_volume_participation=Decimal("5E-1"),
        costs=costs,
    )

    allocation = allocate_equal_cash(request)
    document = request.to_dict()

    assert tuple(item.cash for item in allocation.sleeves) == (
        Decimal("33.36"),
        Decimal("33.36"),
        Decimal("33.35"),
    )
    assert document["total_capital"] == "100.07"
    assert document["max_volume_participation"] == "0.5"
    assert document["costs"]["brokerage_rate"] == "0.0003"


def test_allocation_supports_decimal_at_fixed_point_representation_limit() -> None:
    capital = Decimal("1E+4095")

    allocation = allocate_equal_cash(_request(total_capital=capital))

    serialized = allocation.to_dict()
    assert allocation.sleeves[0].cash == capital
    assert len(serialized["total_capital"]) == 4096
    assert serialized["sleeves"][0]["cash"] == serialized["total_capital"]


def test_request_rejects_duplicate_canonical_instrument_identities() -> None:
    with pytest.raises(PortfolioError, match="unique"):
        _request(sleeves=(_sleeve("tcs"), _sleeve("TCS")))


def test_request_rejects_empty_or_mutable_sleeve_collections() -> None:
    with pytest.raises(PortfolioError, match="non-empty ordered tuple"):
        _request(sleeves=())
    with pytest.raises(PortfolioError, match="non-empty ordered tuple"):
        _request(sleeves=[_sleeve()])


def test_request_rejects_naive_as_of_and_invalid_date_range() -> None:
    with pytest.raises(PortfolioError, match="timezone-aware"):
        _request(as_of=datetime(2026, 2, 1, 12, 0))
    with pytest.raises(PortfolioError, match="ordered calendar dates"):
        _request(start_date=date(2026, 2, 1), end_date=date(2026, 1, 1))
    with pytest.raises(PortfolioError, match="ordered calendar dates"):
        _request(start_date=datetime(2026, 1, 1))  # type: ignore[arg-type]


def test_request_rejects_evaluation_window_beyond_as_of_nse_date() -> None:
    nse = ZoneInfo("Asia/Kolkata")

    with pytest.raises(PortfolioError, match="Asia/Kolkata as_of date"):
        _request(
            start_date=date(2026, 1, 10),
            end_date=date(2026, 1, 12),
            as_of=datetime(2026, 1, 9, 23, 59, tzinfo=nse),
        )
    with pytest.raises(PortfolioError, match="Asia/Kolkata as_of date"):
        _request(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 12),
            as_of=datetime(2026, 1, 11, 12, 0, tzinfo=nse),
        )


def test_request_accepts_as_of_after_the_evaluation_window() -> None:
    request = _request(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 10),
        as_of=datetime(2026, 1, 11, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert request.end_date == date(2026, 1, 10)


def test_request_converts_as_of_offsets_before_applying_nse_date_cutoff() -> None:
    # This input timestamp is dated January 2 locally, but is January 1 in Kolkata.
    before_nse_cutoff = datetime(2026, 1, 2, 0, 15, tzinfo=timezone(timedelta(hours=14)))
    with pytest.raises(PortfolioError, match="Asia/Kolkata as_of date"):
        _request(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            as_of=before_nse_cutoff,
        )

    # This input timestamp is dated January 1 locally, but is January 2 in Kolkata.
    after_nse_cutoff = datetime(2026, 1, 1, 23, 0, tzinfo=timezone(timedelta(hours=-5)))
    request = _request(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        as_of=after_nse_cutoff,
    )
    assert request.end_date == date(2026, 1, 2)


def test_request_includes_exact_as_of_nse_date_but_excludes_next_date() -> None:
    as_of = datetime(2026, 1, 10, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    assert _request(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 10),
        as_of=as_of,
    ).end_date == date(2026, 1, 10)
    with pytest.raises(PortfolioError, match="Asia/Kolkata as_of date"):
        _request(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 11),
            as_of=as_of,
        )


@pytest.mark.parametrize(
    "participation",
    [0.5, Decimal(0), Decimal("-0.1"), Decimal("1.01"), Decimal("NaN"), True],
)
def test_request_rejects_invalid_volume_participation(participation: object) -> None:
    with pytest.raises(PortfolioError, match="max_volume_participation"):
        _request(max_volume_participation=participation)


def test_request_requires_one_paise_per_sleeve() -> None:
    with pytest.raises(PortfolioError, match="at least one paise per sleeve"):
        _request(
            total_capital=Decimal("0.02"),
            sleeves=(_sleeve("TCS"), _sleeve("INFY"), _sleeve("WIPRO")),
        )


def test_allocation_for_one_sleeve_preserves_total_and_identity() -> None:
    request = _request(total_capital=Decimal("100.01"))

    result = allocate_equal_cash(request)

    assert isinstance(result, CapitalAllocation)
    assert result.total_capital == Decimal("100.01")
    assert result.sleeves == (SleeveCashAllocation("NSE", "TCS", Decimal("100.01")),)


def test_allocation_is_equal_when_capital_divides_evenly() -> None:
    result = allocate_equal_cash(
        _request(
            total_capital=Decimal("100.00"),
            sleeves=(_sleeve("TCS"), _sleeve("INFY")),
        )
    )

    assert tuple(item.cash for item in result.sleeves) == (
        Decimal("50.00"),
        Decimal("50.00"),
    )


def test_allocation_assigns_paise_remainder_in_frozen_request_order() -> None:
    request = _request(
        total_capital=Decimal("100.07"),
        sleeves=(_sleeve("TCS"), _sleeve("INFY"), _sleeve("WIPRO")),
    )

    result = allocate_equal_cash(request)

    assert tuple((item.exchange, item.trading_symbol, item.cash) for item in result.sleeves) == (
        ("NSE", "TCS", Decimal("33.36")),
        ("NSE", "INFY", Decimal("33.36")),
        ("NSE", "WIPRO", Decimal("33.35")),
    )


def test_allocation_is_deterministic_conserves_capital_and_ignores_decimal_context() -> None:
    request = _request(
        total_capital=Decimal("100.07"),
        sleeves=(_sleeve("TCS"), _sleeve("INFY"), _sleeve("WIPRO")),
    )

    with localcontext() as context:
        context.prec = 1
        first = allocate_equal_cash(request)
        second = allocate_equal_cash(request)

    assert first == second
    assert sum(int(item.cash * 100) for item in first.sleeves) == 10007
    assert first.to_dict()["sleeves"] == [
        {"exchange": "NSE", "trading_symbol": "TCS", "cash": "33.36"},
        {"exchange": "NSE", "trading_symbol": "INFY", "cash": "33.36"},
        {"exchange": "NSE", "trading_symbol": "WIPRO", "cash": "33.35"},
    ]


def test_allocation_avoids_float_errors_for_exact_tenth_paisa_values() -> None:
    result = allocate_equal_cash(
        _request(
            total_capital=Decimal("0.30"),
            sleeves=(_sleeve("TCS"), _sleeve("INFY"), _sleeve("WIPRO")),
        )
    )

    assert tuple(item.cash for item in result.sleeves) == (
        Decimal("0.10"),
        Decimal("0.10"),
        Decimal("0.10"),
    )


def test_request_and_allocation_serialization_are_detached_and_frozen() -> None:
    request = _request(
        total_capital=Decimal("100.07"),
        sleeves=(_sleeve("TCS"), _sleeve("INFY")),
    )
    allocation = allocate_equal_cash(request)

    with pytest.raises(FrozenInstanceError):
        request.total_capital = Decimal("1.00")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        allocation.sleeves[0].cash = Decimal("1.00")  # type: ignore[misc]

    request_document = request.to_dict()
    request_document["sleeves"][0]["strategy_spec"]["hypothesis"] = "changed"
    request_document["costs"]["brokerage_rate"] = "999"
    request_document["policy"]["allocation_method"] = "changed"
    allocation_document = allocation.to_dict()
    allocation_document["sleeves"][0]["cash"] = "0"

    assert request.sleeves[0].strategy.hypothesis != "changed"
    assert request.costs.brokerage_rate == Decimal(0)
    assert request.policy.allocation_method == "EQUAL_FIXED_CASH"
    assert allocation.sleeves[0].cash == Decimal("50.04")
