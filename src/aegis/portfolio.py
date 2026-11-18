"""Immutable M2 portfolio contracts and deterministic cash allocation.

NSE evaluation dates use Asia/Kolkata: an inclusive ``end_date`` must not be later
than the local calendar date of the timezone-aware ``as_of`` instant. This date-level
request check does not imply that the same-day daily candle is complete. M1B qualifies
each daily candle only when its scheduled ``completes_at`` instant is no later than
``as_of``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from aegis.backtesting import CostAssumptions
from aegis.strategy_lab import StrategySpec

MAX_ORDER_QUANTITY: Final = (1 << 63) - 1
_MAX_DECIMAL_TEXT_LENGTH: Final = 4096
_NSE_TIMEZONE: Final = ZoneInfo("Asia/Kolkata")
_STRATEGY_SEQUENCE_FIELDS: Final = frozenset({"evidence_references", "known_limitations"})
_STRATEGY_INTEGER_FIELDS: Final = frozenset({"fast_window", "slow_window"})
_POLICY_VALUES: Final[tuple[tuple[str, str], ...]] = (
    ("policy_version", "m2-v1"),
    ("allocation_method", "EQUAL_FIXED_CASH"),
    ("remainder_rule", "FROZEN_REQUEST_ORDER"),
    ("unused_cash_policy", "IDLE_IN_SLEEVE"),
    ("cross_sleeve_policy", "NO_BORROW_TRANSFER_REALLOCATION"),
    ("valuation_frequency", "DAILY"),
    ("missing_expected_session_price", "BLOCK_PORTFOLIO"),
    ("confirmed_holiday_mark_policy", "CARRY_LAST_ELIGIBLE_STALE"),
    ("terminal_liquidation_policy", "HYPOTHETICAL_ONLY"),
)


class PortfolioError(ValueError):
    """Raised when an M2 portfolio request or allocation is invalid."""


def _bounded_decimal_tuple(value: object, label: str) -> tuple[int, tuple[int, ...], int]:
    """Bound serialized Decimal size without imposing a financial capital ceiling."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise PortfolioError(f"{label} must be a finite Decimal.")

    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise PortfolioError(f"{label} must have a finite Decimal exponent.")
    if (
        len(digits) > _MAX_DECIMAL_TEXT_LENGTH
        or exponent > _MAX_DECIMAL_TEXT_LENGTH
        or exponent < -_MAX_DECIMAL_TEXT_LENGTH
    ):
        raise PortfolioError(
            f"{label} exceeds the {_MAX_DECIMAL_TEXT_LENGTH}-character Decimal "
            "representation limit."
        )

    if exponent >= 0:
        text_length = len(digits) + exponent
    else:
        decimal_places = -exponent
        integral_digits = max(1, len(digits) - decimal_places)
        text_length = integral_digits + 1 + decimal_places
    if sign:
        text_length += 1
    if text_length > _MAX_DECIMAL_TEXT_LENGTH:
        raise PortfolioError(
            f"{label} exceeds the {_MAX_DECIMAL_TEXT_LENGTH}-character Decimal "
            "representation limit."
        )
    return sign, digits, exponent


def _decimal_text(value: Decimal) -> str:
    _bounded_decimal_tuple(value, "Decimal serialization")
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _paise_value(value: object, label: str) -> int:
    """Convert an exact, positive Decimal amount to integer paise without context math."""

    if not isinstance(value, Decimal):
        raise PortfolioError(f"{label} must be a finite positive Decimal.")
    sign, digits, exponent = _bounded_decimal_tuple(value, label)
    if value <= 0:
        raise PortfolioError(f"{label} must be a finite positive Decimal.")

    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit

    if exponent < -2:
        discarded_places = -2 - exponent
        if discarded_places >= len(digits) or any(digits[-discarded_places:]):
            raise PortfolioError(f"{label} must be represented exactly to INR 0.01.")
        coefficient //= 10**discarded_places
    else:
        coefficient *= 10 ** (exponent + 2)

    return -coefficient if sign else coefficient


def _strategy_fields_are_immutable(strategy: StrategySpec) -> bool:
    """Check every StrategySpec field against its immutable scalar/tuple shape."""

    for strategy_field in fields(strategy):
        value = getattr(strategy, strategy_field.name)
        if strategy_field.name in _STRATEGY_SEQUENCE_FIELDS:
            if type(value) is not tuple or any(type(item) is not str for item in value):
                return False
        elif strategy_field.name in _STRATEGY_INTEGER_FIELDS:
            if type(value) is not int:
                return False
        elif type(value) is not str:
            return False
    return True


def _decimal_from_paise(value: int) -> Decimal:
    whole, fractional = divmod(value, 100)
    if fractional == 0:
        return Decimal(whole)
    return Decimal(f"{whole}.{fractional:02d}")


def _canonical_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioError(f"{label} must be non-empty text.")
    return value.strip().upper()


def _strategy_document(strategy: StrategySpec) -> dict[str, object]:
    document = strategy.to_dict()
    document["evidence_references"] = list(strategy.evidence_references)
    document["known_limitations"] = list(strategy.known_limitations)
    return document


