"""Private, read-only M5-B1 synthetic allocation; never an execution capability.

M2's CapitalAllocation validates the thin paise-allocation adapter below. Historical
M2 qualification/execution batches are deliberately not constructed or promoted.
Phase 8 fee/price arithmetic and persisted RiskLimits are reused, not its mutating
risk/order entry points. Accounts with financial history need a future authenticated
forward sleeve-cash contract and fail closed here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Literal, TypeVar, cast

from aegis.backtesting import CostAssumptions, _fee_total, _fees, _fill_price
from aegis.database import get_readonly_connection
from aegis.forward_runtime import ForwardRuntimeService
from aegis.market_quality import CalendarConfigError, load_approved_calendar_registry
from aegis.paper_equity import EquityEvidenceError, verify_account_inception
from aegis.paper_trading import RiskLimits, _reconcile
from aegis.portfolio import (
    MAX_ORDER_QUANTITY,
    CapitalAllocation,
    PortfolioPolicy,
    SleeveCashAllocation,
    _decimal_from_paise,
    _paise_value,
)

_UNIVERSE = ("NSE:INFY", "NSE:RELIANCE", "NSE:TCS")
_ZERO = Decimal(0)
_Action = Literal["BUY", "SELL", "HOLD"]
_Status = Literal["APPROVED_HYPOTHETICAL", "BLOCKED", "HOLD_NO_ACTION", "UNAVAILABLE_EVIDENCE"]
_T = TypeVar("_T")


class _PretradeError(ValueError):
    """Invalid caller intent or immutable request contract."""


class _Unavailable(ValueError):
    """A deterministic reason code for unavailable authoritative evidence."""


def _text(value: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise _PretradeError("Identifiers must be nonempty bounded text.")


def _instant(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise _PretradeError("Evaluation timestamp must be timezone-aware.")
    return value.astimezone(UTC)


def _time(value: str) -> datetime:
    if type(value) is not str:
        raise _PretradeError("Evidence timestamp must be text.")
    return _instant(datetime.fromisoformat(value))


def _decimal(value: Decimal, *, positive: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0 or (positive and not value):
        raise _PretradeError("Money must be a finite nonnegative Decimal.")
    _, digits, exponent = value.as_tuple()
    if len(digits) > 24 or not isinstance(exponent, int) or abs(exponent) > 12:
        raise _PretradeError("Decimal representation exceeds bounded research precision.")
    return value


def _document(value: object) -> object:
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, datetime):
        return _instant(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _document(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_document(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _document(v) for k, v in value.items()}
    return value


def _canonical(value: object) -> str:
    return json.dumps(_document(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _rebuild(value: object, expected: type[_T]) -> _T:
    """Re-enter an exact dataclass constructor; never trust frozen-object validity."""
    if type(value) is not expected or not is_dataclass(value) or isinstance(value, type):
        raise _PretradeError(f"Expected an exact {expected.__name__} contract.")
    return expected(**{f.name: getattr(value, f.name) for f in fields(value)})


def _result_money(value: Decimal) -> None:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise _PretradeError("Result money must be an exact finite nonnegative Decimal.")
    _, digits, exponent = value.as_tuple()
    if len(digits) > 100 or not isinstance(exponent, int) or abs(exponent) > 100:
        raise _PretradeError("Result Decimal exceeds evaluation precision.")


def _quantity(value: int, maximum: int = MAX_ORDER_QUANTITY) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _PretradeError("Result quantity/count must be an exact bounded nonnegative integer.")


def _fingerprint(value: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise _PretradeError("Expected a canonical SHA-256 fingerprint.")


def _equal_budgets(cash: Decimal) -> tuple[SleeveCashAllocation, ...]:
    """Adapt canonical forward identities to the validated M2 cash-only contract.

    No daily StrategySpec, historical qualification or execution evidence is forged.
    CapitalAllocation independently enforces M2's exact frozen-order remainder rule.
    """
    _decimal(cash, positive=True)
    base, remainder = divmod(_paise_value(cash, "available cash"), len(_UNIVERSE))
    sleeves = tuple(
        SleeveCashAllocation(
            "NSE", instrument.split(":")[1], _decimal_from_paise(base + (i < remainder))
        )
        for i, instrument in enumerate(_UNIVERSE)
    )
    return CapitalAllocation(cash, sleeves).sleeves


def _policy_id(risk: RiskLimits, costs: CostAssumptions) -> str:
    return _hash(("phase8-risk-and-costs", risk, costs, PortfolioPolicy()))


@dataclass(frozen=True, slots=True)
class _EvidenceReference:
    """Locator only; callers cannot supply qualification, price or eligibility."""

    runtime_id: str
    instrument_id: int
    quote_event_id: str
    candle_timestamp: str

    def __post_init__(self) -> None:
        _text(self.runtime_id)
        _text(self.quote_event_id)
        if type(self.instrument_id) is not int or self.instrument_id < 1:
            raise _PretradeError("Evidence instrument ID must be a positive integer.")
        _time(self.candle_timestamp)


@dataclass(frozen=True, slots=True)
class _InstrumentPretradeIntent:
    """Validated non-executable action; None BUY quantity requests affordable sizing."""

    instrument: str
    action: _Action
    requested_quantity: int | None
    evidence: _EvidenceReference | None

    def __post_init__(self) -> None:
        if (
            type(self.instrument) is not str
            or self.instrument not in _UNIVERSE
            or type(self.action) is not str
            or self.action not in ("BUY", "SELL", "HOLD")
        ):
            raise _PretradeError("Unknown instrument or action.")
        q = self.requested_quantity
        if q is not None and (type(q) is not int or not 0 <= q <= MAX_ORDER_QUANTITY):
            raise _PretradeError("Quantity must be a bounded whole integer, never bool.")
        if self.action == "HOLD" and q != 0:
            raise _PretradeError("HOLD quantity must be zero.")
        if self.action == "SELL" and (q is None or q < 1):
            raise _PretradeError("SELL needs a positive requested quantity.")
        if self.action == "BUY" and q == 0:
            raise _PretradeError("BUY quantity must be positive or None for sizing.")
        if self.evidence is not None and type(self.evidence) is not _EvidenceReference:
            raise _PretradeError("Evidence must be a locator, not caller eligibility.")
        if self.evidence is not None:
            object.__setattr__(self, "evidence", _rebuild(self.evidence, _EvidenceReference))


@dataclass(frozen=True, slots=True)
class _PortfolioPretradeRequest:
    evaluation_id: str
    evaluated_at: datetime
    account_id: int
    runtime_id: str
    universe: tuple[str, ...]
    available_cash: Decimal
    sleeve_budgets: tuple[SleeveCashAllocation, ...]
    intents: tuple[_InstrumentPretradeIntent, ...]
    risk_limits: RiskLimits
    costs: CostAssumptions
    risk_policy_id: str

    def __post_init__(self) -> None:
        for identifier in (self.evaluation_id, self.runtime_id, self.risk_policy_id):
            _text(identifier)
        object.__setattr__(self, "evaluated_at", _instant(self.evaluated_at))
        if type(self.account_id) is not int or self.account_id < 1:
            raise _PretradeError("Account ID must be a positive integer.")
        if type(self.universe) is not tuple or any(type(i) is not str for i in self.universe):
            raise _PretradeError("Universe identities must be exact strings.")
        if tuple(sorted(self.universe)) != _UNIVERSE:
            raise _PretradeError("Exactly the fixed three-stock universe is required.")
        if type(self.intents) is not tuple or any(
            type(i) is not _InstrumentPretradeIntent for i in self.intents
        ):
            raise _PretradeError("Intents must be an immutable typed tuple.")
        intents = tuple(_rebuild(i, _InstrumentPretradeIntent) for i in self.intents)
        object.__setattr__(self, "intents", intents)
        if tuple(sorted(i.instrument for i in self.intents)) != _UNIVERSE:
            raise _PretradeError(
                "Exactly one intent per instrument is required; duplicates prohibited."
            )
        if type(self.sleeve_budgets) is not tuple or any(
            type(s) is not SleeveCashAllocation for s in self.sleeve_budgets
        ):
            raise _PretradeError("Budgets must be immutable M2 sleeve allocations.")
        budgets = tuple(_rebuild(s, SleeveCashAllocation) for s in self.sleeve_budgets)
        for sleeve in budgets:
            _decimal(sleeve.cash, positive=True)
        if type(self.risk_limits) is not RiskLimits or type(self.costs) is not CostAssumptions:
            raise _PretradeError("Existing Phase 8 RiskLimits and costs are required.")
        object.__setattr__(self, "risk_limits", _rebuild(self.risk_limits, RiskLimits))
        object.__setattr__(self, "costs", _rebuild(self.costs, CostAssumptions))
        if any(
            type(getattr(self.risk_limits, k)) is not int
            for k in ("max_position_quantity", "max_market_data_age_seconds")
        ):
            raise _PretradeError("Risk quantity and data-age limits must be exact integers.")
        for value in (*asdict(self.risk_limits).values(), *asdict(self.costs).values()):
            if isinstance(value, Decimal):
                _decimal(value)
        if self.risk_policy_id != _policy_id(self.risk_limits, self.costs):
            raise _PretradeError("Risk policy fingerprint mismatch.")
        expected = _equal_budgets(self.available_cash)
        if tuple(sorted(budgets, key=lambda s: s.trading_symbol)) != expected:
            raise _PretradeError("Budgets must match M2 canonical fixed-cash allocation.")
        object.__setattr__(self, "universe", _UNIVERSE)
        object.__setattr__(self, "intents", tuple(sorted(self.intents, key=lambda i: i.instrument)))
        object.__setattr__(self, "sleeve_budgets", expected)


@dataclass(frozen=True, slots=True)
class _InstrumentAllocationDecision:
    instrument: str
    requested_action: _Action
    requested_quantity: int | None
    approved_hypothetical_quantity: int
    sleeve_budget: Decimal
    estimated_price: Decimal | None
    estimated_notional: Decimal
    estimated_transaction_costs: Decimal
    reserved_hypothetical_cash: Decimal
    remaining_hypothetical_cash: Decimal
    risk_decision: _Status
    reason_codes: tuple[str, ...]
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _InstrumentPretradeIntent(
            self.instrument, self.requested_action, self.requested_quantity, None
        )
        _quantity(self.approved_hypothetical_quantity)
        if type(self.risk_decision) is not str or self.risk_decision not in (
            "APPROVED_HYPOTHETICAL",
            "BLOCKED",
            "HOLD_NO_ACTION",
            "UNAVAILABLE_EVIDENCE",
        ):
            raise _PretradeError("Unsupported result risk decision.")
        if type(self.reason_codes) is not tuple or not self.reason_codes:
            raise _PretradeError("Reason codes must be a nonempty immutable tuple.")
        for reason in self.reason_codes:
            _text(reason)
            if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in reason):
                raise _PretradeError("Reason codes must be canonical identifiers.")
        _fingerprint(self.evidence_fingerprint)
        for money in (
            self.sleeve_budget,
            self.estimated_notional,
            self.estimated_transaction_costs,
            self.reserved_hypothetical_cash,
            self.remaining_hypothetical_cash,
        ):
            _result_money(money)
        _paise_value(self.sleeve_budget, "result sleeve budget")
        if self.estimated_price is not None:
            _result_money(self.estimated_price)
            if self.estimated_price == 0:
                raise _PretradeError("Estimated price must be positive when available.")
        if self.remaining_hypothetical_cash + self.reserved_hypothetical_cash != self.sleeve_budget:
            raise _PretradeError("Result sleeve cash is not conserved.")
        if self.risk_decision == "APPROVED_HYPOTHETICAL":
            q = self.approved_hypothetical_quantity
            if (
                self.requested_action != "BUY"
                or q < 1
                or self.estimated_price is None
                or self.estimated_notional != self.estimated_price * q
                or self.reserved_hypothetical_cash
                != self.estimated_notional + self.estimated_transaction_costs
                or (self.requested_quantity is not None and self.requested_quantity != q)
            ):
                raise _PretradeError("Hypothetical approval economics or intent differ.")
        elif self.approved_hypothetical_quantity != 0 or self.reserved_hypothetical_cash != 0:
            raise _PretradeError("Non-approved decisions cannot approve quantity or reserve cash.")
        if self.risk_decision == "HOLD_NO_ACTION" and (
            self.requested_action != "HOLD"
            or self.estimated_notional != 0
            or self.estimated_transaction_costs != 0
        ):
            raise _PretradeError("HOLD must have zero economics and a HOLD intent.")
        if self.requested_action == "HOLD" and (
            self.estimated_notional != 0 or self.estimated_transaction_costs != 0
        ):
            raise _PretradeError("A HOLD intent cannot carry transaction estimates.")


@dataclass(frozen=True, slots=True)
class _PortfolioPretradeResult:
    evaluation_id: str
    evaluated_at: datetime
    account_id: int
    request_fingerprint: str
    decisions: tuple[_InstrumentAllocationDecision, ...]
    total_sleeve_budgets: Decimal
    total_buy_notional: Decimal
    total_buy_costs: Decimal
    total_reserved_cash: Decimal
    remaining_unreserved_cash: Decimal
    total_hypothetical_exposure: Decimal
    accepted_count: int
    blocked_count: int
    hold_count: int

    def __post_init__(self) -> None:
        _text(self.evaluation_id)
        _instant(self.evaluated_at)
        _quantity(self.account_id)
        if self.account_id == 0:
            raise _PretradeError("Result account ID must be positive.")
        _fingerprint(self.request_fingerprint)
        if type(self.decisions) is not tuple:
            raise _PretradeError("Result decisions must be an immutable tuple.")
        decisions = tuple(_rebuild(d, _InstrumentAllocationDecision) for d in self.decisions)
        if tuple(d.instrument for d in decisions) != _UNIVERSE:
            raise _PretradeError("Result universe must be canonical and complete.")
        object.__setattr__(self, "decisions", decisions)
        for money in (
            self.total_sleeve_budgets,
            self.total_buy_notional,
            self.total_buy_costs,
            self.total_reserved_cash,
            self.remaining_unreserved_cash,
            self.total_hypothetical_exposure,
        ):
            _result_money(money)
        for count in (self.accepted_count, self.blocked_count, self.hold_count):
            _quantity(count, len(_UNIVERSE))
        approved = tuple(d for d in decisions if d.risk_decision == "APPROVED_HYPOTHETICAL")
        if (
            self.total_sleeve_budgets != sum((d.sleeve_budget for d in decisions), _ZERO)
            or self.total_buy_notional != sum((d.estimated_notional for d in approved), _ZERO)
            or self.total_buy_costs != sum((d.estimated_transaction_costs for d in approved), _ZERO)
            or self.total_reserved_cash
            != sum((d.reserved_hypothetical_cash for d in decisions), _ZERO)
            or self.total_reserved_cash != self.total_buy_notional + self.total_buy_costs
            or self.remaining_unreserved_cash + self.total_reserved_cash
            != self.total_sleeve_budgets
            or self.total_hypothetical_exposure != self.total_buy_notional
            or self.accepted_count != len(approved)
            or self.blocked_count
            != sum(d.risk_decision in ("BLOCKED", "UNAVAILABLE_EVIDENCE") for d in decisions)
            or self.hold_count != sum(d.risk_decision == "HOLD_NO_ACTION" for d in decisions)
        ):
            raise _PretradeError("Result aggregates do not reconcile with instrument decisions.")

    def canonical_json(self) -> str:
        """Detached serialization, with no executable proposal or capability fields."""
        return _canonical(self)

    @property
    def fingerprint(self) -> str:
        return _hash(self)


@dataclass(frozen=True, slots=True)
class _MarketObservation:
    price: Decimal
    tick: Decimal
    fingerprint: str


class _PortfolioPretradeCoordinator:
    """Evaluate one read-only SQLite snapshot; repeated calls never reserve real cash."""

    def __init__(self, database_path: Path) -> None:
        self._database = Path(database_path)

    def evaluate(self, request: _PortfolioPretradeRequest) -> _PortfolioPretradeResult:
        if type(request) is not _PortfolioPretradeRequest:
            raise _PretradeError("A typed immutable pre-trade request is required.")
        # Reconstruct to validate even a request changed with object.__setattr__.
        request = _rebuild(request, _PortfolioPretradeRequest)
        with localcontext() as context:
            context.prec = 100
            with closing(get_readonly_connection(self._database)) as connection:
                connection.execute("BEGIN")
                return self._validate_result(self._evaluate_snapshot(connection, request), request)

    @staticmethod
    def _validate_result(
        result: _PortfolioPretradeResult, request: _PortfolioPretradeRequest
    ) -> _PortfolioPretradeResult:
        """Validate/detach the entire result, including controlled malformed producers."""
        checked = _rebuild(result, _PortfolioPretradeResult)
        if (
            checked.evaluation_id != request.evaluation_id
            or checked.evaluated_at != request.evaluated_at
            or checked.account_id != request.account_id
            or checked.request_fingerprint != _hash(request)
            or checked.total_sleeve_budgets != request.available_cash
        ):
            raise _PretradeError("Result identity/fingerprint differs from the validated request.")
        for d, intent, sleeve in zip(
            checked.decisions, request.intents, request.sleeve_budgets, strict=True
        ):
            if (
                d.instrument != intent.instrument
                or d.requested_action != intent.action
                or d.requested_quantity != intent.requested_quantity
                or d.sleeve_budget != sleeve.cash
            ):
                raise _PretradeError("Result intent differs from the validated request.")
            if d.risk_decision == "APPROVED_HYPOTHETICAL" and (
                d.approved_hypothetical_quantity > request.risk_limits.max_position_quantity
                or d.estimated_notional > request.risk_limits.max_order_value
                or d.estimated_notional > request.risk_limits.max_instrument_exposure
                or d.estimated_transaction_costs
                != _fee_total(
                    _fees(
                        cast(Decimal, d.estimated_price),
                        d.approved_hypothetical_quantity,
                        "BUY",
                        request.costs,
                    )
                )
            ):
                raise _PretradeError("Result violates persisted risk/cost assumptions.")
        if checked.total_hypothetical_exposure > request.risk_limits.max_portfolio_exposure:
            raise _PretradeError("Result exceeds portfolio exposure.")
        canonical = checked.canonical_json()
        if (
            result.canonical_json() != canonical
            or result.fingerprint != checked.fingerprint
            or checked.fingerprint != hashlib.sha256(canonical.encode()).hexdigest()
        ):
            raise _PretradeError("Result canonical fingerprint is inconsistent.")
        return checked

    def _evaluate_snapshot(
        self, conn: sqlite3.Connection, r: _PortfolioPretradeRequest
    ) -> _PortfolioPretradeResult:
        decisions: list[_InstrumentAllocationDecision] = []
        reserved = exposure = costs_total = daily_loss = _ZERO
        account_reason = None
        account_fingerprint = ""
        service = None
        try:
            service, account_fingerprint = self._load_context(conn, r)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            sqlite3.Error,
            InvalidOperation,
        ) as exc:
            account_reason = (
                str(exc) if isinstance(exc, _Unavailable) else "UNAVAILABLE_ACCOUNT_STATE"
            )
        for intent, sleeve in zip(r.intents, r.sleeve_budgets, strict=True):
            budget = sleeve.cash
            observation = None
            reason = account_reason
            status: _Status = (
                "BLOCKED" if reason == "ACCOUNT_INCEPTION_INTEGRITY" else "UNAVAILABLE_EVIDENCE"
            )
            q = 0
            price = None
            notional = fees = cash = _ZERO
            if reason is None and service is not None:
                try:
                    observation = self._load_observation(conn, r, intent, service)
                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                    sqlite3.Error,
                    InvalidOperation,
                ) as exc:
                    reason = (
                        str(exc) if isinstance(exc, _Unavailable) else "INVALID_MARKET_EVIDENCE"
                    )
            if reason is None and observation is not None:
                if intent.action == "HOLD":
                    status, reason = "HOLD_NO_ACTION", "HOLD_ZERO_RESERVATION"
                    price = observation.price
                elif intent.action == "SELL":
                    # Context authenticates a flat, unused account; its trusted holding is zero.
                    status, reason = "BLOCKED", "SELL_EXCEEDS_TRUSTED_HOLDING"
                    price = observation.price
                else:
                    price, _, _, _ = _fill_price(
                        observation.price, "BUY", observation.tick, r.costs
                    )
                    affordable = self._affordable(price, budget, r.costs)
                    size_reason = None
                    if intent.requested_quantity is None:
                        q, size_reason = self._auto_quantity(r, price, affordable, exposure)
                    else:
                        q = intent.requested_quantity
                    notional = price * q
                    fees = _fee_total(_fees(price, q, "BUY", r.costs)) if q else _ZERO
                    reason = size_reason or self._buy_rejection(
                        r,
                        q,
                        notional,
                        fees,
                        budget,
                        exposure,
                        daily_loss + (price - observation.price) * q,
                    )
                    status = "BLOCKED" if reason else "APPROVED_HYPOTHETICAL"
                    if reason:
                        q = 0
                    else:
                        reason = "BOUNDED_SYNTHETIC_PRETRADE_ONLY"
                        cash = notional + fees
                        reserved += cash
                        exposure += notional
                        costs_total += fees
                        daily_loss += fees + (price - observation.price) * q
            fingerprint = _hash(
                (
                    intent.instrument,
                    intent.evidence,
                    account_fingerprint,
                    observation.fingerprint if observation else None,
                    reason,
                )
            )
            decisions.append(
                _InstrumentAllocationDecision(
                    intent.instrument,
                    intent.action,
                    intent.requested_quantity,
                    q,
                    budget,
                    price,
                    notional,
                    fees,
                    cash,
                    budget - cash,
                    status,
                    (cast(str, reason),),
                    fingerprint,
                )
            )
        if reserved > r.available_cash or reserved < 0:
            raise _PretradeError("Aggregate cash conservation failed.")
        if service is not None:
            # Detect administrator calendar changes during the SQLite read snapshot.
            try:
                load_approved_calendar_registry(expected_registry=service.calendars)
            except CalendarConfigError:
                decisions = [
                    replace(
                        d,
                        approved_hypothetical_quantity=0,
                        estimated_notional=_ZERO,
                        estimated_transaction_costs=_ZERO,
                        reserved_hypothetical_cash=_ZERO,
                        remaining_hypothetical_cash=d.sleeve_budget,
                        risk_decision="UNAVAILABLE_EVIDENCE",
                        reason_codes=("CALENDAR_CHANGED_DURING_EVALUATION",),
                        evidence_fingerprint=_hash(
                            (d.evidence_fingerprint, "CALENDAR_CHANGED_DURING_EVALUATION")
                        ),
                    )
                    for d in decisions
                ]
                reserved = exposure = costs_total = _ZERO
        return _PortfolioPretradeResult(
            r.evaluation_id,
            r.evaluated_at,
            r.account_id,
            _hash(r),
            tuple(decisions),
            sum((s.cash for s in r.sleeve_budgets), _ZERO),
            exposure,
            costs_total,
            reserved,
            r.available_cash - reserved,
            exposure,
            sum(d.risk_decision == "APPROVED_HYPOTHETICAL" for d in decisions),
            sum(d.risk_decision in ("BLOCKED", "UNAVAILABLE_EVIDENCE") for d in decisions),
            sum(d.risk_decision == "HOLD_NO_ACTION" for d in decisions),
        )

    @staticmethod
    def _auto_quantity(
        r: _PortfolioPretradeRequest, price: Decimal, affordable: int, exposure: Decimal
    ) -> tuple[int, str | None]:
        # Zero-size ties follow Phase 8's order -> position -> cash -> instrument -> portfolio order.
        caps = (
            (int(r.risk_limits.max_order_value // price), "MAX_ORDER_VALUE"),
            (r.risk_limits.max_position_quantity, "MAX_POSITION_QUANTITY"),
            (affordable, "INSUFFICIENT_SLEEVE_CASH"),
            (int(r.risk_limits.max_instrument_exposure // price), "MAX_INSTRUMENT_EXPOSURE"),
            (
                max(0, int((r.risk_limits.max_portfolio_exposure - exposure) // price)),
                "MAX_PORTFOLIO_EXPOSURE",
            ),
        )
        q = min(cap for cap, _ in caps)
        return q, next((reason for cap, reason in caps if cap == 0), None)

    @staticmethod
    def _affordable(price: Decimal, cash: Decimal, costs: CostAssumptions) -> int:
        low, high = 0, min(int(cash // price), MAX_ORDER_QUANTITY)
        while low < high:
            mid = (low + high + 1) // 2
            if price * mid + _fee_total(_fees(price, mid, "BUY", costs)) <= cash:
                low = mid
            else:
                high = mid - 1
        return low

    @staticmethod
    def _buy_rejection(
        r: _PortfolioPretradeRequest,
        q: int,
        notional: Decimal,
        fees: Decimal,
        budget: Decimal,
        exposure: Decimal,
        prior_fees: Decimal,
    ) -> str | None:
        risk = r.risk_limits
        if q < 1 or notional + fees > budget:
            return "INSUFFICIENT_SLEEVE_CASH"
        if notional > risk.max_order_value:
            return "MAX_ORDER_VALUE"
        if q > risk.max_position_quantity:
            return "MAX_POSITION_QUANTITY"
        if notional > risk.max_instrument_exposure:
            return "MAX_INSTRUMENT_EXPOSURE"
        if exposure + notional > risk.max_portfolio_exposure:
            return "MAX_PORTFOLIO_EXPOSURE"
        if prior_fees + fees >= risk.max_daily_loss:
            return "MAX_DAILY_LOSS"
        return None

    def _load_context(
        self, conn: sqlite3.Connection, r: _PortfolioPretradeRequest
    ) -> tuple[ForwardRuntimeService, str]:
        if conn.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] != 18:
            raise _Unavailable("SCHEMA_18_REQUIRED")
        row = conn.execute(
            "SELECT * FROM forward_runtimes WHERE runtime_id=?", (r.runtime_id,)
        ).fetchone()
        account = conn.execute(
            "SELECT * FROM paper_accounts WHERE id=?", (r.account_id,)
        ).fetchone()
        if row is None or account is None or row["account_id"] != r.account_id:
            raise _Unavailable("RUNTIME_ACCOUNT_MISMATCH")
        for name in ("config", "state"):
            if hashlib.sha256(row[f"{name}_json"].encode()).hexdigest() != row[f"{name}_sha256"]:
                raise _Unavailable("RUNTIME_CHECKSUM_MISMATCH")
        config, state = json.loads(row["config_json"]), json.loads(row["state_json"])
        instruments = conn.execute(
            "SELECT * FROM instruments WHERE exchange='NSE' AND trading_symbol IN ('INFY','RELIANCE','TCS') ORDER BY id"
        ).fetchall()
        ids = tuple(i["id"] for i in instruments)
        if (
            len(ids) != 3
            or tuple(config["instrument_ids"]) != ids
            or any(
                not i["is_active"] or i["instrument_type"] != "EQUITY" or i["lot_size"] != 1
                for i in instruments
            )
            or config.get("scheduling_only") is not True
            or config.get("observation_only", False)
            or config.get("provider") != "DETERMINISTIC_SYNTHETIC"
            or config.get("mode") != "RESEARCH_ONLY"
            or config["max_candidates"] < len(ids)
        ):
            raise _Unavailable("UNSUPPORTED_SYNTHETIC_RUNTIME")
        if row["status"] not in ("RUNNING", "DEGRADED") or account["halted"]:
            raise _Unavailable("ACCOUNT_OR_RUNTIME_HALTED")
        risk_document = json.loads(account["risk_limits_json"])
        cost_document = json.loads(account["cost_assumptions_json"])
        if risk_document != r.risk_limits.to_dict() or cost_document != r.costs.to_dict():
            raise _Unavailable("PERSISTED_RISK_COST_MISMATCH")
        if _time(account["created_at"]) > r.evaluated_at or _reconcile(
            conn, account, return_errors=True
        ):
            raise _Unavailable("ACCOUNT_RECONCILIATION_FAILED")
        if any(
            conn.execute(
                f"SELECT 1 FROM {table} WHERE account_id=? LIMIT 1", (r.account_id,)
            ).fetchone()
            for table in (
                "paper_orders",
                "paper_positions",
                "paper_ledger_entries",
                "paper_realized_pnl",
                "paper_daily_state",
            )
        ):
            raise _Unavailable("FORWARD_SLEEVE_STATE_UNAVAILABLE")
        if _decimal(Decimal(account["initial_cash"]), positive=True) != r.available_cash:
            raise _Unavailable("AVAILABLE_CASH_MISMATCH")
        try:
            inception, equity = verify_account_inception(conn, r.account_id, r.runtime_id)
            if any(_time(d["timestamp"]) > r.evaluated_at for d in (*inception, *equity)):
                raise EquityEvidenceError("Future equity lifecycle evidence")
        except (EquityEvidenceError, KeyError, TypeError, ValueError) as exc:
            raise _Unavailable("ACCOUNT_INCEPTION_INTEGRITY") from exc
        calendars = load_approved_calendar_registry()
        service = ForwardRuntimeService(
            self._database,
            r.runtime_id,
            r.account_id,
            ids,
            calendars,
            max_candidates=config["max_candidates"],
            max_quote_delay_seconds=config["max_quote_delay_seconds"],
            degraded_after_seconds=config["degraded_after_seconds"],
            stale_after_seconds=config["stale_after_seconds"],
            max_execution_price_deviation=None,
            state=state,
        )
        # No open()/recover()/readiness methods: they can reconcile and write.
        return service, _hash(
            (
                dict(account),
                config,
                calendars.configuration_sha256,
                tuple(d["sha256"] for d in (*inception, *equity)),
                # Bind financial registry identity, not admin wall times or legacy REAL ticks.
                [
                    tuple(
                        i[k]
                        for k in (
                            "id",
                            "exchange",
                            "trading_symbol",
                            "instrument_type",
                            "segment",
                            "lot_size",
                            "tick_size_decimal",
                            "is_active",
                        )
                    )
                    for i in instruments
                ],
            )
        )

    @staticmethod
    def _load_observation(
        conn: sqlite3.Connection,
        r: _PortfolioPretradeRequest,
        intent: _InstrumentPretradeIntent,
        service: ForwardRuntimeService,
    ) -> _MarketObservation:
        ref = intent.evidence
        if ref is None:
            raise _Unavailable("MISSING_EVIDENCE")
        instrument = conn.execute(
            "SELECT * FROM instruments WHERE id=?", (ref.instrument_id,)
        ).fetchone()
        if (
            instrument is None
            or f"{instrument['exchange']}:{instrument['trading_symbol']}" != intent.instrument
            or ref.runtime_id != r.runtime_id
        ):
            raise _Unavailable("INSTRUMENT_SESSION_MISMATCH")
        quote = conn.execute(
            "SELECT * FROM forward_quotes WHERE runtime_id=? AND instrument_id=? AND accepted=1 ORDER BY sequence DESC LIMIT 1",
            (r.runtime_id, ref.instrument_id),
        ).fetchone()
        candle = conn.execute(
            "SELECT * FROM forward_runtime_candles WHERE runtime_id=? AND instrument_id=? AND interval='1m' ORDER BY candle_timestamp DESC LIMIT 1",
            (r.runtime_id, ref.instrument_id),
        ).fetchone()
        if quote is None or candle is None:
            raise _Unavailable("MISSING_QUOTE_OR_COMPLETED_CANDLE")
        if (
            quote["event_id"] != ref.quote_event_id
            or candle["candle_timestamp"] != ref.candle_timestamp
        ):
            raise _Unavailable("EVIDENCE_SUPERSEDED")
        cursor = service._state["cursor"][str(ref.instrument_id)]
        if cursor != {"sequence": quote["sequence"], "timestamp": quote["quote_timestamp"]}:
            raise _Unavailable("CURSOR_QUOTE_MISMATCH")
        if Decimal(service._state["last_prices"][str(ref.instrument_id)]) != Decimal(
            quote["price_decimal"]
        ):
            raise _Unavailable("CURSOR_PRICE_MISMATCH")
        if service._state["feed"][str(ref.instrument_id)]["status"] != "HEALTHY":
            raise _Unavailable("FEED_NOT_HEALTHY")
        observed, received = _time(quote["quote_timestamp"]), _time(quote["received_at"])
        if (
            not observed <= received <= r.evaluated_at
            or _time(candle["completed_at"]) > r.evaluated_at
        ):
            raise _Unavailable("FUTURE_EVIDENCE")
        if (r.evaluated_at - observed).total_seconds() > r.risk_limits.max_market_data_age_seconds:
            raise _Unavailable("STALE_EVIDENCE")
        if (received - observed).total_seconds() > service.max_quote_delay_seconds:
            raise _Unavailable("DELAYED_EVIDENCE")
        calendar = service.calendars.resolve(instrument["exchange"], instrument["segment"])
        if (
            calendar is None
            or calendar.confidence != "CONFIRMED"
            or service._session_rejection(instrument, r.evaluated_at)
        ):
            raise _Unavailable("CALENDAR_UNAVAILABLE_OR_UNCONFIRMED")
        imported = conn.execute(
            "SELECT d.* FROM forward_runtime_imports ri JOIN data_imports d ON d.id=ri.import_id WHERE ri.runtime_id=? AND ri.instrument_id=? AND ri.interval='1m'",
            (r.runtime_id, ref.instrument_id),
        ).fetchone()
        if imported is None:
            raise _Unavailable("MISSING_SOURCE_IMPORT")
        metadata = json.loads(imported["metadata_json"])
        expected_hash = hashlib.sha256(
            f"{r.runtime_id}:{ref.instrument_id}:1m".encode()
        ).hexdigest()
        if (
            imported["instrument_id"] != ref.instrument_id
            or imported["interval"] != "1m"
            or imported["import_type"] != "SYNTHETIC_STREAM"
            or imported["status"] != "COMPLETED"
            or imported["source_use_status"] != "VERIFIED_PERMITTED"
            or imported["file_sha256"] != expected_hash
            or metadata.get("runtime_id") != r.runtime_id
            or metadata.get("provider") != "DETERMINISTIC_SYNTHETIC"
            or metadata.get("source_use_rationale") != "Generated in-process synthetic data."
            or _time(imported["completed_at"]) > r.evaluated_at
        ):
            raise _Unavailable("UNSUPPORTED_SOURCE_PROVENANCE")
        declaration = conn.execute(
            "SELECT new_status,declared_by,rationale FROM import_source_use_history WHERE import_id=? ORDER BY id DESC LIMIT 1",
            (imported["id"],),
        ).fetchone()
        if declaration is None or declaration["new_status"] != "VERIFIED_PERMITTED":
            raise _Unavailable("SOURCE_DECLARATION_UNAVAILABLE")
        boundary, unavailable = service._candle_decision_eligibility(conn, dict(candle))
        if unavailable is not None:
            raise _Unavailable(unavailable)
        event_id = service._event_id(ref.instrument_id, "1m", ref.candle_timestamp)
        event = conn.execute(
            "SELECT * FROM forward_scheduler_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if (
            event is None
            or event["runtime_id"] != r.runtime_id
            or event["instrument_id"] != ref.instrument_id
            or event["interval"] != "1m"
            or event["candle_timestamp"] != ref.candle_timestamp
            or _time(event["decision_timestamp"]) != boundary
            or event["status"] != "PENDING"
            or event["proposal_binding_state"] != "UNBOUND"
            or event["proposal_json"] is not None
            or event["proposal_sha256"] is not None
            or event["proposal_id"] != f"synthetic-{event_id[:40]}"
        ):
            raise _Unavailable("PENDING_UNBOUND_SCHEDULER_EVIDENCE_REQUIRED")
        price, tick = (
            _decimal(Decimal(quote["price_decimal"]), positive=True),
            _decimal(Decimal(instrument["tick_size_decimal"]), positive=True),
        )
        if price % tick:
            raise _Unavailable("PRICE_TICK_MISMATCH")
        return _MarketObservation(
            price,
            tick,
            _hash(
                (
                    dict(quote),
                    dict(candle),
                    imported["id"],
                    imported["file_sha256"],
                    metadata,
                    tuple(declaration),
                    event_id,
                    event["status"],
                    event["proposal_binding_state"],
                )
            ),
        )


def _evaluate_synthetic_portfolio(
    database_path: Path, request: _PortfolioPretradeRequest
) -> _PortfolioPretradeResult:
    """Thin M5-A observation/report integration; never opens or advances its runtime."""
    return _PortfolioPretradeCoordinator(database_path).evaluate(request)
