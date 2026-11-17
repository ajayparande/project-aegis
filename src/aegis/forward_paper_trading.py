"""Forward paper-trading infrastructure for deterministic proposal intake and routing."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from aegis.database import get_connection, get_readonly_connection
from aegis.market_quality import (
    CalendarConfigError,
    CalendarRegistry,
    DatasetQualityReport,
    analyze_dataset,
    load_approved_calendar_registry,
)
from aegis.paper_trading import (
    ExecutionSnapshot,
    PaperOrderRequest,
    PaperTradingError,
    RiskLimits,
    _forward_context_error,
    submit_paper_order,
)


class ForwardTradingError(ValueError):
    """Raised when a forward-paper-trading input or state is invalid."""


_INDIA = ZoneInfo("Asia/Kolkata")


class ProposalValidationError(ForwardTradingError):
    """Raised when an AI proposal violates the deterministic acceptance contract."""


class DecisionAction(str, Enum):
    """Allowed actions for future AI trading proposals."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class MarketEvidence:
    """Read-only evidence bundle for one instrument at one evaluation point."""

    instrument_id: int
    exchange: str
    trading_symbol: str
    interval: str
    as_of: str
    reference_price: Decimal
    quality: DatasetQualityReport
    source_import_id: int | None = None
    latest_candle_timestamp: str | None = None
    candidate_score: Decimal = Decimal(0)
    market_regime: str = "UNKNOWN"
    evidence_notes: tuple[str, ...] = ()
    runtime_id: str | None = None
    evidence_id: str | None = None
    scheduler_event_id: str | None = None
    proposal_id: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.instrument_id, bool)
            or not isinstance(self.instrument_id, int)
            or self.instrument_id < 1
        ):
            raise ForwardTradingError("instrument_id must be a positive integer.")
        if not isinstance(self.exchange, str) or not self.exchange.strip():
            raise ForwardTradingError("exchange must be non-empty.")
        if not isinstance(self.trading_symbol, str) or not self.trading_symbol.strip():
            raise ForwardTradingError("trading_symbol must be non-empty.")
        if not isinstance(self.interval, str) or not self.interval.strip():
            raise ForwardTradingError("interval must be non-empty.")
        _parse_timestamp(self.as_of, field="evidence as_of")
        if (
            not isinstance(self.reference_price, Decimal)
            or not self.reference_price.is_finite()
            or self.reference_price <= 0
        ):
            raise ForwardTradingError("reference_price must be a positive finite Decimal.")
        if not isinstance(self.quality, DatasetQualityReport):
            raise ForwardTradingError("quality must be a DatasetQualityReport instance.")
        if self.source_import_id is not None and (
            isinstance(self.source_import_id, bool)
            or not isinstance(self.source_import_id, int)
            or self.source_import_id < 1
        ):
            raise ForwardTradingError("source_import_id must be a positive integer when supplied.")
        if self.latest_candle_timestamp is not None and not isinstance(
            self.latest_candle_timestamp, str
        ):
            raise ForwardTradingError("latest_candle_timestamp must be text when supplied.")
        if not isinstance(self.candidate_score, Decimal) or not self.candidate_score.is_finite():
            raise ForwardTradingError("candidate_score must be a finite Decimal.")
        if not isinstance(self.evidence_notes, tuple):
            raise ForwardTradingError("evidence_notes must be a tuple of strings.")
        if self.runtime_id is not None and (
            not isinstance(self.runtime_id, str) or not self.runtime_id.strip()
        ):
            raise ForwardTradingError("runtime_id must be non-empty text when supplied.")
        if self.evidence_id is not None and (
            not isinstance(self.evidence_id, str) or not self.evidence_id.strip()
        ):
            raise ForwardTradingError("evidence_id must be non-empty text when supplied.")
        for field_name in ("scheduler_event_id", "proposal_id"):
            field_value = getattr(self, field_name)
            if field_value is not None and (
                not isinstance(field_value, str) or not field_value.strip()
            ):
                raise ForwardTradingError(f"{field_name} must be non-empty text when supplied.")

    @property
    def quality_report(self) -> DatasetQualityReport:
        return self.quality

    @property
    def last_candle_timestamp(self) -> str | None:
        return self.latest_candle_timestamp

    @property
    def price(self) -> Decimal:
        return self.reference_price


@dataclass(frozen=True, slots=True)
class MarketCandidate:
    """A ranked instrument candidate for future AI review or deterministic routing."""

    evidence: MarketEvidence
    score: Decimal
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, MarketEvidence):
            raise ForwardTradingError("evidence must be a MarketEvidence instance.")
        if not isinstance(self.score, Decimal) or not self.score.is_finite():
            raise ForwardTradingError("score must be a finite Decimal.")
        if not isinstance(self.rationale, str):
            raise ForwardTradingError("rationale must be text.")

    @property
    def candidate_score(self) -> Decimal:
        return self.score


@dataclass(frozen=True, slots=True)
class DecisionProposal:
    """Deterministic, auditable trading proposal payload for a future AI layer."""

    proposal_id: str
    instrument_id: int
    action: DecisionAction
    quantity: int = 0
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    target_price: Decimal | None = None
    confidence: Decimal = Decimal("0.5")
    rationale: str = ""
    timeframe: str = "1m"
    decision_timestamp: str | None = None
    submitted_at: str | None = None
    source: str = "TEST_FIXTURE"
    evidence_key: str | None = None
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not self.proposal_id.strip():
            raise ProposalValidationError("proposal_id must be non-empty text.")
        if (
            isinstance(self.instrument_id, bool)
            or not isinstance(self.instrument_id, int)
            or self.instrument_id < 1
        ):
            raise ProposalValidationError("instrument_id must be a positive integer.")
        try:
            action = (
                DecisionAction(self.action)
                if not isinstance(self.action, DecisionAction)
                else self.action
            )
        except ValueError as exc:
            raise ProposalValidationError("action must be BUY, SELL, or HOLD.") from exc
        if action not in (DecisionAction.BUY, DecisionAction.SELL, DecisionAction.HOLD):
            raise ProposalValidationError("action must be BUY, SELL, or HOLD.")
        object.__setattr__(self, "action", action)
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise ProposalValidationError("quantity must be an integer.")
        if action == DecisionAction.HOLD:
            if self.quantity != 0:
                raise ProposalValidationError("HOLD proposals must carry zero quantity.")
        else:
            if self.quantity < 1:
                raise ProposalValidationError("BUY and SELL proposals require a positive quantity.")
        if self.timeframe and not isinstance(self.timeframe, str):
            raise ProposalValidationError("timeframe must be text when supplied.")
        if self.timeframe not in {"1m", "5m", "15m", "1h", "1d"}:
            raise ProposalValidationError("Unsupported proposal timeframe.")
        for name in ("entry_price", "stop_loss", "target_price"):
            value = getattr(self, name)
            if value is None:
                continue
            decimal_value = _as_decimal(value, field=name)
            if name == "entry_price" and action != DecisionAction.HOLD and decimal_value <= 0:
                raise ProposalValidationError(
                    f"{name} must be a positive price when action is not HOLD."
                )
            if name in {"stop_loss", "target_price"} and decimal_value <= 0:
                raise ProposalValidationError(f"{name} must be a positive price.")
            object.__setattr__(self, name, decimal_value)
        if action != DecisionAction.HOLD:
            if self.entry_price is None:
                raise ProposalValidationError("entry_price is required for BUY/SELL proposals.")
            if self.stop_loss is not None:
                if action == DecisionAction.BUY and self.stop_loss >= self.entry_price:
                    raise ProposalValidationError("BUY stop_loss must be below entry_price.")
                if action == DecisionAction.SELL and self.stop_loss <= self.entry_price:
                    raise ProposalValidationError("SELL stop_loss must be above entry_price.")
            if self.target_price is not None:
                if action == DecisionAction.BUY and self.target_price <= self.entry_price:
                    raise ProposalValidationError("BUY target_price must be above entry_price.")
                if action == DecisionAction.SELL and self.target_price >= self.entry_price:
                    raise ProposalValidationError("SELL target_price must be below entry_price.")
        confidence = _as_decimal(self.confidence, field="confidence")
        if confidence < Decimal(0) or confidence > Decimal(1):
            raise ProposalValidationError("confidence must be between 0 and 1 inclusive.")
        object.__setattr__(self, "confidence", confidence)
        if self.rationale is not None and not isinstance(self.rationale, str):
            raise ProposalValidationError("rationale must be text when supplied.")
        if not isinstance(self.source, str) or not self.source:
            raise ProposalValidationError("source must be non-empty text.")
        if self.evidence_key is not None and not isinstance(self.evidence_key, str):
            raise ProposalValidationError("evidence_key must be text or None.")
        if self.decision_timestamp is not None:
            _validate_iso_timestamp(self.decision_timestamp, field="decision_timestamp")
        if self.submitted_at is not None:
            _validate_iso_timestamp(self.submitted_at, field="submitted_at")
            if self.decision_timestamp is not None and _parse_timestamp(
                self.submitted_at, field="submitted_at"
            ) < _parse_timestamp(self.decision_timestamp, field="decision_timestamp"):
                raise ProposalValidationError("submitted_at cannot precede decision_timestamp.")
        if self.expires_at is not None:
            _validate_iso_timestamp(self.expires_at, field="expires_at")
            expires = _parse_timestamp(self.expires_at, field="expires_at")
            if self.decision_timestamp is not None and expires <= _parse_timestamp(
                self.decision_timestamp, field="decision_timestamp"
            ):
                raise ProposalValidationError("expires_at must be later than decision_timestamp.")
            if self.submitted_at is not None and expires <= _parse_timestamp(
                self.submitted_at, field="submitted_at"
            ):
                raise ProposalValidationError("expires_at must be later than submitted_at.")

    @property
    def side(self) -> str:
        if self.action == DecisionAction.HOLD:
            return "HOLD"
        return self.action.value

    @property
    def order_side(self) -> str:
        return self.side


@dataclass(frozen=True, slots=True)
class ProposalValidationResult:
    """Outcome of deterministic validation before any paper order is submitted."""

    valid: bool
    reason: str | None = None
    normalized_action: DecisionAction | None = None
    evidence: MarketEvidence | None = None


@dataclass(frozen=True, slots=True)
class ProposalExecutionResult:
    """Result of routing a decision proposal to the deterministic paper engine."""

    proposal_id: str
    status: Literal["HOLD", "REJECTED", "FILLED"]
    order_id: str | None = None
    reason: str | None = None
    fill_price: Decimal | None = None
    duplicate: bool = False


AITradingProposal = DecisionProposal
ForwardTradingProposal = DecisionProposal
PaperDecisionProposal = DecisionProposal


def _as_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ProposalValidationError(f"{field} must be a decimal or numeric value, not a boolean.")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, float):
        if not isfinite(value):
            raise ProposalValidationError(f"{field} must be finite.")
        parsed = Decimal(str(value))
    elif isinstance(value, str):
        cleaned = value.strip()
        try:
            parsed = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ProposalValidationError(f"{field} must be a valid decimal value.") from exc
    else:
        raise ProposalValidationError(f"{field} must be a numeric or decimal value.")
    if not parsed.is_finite():
        raise ProposalValidationError(f"{field} must be finite.")
    return parsed


def _validate_iso_timestamp(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProposalValidationError(f"{field} must be non-empty ISO 8601 text.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProposalValidationError(f"{field} must be a valid ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProposalValidationError(f"{field} must include a timezone.")
    return value


def _parse_timestamp(value: str, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProposalValidationError(f"{field} must be non-empty ISO 8601 text.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProposalValidationError(f"{field} must be a valid ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProposalValidationError(f"{field} must include a timezone.")
    return parsed.astimezone(UTC)


def _proposal_canonical_json(proposal: DecisionProposal) -> str:
    """Serialize proposal v1 with UTC timestamps retaining all six fractional digits."""

    def decimal_value(value: Decimal | None) -> str | None:
        if value is None:
            return None
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text

    def timestamp_value(value: str | None, field: str) -> str | None:
        if value is None:
            return None
        timestamp = _parse_timestamp(value, field=field)
        return timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")

    payload = {
        "canonicalization_version": "aegis.decision-proposal/v1",
        "proposal_id": proposal.proposal_id,
        "instrument_id": proposal.instrument_id,
        "action": proposal.action.value,
        "quantity": proposal.quantity,
        "entry_price": decimal_value(proposal.entry_price),
        "stop_loss": decimal_value(proposal.stop_loss),
        "target_price": decimal_value(proposal.target_price),
        "confidence": decimal_value(proposal.confidence),
        "rationale": proposal.rationale,
        "timeframe": proposal.timeframe,
        "decision_timestamp": timestamp_value(
            proposal.decision_timestamp, "proposal decision timestamp"
        ),
        "submitted_at": timestamp_value(proposal.submitted_at, "proposal submitted_at"),
        "source": proposal.source,
        "evidence_key": proposal.evidence_key,
        "expires_at": timestamp_value(proposal.expires_at, "proposal expires_at"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _proposal_sha256(proposal: DecisionProposal) -> str:
    return hashlib.sha256(_proposal_canonical_json(proposal).encode("utf-8")).hexdigest()


def _is_forward_runtime_import(database_path: Path, import_id: int) -> bool:
    with closing(get_readonly_connection(database_path)) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM forward_runtime_imports WHERE import_id = ?", (import_id,)
            ).fetchone()
            is not None
        )


def _runtime_evidence_id(
    runtime_id: str,
    instrument_id: int,
    interval: str,
    import_id: int,
    candle_timestamp: str,
    as_of: str,
) -> str:
    as_of_time = _parse_timestamp(as_of, field="evidence as_of")
    value = (
        f"evidence:{runtime_id}:{instrument_id}:{interval}:{import_id}:"
        f"{candle_timestamp}:"
        f"{as_of_time.isoformat(timespec='seconds').replace('+00:00', 'Z')}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _assert_verified_permitted_import(
    database_path: Path,
    *,
    instrument_id: int,
    interval: str,
    import_id: int | None,
    as_of: str,
) -> None:
    if import_id is None:
        raise ProposalValidationError(
            "Evidence does not include a verified, permitted source import reference."
        )
    as_of_time = _parse_timestamp(as_of, field="evidence as_of")
    with closing(get_readonly_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT instrument_id, interval, status, accepted_rows, source_use_status,
                   file_sha256, completed_at
            FROM data_imports WHERE id = ?
            """,
            (import_id,),
        ).fetchone()
    if row is None:
        raise ProposalValidationError("Evidence references a source import that does not exist.")
    if row["instrument_id"] != instrument_id:
        raise ProposalValidationError(
            "Evidence source import does not match the proposal instrument."
        )
    if (row["interval"] or "").lower() != interval.strip().lower():
        raise ProposalValidationError(
            "Evidence source import interval does not match the proposal timeframe."
        )
    if row["status"] != "COMPLETED":
        raise ProposalValidationError("Evidence source import is not completed.")
    if int(row["accepted_rows"] or 0) < 1:
        raise ProposalValidationError("Evidence source import has no accepted candle rows.")
    if row["source_use_status"] != "VERIFIED_PERMITTED":
        raise ProposalValidationError(
            "Evidence source import is not explicitly VERIFIED_PERMITTED for forward-paper routing."
        )
    if not row["file_sha256"]:
        raise ProposalValidationError(
            "Evidence source import is missing a file fingerprint required for provenance."
        )
    if not row["completed_at"]:
        raise ProposalValidationError("Evidence source import has no completion timestamp.")
    completed_at = _parse_timestamp(str(row["completed_at"]), field="import completion timestamp")
    if completed_at > as_of_time:
        raise ProposalValidationError("Evidence source import completed after its as-of time.")


def _resolve_calendar_registry(
    calendars: CalendarRegistry | None = None,
) -> CalendarRegistry:
    try:
        return load_approved_calendar_registry(expected_registry=calendars)
    except CalendarConfigError as exc:
        raise ForwardTradingError(f"Calendar configuration is invalid: {exc}") from exc


def build_mock_proposal(
    *,
    instrument_id: int,
    action: str | DecisionAction,
    quantity: int,
    entry_price: Decimal | str | int,
    stop_loss: Decimal | str | int | None = None,
    target_price: Decimal | str | int | None = None,
    proposal_id: str | None = None,
    confidence: Decimal | float = Decimal("0.8"),
    rationale: str = "Synthetic deterministic fixture; this proposal is for local validation only.",
    decision_timestamp: str | None = None,
    submitted_at: str | None = None,
    expires_at: str | None = None,
    source: str = "TEST_FIXTURE",
) -> DecisionProposal:
    """Create a deterministic proposal fixture for isolated forward-paper validation."""
    if not isinstance(instrument_id, int) or instrument_id < 1:
        raise ForwardTradingError("instrument_id must be a positive integer.")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise ForwardTradingError("quantity must be a non-negative integer.")
    normalized_action = DecisionAction(action)
    when = decision_timestamp or datetime.now(UTC).isoformat()
    submitted = submitted_at or when
    expiry_base = max(
        _parse_timestamp(when, field="decision_timestamp"),
        _parse_timestamp(submitted, field="submitted_at"),
    )
    expiry = expires_at or (expiry_base + timedelta(minutes=1)).isoformat()
    normalized_entry = _as_decimal(entry_price, field="entry_price")
    normalized_stop = _as_decimal(stop_loss, field="stop_loss") if stop_loss is not None else None
    normalized_target = (
        _as_decimal(target_price, field="target_price") if target_price is not None else None
    )
    normalized_confidence = _as_decimal(confidence, field="confidence")
    return DecisionProposal(
        proposal_id=proposal_id or f"proposal-{instrument_id}-{normalized_action.value}-{quantity}",
        instrument_id=instrument_id,
        action=normalized_action,
        quantity=quantity,
        entry_price=normalized_entry,
        stop_loss=normalized_stop,
        target_price=normalized_target,
        confidence=normalized_confidence,
        rationale=rationale,
        timeframe="1m",
        decision_timestamp=when,
        submitted_at=submitted,
        expires_at=expiry,
        source=source,
    )


def _select_reference_price(
    database_path: Path,
    instrument_id: int,
    interval: str,
    import_id: int | None = None,
    candle_timestamp: str | None = None,
) -> tuple[Decimal, str | None, int | None]:
    with closing(get_readonly_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT c.close_decimal, c.candle_timestamp, c.import_id
            FROM historical_candles AS c
            WHERE c.instrument_id = ? AND c.interval = ?
              AND (? IS NULL OR c.import_id = ?)
              AND (? IS NULL OR c.candle_timestamp = ?)
            ORDER BY c.candle_timestamp DESC
            LIMIT 1
            """,
            (
                instrument_id,
                interval.strip().lower(),
                import_id,
                import_id,
                candle_timestamp,
                candle_timestamp,
            ),
        ).fetchone()
    if row is None:
        raise ForwardTradingError("No candlesticks are available for evidence creation.")
    close_decimal = row["close_decimal"]
    if not isinstance(close_decimal, str):
        raise ForwardTradingError("Latest candle close is missing or malformed.")
    parsed = Decimal(close_decimal)
    import_id = row["import_id"]
    return parsed, str(row["candle_timestamp"]), int(import_id) if import_id is not None else None


def _instrument_summary(database_path: Path, instrument_id: int) -> tuple[str, str]:
    with closing(get_readonly_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT exchange, trading_symbol FROM instruments WHERE id = ?",
            (instrument_id,),
        ).fetchone()
    if row is None:
        raise ForwardTradingError(f"Instrument {instrument_id} does not exist.")
    return str(row["exchange"]), str(row["trading_symbol"])


class ForwardPaperTradingEngine:
    """Synthetic routing engine for proposal validation and paper execution."""

    def __init__(
        self,
        database_path: Path,
        account_id: int,
        *,
        risk_limits: RiskLimits | None = None,
        calendars: CalendarRegistry | None = None,
        max_execution_price_deviation: Decimal | None = None,
    ) -> None:
        if not isinstance(database_path, Path):
            database_path = Path(database_path)
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id < 1:
            raise ForwardTradingError("account_id must be a positive integer.")
        self.database_path = database_path
        self.account_id = account_id
        self.risk_limits = risk_limits
        self.calendars = calendars
        if max_execution_price_deviation is not None and (
            not isinstance(max_execution_price_deviation, Decimal)
            or not max_execution_price_deviation.is_finite()
            or max_execution_price_deviation < 0
        ):
            raise ForwardTradingError(
                "max_execution_price_deviation must be a non-negative finite Decimal."
            )
        self.max_execution_price_deviation = max_execution_price_deviation

    def build_market_evidence(
        self,
        instrument_id: int,
        *,
        interval: str = "1m",
        as_of: datetime | str | None = None,
        calendars: CalendarRegistry | None = None,
        source_import_id: int | None = None,
        source_candle_timestamp: str | None = None,
        runtime_id: str | None = None,
        evidence_id: str | None = None,
        scheduler_event_id: str | None = None,
        proposal_id: str | None = None,
    ) -> MarketEvidence:
        exchange, trading_symbol = _instrument_summary(self.database_path, instrument_id)
        as_of_value = as_of or datetime.now(UTC)
        if isinstance(as_of_value, str):
            as_of_dt = _parse_timestamp(as_of_value, field="evidence as_of")
        else:
            if as_of_value.tzinfo is None or as_of_value.utcoffset() is None:
                raise ProposalValidationError("evidence as_of must include a timezone.")
            as_of_dt = as_of_value.astimezone(UTC)
        if as_of_dt > datetime.now(UTC):
            raise ProposalValidationError("Evidence cannot be future-dated.")
        reference_price, latest_ts, selected_import_id = _select_reference_price(
            self.database_path,
            instrument_id,
            interval,
            source_import_id,
            source_candle_timestamp,
        )
        if selected_import_id is None:
            raise ForwardTradingError("Market evidence has no exact source import identity.")
        evidence_import_id = source_import_id or selected_import_id
        report = analyze_dataset(
            self.database_path,
            exchange,
            trading_symbol,
            interval,
            as_of_dt.date(),
            as_of_dt.date(),
            as_of=as_of_dt,
            calendars=_resolve_calendar_registry(calendars or self.calendars),
            source_import_id=evidence_import_id,
        )
        if not report.actual_candles and not report.expected_candles:
            raise ForwardTradingError(
                f"No historical candle evidence is available for {exchange}:{trading_symbol} on {interval}."
            )
        if source_import_id is not None:
            _assert_verified_permitted_import(
                self.database_path,
                instrument_id=instrument_id,
                interval=interval,
                import_id=source_import_id,
                as_of=as_of_dt.isoformat(),
            )
        score = Decimal(0)
        if report.overall_status == "COMPLETE":
            score += Decimal(50)
        if report.calendar_status == "FULL":
            score += Decimal(25)
        if report.staleness_status == "FRESH":
            score += Decimal(15)
        if report.completeness_percentage is not None:
            score += report.completeness_percentage
        return MarketEvidence(
            instrument_id=instrument_id,
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            as_of=as_of_dt.isoformat(),
            reference_price=reference_price,
            quality=report,
            source_import_id=evidence_import_id,
            latest_candle_timestamp=latest_ts,
            candidate_score=score,
            market_regime="NORMAL",
            evidence_notes=tuple(report.reasons),
            runtime_id=runtime_id,
            evidence_id=evidence_id,
            scheduler_event_id=scheduler_event_id,
            proposal_id=proposal_id,
        )

    def select_candidates(
        self,
        instrument_ids: list[int],
        *,
        interval: str = "1m",
        as_of: datetime | str | None = None,
        calendars: CalendarRegistry | None = None,
    ) -> list[MarketCandidate]:
        candidates: list[MarketCandidate] = []
        for instrument_id in instrument_ids:
            evidence = self.build_market_evidence(
                instrument_id,
                interval=interval,
                as_of=as_of,
                calendars=calendars,
            )
            rationale = (
                f"overall_status={evidence.quality.overall_status}, "
                f"calendar_status={evidence.quality.calendar_status}, "
                f"staleness_status={evidence.quality.staleness_status}"
            )
            candidates.append(
                MarketCandidate(
                    evidence=evidence, score=evidence.candidate_score, rationale=rationale
                )
            )
        return sorted(candidates, key=lambda item: item.score, reverse=True)

    def validate_decision_proposal(
        self,
        proposal: DecisionProposal,
        evidence: MarketEvidence,
        *,
        evaluation_time: str | datetime | None = None,
    ) -> ProposalValidationResult:
        if not isinstance(proposal, DecisionProposal):
            raise ProposalValidationError("proposal must be a DecisionProposal.")
        if isinstance(evaluation_time, datetime):
            if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
                raise ProposalValidationError("evaluation_time must include a timezone.")
            current_time = evaluation_time.astimezone(UTC)
        elif evaluation_time is not None:
            current_time = _parse_timestamp(evaluation_time, field="evaluation_time")
        else:
            current_time = datetime.now(UTC)
        evidence_as_of = _parse_timestamp(evidence.as_of, field="evidence as_of")
        if proposal.instrument_id != evidence.instrument_id:
            return ProposalValidationResult(
                valid=False,
                reason="Proposal instrument does not match the supplied market evidence.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if proposal.timeframe != evidence.interval:
            return ProposalValidationResult(
                valid=False,
                reason="Proposal timeframe does not match the supplied market evidence interval.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if proposal.action == DecisionAction.HOLD:
            return ProposalValidationResult(
                valid=True,
                reason="HOLD decisions do not create paper orders.",
                normalized_action=DecisionAction.HOLD,
                evidence=evidence,
            )
        if evidence_as_of > current_time:
            return ProposalValidationResult(
                valid=False,
                reason="Market evidence is future-dated relative to the evaluation time.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if evidence.quality.overall_status != "COMPLETE":
            return ProposalValidationResult(
                valid=False,
                reason="Market evidence is incomplete; the proposal cannot be routed.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if (
            evidence.quality.calendar_status != "FULL"
            or evidence.quality.calendar_confidence != "CONFIRMED"
        ):
            return ProposalValidationResult(
                valid=False,
                reason="Confirmed calendar coverage is required before a proposal is routed.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if evidence.quality.staleness_status != "FRESH":
            return ProposalValidationResult(
                valid=False,
                reason="Stale evidence is not valid for forward-paper routing.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if proposal.entry_price is None:
            return ProposalValidationResult(
                valid=False,
                reason="BUY/SELL proposals must include an explicit entry price.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if proposal.entry_price <= Decimal(0):
            return ProposalValidationResult(
                valid=False,
                reason="entry_price must be positive.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if proposal.expires_at is None:
            return ProposalValidationResult(
                valid=False,
                reason="BUY/SELL proposals require an explicit timezone-aware expiry.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        expires_at = _parse_timestamp(proposal.expires_at, field="expires_at")
        if expires_at <= current_time:
            return ProposalValidationResult(
                valid=False,
                reason="Proposal has expired and cannot be routed.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        confidence = proposal.confidence
        if confidence < Decimal(0) or confidence > Decimal(1):
            return ProposalValidationResult(
                valid=False,
                reason="confidence must be between 0 and 1 inclusive.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if proposal.stop_loss is not None and proposal.stop_loss <= Decimal(0):
            return ProposalValidationResult(
                valid=False,
                reason="stop_loss must be positive when supplied.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if proposal.target_price is not None and proposal.target_price <= Decimal(0):
            return ProposalValidationResult(
                valid=False,
                reason="target_price must be positive when supplied.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if evidence.source_import_id is None:
            return ProposalValidationResult(
                valid=False,
                reason="Evidence does not include a source import reference for provenance validation.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        try:
            _assert_verified_permitted_import(
                self.database_path,
                instrument_id=proposal.instrument_id,
                interval=evidence.interval,
                import_id=evidence.source_import_id,
                as_of=evidence.as_of,
            )
        except ProposalValidationError as exc:
            return ProposalValidationResult(
                valid=False,
                reason=str(exc),
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if (
            proposal.action == DecisionAction.SELL
            and proposal.stop_loss is not None
            and proposal.stop_loss <= proposal.entry_price
        ):
            return ProposalValidationResult(
                valid=False,
                reason="SELL stop_loss must be above the entry price.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        if (
            proposal.action == DecisionAction.SELL
            and proposal.target_price is not None
            and proposal.target_price >= proposal.entry_price
        ):
            return ProposalValidationResult(
                valid=False,
                reason="SELL target_price must be below the entry price.",
                normalized_action=proposal.action,
                evidence=evidence,
            )
        return ProposalValidationResult(
            valid=True,
            reason=None,
            normalized_action=proposal.action,
            evidence=evidence,
        )

    def route_proposal(
        self,
        proposal: DecisionProposal,
        evidence: MarketEvidence | None = None,
        *,
        execution_snapshot: ExecutionSnapshot | None = None,
        evaluation_time: str | datetime | None = None,
    ) -> ProposalExecutionResult:
        """Route generic paper proposals; runtime evidence must use route_event()."""
        return self._route_proposal(
            proposal,
            evidence,
            execution_snapshot=execution_snapshot,
            evaluation_time=evaluation_time,
            runtime_id=None,
            forward_event_id=None,
        )

    def _route_runtime_proposal(
        self,
        proposal: DecisionProposal,
        evidence: MarketEvidence,
        *,
        execution_snapshot: ExecutionSnapshot | None,
        runtime_id: str,
        event_id: str,
        evaluation_time: str | datetime,
    ) -> ProposalExecutionResult:
        """Route a proposal after binding it to one persisted runtime event."""
        with closing(get_readonly_connection(self.database_path)) as connection:
            event = connection.execute(
                """SELECT status, proposal_id, proposal_sha256, proposal_json
                   FROM forward_scheduler_events
                   WHERE event_id = ? AND runtime_id = ?""",
                (event_id, runtime_id),
            ).fetchone()
        if event is not None and event["status"] == "FILLED":
            canonical = _proposal_canonical_json(proposal)
            if (
                event["proposal_id"] != proposal.proposal_id
                or event["proposal_json"] != canonical
                or event["proposal_sha256"] != hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                or execution_snapshot is None
            ):
                return ProposalExecutionResult(
                    proposal.proposal_id,
                    "REJECTED",
                    reason="Proposal content does not match the filled runtime event.",
                )
            if proposal.action == DecisionAction.BUY:
                side: Literal["BUY", "SELL"] = "BUY"
            elif proposal.action == DecisionAction.SELL:
                side = "SELL"
            else:
                return ProposalExecutionResult(
                    proposal.proposal_id,
                    "REJECTED",
                    reason="A filled runtime event cannot be replayed as a HOLD proposal.",
                )
            request = PaperOrderRequest(
                proposal.instrument_id,
                side,
                proposal.quantity,
                proposal.decision_timestamp or "",
                proposal.submitted_at or proposal.decision_timestamp or "",
            )
            try:
                duplicate = submit_paper_order(
                    self.database_path,
                    self.account_id,
                    proposal.proposal_id,
                    request,
                    execution_snapshot,
                    forward_event_id=event_id,
                )
            except PaperTradingError as exc:
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=str(exc))
            return ProposalExecutionResult(
                proposal.proposal_id,
                duplicate.status,
                order_id=duplicate.order_id,
                reason=duplicate.reason,
                fill_price=(
                    execution_snapshot.reference_price if duplicate.status == "FILLED" else None
                ),
                duplicate=True,
            )
        return self._route_proposal(
            proposal,
            evidence,
            execution_snapshot=execution_snapshot,
            evaluation_time=evaluation_time,
            runtime_id=runtime_id,
            forward_event_id=event_id,
        )

    def _route_proposal(
        self,
        proposal: DecisionProposal,
        evidence: MarketEvidence | None,
        *,
        execution_snapshot: ExecutionSnapshot | None,
        evaluation_time: str | datetime | None,
        runtime_id: str | None,
        forward_event_id: str | None,
    ) -> ProposalExecutionResult:
        if not isinstance(proposal, DecisionProposal):
            raise ProposalValidationError("proposal must be a DecisionProposal instance.")
        if isinstance(evaluation_time, datetime):
            if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
                raise ProposalValidationError("evaluation_time must include a timezone.")
            evaluation_time = evaluation_time.astimezone(UTC)
        elif isinstance(evaluation_time, str):
            evaluation_time = _parse_timestamp(evaluation_time, field="evaluation_time")
        else:
            evaluation_time = datetime.now(UTC)
        if evidence is None:
            if forward_event_id is not None:
                return ProposalExecutionResult(
                    proposal_id=proposal.proposal_id,
                    status="REJECTED",
                    reason="Forward event routing requires event-bound runtime evidence.",
                )
            evidence = self.build_market_evidence(
                proposal.instrument_id, interval=proposal.timeframe
            )
        if forward_event_id is not None:
            if runtime_id is None:
                return ProposalExecutionResult(
                    proposal.proposal_id,
                    "REJECTED",
                    reason="Forward event routing requires a persisted runtime identity.",
                )
            try:
                self._validate_runtime_evidence(
                    proposal,
                    evidence,
                    runtime_id=runtime_id,
                    event_id=forward_event_id,
                    evaluation_time=evaluation_time,
                )
            except ProposalValidationError as exc:
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=str(exc))
        validation = self.validate_decision_proposal(
            proposal, evidence, evaluation_time=evaluation_time
        )
        if not validation.valid:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason=validation.reason,
            )
        if forward_event_id is None:
            if evidence.runtime_id is not None or evidence.scheduler_event_id is not None:
                return ProposalExecutionResult(
                    proposal.proposal_id,
                    "REJECTED",
                    reason="Runtime evidence must be routed through its persisted scheduler event.",
                )
            if evidence.source_import_id is not None and _is_forward_runtime_import(
                self.database_path, evidence.source_import_id
            ):
                return ProposalExecutionResult(
                    proposal.proposal_id,
                    "REJECTED",
                    reason="Forward-runtime source imports must be routed through route_event().",
                )
        elif (
            evidence.runtime_id != runtime_id
            or evidence.scheduler_event_id != forward_event_id
            or evidence.proposal_id != proposal.proposal_id
        ):
            return ProposalExecutionResult(
                proposal.proposal_id,
                "REJECTED",
                reason="Evidence is not bound to this runtime, scheduler event, and proposal.",
            )
        proposal_entry_price = proposal.entry_price
        if proposal.action != DecisionAction.HOLD and proposal_entry_price is None:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="BUY/SELL proposals must include an explicit entry price.",
            )
        if proposal.action == DecisionAction.HOLD:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="HOLD",
                reason="No order created for HOLD decision.",
            )
        if execution_snapshot is None:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="An independently validated execution snapshot is required for BUY/SELL routing.",
            )
        if execution_snapshot.instrument_id != proposal.instrument_id:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="Execution snapshot instrument does not match the proposal instrument.",
            )
        if execution_snapshot.interval != evidence.interval:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="Execution snapshot interval does not match the proposal evidence interval.",
            )
        if execution_snapshot.reference_price <= 0:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="Execution snapshot reference_price must be positive.",
            )
        snapshot_as_of = _parse_timestamp(
            execution_snapshot.as_of, field="execution_snapshot.as_of"
        )
        if snapshot_as_of > evaluation_time:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="Execution snapshot is future-dated.",
            )
        if (
            execution_snapshot.quality.overall_status != "COMPLETE"
            or execution_snapshot.quality.staleness_status != "FRESH"
        ):
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="Execution snapshot does not satisfy the market-quality and freshness gates.",
            )
        try:
            _assert_verified_permitted_import(
                self.database_path,
                instrument_id=proposal.instrument_id,
                interval=execution_snapshot.interval,
                import_id=execution_snapshot.import_id,
                as_of=execution_snapshot.as_of,
            )
        except ProposalValidationError as exc:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason=str(exc),
            )
        if forward_event_id is not None:
            exchange, trading_symbol = _instrument_summary(
                self.database_path, proposal.instrument_id
            )
            independently_checked_quality = analyze_dataset(
                self.database_path,
                exchange,
                trading_symbol,
                execution_snapshot.interval,
                snapshot_as_of.astimezone(_INDIA).date(),
                snapshot_as_of.astimezone(_INDIA).date(),
                as_of=snapshot_as_of,
                calendars=_resolve_calendar_registry(self.calendars),
                source_import_id=execution_snapshot.import_id,
            )
            if independently_checked_quality != execution_snapshot.quality:
                return ProposalExecutionResult(
                    proposal_id=proposal.proposal_id,
                    status="REJECTED",
                    reason="Execution snapshot quality evidence does not match the stored dataset.",
                )
        requested_timestamp = proposal.decision_timestamp or evidence.as_of
        execution_time = _parse_timestamp(
            execution_snapshot.timestamp, field="execution_snapshot.timestamp"
        )
        if execution_time < _parse_timestamp(requested_timestamp, field="proposal timestamp"):
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="Execution snapshot predates the proposal decision timestamp.",
            )
        if proposal.action == DecisionAction.BUY and self.max_execution_price_deviation is None:
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="BUY routing requires an explicit execution-price deviation limit.",
            )
        if (
            self.max_execution_price_deviation is not None
            and proposal_entry_price is not None
            and abs(execution_snapshot.reference_price - proposal_entry_price)
            > self.max_execution_price_deviation
        ):
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason="Execution price exceeds the configured proposal deviation limit.",
            )
        order_request = PaperOrderRequest(
            instrument_id=proposal.instrument_id,
            side=proposal.action.value,
            quantity=proposal.quantity,
            signal_timestamp=requested_timestamp,
            submitted_timestamp=proposal.submitted_at or requested_timestamp,
        )
        authorization_error: str | None = None
        if forward_event_id is not None:
            connection = get_connection(self.database_path)
            try:
                connection.execute("BEGIN IMMEDIATE")
                reason = _forward_context_error(
                    connection,
                    self.account_id,
                    forward_event_id,
                    proposal.proposal_id,
                    order_request,
                    execution_snapshot,
                    require_authorization=False,
                )
                if reason is not None:
                    raise PaperTradingError(reason)
                event = connection.execute(
                    """SELECT runtime_id, proposal_id, proposal_sha256,
                              instrument_id, interval
                       FROM forward_scheduler_events WHERE event_id = ?""",
                    (forward_event_id,),
                ).fetchone()
                quote = connection.execute(
                    """SELECT quote_timestamp, received_at FROM forward_quotes
                       WHERE runtime_id = ? AND instrument_id = ? AND event_id = ?
                         AND accepted = 1""",
                    (
                        event["runtime_id"],
                        proposal.instrument_id,
                        execution_snapshot.quote_event_id,
                    ),
                ).fetchone()
                authorized_at = (
                    _parse_timestamp(execution_snapshot.as_of, field="snapshot as_of")
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
                connection.execute(
                    """INSERT INTO forward_execution_authorizations
                       (event_id, runtime_id, account_id, proposal_id, proposal_sha256,
                        authorization_kind, instrument_id, interval, snapshot_id,
                        snapshot_sha256, quote_event_id, quote_timestamp, received_at,
                        import_id, status, authorized_at)
                       VALUES (?, ?, ?, ?, ?, 'PROPOSAL', ?, ?, ?, ?, ?, ?, ?, ?,
                               'AUTHORIZED', ?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    (
                        forward_event_id,
                        event["runtime_id"],
                        self.account_id,
                        event["proposal_id"],
                        event["proposal_sha256"],
                        event["instrument_id"],
                        event["interval"],
                        execution_snapshot.snapshot_id,
                        execution_snapshot.snapshot_sha256,
                        execution_snapshot.quote_event_id,
                        quote["quote_timestamp"],
                        quote["received_at"],
                        execution_snapshot.import_id,
                        authorized_at,
                    ),
                )
                connection.commit()
            except PaperTradingError as exc:
                if connection.in_transaction:
                    connection.rollback()
                authorization_error = str(exc)
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()
        try:
            result = submit_paper_order(
                self.database_path,
                self.account_id,
                proposal.proposal_id,
                order_request,
                execution_snapshot,
                forward_event_id=forward_event_id,
            )
        except PaperTradingError as exc:
            if forward_event_id is None:
                raise
            return ProposalExecutionResult(
                proposal_id=proposal.proposal_id,
                status="REJECTED",
                reason=authorization_error or str(exc),
            )
        return ProposalExecutionResult(
            proposal_id=proposal.proposal_id,
            status=result.status,
            order_id=result.order_id,
            reason=result.reason,
            fill_price=execution_snapshot.reference_price if result.status == "FILLED" else None,
            duplicate=result.duplicate,
        )

    def _validate_runtime_evidence(
        self,
        proposal: DecisionProposal,
        evidence: MarketEvidence,
        *,
        runtime_id: str,
        event_id: str,
        evaluation_time: str | datetime,
    ) -> None:
        evaluation = (
            evaluation_time
            if isinstance(evaluation_time, datetime)
            else _parse_timestamp(evaluation_time, field="evaluation_time")
        )
        if evaluation.tzinfo is None or evaluation.utcoffset() is None:
            raise ProposalValidationError("evaluation_time must include a timezone.")
        evaluation = evaluation.astimezone(UTC)
        if proposal.expires_at is None:
            raise ProposalValidationError("Forward-runtime proposals require an explicit expiry.")
        if _parse_timestamp(proposal.expires_at, field="expires_at") <= evaluation:
            raise ProposalValidationError(
                "Proposal has expired at the authoritative evaluation time."
            )
        with closing(get_readonly_connection(self.database_path)) as connection:
            row = connection.execute(
                """SELECT e.instrument_id, e.interval, e.candle_timestamp,
                          e.decision_timestamp, e.status, e.proposal_id,
                          e.proposal_sha256, e.proposal_json,
                          r.account_id, r.status AS runtime_status,
                          ri.import_id, d.import_type, d.status AS import_status,
                          d.source_use_status, d.file_sha256, d.completed_at,
                          d.accepted_rows, d.metadata_json,
                          c.completed_at AS candle_completed_at,
                          c.close_decimal, h.close_decimal AS imported_close
                   FROM forward_scheduler_events e
                   JOIN forward_runtimes r ON r.runtime_id = e.runtime_id
                   JOIN forward_runtime_imports ri
                     ON ri.runtime_id = e.runtime_id
                    AND ri.instrument_id = e.instrument_id AND ri.interval = e.interval
                   JOIN data_imports d ON d.id = ri.import_id
                   JOIN forward_runtime_candles c
                     ON c.runtime_id = e.runtime_id AND c.instrument_id = e.instrument_id
                    AND c.interval = e.interval AND c.candle_timestamp = e.candle_timestamp
                   JOIN historical_candles h
                     ON h.import_id = ri.import_id AND h.instrument_id = e.instrument_id
                    AND h.interval = e.interval AND h.candle_timestamp = e.candle_timestamp
                   WHERE e.event_id = ? AND e.runtime_id = ?""",
                (event_id, runtime_id),
            ).fetchone()
        if (
            row is None
            or row["status"] != "PENDING"
            or row["runtime_status"] not in ("RUNNING", "DEGRADED")
            or row["account_id"] != self.account_id
            or row["proposal_id"] != proposal.proposal_id
            or row["proposal_sha256"] != _proposal_sha256(proposal)
            or row["proposal_json"] != _proposal_canonical_json(proposal)
            or row["instrument_id"] != proposal.instrument_id
            or row["interval"] != proposal.timeframe
            or evidence.instrument_id != row["instrument_id"]
            or evidence.interval != row["interval"]
            or evidence.source_import_id != row["import_id"]
            or evidence.runtime_id != runtime_id
            or evidence.scheduler_event_id != event_id
            or evidence.proposal_id != proposal.proposal_id
            or evidence.latest_candle_timestamp != row["candle_timestamp"]
            or row["close_decimal"] != row["imported_close"]
            or row["import_type"] != "SYNTHETIC_STREAM"
            or row["import_status"] != "COMPLETED"
            or row["source_use_status"] != "VERIFIED_PERMITTED"
            or not row["file_sha256"]
            or int(row["accepted_rows"] or 0) < 1
        ):
            raise ProposalValidationError(
                "Runtime evidence does not match the persisted event, proposal, instrument, "
                "interval, and permitted synthetic import."
            )
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProposalValidationError(
                "Runtime source provenance metadata is malformed."
            ) from exc
        if (
            not isinstance(metadata, dict)
            or metadata.get("runtime_id") != runtime_id
            or metadata.get("provider") != "DETERMINISTIC_SYNTHETIC"
            or metadata.get("source_use_rationale") != "Generated in-process synthetic data."
        ):
            raise ProposalValidationError("Runtime source origin is not deterministic synthetic.")
        if evidence.evidence_id != _runtime_evidence_id(
            runtime_id,
            proposal.instrument_id,
            proposal.timeframe,
            int(row["import_id"]),
            str(row["candle_timestamp"]),
            evidence.as_of,
        ):
            raise ProposalValidationError("Runtime evidence identity is unverifiable.")
        decision_time = _parse_timestamp(row["decision_timestamp"], field="event decision time")
        if (
            _parse_timestamp(evidence.as_of, field="evidence as_of") != decision_time
            or evaluation < decision_time
            or _parse_timestamp(row["completed_at"], field="import completion time") > decision_time
            or _parse_timestamp(row["candle_completed_at"], field="candle completion time")
            > decision_time
        ):
            raise ProposalValidationError(
                "Runtime evidence is future-dated or unavailable at the event decision time."
            )


PaperDecisionRouter = ForwardPaperTradingEngine


def create_test_fixture(
    database_path: Path,
    *,
    instrument_id: int,
    account_id: int,
    interval: str = "1m",
    as_of: datetime | str | None = None,
) -> tuple[MarketEvidence, DecisionProposal, ForwardPaperTradingEngine]:
    """Create a deterministic, valid market-evidence bundle and proposal for synthetic tests."""
    engine = ForwardPaperTradingEngine(database_path, account_id)
    evidence = engine.build_market_evidence(instrument_id, interval=interval, as_of=as_of)
    proposal = build_mock_proposal(
        instrument_id=instrument_id,
        action="BUY",
        quantity=1,
        entry_price=evidence.reference_price,
        proposal_id=f"fixture-{instrument_id}-buy",
        decision_timestamp=evidence.as_of,
        submitted_at=evidence.as_of,
    )
    return evidence, proposal, engine


def reject_if_missing_risk_limits(candidate: MarketCandidate, risk_limits: RiskLimits) -> None:
    """Validation helper for research or dashboard workflows that expect deterministic risk gating."""
    if not isinstance(risk_limits, RiskLimits):
        raise ForwardTradingError("risk_limits must be a RiskLimits instance.")
    if (
        candidate.evidence.reference_price * Decimal(candidate.evidence.quality.actual_candles)
        > risk_limits.max_order_value
    ):
        raise ProposalValidationError("Candidate evidence exceeds account order-value risk limits.")


__all__ = [
    "AITradingProposal",
    "DecisionAction",
    "DecisionProposal",
    "ForwardPaperTradingEngine",
    "ForwardTradingError",
    "ForwardTradingProposal",
    "MarketCandidate",
    "MarketEvidence",
    "PaperDecisionProposal",
    "PaperDecisionRouter",
    "ProposalExecutionResult",
    "ProposalValidationError",
    "ProposalValidationResult",
    "build_mock_proposal",
    "create_test_fixture",
    "reject_if_missing_risk_limits",
]