def _costs_document(costs: CostAssumptions) -> dict[str, str]:
    """Serialize shared costs through the portfolio's bounded Decimal formatter."""

    return {
        cost_field.name: _decimal_text(getattr(costs, cost_field.name))
        for cost_field in fields(costs)
    }


@dataclass(frozen=True, slots=True)
class PortfolioPolicy:
    """Versioned, fixed M2 v1 accounting and valuation policy."""

    policy_version: str = "m2-v1"
    allocation_method: str = "EQUAL_FIXED_CASH"
    remainder_rule: str = "FROZEN_REQUEST_ORDER"
    unused_cash_policy: str = "IDLE_IN_SLEEVE"
    cross_sleeve_policy: str = "NO_BORROW_TRANSFER_REALLOCATION"
    valuation_frequency: str = "DAILY"
    missing_expected_session_price: str = "BLOCK_PORTFOLIO"
    confirmed_holiday_mark_policy: str = "CARRY_LAST_ELIGIBLE_STALE"
    terminal_liquidation_policy: str = "HYPOTHETICAL_ONLY"

    def __post_init__(self) -> None:
        for field_name, expected in _POLICY_VALUES:
            value = getattr(self, field_name)
            if not isinstance(value, str) or value != expected:
                raise PortfolioError(f"{field_name} must be {expected!r} for M2 v1.")

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-ready policy document."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class SleeveRequest:
    """Caller intent for one canonical instrument and its strategy."""

    exchange: str
    trading_symbol: str
    strategy: StrategySpec
    order_quantity: int

    def __post_init__(self) -> None:
        exchange = _canonical_text(self.exchange, "exchange")
        trading_symbol = _canonical_text(self.trading_symbol, "trading_symbol")
        if exchange != "NSE":
            raise PortfolioError("M2 v1 supports NSE cash EQUITY instruments only.")
        if type(self.strategy) is not StrategySpec:
            raise PortfolioError("strategy must be a StrategySpec.")
        if self.strategy.exchange != exchange or self.strategy.trading_symbol != trading_symbol:
            raise PortfolioError("Sleeve identity must match its StrategySpec.")
        if self.strategy.instrument_type != "EQUITY":
            raise PortfolioError("M2 v1 supports NSE cash EQUITY instruments only.")
        if self.strategy.interval != "1d":
            raise PortfolioError("M2 v1 supports daily strategy intervals only.")
        if not _strategy_fields_are_immutable(self.strategy):
            raise PortfolioError("StrategySpec fields must use immutable scalar or tuple values.")
        if (
            isinstance(self.order_quantity, bool)
            or not isinstance(self.order_quantity, int)
            or not 1 <= self.order_quantity <= MAX_ORDER_QUANTITY
        ):
            raise PortfolioError(
                f"order_quantity must be an integer from 1 to {MAX_ORDER_QUANTITY}."
            )
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "trading_symbol", trading_symbol)

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-ready caller-intent document."""

        return {
            "exchange": self.exchange,
            "trading_symbol": self.trading_symbol,
            "strategy_spec": _strategy_document(self.strategy),
            "order_quantity": self.order_quantity,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRequest:
    """Immutable M2 v1 request; it contains no qualified market-data evidence."""

    total_capital: Decimal
    sleeves: tuple[SleeveRequest, ...]
    start_date: date
    end_date: date
    as_of: datetime
    costs: CostAssumptions
    max_volume_participation: Decimal
    policy: PortfolioPolicy

    def __post_init__(self) -> None:
        total_paise = _paise_value(self.total_capital, "total_capital")
        if not isinstance(self.sleeves, tuple) or not self.sleeves:
            raise PortfolioError("sleeves must be a non-empty ordered tuple.")
        if any(not isinstance(sleeve, SleeveRequest) for sleeve in self.sleeves):
            raise PortfolioError("sleeves must contain SleeveRequest values.")
        identities = tuple((sleeve.exchange, sleeve.trading_symbol) for sleeve in self.sleeves)
        if len(set(identities)) != len(identities):
            raise PortfolioError("Portfolio sleeve instruments must be unique.")
        if total_paise < len(self.sleeves):
            raise PortfolioError("total_capital must provide at least one paise per sleeve.")
        if (
            not isinstance(self.start_date, date)
            or isinstance(self.start_date, datetime)
            or not isinstance(self.end_date, date)
            or isinstance(self.end_date, datetime)
            or self.start_date > self.end_date
        ):
            raise PortfolioError("start_date and end_date must be ordered calendar dates.")
        if (
            not isinstance(self.as_of, datetime)
            or self.as_of.tzinfo is None
            or self.as_of.utcoffset() is None
        ):
            raise PortfolioError("as_of must be a timezone-aware datetime.")
        try:
            as_of_nse_date = self.as_of.astimezone(_NSE_TIMEZONE).date()
        except (OverflowError, ValueError) as exc:
            raise PortfolioError("as_of cannot be represented in the NSE timezone.") from exc
        if self.end_date > as_of_nse_date:
            raise PortfolioError(
                "The evaluation window cannot extend beyond the Asia/Kolkata as_of date."
            )
        if not isinstance(self.costs, CostAssumptions):
            raise PortfolioError("costs must be CostAssumptions.")
        for cost_field in fields(self.costs):
            _bounded_decimal_tuple(getattr(self.costs, cost_field.name), f"costs.{cost_field.name}")
        if not isinstance(self.max_volume_participation, Decimal):
            raise PortfolioError("max_volume_participation must be a Decimal in (0, 1].")
        _bounded_decimal_tuple(self.max_volume_participation, "max_volume_participation")
        if not Decimal(0) < self.max_volume_participation <= Decimal(1):
            raise PortfolioError("max_volume_participation must be a Decimal in (0, 1].")
        if not isinstance(self.policy, PortfolioPolicy):
            raise PortfolioError("policy must be a validated PortfolioPolicy.")

    def to_dict(self) -> dict[str, object]:
        """Return a detached, canonical JSON-ready request document."""

        as_of_utc = self.as_of.astimezone(UTC).isoformat(timespec="microseconds")
        return {
            "portfolio_schema_version": 1,
            "total_capital": _decimal_text(self.total_capital),
            "sleeves": [sleeve.to_dict() for sleeve in self.sleeves],
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "as_of": as_of_utc.replace("+00:00", "Z"),
            "costs": _costs_document(self.costs),
            "max_volume_participation": _decimal_text(self.max_volume_participation),
            "policy": self.policy.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SleeveCashAllocation:
    """One immutable cash allocation, retaining its original request identity."""

    exchange: str
    trading_symbol: str
    cash: Decimal

    def __post_init__(self) -> None:
        exchange = _canonical_text(self.exchange, "exchange")
        trading_symbol = _canonical_text(self.trading_symbol, "trading_symbol")
        if exchange != "NSE":
            raise PortfolioError("M2 v1 allocations support NSE instruments only.")
        _paise_value(self.cash, "sleeve cash")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "trading_symbol", trading_symbol)

    def to_dict(self) -> dict[str, str]:
        """Return a detached JSON-ready allocation entry."""

        return {
            "exchange": self.exchange,
            "trading_symbol": self.trading_symbol,
            "cash": _decimal_text(self.cash),
        }


@dataclass(frozen=True, slots=True)
class CapitalAllocation:
    """Immutable allocation result in the original frozen sleeve order."""

    total_capital: Decimal
    sleeves: tuple[SleeveCashAllocation, ...]

    def __post_init__(self) -> None:
        total_paise = _paise_value(self.total_capital, "total_capital")
        if not isinstance(self.sleeves, tuple) or not self.sleeves:
            raise PortfolioError("allocation sleeves must be a non-empty tuple.")
        if any(not isinstance(sleeve, SleeveCashAllocation) for sleeve in self.sleeves):
            raise PortfolioError("allocation sleeves must contain SleeveCashAllocation values.")
        identities = tuple((sleeve.exchange, sleeve.trading_symbol) for sleeve in self.sleeves)
        if len(set(identities)) != len(identities):
            raise PortfolioError("Allocation instrument identities must be unique.")
        if total_paise < len(self.sleeves):
            raise PortfolioError("total_capital must provide at least one paise per sleeve.")
        base, remainder = divmod(total_paise, len(self.sleeves))
        allocated_paise = 0
        for index, sleeve in enumerate(self.sleeves):
            expected = base + (1 if index < remainder else 0)
            actual = _paise_value(sleeve.cash, "sleeve cash")
            if actual != expected:
                raise PortfolioError("Sleeve cash does not match equal frozen-order allocation.")
            allocated_paise += actual
        if allocated_paise != total_paise:
            raise PortfolioError("Sleeve allocations must conserve total_capital.")

    def to_dict(self) -> dict[str, object]:
        """Return a detached JSON-ready allocation document."""

        return {
            "total_capital": _decimal_text(self.total_capital),
            "sleeves": [sleeve.to_dict() for sleeve in self.sleeves],
        }


def allocate_equal_cash(request: PortfolioRequest) -> CapitalAllocation:
    """Allocate total capital equally in paise, assigning remainder in request order."""

    if not isinstance(request, PortfolioRequest):
        raise PortfolioError("request must be a PortfolioRequest.")
    total_paise = _paise_value(request.total_capital, "total_capital")
    sleeve_count = len(request.sleeves)
    if sleeve_count == 0:
        raise PortfolioError("At least one sleeve is required for allocation.")
    if total_paise < sleeve_count:
        raise PortfolioError("total_capital must provide at least one paise per sleeve.")

    base, remainder = divmod(total_paise, sleeve_count)
    allocations = tuple(
        SleeveCashAllocation(
            exchange=sleeve.exchange,
            trading_symbol=sleeve.trading_symbol,
            cash=_decimal_from_paise(base + (1 if index < remainder else 0)),
        )
        for index, sleeve in enumerate(request.sleeves)
    )
    return CapitalAllocation(total_capital=request.total_capital, sleeves=allocations)
