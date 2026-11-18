"""Persistent deterministic paper execution; no broker or order-routing interface."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, DecimalException, InvalidOperation, localcontext
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from aegis import decision_journal
from aegis.backtesting import (
    BacktestError,
    CostAssumptions,
    _decimal_text,
    _fee_total,
    _fees,
    _fill_price,
)
from aegis.database import get_connection
from aegis.market_quality import (
    CalendarConfigError,
    DatasetQualityReport,
    analyze_dataset,
    load_approved_calendar_registry,
)

_CURRENCY = "INR"
_INDIA = ZoneInfo("Asia/Kolkata")


class PaperTradingError(ValueError):
    """Raised for invalid paper-account configuration or unreconciled state."""


class PaperHaltClass(str, Enum):
    """Persisted halt classes that determine which risk-reducing actions remain allowed."""

    NONE = "NONE"
    RISK = "RISK"
    INTEGRITY = "INTEGRITY"
    UNCLASSIFIED = "UNCLASSIFIED"


class PaperRiskHaltReason(str, Enum):
    """Recognized risk/data halt reasons that may allow a valid reducing SELL."""

    STALE_EXECUTION_DATA = "Stale execution data."
    MARKET_QUALITY_FAILURE = "Market quality gate failed."
    SOURCE_PERMISSION_FAILURE = "Source permission gate failed."
    SOURCE_COMPLETION_FUTURE_DATED = "Source completion is future-dated."
    IMPORTED_DATA_QUALITY_FAILURE = "Imported data quality failed."
    STALE_POSITION_MARK = "Stale position mark."
    MAXIMUM_ORDER_VALUE = "Maximum order value breached."
    MAXIMUM_POSITION_QUANTITY = "Maximum position quantity breached."
    MAXIMUM_INSTRUMENT_EXPOSURE = "Maximum instrument exposure breached."
    MAXIMUM_PORTFOLIO_EXPOSURE = "Maximum portfolio exposure breached."
    MAXIMUM_DAILY_LOSS = "Maximum daily loss breached."
    LEGACY_DAILY_LOSS = "Daily loss limit."


def halt_blocks_order(halted: object, halt_class: object, halt_reason: object, side: str) -> bool:
    """Fail closed unless a persisted halt is a recognized risk-only SELL allowance."""
    if halted not in (0, 1, False, True):
        return True
    if side not in {"BUY", "SELL"}:
        return True
    try:
        classification = PaperHaltClass(halt_class)
    except (TypeError, ValueError):
        return True
    if not halted:
        return classification != PaperHaltClass.NONE or halt_reason is not None
    if classification != PaperHaltClass.RISK or side != "SELL":
        return True
    try:
        PaperRiskHaltReason(halt_reason)
    except (TypeError, ValueError):
        return True
    return False


def _set_account_halt(
    connection: sqlite3.Connection,
    account_id: int,
    reason: str,
    classification: PaperHaltClass,
) -> None:
    """Persist an explicit halt class without downgrading an existing integrity halt."""
    current = connection.execute(
        "SELECT halted, halt_class FROM paper_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if current is None:
        raise PaperTradingError("Paper account does not exist.")
    if (
        current["halted"]
        and current["halt_class"]
        in (PaperHaltClass.INTEGRITY.value, PaperHaltClass.UNCLASSIFIED.value)
        and classification == PaperHaltClass.RISK
    ):
        return
    if classification == PaperHaltClass.RISK:
        try:
            PaperRiskHaltReason(reason)
        except (TypeError, ValueError):
            classification = PaperHaltClass.UNCLASSIFIED
    connection.execute(
        "UPDATE paper_accounts SET halted = 1, halt_reason = ?, halt_class = ? WHERE id = ?",
        (reason, classification.value, account_id),
    )


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Explicit per-account hard limits; no default limits are supplied."""

    max_order_value: Decimal
    max_position_quantity: int
    max_instrument_exposure: Decimal
    max_portfolio_exposure: Decimal
    max_daily_loss: Decimal
    max_market_data_age_seconds: int

    def __post_init__(self) -> None:
        for name in (
            "max_order_value",
            "max_instrument_exposure",
            "max_portfolio_exposure",
            "max_daily_loss",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise PaperTradingError(f"{name} must be a finite positive Decimal.")
        if (
            isinstance(self.max_position_quantity, bool)
            or not isinstance(self.max_position_quantity, int)
            or self.max_position_quantity < 1
        ):
            raise PaperTradingError("max_position_quantity must be a positive integer.")
        if (
            isinstance(self.max_market_data_age_seconds, bool)
            or not isinstance(self.max_market_data_age_seconds, int)
            or self.max_market_data_age_seconds < 0
        ):
            raise PaperTradingError("max_market_data_age_seconds must be a non-negative integer.")

    def to_dict(self) -> dict[str, str | int]:
        return {
            key: _decimal_text(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


@dataclass(frozen=True, slots=True)
class PaperOrderRequest:
    """A single long-only market order with separated signal and submission times."""

    instrument_id: int
    side: Literal["BUY", "SELL"]
    quantity: int
    signal_timestamp: str
    submitted_timestamp: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.instrument_id, bool)
            or not isinstance(self.instrument_id, int)
            or self.instrument_id < 1
        ):
            raise PaperTradingError("instrument_id must be a positive integer.")
        if self.side not in ("BUY", "SELL"):
            raise PaperTradingError("Only BUY and SELL orders are supported.")
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity < 1
        ):
            raise PaperTradingError("quantity must be a positive integer.")
        signal = _timestamp(self.signal_timestamp, "signal_timestamp")
        submitted = _timestamp(self.submitted_timestamp, "submitted_timestamp")
        if submitted < signal:
            raise PaperTradingError("submitted_timestamp cannot precede signal_timestamp.")

    def canonical(self) -> dict[str, str | int]:
        return {
            "instrument_id": self.instrument_id,
            "side": self.side,
            "quantity": self.quantity,
            "signal_timestamp": _timestamp_text(self.signal_timestamp),
            "submitted_timestamp": _timestamp_text(self.submitted_timestamp),
        }


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    """Explicit executable reference price and matching verified data-quality evidence."""

    instrument_id: int
    interval: str
    timestamp: str
    as_of: str
    reference_price: Decimal
    price_field: Literal["execution_quote"]
    available_quantity: int
    import_id: int
    quality: DatasetQualityReport
    quote_event_id: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.instrument_id, bool)
            or not isinstance(self.instrument_id, int)
            or self.instrument_id < 1
        ):
            raise PaperTradingError("snapshot instrument_id must be positive.")
        if (
            isinstance(self.import_id, bool)
            or not isinstance(self.import_id, int)
            or self.import_id < 1
        ):
            raise PaperTradingError("snapshot import_id must be positive.")
        if not self.interval.strip():
            raise PaperTradingError("snapshot interval must be non-empty.")
        observed = _timestamp(self.timestamp, "snapshot timestamp")
        as_of = _timestamp(self.as_of, "snapshot as_of")
        if as_of < observed:
            raise PaperTradingError("snapshot as_of cannot precede its timestamp.")
        if (
            not isinstance(self.reference_price, Decimal)
            or not self.reference_price.is_finite()
            or self.reference_price <= 0
        ):
            raise PaperTradingError("reference_price must be a finite positive Decimal.")
        if self.price_field != "execution_quote":
            raise PaperTradingError(
                "Only an explicit execution-time quote can be a fill reference."
            )
        if (
            isinstance(self.available_quantity, bool)
            or not isinstance(self.available_quantity, int)
            or self.available_quantity < 0
        ):
            raise PaperTradingError("available_quantity must be a non-negative integer.")
        if not isinstance(self.quality, DatasetQualityReport):
            raise PaperTradingError("A DatasetQualityReport is required for execution.")
        if self.quote_event_id is not None and (
            not isinstance(self.quote_event_id, str) or not self.quote_event_id.strip()
        ):
            raise PaperTradingError("quote_event_id must be non-empty text when supplied.")

    @property
    def snapshot_sha256(self) -> str:
        """Return a stable fingerprint of the independently observed execution quote."""

        def serialize_quality(value: Any) -> Any:
            if isinstance(value, datetime):
                return _timestamp_text(value.isoformat())
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, Decimal):
                return _decimal_text(value)
            if isinstance(value, dict):
                return {str(key): serialize_quality(item) for key, item in value.items()}
            if isinstance(value, tuple):
                return [serialize_quality(item) for item in value]
            if isinstance(value, list):
                return [serialize_quality(item) for item in value]
            if value is None or isinstance(value, (bool, int, str)):
                return value
            raise PaperTradingError("Execution snapshot quality contains an unsupported value.")

        payload = _canonical_json(
            {
                "snapshot_schema_version": "aegis.execution-snapshot/v2",
                "instrument_id": self.instrument_id,
                "interval": self.interval,
                "timestamp": _timestamp_text(self.timestamp),
                "as_of": _timestamp_text(self.as_of),
                "reference_price": _decimal_text(self.reference_price),
                "price_field": self.price_field,
                "available_quantity": self.available_quantity,
                "import_id": self.import_id,
                "quote_event_id": self.quote_event_id,
                "quality": serialize_quality(asdict(self.quality)),
            }
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def snapshot_id(self) -> str:
        """Return the deterministic identity used to enforce single-use execution evidence."""
        return hashlib.sha256(f"execution-snapshot:{self.snapshot_sha256}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperOrderResult:
    order_id: str
    status: Literal["FILLED", "REJECTED"]
    reason: str | None
    duplicate: bool


@contextmanager
def _transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
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


def _forward_context_error(
    connection: sqlite3.Connection,
    account_id: int,
    event_id: str,
    idempotency_key: str,
    request: PaperOrderRequest,
    snapshot: ExecutionSnapshot,
    *,
    require_authorization: bool,
) -> str | None:
    event = connection.execute(
        """SELECT e.*, r.account_id AS runtime_account_id, r.status AS runtime_status,
                  r.config_json, r.config_sha256, r.state_json, r.state_sha256,
                  ri.import_id AS runtime_import_id
           FROM forward_scheduler_events e
           JOIN forward_runtimes r ON r.runtime_id = e.runtime_id
           LEFT JOIN forward_runtime_imports ri
             ON ri.runtime_id = e.runtime_id AND ri.instrument_id = e.instrument_id
            AND ri.interval = e.interval
           WHERE e.event_id = ?""",
        (event_id,),
    ).fetchone()
    if (
        event is None
        or event["status"] != "PENDING"
        or event["runtime_account_id"] != account_id
        or event["runtime_status"] not in ("RUNNING", "DEGRADED")
        or event["runtime_import_id"] != snapshot.import_id
        or event["instrument_id"] != request.instrument_id
        or event["interval"] != snapshot.interval
    ):
        return "Forward execution is not bound to a pending event for this account and instrument."
    if not isinstance(event["proposal_json"], str) or not event["proposal_json"]:
        return "Forward event has no canonical proposal payload."
    if (
        hashlib.sha256(event["proposal_json"].encode("utf-8")).hexdigest()
        != event["proposal_sha256"]
    ):
        return "Forward event proposal content does not match its persisted digest."
    try:
        payload = json.loads(event["proposal_json"])
        config = json.loads(event["config_json"])
        state = json.loads(event["state_json"])
    except (TypeError, json.JSONDecodeError):
        return "Forward event, runtime configuration, or runtime state is malformed."
    if (
        not isinstance(payload, dict)
        or not isinstance(config, dict)
        or not isinstance(state, dict)
        or hashlib.sha256(event["config_json"].encode("utf-8")).hexdigest()
        != event["config_sha256"]
        or hashlib.sha256(event["state_json"].encode("utf-8")).hexdigest() != event["state_sha256"]
    ):
        return "Forward runtime configuration or state failed its integrity check."
    instrument_key = str(request.instrument_id)
    feed = state.get("feed")
    feed_record = feed.get(instrument_key) if isinstance(feed, dict) else None
    if not isinstance(feed_record, dict) or feed_record.get("status") != "HEALTHY":
        return "Forward execution requires a healthy, verified instrument feed."
    quote_event_id = snapshot.quote_event_id
    if quote_event_id is None:
        return "Forward execution snapshot has no persisted quote identity."
    quote = connection.execute(
        """SELECT event_id, sequence, quote_timestamp, received_at, price_decimal, quantity
           FROM forward_quotes WHERE runtime_id = ? AND instrument_id = ?
             AND event_id = ? AND accepted = 1""",
        (event["runtime_id"], request.instrument_id, quote_event_id),
    ).fetchone()
    latest = connection.execute(
        """SELECT event_id FROM forward_quotes WHERE runtime_id = ? AND instrument_id = ?
             AND accepted = 1 ORDER BY sequence DESC LIMIT 1""",
        (event["runtime_id"], request.instrument_id),
    ).fetchone()
    if quote is None or latest is None or latest["event_id"] != quote_event_id:
        return (
            "Forward execution quote is missing, rejected, or no longer the latest accepted quote."
        )
    try:
        observed = _timestamp(quote["quote_timestamp"], "forward quote timestamp")
        received = _timestamp(quote["received_at"], "forward quote received_at")
        decision_time = _timestamp(event["decision_timestamp"], "event decision timestamp")
        execution_time = _timestamp(snapshot.timestamp, "snapshot timestamp")
        as_of = _timestamp(snapshot.as_of, "snapshot as_of")
        configured_delay = int(config["max_quote_delay_seconds"])
        degraded_after = int(config["degraded_after_seconds"])
        if (
            configured_delay < 0
            or degraded_after < 0
            or observed != execution_time
            or received != as_of
            or Decimal(quote["price_decimal"]) != snapshot.reference_price
            or int(quote["quantity"]) != snapshot.available_quantity
            or observed < decision_time
            or received > decision_time + timedelta(seconds=configured_delay)
            or (max(decision_time, as_of) - observed).total_seconds() > degraded_after
            or (received - observed).total_seconds() > configured_delay
        ):
            return "Forward execution snapshot does not match the current accepted quote."
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return "Forward quote timing or content is malformed."
    if payload.get("proposal_id") != event["proposal_id"]:
        return "Forward event proposal identity does not match its canonical payload."
    try:
        if payload.get("canonicalization_version") == "aegis.decision-proposal/v1":
            action = payload["action"]
            quantity = payload["quantity"]
            entry_price = Decimal(payload["entry_price"])
            decision_timestamp = _timestamp(
                payload["decision_timestamp"], "proposal decision timestamp"
            )
            submitted_timestamp = _timestamp(payload["submitted_at"], "proposal submitted_at")
            expiry = _timestamp(payload["expires_at"], "proposal expiry")
            if (
                action not in ("BUY", "SELL")
                or isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or action != request.side
                or idempotency_key != event["proposal_id"]
                or quantity != request.quantity
                or payload.get("instrument_id") != request.instrument_id
                or payload.get("timeframe") != event["interval"]
                or decision_timestamp != _timestamp(request.signal_timestamp, "signal timestamp")
                or submitted_timestamp
                != _timestamp(request.submitted_timestamp, "submission timestamp")
                or expiry <= as_of
                or not entry_price.is_finite()
            ):
                return "Forward order request does not match the unexpired canonical proposal."
            authorization_kind = "PROPOSAL"
        elif payload.get("canonicalization_version") == "aegis.forward-exit/v1":
            action = payload.get("action")
            entry_price = Decimal(payload["entry_price"])
            expiry = _timestamp(payload["expires_at"], "exit proposal expiry")
            decision_timestamp = _timestamp(
                payload["decision_timestamp"], "exit decision timestamp"
            )
            submitted_timestamp = _timestamp(payload["submitted_at"], "exit submitted_at")
            if (
                payload.get("runtime_id") != event["runtime_id"]
                or payload.get("event_id") != event_id
                or payload.get("instrument_id") != request.instrument_id
                or action != "SELL"
                or idempotency_key != event_id
                or request.side != "SELL"
                or isinstance(payload.get("quantity"), bool)
                or payload.get("quantity") != request.quantity
                or decision_timestamp != _timestamp(request.signal_timestamp, "signal timestamp")
                or submitted_timestamp
                != _timestamp(request.submitted_timestamp, "submission timestamp")
                or expiry <= as_of
                or not entry_price.is_finite()
            ):
                return "Automatic exit request does not match its persisted trigger payload."
            control = connection.execute(
                """SELECT proposal_id, stop_loss, target_price, active, exit_event_id
                   FROM forward_trade_controls WHERE runtime_id = ? AND instrument_id = ?""",
                (event["runtime_id"], request.instrument_id),
            ).fetchone()
            position = connection.execute(
                """SELECT quantity FROM paper_positions
                   WHERE account_id = ? AND instrument_id = ?""",
                (account_id, request.instrument_id),
            ).fetchone()
            if (
                control is None
                or not control["active"]
                or control["exit_event_id"] != event_id
                or control["proposal_id"] != payload.get("source_proposal_id")
                or position is None
                or int(position["quantity"]) != request.quantity
            ):
                return "Automatic exit is not bound to the exact active position."
            stop = Decimal(control["stop_loss"]) if control["stop_loss"] else None
            target = Decimal(control["target_price"]) if control["target_price"] else None
            trigger = payload.get("trigger")
            if not (
                (trigger == "STOP" and stop is not None and snapshot.reference_price <= stop)
                or (
                    trigger == "TARGET"
                    and target is not None
                    and snapshot.reference_price >= target
                )
            ):
                return "Automatic exit quote does not satisfy its persisted stop/target trigger."
            authorization_kind = "STOP_TARGET_EXIT"
        else:
            return "Forward event uses an unsupported canonical proposal version."
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return "Forward proposal payload contains malformed order fields."
    try:
        deviation = config.get("max_execution_price_deviation")
        if deviation is None:
            return "Forward runtime has no execution-price deviation limit."
        deviation_limit = Decimal(deviation)
        if (
            not deviation_limit.is_finite()
            or deviation_limit < 0
            or abs(snapshot.reference_price - entry_price) > deviation_limit
        ):
            return "Forward execution price exceeds the configured deviation limit."
    except (TypeError, InvalidOperation):
        return "Forward runtime execution-price deviation limit is malformed."
    if require_authorization:
        authorization = connection.execute(
            """SELECT * FROM forward_execution_authorizations
               WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        if (
            authorization is None
            or authorization["status"] != "AUTHORIZED"
            or authorization["runtime_id"] != event["runtime_id"]
            or authorization["account_id"] != account_id
            or authorization["proposal_id"] != event["proposal_id"]
            or authorization["proposal_sha256"] != event["proposal_sha256"]
            or authorization["authorization_kind"] != authorization_kind
            or authorization["instrument_id"] != request.instrument_id
            or authorization["interval"] != snapshot.interval
            or authorization["snapshot_id"] != snapshot.snapshot_id
            or authorization["snapshot_sha256"] != snapshot.snapshot_sha256
            or authorization["quote_event_id"] != quote_event_id
            or authorization["quote_timestamp"] != quote["quote_timestamp"]
            or authorization["received_at"] != quote["received_at"]
            or authorization["import_id"] != snapshot.import_id
        ):
            return "Forward execution has no matching persisted authorization for this snapshot."
    return None


def create_paper_account(
    database_path: Path,
    *,
    name: str,
    initial_cash: Decimal,
    risk_limits: RiskLimits,
    costs: CostAssumptions,
    created_at: str,
) -> int:
    """Create a simulated-only account with explicit capital, risk, and cost assumptions."""
    if not isinstance(name, str) or not name.strip():
        raise PaperTradingError("Paper account name must be non-empty.")
    if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite() or initial_cash <= 0:
        raise PaperTradingError("initial_cash must be a finite positive Decimal.")
    if not isinstance(risk_limits, RiskLimits) or not isinstance(costs, CostAssumptions):
        raise PaperTradingError("Explicit RiskLimits and CostAssumptions are required.")
    timestamp = _timestamp_text(created_at)
    if not _has_paisa_precision(initial_cash):
        raise PaperTradingError("initial_cash must be representable to INR 0.01.")
    with _transaction(database_path) as connection:
        try:
            cursor = connection.execute(
                """INSERT INTO paper_accounts
                   (name, initial_cash, risk_limits_json, cost_assumptions_json, halted,
                    halt_reason, halt_class, created_at)
                   VALUES (?, ?, ?, ?, 0, NULL, 'NONE', ?)""",
                (
                    name.strip(),
                    _decimal_text(initial_cash),
                    _canonical_json(risk_limits.to_dict()),
                    _canonical_json(costs.to_dict()),
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PaperTradingError(
                "Paper account name already exists or schema is unavailable."
            ) from exc
        if cursor.lastrowid is None:
            raise PaperTradingError("SQLite did not return the created account identifier.")
        return int(cursor.lastrowid)


def submit_paper_order(
    database_path: Path,
    account_id: int,
    idempotency_key: str,
    request: PaperOrderRequest,
    snapshot: ExecutionSnapshot,
    *,
    forward_event_id: str | None = None,
) -> PaperOrderResult:
    """Atomically record and execute/reject one all-or-none simulated market order.

    A fill uses only the supplied snapshot, which must match a confirmed, complete,
    fresh quality report and an explicitly permitted completed import. The execution
    timestamp is the supplied quote observation time; the quality report must end
    strictly before that time. No current or future candle OHLC/volume is consulted.
    """
    if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id < 1:
        raise PaperTradingError("account_id must be positive.")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise PaperTradingError("idempotency_key must be non-empty.")
    if forward_event_id is not None and (
        not isinstance(forward_event_id, str) or not forward_event_id.strip()
    ):
        raise PaperTradingError("forward_event_id must be non-empty text when supplied.")
    request_json = _canonical_json(request.canonical())
    request_hash = hashlib.sha256(request_json.encode()).hexdigest()
    order_id = hashlib.sha256(f"{account_id}:{idempotency_key.strip()}".encode()).hexdigest()
    risk_started = time.perf_counter_ns()
    with _transaction(database_path) as connection:
        existing = connection.execute(
            "SELECT order_id, request_sha256, status, reason FROM paper_orders "
            "WHERE account_id = ? AND idempotency_key = ?",
            (account_id, idempotency_key.strip()),
        ).fetchone()
        if existing is not None:
            if existing["request_sha256"] != request_hash:
                raise PaperTradingError("Idempotency key was reused for a different order request.")
            previous_use = connection.execute(
                """SELECT runtime_id, event_id, proposal_id, snapshot_sha256
                   FROM paper_execution_snapshot_uses WHERE order_id = ?""",
                (existing["order_id"],),
            ).fetchone()
            if forward_event_id is not None:
                authorization = connection.execute(
                    """SELECT status, consumed_order_id, snapshot_id, snapshot_sha256
                       FROM forward_execution_authorizations WHERE event_id = ?""",
                    (forward_event_id,),
                ).fetchone()
                if (
                    previous_use is None
                    or previous_use["event_id"] != forward_event_id
                    or previous_use["snapshot_sha256"] != snapshot.snapshot_sha256
                    or authorization is None
                    or authorization["status"] != "CONSUMED"
                    or authorization["consumed_order_id"] != existing["order_id"]
                    or authorization["snapshot_id"] != snapshot.snapshot_id
                    or authorization["snapshot_sha256"] != snapshot.snapshot_sha256
                ):
                    raise PaperTradingError(
                        "Idempotent order replay does not match its consumed forward authorization."
                    )
            elif previous_use is not None and previous_use["runtime_id"] is not None:
                raise PaperTradingError(
                    "Forward-runtime orders cannot be replayed through the generic paper-order path."
                )
            return PaperOrderResult(
                existing["order_id"], existing["status"], existing["reason"], True
            )

        account = connection.execute(
            "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise PaperTradingError("Paper account does not exist.")
        is_runtime_import = connection.execute(
            "SELECT 1 FROM forward_runtime_imports WHERE import_id = ?", (snapshot.import_id,)
        ).fetchone()
        if is_runtime_import is not None and forward_event_id is None:
            raise PaperTradingError(
                "Forward-runtime imports cannot be submitted through the generic paper-order path."
            )
        forward_authorization = None
        if forward_event_id is not None:
            forward_error = _forward_context_error(
                connection,
                account_id,
                forward_event_id,
                idempotency_key.strip(),
                request,
                snapshot,
                require_authorization=True,
            )
            if forward_error is not None:
                raise PaperTradingError(forward_error)
            forward_authorization = connection.execute(
                """SELECT runtime_id, event_id, proposal_id
                   FROM forward_execution_authorizations WHERE event_id = ?""",
                (forward_event_id,),
            ).fetchone()
        try:
            risk = _risk_from_json(account["risk_limits_json"])
            costs = _costs_from_json(account["cost_assumptions_json"])
        except (KeyError, TypeError, ValueError, BacktestError) as exc:
            raise PaperTradingError(
                "Persisted risk or cost settings are malformed; no order was submitted."
            ) from exc
        instrument = connection.execute(
            "SELECT * FROM instruments WHERE id = ?", (request.instrument_id,)
        ).fetchone()
        instrument_tick_size = None
        if instrument is not None and instrument["tick_size_decimal"] is not None:
            try:
                instrument_tick_size = Decimal(instrument["tick_size_decimal"])
            except (InvalidOperation, TypeError):
                instrument_tick_size = None
        reason: str | None = None
        halt_reason: str | None = None
        halt_classification = PaperHaltClass.RISK
        snapshot_accepted = False
        if instrument is None or not instrument["is_active"]:
            reason = "Instrument is unavailable or inactive."
        elif instrument["exchange"] != "NSE" or instrument["instrument_type"] != "EQUITY":
            reason = "Paper execution is limited to registered NSE cash equities."
        elif not isinstance(instrument["lot_size"], int) or instrument["lot_size"] < 1:
            reason = "Registered instrument lot-size metadata is invalid."
        elif instrument["tick_size_decimal"] is None or instrument["tick_size"] is not None:
            reason = "An exact authoritative decimal tick size is required."
        elif (
            instrument_tick_size is None
            or not instrument_tick_size.is_finite()
            or instrument_tick_size <= 0
        ):
            reason = "Registered exact decimal tick size is invalid."
        elif request.quantity % int(instrument["lot_size"]) != 0:
            reason = "Order quantity must be a multiple of the registered lot size."
        elif snapshot.instrument_id != request.instrument_id:
            reason = "Execution snapshot instrument does not match the order."
        elif snapshot.interval != snapshot.quality.interval:
            reason = "Execution snapshot interval does not match its quality report."
        elif _timestamp(snapshot.as_of, "snapshot as_of") > datetime.now(UTC):
            reason = "Execution snapshot is future-dated."
        elif halt_blocks_order(
            account["halted"], account["halt_class"], account["halt_reason"], request.side
        ):
            reason = account["halt_reason"]
            if not reason:
                reason = "Account halt is unclassified; order rejected."

        if reason is None and forward_event_id is not None:
            snapshot_as_of = _timestamp(snapshot.as_of, "snapshot as_of")
            trading_date = snapshot_as_of.astimezone(_INDIA).date()
            try:
                calendars = load_approved_calendar_registry()
                current_quality = analyze_dataset(
                    database_path,
                    instrument["exchange"],
                    instrument["trading_symbol"],
                    snapshot.interval,
                    trading_date,
                    trading_date,
                    as_of=snapshot_as_of,
                    calendars=calendars,
                    source_import_id=snapshot.import_id,
                    connection=connection,
                )
            except (CalendarConfigError, OSError, ValueError) as exc:
                reason = f"Current approved-calendar qualification failed: {exc}"
                halt_reason = PaperRiskHaltReason.MARKET_QUALITY_FAILURE.value
            else:
                if current_quality != snapshot.quality:
                    reason = (
                        "Execution snapshot quality no longer matches the current "
                        "approved-calendar dataset."
                    )
                    halt_reason = PaperRiskHaltReason.MARKET_QUALITY_FAILURE.value

        if reason is None:
            prior_snapshot_use = connection.execute(
                "SELECT order_id FROM paper_execution_snapshot_uses WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if prior_snapshot_use is not None:
                reason = "Execution snapshot has already been consumed."
                snapshot_accepted = False
            else:
                prior_event_use = (
                    connection.execute(
                        """SELECT order_id FROM paper_execution_snapshot_uses
                           WHERE runtime_id = ? AND event_id = ? AND proposal_id = ?""",
                        (
                            forward_authorization["runtime_id"],
                            forward_authorization["event_id"],
                            forward_authorization["proposal_id"],
                        ),
                    ).fetchone()
                    if forward_authorization is not None
                    else None
                )
                if prior_event_use is not None:
                    reason = "Runtime event already has a consumed execution snapshot."

        if reason is None:
            reason, halt_reason = _validate_execution_evidence(
                connection, account_id, request, snapshot, instrument, risk
            )
            snapshot_accepted = reason is None

        quantity = request.quantity
        side = request.side
        if reason is None:
            try:
                _reconcile(connection, account)
            except PaperTradingError as exc:
                reason = str(exc)
                halt_reason = f"Reconciliation failure: {exc}"
                halt_classification = PaperHaltClass.INTEGRITY
                snapshot_accepted = False

        position = connection.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? AND instrument_id = ?",
            (account_id, request.instrument_id),
        ).fetchone()
        if (
            reason is None
            and side == "SELL"
            and (position is None or quantity > position["quantity"])
        ):
            reason = "Long-only order cannot sell more than the available position."
        if reason is None and quantity > snapshot.available_quantity:
            reason = "All-or-none order quantity exceeds the supplied execution capacity."

        with localcontext() as context:
            context.prec = 50
            context.rounding = ROUND_HALF_UP
            tick_size = instrument_tick_size or Decimal(1)
            fill_price = snapshot.reference_price
            spread_per_unit = Decimal(0)
            slippage_per_unit = Decimal(0)
            tick_rounding_per_unit = Decimal(0)
            fees: dict[str, Decimal] = {}
            total_fee = Decimal(0)
            cash = _cash_balance(connection, account)
            if reason is None:
                try:
                    fill_price, spread_per_unit, slippage_per_unit, tick_rounding_per_unit = (
                        _fill_price(snapshot.reference_price, side, tick_size, costs)
                    )
                    fees = _fees(fill_price, quantity, side, costs)
                    total_fee = _fee_total(fees)
                    reason = _risk_rejection(
                        connection,
                        account,
                        request,
                        snapshot,
                        risk,
                        fill_price,
                        total_fee,
                        position,
                        cash,
                    )
                except (BacktestError, PaperTradingError, DecimalException, OverflowError) as exc:
                    reason = f"Execution calculation failed closed: {exc}"
                    halt_reason = reason
                    halt_classification = PaperHaltClass.INTEGRITY

        risk_duration_ns = time.perf_counter_ns() - risk_started
        journal_id = None
        connection.execute("SAVEPOINT journal_intent")
        try:
            journal_id = decision_journal._order_intent(
                connection, account_id, idempotency_key.strip(), request, snapshot, forward_event_id
            )
        except (decision_journal.JournalError, sqlite3.Error):
            connection.execute("ROLLBACK TO journal_intent")
            if side == "BUY" and reason is None:
                raise PaperTradingError(
                    "Decision journal persistence failed; new entry blocked."
                ) from None
            # Rejected entries cannot fill; exits retain their financial-ledger audit.
        finally:
            connection.execute("RELEASE journal_intent")
        execution_started = time.perf_counter_ns()
        status: Literal["FILLED", "REJECTED"] = "REJECTED" if reason else "FILLED"
        execution_timestamp = _timestamp_text(snapshot.timestamp)
        created_at = _timestamp_text(snapshot.as_of)
        connection.execute(
            """INSERT INTO paper_orders
               (order_id, account_id, idempotency_key, request_sha256, instrument_id,
                requested_instrument_id, side, quantity, status, reason, signal_timestamp, submitted_timestamp,
                execution_timestamp, reference_price, fill_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id,
                account_id,
                idempotency_key.strip(),
                request_hash,
                instrument["id"] if instrument is not None else None,
                request.instrument_id,
                side,
                quantity,
                status,
                reason,
                _timestamp_text(request.signal_timestamp),
                _timestamp_text(request.submitted_timestamp),
                execution_timestamp,
                _decimal_text(snapshot.reference_price),
                _decimal_text(fill_price) if status == "FILLED" else None,
                created_at,
            ),
        )
        _insert_order_event(
            connection,
            order_id,
            "SUBMITTED",
            request.submitted_timestamp,
            {"side": side, "quantity": quantity},
        )
        if status == "FILLED":
            fill_id = hashlib.sha256(f"{order_id}:fill:1".encode()).hexdigest()
            connection.execute(
                """INSERT INTO paper_fills
                   (fill_id, order_id, instrument_id, quantity, fill_price, fees_json,
                    execution_costs_json, execution_timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fill_id,
                    order_id,
                    request.instrument_id,
                    quantity,
                    _decimal_text(fill_price),
                    _canonical_json({key: _decimal_text(value) for key, value in fees.items()}),
                    _canonical_json(
                        {
                            "spread_cost": _decimal_text(spread_per_unit * quantity),
                            "slippage_cost": _decimal_text(slippage_per_unit * quantity),
                            "adverse_tick_rounding_cost": _decimal_text(
                                tick_rounding_per_unit * quantity
                            ),
                        }
                    ),
                    execution_timestamp,
                ),
            )
            turnover = fill_price * quantity
            cash_delta = -(turnover + total_fee) if side == "BUY" else turnover - total_fee
            entry_type = "BUY_CASH_DEBIT" if side == "BUY" else "SELL_CASH_CREDIT"
            entry_id = hashlib.sha256(f"{order_id}:ledger:{entry_type}".encode()).hexdigest()
            connection.execute(
                """INSERT INTO paper_ledger_entries
                   (entry_id, account_id, order_id, fill_id, entry_type, amount, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    account_id,
                    order_id,
                    fill_id,
                    entry_type,
                    _decimal_text(cash_delta),
                    execution_timestamp,
                ),
            )
            _apply_position_fill(
                connection,
                account_id,
                request.instrument_id,
                order_id,
                side,
                quantity,
                fill_price,
                total_fee,
                execution_timestamp,
            )
            _insert_order_event(
                connection,
                order_id,
                "FILLED",
                execution_timestamp,
                {"fill_id": fill_id, "fill_price": _decimal_text(fill_price)},
            )
        else:
            _insert_order_event(
                connection, order_id, "REJECTED", execution_timestamp, {"reason": reason}
            )
        if snapshot_accepted:
            connection.execute(
                """INSERT INTO paper_execution_snapshot_uses
                   (snapshot_id, account_id, instrument_id, import_id, runtime_id,
                    event_id, proposal_id, snapshot_sha256, order_id, used_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.snapshot_id,
                    account_id,
                    request.instrument_id,
                    snapshot.import_id,
                    *(
                        (
                            forward_authorization["runtime_id"],
                            forward_authorization["event_id"],
                            forward_authorization["proposal_id"],
                        )
                        if forward_authorization is not None
                        else (None, None, None)
                    ),
                    snapshot.snapshot_sha256,
                    order_id,
                    execution_timestamp,
                ),
            )
        if snapshot_accepted:
            connection.execute(
                """INSERT INTO paper_marks
                   (account_id, instrument_id, mark_price, observed_at, source_import_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, instrument_id) DO UPDATE SET
                     mark_price = excluded.mark_price, observed_at = excluded.observed_at,
                     source_import_id = excluded.source_import_id""",
                (
                    account_id,
                    request.instrument_id,
                    _decimal_text(snapshot.reference_price),
                    execution_timestamp,
                    snapshot.import_id,
                ),
            )
        if forward_authorization is not None:
            connection.execute(
                """UPDATE forward_execution_authorizations
                   SET status = ?, consumed_order_id = ?
                   WHERE event_id = ? AND status = 'AUTHORIZED'""",
                (
                    "CONSUMED" if status == "FILLED" else "REVOKED",
                    order_id if status == "FILLED" else None,
                    forward_event_id,
                ),
            )
        if halt_reason:
            _set_account_halt(connection, account_id, halt_reason, halt_classification)
        elif reason and any(
            word in reason.lower()
            for word in (
                "stale",
                "quality",
                "calendar",
                "reconciliation",
                "maximum",
            )
        ):
            _set_account_halt(connection, account_id, reason, PaperHaltClass.RISK)
        if journal_id is not None:
            connection.execute("SAVEPOINT journal_result")
            try:
                decision_journal._order_result(
                    connection,
                    journal_id,
                    order_id,
                    status,
                    snapshot,
                    risk_duration_ns=risk_duration_ns,
                    execution_duration_ns=time.perf_counter_ns() - execution_started,
                )
            except (decision_journal.JournalError, sqlite3.Error):
                connection.execute("ROLLBACK TO journal_result")
                if side == "BUY" and status == "FILLED":
                    raise PaperTradingError(
                        "Decision journal result failed; new entry rolled back."
                    ) from None
            finally:
                connection.execute("RELEASE journal_result")
        return PaperOrderResult(order_id, status, reason, False)


def record_paper_mark(
    connection: sqlite3.Connection,
    account_id: int,
    instrument_id: int,
    mark_price: Decimal,
    observed_at: str,
    source_import_id: int,
) -> bool:
    """Update an open position's last-trade mark inside the caller's transaction."""
    if not isinstance(mark_price, Decimal) or not mark_price.is_finite() or mark_price <= 0:
        raise PaperTradingError("mark_price must be a finite positive Decimal.")
    if (
        isinstance(account_id, bool)
        or not isinstance(account_id, int)
        or account_id < 1
        or isinstance(instrument_id, bool)
        or not isinstance(instrument_id, int)
        or instrument_id < 1
        or isinstance(source_import_id, bool)
        or not isinstance(source_import_id, int)
        or source_import_id < 1
    ):
        raise PaperTradingError("Paper mark identities must be positive integers.")
    timestamp = _timestamp_text(observed_at)
    position = connection.execute(
        """SELECT 1 FROM paper_positions
           WHERE account_id = ? AND instrument_id = ?""",
        (account_id, instrument_id),
    ).fetchone()
    if position is None:
        return False
    imported = connection.execute(
        """SELECT instrument_id, interval, status, accepted_rows, source_use_status,
                  file_sha256, completed_at
           FROM data_imports WHERE id = ?""",
        (source_import_id,),
    ).fetchone()
    if (
        imported is None
        or imported["instrument_id"] != instrument_id
        or imported["interval"] != "1m"
        or imported["status"] != "COMPLETED"
        or imported["source_use_status"] != "VERIFIED_PERMITTED"
        or not imported["file_sha256"]
        or not imported["completed_at"]
    ):
        raise PaperTradingError("Paper mark source import is not verified for this position.")
    if _timestamp(imported["completed_at"], "mark import completion") > _timestamp(
        timestamp, "mark timestamp"
    ):
        raise PaperTradingError("Paper mark import completed after the supplied mark.")
    previous = connection.execute(
        """SELECT observed_at FROM paper_marks
           WHERE account_id = ? AND instrument_id = ?""",
        (account_id, instrument_id),
    ).fetchone()
    if previous is not None and _timestamp(timestamp, "mark timestamp") < _timestamp(
        previous["observed_at"], "previous mark timestamp"
    ):
        raise PaperTradingError("Paper mark timestamp cannot move backwards.")
    connection.execute(
        """INSERT INTO paper_marks
           (account_id, instrument_id, mark_price, observed_at, source_import_id)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(account_id, instrument_id) DO UPDATE SET
             mark_price = excluded.mark_price, observed_at = excluded.observed_at,
             source_import_id = excluded.source_import_id""",
        (account_id, instrument_id, _decimal_text(mark_price), timestamp, source_import_id),
    )
    return True


def reconcile_paper_account(database_path: Path, account_id: int) -> dict[str, object]:
    """Compare persisted cash and position projections with immutable fills/ledger history."""
    with _transaction(database_path) as connection:
        account = connection.execute(
            "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise PaperTradingError("Paper account does not exist.")
        errors = _reconcile(connection, account, return_errors=True)
        if errors:
            reason = "Reconciliation failure: " + "; ".join(errors)
            _set_account_halt(connection, account_id, reason, PaperHaltClass.INTEGRITY)
        return {"status": "FAILED" if errors else "RECONCILED", "errors": errors}


def get_paper_account_report(
    database_path: Path, account_id: int, *, report_date: date | None = None
) -> dict[str, object]:
    """Return a clearly labelled simulated account snapshot and daily order activity."""
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        return _get_paper_account_report(database_path, account_id, report_date=report_date)


def _get_paper_account_report(
    database_path: Path, account_id: int, *, report_date: date | None = None
) -> dict[str, object]:
    connection = _readonly_connection(database_path)
    try:
        account = connection.execute(
            "SELECT * FROM paper_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise PaperTradingError("Paper account does not exist.")
        reconciliation = _reconcile(connection, account, return_errors=True)
        try:
            _risk_from_json(account["risk_limits_json"])
            _costs_from_json(account["cost_assumptions_json"])
        except (KeyError, TypeError, ValueError, BacktestError):
            reconciliation.append("Persisted risk or cost settings are malformed.")
        cash = _cash_balance(connection, account)
        positions = []
        unrealized = Decimal(0)
        exposure = Decimal(0)
        marks = connection.execute(
            """SELECT p.*, i.exchange, i.trading_symbol, m.mark_price, m.observed_at
               FROM paper_positions p LEFT JOIN instruments i ON i.id = p.instrument_id
               LEFT JOIN paper_marks m ON m.account_id = p.account_id AND m.instrument_id = p.instrument_id
               WHERE p.account_id = ? ORDER BY i.exchange, i.trading_symbol""",
            (account_id,),
        ).fetchall()
        for row in marks:
            mark = Decimal(row["mark_price"]) if row["mark_price"] is not None else None
            qty = int(row["quantity"])
            position_unrealized = Decimal(0)
            if mark is not None:
                exposure += mark * qty
                position_unrealized = (mark - Decimal(row["average_entry_price"])) * qty
                unrealized += position_unrealized
            positions.append(
                {
                    "exchange": row["exchange"],
                    "symbol": row["trading_symbol"],
                    "quantity": qty,
                    "average_entry_price": row["average_entry_price"],
                    "mark_price": row["mark_price"],
                    "mark_timestamp": row["observed_at"],
                    "realized_net_pnl": row["realized_net_pnl"],
                    "unrealized_gross_pnl": (
                        _decimal_text(position_unrealized) if mark is not None else None
                    ),
                }
            )
            if mark is None:
                reconciliation.append(
                    f"Open position {row['exchange']}:{row['trading_symbol']} has no current mark."
                )
        recent = connection.execute(
            "SELECT status, COUNT(*) AS count FROM paper_orders WHERE account_id = ? GROUP BY status",
            (account_id,),
        ).fetchall()
        orders = [
            {
                "order_id": row["order_id"],
                "requested_instrument_id": row["requested_instrument_id"],
                "side": row["side"],
                "quantity": row["quantity"],
                "status": row["status"],
                "reason": row["reason"],
                "signal_timestamp": row["signal_timestamp"],
                "submitted_timestamp": row["submitted_timestamp"],
                "execution_timestamp": row["execution_timestamp"],
                "reference_price": row["reference_price"],
                "simulated_fill_price": row["fill_price"],
                "simulated_fees": json.loads(row["fees_json"]) if row["fees_json"] else None,
                "simulated_execution_costs": (
                    json.loads(row["execution_costs_json"]) if row["execution_costs_json"] else None
                ),
            }
            for row in connection.execute(
                """SELECT o.*, f.fees_json, f.execution_costs_json
                   FROM paper_orders o LEFT JOIN paper_fills f ON f.order_id = o.order_id
                   WHERE o.account_id = ? ORDER BY o.created_at, o.order_id""",
                (account_id,),
            )
        ]
        selected_date = report_date or datetime.now(UTC).astimezone(_INDIA).date()
        daily_orders: dict[str, int] = {}
        for row in connection.execute(
            "SELECT status, submitted_timestamp FROM paper_orders WHERE account_id = ?",
            (account_id,),
        ):
            if (
                _timestamp(row["submitted_timestamp"], "order submission time")
                .astimezone(_INDIA)
                .date()
                == selected_date
            ):
                daily_orders[row["status"]] = daily_orders.get(row["status"], 0) + 1
        daily_realized = sum(
            (
                Decimal(row["realized_net_pnl"])
                for row in connection.execute(
                    "SELECT realized_net_pnl, closed_at FROM paper_realized_pnl WHERE account_id = ?",
                    (account_id,),
                )
                if _timestamp(row["closed_at"], "realized P&L timestamp").astimezone(_INDIA).date()
                == selected_date
            ),
            Decimal(0),
        )
        daily_state = connection.execute(
            "SELECT starting_equity FROM paper_daily_state WHERE account_id = ? AND local_date = ?",
            (account_id, selected_date.isoformat()),
        ).fetchone()
        daily_net_equity_change = (
            cash + exposure - Decimal(daily_state["starting_equity"])
            if daily_state is not None
            else None
        )
        return {
            "simulated": True,
            "label": "SIMULATED PAPER ACCOUNT — NOT ACTUAL CASH OR PROFIT",
            "account_id": account_id,
            "name": account["name"],
            "status": "HALTED"
            if account["halted"] or reconciliation
            else "AVAILABLE FOR SIMULATION API",
            "halt_class": account["halt_class"],
            "halt_reason": account["halt_reason"]
            or ("; ".join(reconciliation) if reconciliation else None),
            "currency": _CURRENCY,
            "initial_cash": account["initial_cash"],
            "available_cash": _decimal_text(cash),
            "gross_exposure": _decimal_text(exposure),
            "realized_net_pnl": _decimal_text(
                sum(
                    (
                        Decimal(row["realized_net_pnl"])
                        for row in connection.execute(
                            "SELECT realized_net_pnl FROM paper_realized_pnl WHERE account_id = ?",
                            (account_id,),
                        )
                    ),
                    Decimal(0),
                )
            ),
            "unrealized_gross_pnl": _decimal_text(unrealized),
            "equity_before_estimated_liquidation_costs": _decimal_text(cash + exposure),
            "positions": positions,
            "orders": orders,
            "order_counts": {row["status"]: row["count"] for row in recent},
            "daily_report": {
                "local_date": selected_date.isoformat(),
                "order_counts": daily_orders,
                "realized_net_pnl": _decimal_text(daily_realized),
                "net_equity_change_before_estimated_liquidation_costs": (
                    _decimal_text(daily_net_equity_change)
                    if daily_net_equity_change is not None
                    else None
                ),
            },
            "reconciliation": {
                "status": "FAILED" if reconciliation else "RECONCILED",
                "errors": reconciliation,
            },
            "risk_limits": json.loads(account["risk_limits_json"]),
            "cost_assumptions": json.loads(account["cost_assumptions_json"]),
        }
    finally:
        connection.close()


def list_paper_accounts(database_path: Path) -> list[dict[str, object]]:
    """List account names and simulated status without creating a database connection for writes."""
    if not database_path.is_file():
        return []
    connection = _readonly_connection(database_path)
    try:
        return [
            {
                "account_id": row["id"],
                "name": row["name"],
                "halted": bool(row["halted"]),
                "halt_class": row["halt_class"],
                "simulated": True,
            }
            for row in connection.execute(
                "SELECT id, name, halted, halt_class FROM paper_accounts ORDER BY id"
            )
        ]
    finally:
        connection.close()


def _validate_execution_evidence(connection, account_id, request, snapshot, instrument, risk):
    signal_time = _timestamp(request.signal_timestamp, "signal_timestamp")
    submitted_time = _timestamp(request.submitted_timestamp, "submitted_timestamp")
    execution_time = _timestamp(snapshot.timestamp, "execution timestamp")
    as_of = _timestamp(snapshot.as_of, "snapshot as_of")
    if execution_time < submitted_time:
        return "Execution snapshot predates order submission.", None
    if execution_time < signal_time:
        return "Execution snapshot predates the signal.", None
    if (as_of - execution_time).total_seconds() > risk.max_market_data_age_seconds:
        return "Execution snapshot is stale; account halted.", "Stale execution data."
    evidence = snapshot.quality
    if (
        evidence.exchange != instrument["exchange"]
        or evidence.trading_symbol != instrument["trading_symbol"]
        or evidence.interval != snapshot.interval
        or evidence.last_candle_timestamp is None
        or _timestamp(evidence.last_candle_timestamp, "quality last candle") >= execution_time
        or evidence.calendar_status != "FULL"
        or evidence.calendar_confidence != "CONFIRMED"
        or evidence.overall_status != "COMPLETE"
        or evidence.staleness_status != "FRESH"
        or evidence.completeness_percentage != Decimal(100)
        or _timestamp(evidence.as_of.isoformat(), "quality as_of") != as_of
    ):
        return (
            "Market-quality or confirmed-calendar evidence is insufficient; account halted.",
            "Market quality gate failed.",
        )
    imported = connection.execute(
        """SELECT instrument_id, interval, status, accepted_rows, source_use_status,
                  file_sha256, completed_at, import_type, metadata_json
           FROM data_imports WHERE id = ?""",
        (snapshot.import_id,),
    ).fetchone()
    if (
        imported is None
        or imported["instrument_id"] != request.instrument_id
        or imported["interval"] != snapshot.interval
        or imported["status"] != "COMPLETED"
        or imported["accepted_rows"] < 1
        or imported["source_use_status"] != "VERIFIED_PERMITTED"
        or not imported["file_sha256"]
        or not imported["completed_at"]
    ):
        return (
            "Import provenance is not verified and permitted; account halted.",
            "Source permission gate failed.",
        )
    try:
        import_completed = _timestamp(
            imported["completed_at"], "execution source import completion"
        )
    except PaperTradingError as exc:
        return "Import completion timestamp is invalid.", str(exc)
    if import_completed > as_of:
        return (
            "Import completed after the supplied execution evidence.",
            "Source completion is future-dated.",
        )
    errors = connection.execute(
        "SELECT COUNT(*) FROM data_quality_issues WHERE import_id = ? AND severity = 'ERROR'",
        (snapshot.import_id,),
    ).fetchone()[0]
    if errors:
        return (
            "Source import contains error-level quality issues; account halted.",
            "Imported data quality failed.",
        )
    # A SELL is gated by its own fresh, permitted snapshot above. Existing portfolio marks
    # must be fresh before adding risk, but cannot trap a position from being reduced.
    if request.side == "BUY":
        for mark in connection.execute(
            "SELECT observed_at FROM paper_marks WHERE account_id = ?", (account_id,)
        ):
            age = (
                as_of - _timestamp(mark["observed_at"], "position mark timestamp")
            ).total_seconds()
            if age < 0 or age > risk.max_market_data_age_seconds:
                return (
                    "An open position has a stale market mark; account halted.",
                    "Stale position mark.",
                )
    return None, None


def _risk_rejection(
    connection, account, request, snapshot, risk, fill_price, fee_total, position, cash
):
    quantity = request.quantity
    turnover = fill_price * quantity
    if turnover > risk.max_order_value:
        return "Maximum order value breached."
    if request.side == "BUY":
        prior_qty = int(position["quantity"]) if position is not None else 0
        if prior_qty + quantity > risk.max_position_quantity:
            return "Maximum position quantity breached."
        if cash < turnover + fee_total:
            return "Insufficient virtual cash for fill and charges."
        projected_instrument = (prior_qty + quantity) * snapshot.reference_price
        current_total = _gross_exposure(connection, account["id"])
        current_instrument = prior_qty * _latest_mark(
            connection, account["id"], request.instrument_id
        )
        projected_total = (
            current_total - current_instrument + ((prior_qty + quantity) * snapshot.reference_price)
        )
        if projected_instrument > risk.max_instrument_exposure:
            return "Maximum instrument exposure breached."
        if projected_total > risk.max_portfolio_exposure:
            return "Maximum portfolio exposure breached."
    else:
        current_qty = int(position["quantity"]) if position is not None else 0
        if current_qty - quantity < 0:
            return "Sell quantity exceeds the available long position."
    day = _timestamp(snapshot.timestamp, "snapshot timestamp").astimezone(_INDIA).date().isoformat()
    state = connection.execute(
        "SELECT starting_equity FROM paper_daily_state WHERE account_id = ? AND local_date = ?",
        (account["id"], day),
    ).fetchone()
    if state is None:
        starting_equity = cash + _gross_exposure(connection, account["id"])
        connection.execute(
            "INSERT INTO paper_daily_state (account_id, local_date, starting_equity) VALUES (?, ?, ?)",
            (account["id"], day, _decimal_text(starting_equity)),
        )
    else:
        starting_equity = Decimal(state["starting_equity"])
    projected_cash = (
        cash - turnover - fee_total if request.side == "BUY" else cash + turnover - fee_total
    )
    if request.side == "BUY":
        current_exposure = _gross_exposure(connection, account["id"])
        old_position_qty = int(position["quantity"]) if position is not None else 0
        old_instrument_exposure = old_position_qty * _latest_mark(
            connection, account["id"], request.instrument_id
        )
        projected_exposure = (
            current_exposure
            - old_instrument_exposure
            + (old_position_qty + quantity) * snapshot.reference_price
        )
        projected_equity = projected_cash + projected_exposure
    else:
        # A sell that passes evidence, long-only quantity and order-value checks is an
        # exposure-reducing exit. Do not let an already breached daily-loss limit trap it.
        projected_equity = starting_equity
    if request.side == "BUY" and starting_equity - projected_equity >= risk.max_daily_loss:
        return "Maximum daily loss breached."
    return None


def _apply_position_fill(
    connection, account_id, instrument_id, order_id, side, quantity, price, fee, timestamp
):
    row = connection.execute(
        "SELECT * FROM paper_positions WHERE account_id = ? AND instrument_id = ?",
        (account_id, instrument_id),
    ).fetchone()
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        if side == "BUY":
            old_qty = int(row["quantity"]) if row else 0
            old_cost = Decimal(row["average_entry_price"]) * old_qty if row else Decimal(0)
            old_fees = Decimal(row["remaining_entry_fees"]) if row else Decimal(0)
            new_qty = old_qty + quantity
            average = (old_cost + price * quantity) / new_qty
            remaining_fees = old_fees + fee
            realized = Decimal(row["realized_net_pnl"]) if row else Decimal(0)
            connection.execute(
                """INSERT INTO paper_positions VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, instrument_id) DO UPDATE SET quantity = excluded.quantity,
                   average_entry_price = excluded.average_entry_price,
                   remaining_entry_fees = excluded.remaining_entry_fees,
                   realized_net_pnl = excluded.realized_net_pnl""",
                (
                    account_id,
                    instrument_id,
                    new_qty,
                    _decimal_text(average),
                    _decimal_text(remaining_fees),
                    _decimal_text(realized),
                ),
            )
        else:
            if row is None or quantity > row["quantity"]:
                raise PaperTradingError("Position changed before the sell fill.")
            old_qty = int(row["quantity"])
            remaining_qty = old_qty - quantity
            old_fees = Decimal(row["remaining_entry_fees"])
            allocated_entry_fee = old_fees * Decimal(quantity) / old_qty
            realized_delta = (
                (price - Decimal(row["average_entry_price"])) * quantity - allocated_entry_fee - fee
            )
            realized = Decimal(row["realized_net_pnl"]) + realized_delta
            connection.execute(
                "INSERT INTO paper_realized_pnl VALUES (?, ?, ?, ?, ?)",
                (order_id, account_id, instrument_id, _decimal_text(realized_delta), timestamp),
            )
            if remaining_qty:
                connection.execute(
                    "UPDATE paper_positions SET quantity = ?, remaining_entry_fees = ?, realized_net_pnl = ? WHERE account_id = ? AND instrument_id = ?",
                    (
                        remaining_qty,
                        _decimal_text(old_fees - allocated_entry_fee),
                        _decimal_text(realized),
                        account_id,
                        instrument_id,
                    ),
                )
            else:
                connection.execute(
                    "DELETE FROM paper_positions WHERE account_id = ? AND instrument_id = ?",
                    (account_id, instrument_id),
                )


def _reconcile(connection, account, *, return_errors=False):
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_UP
        return _reconcile_inner(connection, account, return_errors=return_errors)


def _reconcile_inner(connection, account, *, return_errors=False):
    account_id = account["id"]
    errors = _financial_relationship_errors(connection, account_id)
    if errors:
        return errors if return_errors else _raise_if_errors(errors)
    entries = connection.execute(
        """SELECT e.*, f.order_id AS fill_order_id, f.quantity, f.fill_price,
                  f.fees_json, f.instrument_id,
                  f.execution_timestamp, o.account_id AS order_account_id,
                  o.status AS order_status, o.fill_price AS order_fill_price, o.side
           FROM paper_ledger_entries e JOIN paper_fills f ON f.fill_id = e.fill_id
           JOIN paper_orders o ON o.order_id = e.order_id
           WHERE e.account_id = ? ORDER BY f.execution_timestamp, e.entry_id""",
        (account_id,),
    ).fetchall()
    errors = []
    expected_cash = Decimal(account["initial_cash"])
    expected_realized = Decimal(0)
    expected_realized_by_order: dict[str, Decimal] = {}
    expected_positions: dict[int, dict[str, Decimal | int]] = {}
    for entry in entries:
        amount = Decimal(entry["amount"])
        instrument_id = int(entry["instrument_id"])
        state = expected_positions.setdefault(
            instrument_id,
            {"quantity": 0, "cost": Decimal(0), "fees": Decimal(0), "realized": Decimal(0)},
        )
        qty = int(entry["quantity"])
        price = Decimal(entry["fill_price"])
        fees = sum(
            (Decimal(value) for value in json.loads(entry["fees_json"]).values()), Decimal(0)
        )
        turnover = qty * price
        expected_amount = (
            -(turnover + fees) if entry["entry_type"] == "BUY_CASH_DEBIT" else turnover - fees
        )
        if amount != expected_amount:
            errors.append(
                f"Cash ledger entry does not match fill economics for {entry['order_id']}."
            )
        expected_cash += expected_amount
        if (
            entry["fill_order_id"] != entry["order_id"]
            or entry["order_status"] != "FILLED"
            or entry["order_fill_price"] != entry["fill_price"]
        ):
            errors.append(f"Order and fill price/status disagree for {entry['order_id']}.")
        expected_entry_type = "BUY_CASH_DEBIT" if entry["side"] == "BUY" else "SELL_CASH_CREDIT"
        if entry["entry_type"] != expected_entry_type:
            errors.append(f"Order side and ledger direction disagree for {entry['order_id']}.")
        if entry["order_account_id"] != account_id:
            errors.append(f"Order and ledger account disagree for {entry['order_id']}.")
        if entry["entry_type"] == "BUY_CASH_DEBIT":
            state["cost"] = Decimal(state["cost"]) + qty * price
            state["fees"] = Decimal(state["fees"]) + fees
            state["quantity"] = int(state["quantity"]) + qty
        elif entry["entry_type"] == "SELL_CASH_CREDIT":
            held = int(state["quantity"])
            if qty > held or held == 0:
                errors.append(
                    f"Sell fill exceeds replayed long position for instrument {instrument_id}."
                )
                continue
            avg = Decimal(state["cost"]) / held
            allocated_fees = Decimal(state["fees"]) * qty / held
            state["realized"] = (
                Decimal(state["realized"]) + (price - avg) * qty - allocated_fees - fees
            )
            realized_delta = (price - avg) * qty - allocated_fees - fees
            expected_realized += realized_delta
            expected_realized_by_order[entry["order_id"]] = realized_delta
            state["cost"] = Decimal(state["cost"]) - avg * qty
            state["fees"] = Decimal(state["fees"]) - allocated_fees
            remaining_qty = held - qty
            state["quantity"] = remaining_qty
            if remaining_qty == 0:
                state["cost"] = Decimal(0)
                state["fees"] = Decimal(0)
                state["realized"] = Decimal(0)
        else:
            errors.append(f"Unknown ledger entry type {entry['entry_type']}.")
    if expected_cash != _cash_balance(connection, account):
        errors.append("Cash ledger does not match persisted cash projection.")
    persisted = connection.execute(
        "SELECT * FROM paper_positions WHERE account_id = ?", (account_id,)
    ).fetchall()
    persisted_by_instrument = {int(row["instrument_id"]): row for row in persisted}
    for instrument_id, state in expected_positions.items():
        qty = int(state["quantity"])
        row = persisted_by_instrument.pop(instrument_id, None)
        if qty == 0:
            if row is not None:
                errors.append(f"Unexpected persisted flat position for instrument {instrument_id}.")
            continue
        if row is None:
            errors.append(f"Missing persisted position for instrument {instrument_id}.")
            continue
        average = Decimal(state["cost"]) / qty
        if (
            row["quantity"] != qty
            or Decimal(row["average_entry_price"]) != average
            or Decimal(row["remaining_entry_fees"]) != Decimal(state["fees"])
            or Decimal(row["realized_net_pnl"]) != Decimal(state["realized"])
        ):
            errors.append(f"Persisted position does not reconcile for instrument {instrument_id}.")
    if persisted_by_instrument:
        errors.append("Unexpected persisted position without matching fill history.")
    stored_rows = connection.execute(
        "SELECT order_id, realized_net_pnl FROM paper_realized_pnl WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    stored_realized_by_order = {
        row["order_id"]: Decimal(row["realized_net_pnl"]) for row in stored_rows
    }
    stored_realized = sum(stored_realized_by_order.values(), Decimal(0))
    if stored_realized != expected_realized:
        errors.append("Realized P&L history does not match fill replay.")
    if stored_realized_by_order != expected_realized_by_order:
        errors.append("Realized P&L entries do not match individual sell fills.")
    for order in connection.execute(
        "SELECT order_id, status FROM paper_orders WHERE account_id = ?", (account_id,)
    ):
        events = [
            row["event_type"]
            for row in connection.execute(
                "SELECT event_type FROM paper_order_events WHERE order_id = ? "
                "ORDER BY event_sequence",
                (order["order_id"],),
            )
        ]
        if events != ["SUBMITTED", order["status"]]:
            errors.append(f"Order lifecycle events do not reconcile for {order['order_id']}.")
    return errors if return_errors else (_raise_if_errors(errors))


def _financial_relationship_errors(connection, account_id):
    """Validate order/fill/ledger ownership and one-to-one financial lifecycle."""
    errors: list[str] = []
    orphan_fills = connection.execute(
        """SELECT COUNT(*) FROM paper_fills f LEFT JOIN paper_orders o
           ON o.order_id = f.order_id WHERE o.order_id IS NULL"""
    ).fetchone()[0]
    if orphan_fills:
        errors.append("Fill history contains an orphaned order reference.")
    invalid_ledger_links = connection.execute(
        """SELECT COUNT(*) FROM paper_ledger_entries e
           LEFT JOIN paper_orders o ON o.order_id = e.order_id
           LEFT JOIN paper_fills f ON f.fill_id = e.fill_id
           WHERE o.order_id IS NULL OR f.fill_id IS NULL OR f.order_id != e.order_id"""
    ).fetchone()[0]
    if invalid_ledger_links:
        errors.append("Ledger entries do not reference a fill belonging to their order.")
    orphan_realized = connection.execute(
        """SELECT COUNT(*) FROM paper_realized_pnl r LEFT JOIN paper_orders o
           ON o.order_id = r.order_id WHERE o.order_id IS NULL"""
    ).fetchone()[0]
    if orphan_realized:
        errors.append("Realized P&L history contains an orphaned order reference.")
    duplicate_ledgers = connection.execute(
        """SELECT COUNT(*) FROM (
               SELECT order_id FROM paper_ledger_entries GROUP BY order_id HAVING COUNT(*) != 1
           )"""
    ).fetchone()[0]
    if duplicate_ledgers:
        errors.append("An order has duplicate or contradictory financial ledger entries.")

    orders = connection.execute(
        "SELECT * FROM paper_orders WHERE account_id = ? ORDER BY order_id", (account_id,)
    ).fetchall()
    for order in orders:
        fills = connection.execute(
            "SELECT * FROM paper_fills WHERE order_id = ?", (order["order_id"],)
        ).fetchall()
        ledger = connection.execute(
            "SELECT * FROM paper_ledger_entries WHERE order_id = ?", (order["order_id"],)
        ).fetchall()
        realized_rows = connection.execute(
            "SELECT * FROM paper_realized_pnl WHERE order_id = ?", (order["order_id"],)
        ).fetchall()
        if order["status"] == "FILLED":
            if len(fills) != 1 or len(ledger) != 1:
                errors.append(
                    f"Filled order {order['order_id']} must have exactly one fill and ledger entry."
                )
                continue
            fill = fills[0]
            entry = ledger[0]
            expected_entry_type = "BUY_CASH_DEBIT" if order["side"] == "BUY" else "SELL_CASH_CREDIT"
            if (
                fill["instrument_id"] != order["instrument_id"]
                or order["instrument_id"] != order["requested_instrument_id"]
                or fill["quantity"] != order["quantity"]
                or fill["fill_price"] != order["fill_price"]
                or fill["execution_timestamp"] != order["execution_timestamp"]
            ):
                errors.append(f"Order and fill fields disagree for {order['order_id']}.")
            if (
                entry["account_id"] != account_id
                or entry["fill_id"] != fill["fill_id"]
                or entry["entry_type"] != expected_entry_type
            ):
                errors.append(
                    f"Fill and cash ledger ownership/direction disagree for {order['order_id']}."
                )
            expected_realized_count = 1 if order["side"] == "SELL" else 0
            if len(realized_rows) != expected_realized_count:
                errors.append(f"Realized P&L records do not match {order['order_id']} direction.")
            elif realized_rows and (
                realized_rows[0]["account_id"] != account_id
                or realized_rows[0]["instrument_id"] != order["instrument_id"]
            ):
                errors.append(f"Realized P&L ownership disagrees for {order['order_id']}.")
        elif fills or ledger or realized_rows or order["fill_price"] is not None:
            errors.append(f"Rejected order {order['order_id']} has financial records.")
    return errors


def _raise_if_errors(errors):
    if errors:
        raise PaperTradingError("; ".join(errors))


def _cash_balance(connection, account):
    with localcontext() as context:
        context.prec = 50
        total = Decimal(account["initial_cash"])
        for row in connection.execute(
            "SELECT amount FROM paper_ledger_entries WHERE account_id = ?", (account["id"],)
        ):
            total += Decimal(row["amount"])
        return total


def _gross_exposure(connection, account_id):
    with localcontext() as context:
        context.prec = 50
        total = Decimal(0)
        for row in connection.execute(
            """SELECT p.quantity, m.mark_price FROM paper_positions p LEFT JOIN paper_marks m
               ON m.account_id = p.account_id AND m.instrument_id = p.instrument_id
               WHERE p.account_id = ?""",
            (account_id,),
        ):
            if row["mark_price"] is None:
                raise PaperTradingError(
                    "Open position has no mark; reconciliation/risk check fails closed."
                )
            total += int(row["quantity"]) * Decimal(row["mark_price"])
        return total


def _latest_mark(connection, account_id, instrument_id):
    row = connection.execute(
        "SELECT mark_price FROM paper_marks WHERE account_id = ? AND instrument_id = ?",
        (account_id, instrument_id),
    ).fetchone()
    return Decimal(row["mark_price"]) if row is not None else Decimal(0)


def _risk_from_json(document):
    values = json.loads(document)
    return RiskLimits(
        max_order_value=Decimal(values["max_order_value"]),
        max_position_quantity=values["max_position_quantity"],
        max_instrument_exposure=Decimal(values["max_instrument_exposure"]),
        max_portfolio_exposure=Decimal(values["max_portfolio_exposure"]),
        max_daily_loss=Decimal(values["max_daily_loss"]),
        max_market_data_age_seconds=values["max_market_data_age_seconds"],
    )


def _costs_from_json(document):
    values = json.loads(document)
    return CostAssumptions(**{key: Decimal(value) for key, value in values.items()})


def _insert_order_event(connection, order_id, event, timestamp, details):
    event_id = hashlib.sha256(f"{order_id}:{event}".encode()).hexdigest()
    sequence = connection.execute(
        "SELECT COALESCE(MAX(event_sequence), 0) + 1 FROM paper_order_events WHERE order_id = ?",
        (order_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO paper_order_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            event_id,
            order_id,
            sequence,
            event,
            _timestamp_text(timestamp),
            _canonical_json(details),
        ),
    )


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _timestamp(value, label):
    if not isinstance(value, str):
        raise PaperTradingError(f"{label} must be an ISO-8601 string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PaperTradingError(f"{label} is not a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaperTradingError(f"{label} must include a UTC offset.")
    return parsed.astimezone(UTC)


def _timestamp_text(value):
    instant = _timestamp(value, "timestamp")
    precision = "microseconds" if instant.microsecond else "seconds"
    return instant.isoformat(timespec=precision).replace("+00:00", "Z")


def _has_paisa_precision(value: Decimal) -> bool:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    if exponent >= -2:
        return True
    fractional_digits_beyond_paisa = -2 - exponent
    digits = value.as_tuple().digits
    return not any(digits[-fractional_digits_beyond_paisa:])


def _readonly_connection(database_path):
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection
