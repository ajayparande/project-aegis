"""Versioned common proposal contract for deterministic research proposers."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

SUPPORTED_PROPOSAL_CONTRACT_VERSIONS = frozenset({"1.0.0"})
PROPOSAL_CONTRACT_VERSION = "1.0.0"


class ProposalDecision(str, Enum):
    """Valid action emitted by a research proposer.

    SELL proposes reducing or closing an existing long position. It never
    proposes opening a short position, and does not establish that an eligible
    long position exists or that the proposal can execute.
    """

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


def _as_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        candidate = Decimal(str(value))
    elif isinstance(value, str):
        try:
            candidate = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{field_name} must be a valid decimal value.") from exc
    else:
        raise TypeError(f"{field_name} must be a decimal value.")
    if not candidate.is_finite():
        raise ValueError(f"{field_name} must be finite.")
    return candidate


def _parse_iso8601(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string ISO-8601 timestamp.")
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 timestamp.")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include an explicit timezone offset.")
    return parsed.astimezone(UTC)


def _normalize_instrument(instrument: Any) -> str:
    if not isinstance(instrument, str):
        raise TypeError("instrument must be a non-empty exchange:symbol string.")
    candidate = instrument.strip()
    if not candidate or ":" not in candidate:
        raise ValueError("instrument must be in exchange:symbol format, e.g. 'NSE:RELIANCE'.")
    exchange, symbol = candidate.split(":", 1)
    if not exchange.strip() or not symbol.strip():
        raise ValueError("instrument must include both exchange and symbol values.")
    return f"{exchange.strip().upper()}:{symbol.strip().upper()}"


def _normalize_evidence_references(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Sequence):
        items = list(value)
    else:
        raise TypeError("evidence_references must be a string or sequence of strings.")
    cleaned: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("evidence_references may only contain non-empty strings.")
        cleaned.append(item.strip())
    return tuple(sorted(set(cleaned)))


def _normalize_invalidation_conditions(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raise TypeError("invalidation_conditions must be a sequence, not a string.")
    if not isinstance(value, Sequence):
        raise TypeError("invalidation_conditions must be a sequence of strings.")
    conditions = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in conditions):
        raise ValueError("invalidation_conditions may only contain non-empty strings.")
    return tuple(item.strip() for item in conditions)


def _parse_data_window(value: Any, field_name: str = "data_window") -> tuple[datetime, datetime]:
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError(f"{field_name} must contain start and end values.")
        start_value, end_value = value
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            raise ValueError(f"{field_name} must be a non-empty string.")
        start_value, end_value = None, None
        for delimiter in ("/", " to "):
            if delimiter in candidate:
                start_value, end_value = [part.strip() for part in candidate.split(delimiter, 1)]
                break
        if start_value is None or end_value is None:
            raise ValueError(
                f"{field_name} must be a chronological range such as '2024-01-01T00:00:00Z/2024-01-10T00:00:00Z'."
            )
    else:
        raise TypeError(f"{field_name} must be a chronological range string or tuple.")
    start = _parse_iso8601(start_value, f"{field_name} start")
    end = _parse_iso8601(end_value, f"{field_name} end")
    if end < start:
        raise ValueError(f"{field_name} end must be later than or equal to its start.")
    return start, end


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _normalize_decision(value: Any) -> ProposalDecision:
    if isinstance(value, ProposalDecision):
        return value
    if not isinstance(value, str):
        raise TypeError("decision must be a ProposalDecision value or string.")
    try:
        return ProposalDecision(value.upper())
    except ValueError as exc:
        raise ValueError("decision must be BUY, SELL or HOLD.") from exc


def _identity_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _stable_proposal_id(
    *,
    proposer_id: str,
    proposer_version: str,
    instrument: str,
    decision: ProposalDecision,
    timestamp: str,
    data_window: tuple[datetime, datetime],
    data_as_of: datetime,
    confidence: Decimal | None,
    rationale: str,
    evidence_references: tuple[str, ...],
    proposed_entry: Decimal | None,
    proposed_stop: Decimal | None,
    proposed_target: Decimal | None,
    proposed_exit_price: Decimal | None,
    invalidation_conditions: tuple[str, ...],
    contract_version: str,
) -> str:
    canonical = json.dumps(
        {
            "proposer_id": proposer_id,
            "proposer_version": proposer_version,
            "instrument": instrument,
            "decision": decision.value,
            "timestamp": _format_timestamp(_parse_iso8601(timestamp, "timestamp")),
            "data_window": [_format_timestamp(value) for value in data_window],
            "data_as_of": _format_timestamp(data_as_of),
            "confidence": _identity_decimal(confidence),
            "rationale": rationale,
            "evidence_references": sorted(evidence_references),
            "proposed_entry": _identity_decimal(proposed_entry),
            "proposed_stop": _identity_decimal(proposed_stop),
            "proposed_target": _identity_decimal(proposed_target),
            "proposed_exit_price": _identity_decimal(proposed_exit_price),
            "invalidation_conditions": list(invalidation_conditions),
            "contract_version": contract_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = uuid.uuid5(uuid.NAMESPACE_URL, canonical).hex
    return f"proposal-{digest[:24]}"


@dataclass(frozen=True, slots=True)
class ProposalContract:
    """Validated research proposal; validation does not establish evidence or execution safety.

    For version 1.0.0 payloads that omit ``data_as_of``, the observation-window
    end is used as the compatibility boundary. Evidence references are an
    unordered set and are serialized in sorted, deduplicated order. SELL is a long-exit intent;
    legacy SELL entry/stop/target fields remain parseable but are advisory only
    and do not form a short-trade risk model. New adapters use
    ``proposed_exit_price`` for a known exit reference.
    """

    proposal_id: str
    contract_version: str
    proposer_id: str
    proposer_version: str
    instrument: str
    decision: ProposalDecision
    confidence: Decimal | None
    rationale: str
    evidence_references: tuple[str, ...]
    timestamp: str
    data_window: str
    proposed_entry: Decimal | None = None
    proposed_stop: Decimal | None = None
    proposed_target: Decimal | None = None
    invalidation_conditions: tuple[str, ...] = ()
    data_as_of: str | None = None
    proposed_exit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str):
            raise TypeError("proposal_id must be text.")
        if not self.proposal_id.strip():
            raise ValueError("proposal_id must be non-empty text.")
        if not isinstance(self.contract_version, str):
            raise TypeError("contract_version must be text.")
        if not self.contract_version.strip():
            raise ValueError("contract_version must be non-empty text.")
        if self.contract_version not in SUPPORTED_PROPOSAL_CONTRACT_VERSIONS:
            raise ValueError(
                f"Unsupported proposal contract version {self.contract_version!r}. "
                f"Supported versions: {sorted(SUPPORTED_PROPOSAL_CONTRACT_VERSIONS)}."
            )
        if not isinstance(self.proposer_id, str):
            raise TypeError("proposer_id must be text.")
        if not self.proposer_id.strip():
            raise ValueError("proposer_id must be non-empty text.")
        if not isinstance(self.proposer_version, str):
            raise TypeError("proposer_version must be text.")
        if not self.proposer_version.strip():
            raise ValueError("proposer_version must be non-empty text.")
        object.__setattr__(self, "instrument", _normalize_instrument(self.instrument))
        decision = _normalize_decision(self.decision)
        object.__setattr__(self, "decision", decision)
        if not isinstance(self.rationale, str):
            raise TypeError("rationale must be text.")
        if not self.rationale.strip():
            raise ValueError("rationale must be non-empty text.")
        object.__setattr__(
            self, "evidence_references", _normalize_evidence_references(self.evidence_references)
        )
        proposal_time = _parse_iso8601(self.timestamp, "timestamp")
        object.__setattr__(self, "timestamp", _format_timestamp(proposal_time))
        data_start, data_end = _parse_data_window(self.data_window, "data_window")
        object.__setattr__(
            self,
            "data_window",
            f"{_format_timestamp(data_start)}/{_format_timestamp(data_end)}",
        )
        data_as_of = (
            data_end if self.data_as_of is None else _parse_iso8601(self.data_as_of, "data_as_of")
        )
        if data_as_of < data_end:
            raise ValueError("data_window end must not be later than data_as_of.")
        if proposal_time < data_as_of:
            raise ValueError("proposal timestamp must not be earlier than data_as_of.")
        object.__setattr__(self, "data_as_of", _format_timestamp(data_as_of))
        if self.confidence is not None:
            confidence = _as_decimal(self.confidence, "confidence")
            if confidence < Decimal(0) or confidence > Decimal(1):
                raise ValueError("confidence must be between 0 and 1 inclusive.")
            object.__setattr__(self, "confidence", confidence)
        object.__setattr__(
            self,
            "invalidation_conditions",
            _normalize_invalidation_conditions(self.invalidation_conditions),
        )
        if self.decision == ProposalDecision.HOLD:
            if (
                self.proposed_entry is not None
                or self.proposed_stop is not None
                or self.proposed_target is not None
                or self.proposed_exit_price is not None
            ):
                raise ValueError("HOLD proposals must not include proposed price values.")
        else:
            if self.decision == ProposalDecision.BUY and self.proposed_entry is None:
                raise ValueError("BUY proposals require a positive proposed_entry.")
            for field_name in (
                "proposed_entry",
                "proposed_stop",
                "proposed_target",
                "proposed_exit_price",
            ):
                value = getattr(self, field_name)
                if value is not None:
                    price = _as_decimal(value, field_name)
                    if price <= 0:
                        raise ValueError(f"{field_name} must be a positive price when supplied.")
                    object.__setattr__(self, field_name, price)
            if self.decision == ProposalDecision.BUY:
                entry_price = self.proposed_entry
                assert entry_price is not None
                if self.proposed_stop is not None and self.proposed_stop >= entry_price:
                    raise ValueError("BUY proposed_stop must be below the proposed_entry.")
                if self.proposed_target is not None and self.proposed_target <= entry_price:
                    raise ValueError("BUY proposed_target must be above the proposed_entry.")
                if self.proposed_exit_price is not None:
                    raise ValueError("BUY proposals must not include proposed_exit_price.")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProposalContract:
        if not isinstance(payload, Mapping):
            raise TypeError("ProposalContract payload must be a mapping.")
        allowed_fields = {
            "proposal_id",
            "contract_version",
            "proposer_id",
            "proposer_version",
            "instrument",
            "decision",
            "confidence",
            "rationale",
            "evidence_references",
            "timestamp",
            "data_window",
            "proposed_entry",
            "proposed_stop",
            "proposed_target",
            "invalidation_conditions",
            "data_as_of",
            "proposed_exit_price",
        }
        unknown = [key for key in payload if key not in allowed_fields]
        if unknown:
            raise ValueError(
                f"Proposal payload contains unknown fields: {sorted(map(str, unknown))}."
            )
        missing = [
            field_name
            for field_name in (
                "proposal_id",
                "contract_version",
                "proposer_id",
                "proposer_version",
                "instrument",
                "decision",
                "confidence",
                "rationale",
                "timestamp",
                "data_window",
            )
            if field_name not in payload
        ]
        if missing:
            raise ValueError(f"Proposal payload is missing required fields: {missing}.")
        if "data_as_of" in payload and payload["data_as_of"] is None:
            raise TypeError("data_as_of must be a timezone-aware timestamp when supplied.")
        raw_confidence = payload.get("confidence")
        confidence = None if raw_confidence is None else _as_decimal(raw_confidence, "confidence")
        evidence = _normalize_evidence_references(payload.get("evidence_references", ()))
        invalidation = payload.get("invalidation_conditions", ())
        return cls(
            proposal_id=payload["proposal_id"],
            contract_version=payload["contract_version"],
            proposer_id=payload["proposer_id"],
            proposer_version=payload["proposer_version"],
            instrument=payload["instrument"],
            decision=payload["decision"],
            confidence=confidence,
            rationale=payload["rationale"],
            evidence_references=evidence,
            timestamp=payload["timestamp"],
            data_window=payload["data_window"],
            proposed_entry=(
                None
                if payload.get("proposed_entry") is None
                else _as_decimal(payload["proposed_entry"], "proposed_entry")
            ),
            proposed_stop=(
                None
                if payload.get("proposed_stop") is None
                else _as_decimal(payload["proposed_stop"], "proposed_stop")
            ),
            proposed_target=(
                None
                if payload.get("proposed_target") is None
                else _as_decimal(payload["proposed_target"], "proposed_target")
            ),
            invalidation_conditions=_normalize_invalidation_conditions(invalidation),
            data_as_of=payload.get("data_as_of"),
            proposed_exit_price=(
                None
                if payload.get("proposed_exit_price") is None
                else _as_decimal(payload["proposed_exit_price"], "proposed_exit_price")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "contract_version": self.contract_version,
            "proposer_id": self.proposer_id,
            "proposer_version": self.proposer_version,
            "instrument": self.instrument,
            "decision": self.decision.value,
            "confidence": None if self.confidence is None else str(self.confidence),
            "rationale": self.rationale,
            "evidence_references": list(self.evidence_references),
            "timestamp": self.timestamp,
            "data_window": self.data_window,
            "data_as_of": self.data_as_of,
            "invalidation_conditions": list(self.invalidation_conditions),
        }
        for field_name in (
            "proposed_entry",
            "proposed_stop",
            "proposed_target",
            "proposed_exit_price",
        ):
            value = getattr(self, field_name)
            if value is not None:
                result[field_name] = str(value)
        return result

    def to_json(self, *, sort_keys: bool = True) -> str:
        return json.dumps(self.to_dict(), sort_keys=sort_keys, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> ProposalContract:
        document = json.loads(payload)
        return cls.from_dict(document)

    @classmethod
    def create(
        cls,
        *,
        proposer_id: str,
        proposer_version: str,
        instrument: str,
        decision: ProposalDecision | str,
        confidence: Decimal | float | str | None,
        rationale: str,
        evidence_references: Sequence[str],
        timestamp: str,
        data_window: str | tuple[str, str],
        proposed_entry: Decimal | float | str | None = None,
        proposed_stop: Decimal | float | str | None = None,
        proposed_target: Decimal | float | str | None = None,
        proposed_exit_price: Decimal | float | str | None = None,
        invalidation_conditions: Sequence[str] = (),
        data_as_of: str | None = None,
    ) -> ProposalContract:
        """Convenience constructor that generates a stable proposal identifier."""

        normalized_decision = _normalize_decision(decision)
        normalized_instrument = _normalize_instrument(instrument)
        proposal_time = _parse_iso8601(timestamp, "timestamp")
        parsed_window = _parse_data_window(data_window)
        data_as_of_time = (
            parsed_window[1] if data_as_of is None else _parse_iso8601(data_as_of, "data_as_of")
        )
        normalized_confidence = (
            None if confidence is None else _as_decimal(confidence, "confidence")
        )
        evidence = _normalize_evidence_references(evidence_references)
        entry_value = (
            None if proposed_entry is None else _as_decimal(proposed_entry, "proposed_entry")
        )
        stop_value = None if proposed_stop is None else _as_decimal(proposed_stop, "proposed_stop")
        target_value = (
            None if proposed_target is None else _as_decimal(proposed_target, "proposed_target")
        )
        exit_price_value = (
            None
            if proposed_exit_price is None
            else _as_decimal(proposed_exit_price, "proposed_exit_price")
        )
        conditions = _normalize_invalidation_conditions(invalidation_conditions)
        if not isinstance(proposer_id, str):
            raise TypeError("proposer_id must be text.")
        if not proposer_id.strip():
            raise ValueError("proposer_id must be non-empty text.")
        if not isinstance(proposer_version, str):
            raise TypeError("proposer_version must be text.")
        if not proposer_version.strip():
            raise ValueError("proposer_version must be non-empty text.")
        if not isinstance(rationale, str):
            raise TypeError("rationale must be text.")
        if not rationale.strip():
            raise ValueError("rationale must be non-empty text.")
        data_window_text = "/".join(_format_timestamp(value) for value in parsed_window)
        data_as_of_text = _format_timestamp(data_as_of_time)
        proposal_id = _stable_proposal_id(
            proposer_id=proposer_id,
            proposer_version=proposer_version,
            instrument=normalized_instrument,
            decision=normalized_decision,
            timestamp=_format_timestamp(proposal_time),
            data_window=parsed_window,
            data_as_of=data_as_of_time,
            confidence=normalized_confidence,
            rationale=rationale,
            evidence_references=evidence,
            proposed_entry=entry_value,
            proposed_stop=stop_value,
            proposed_target=target_value,
            proposed_exit_price=exit_price_value,
            invalidation_conditions=conditions,
            contract_version=PROPOSAL_CONTRACT_VERSION,
        )
        return cls(
            proposal_id=proposal_id,
            contract_version=PROPOSAL_CONTRACT_VERSION,
            proposer_id=proposer_id,
            proposer_version=proposer_version,
            instrument=normalized_instrument,
            decision=normalized_decision,
            confidence=normalized_confidence,
            rationale=rationale,
            evidence_references=evidence,
            timestamp=timestamp,
            data_window=data_window_text,
            proposed_entry=entry_value,
            proposed_stop=stop_value,
            proposed_target=target_value,
            invalidation_conditions=conditions,
            data_as_of=data_as_of_text,
            proposed_exit_price=exit_price_value,
        )

    @classmethod
    def from_strategy_signal(
        cls,
        *,
        instrument: str,
        signal: Any,
        proposer_id: str,
        proposer_version: str,
        rationale: str | None = None,
        evidence_references: Sequence[str] = (),
    ) -> ProposalContract:
        """Adapt known signal values without inventing confidence, risk or evidence.

        ``StrategySignal`` contains no full observation window or generation-time
        metadata, so its timestamp is used as the known point-in-time boundary.
        """

        from aegis.strategy_lab import StrategySignal

        if not isinstance(signal, StrategySignal):
            raise TypeError("signal must be a StrategySignal instance.")
        action = signal.action
        decision: ProposalDecision
        entry = Decimal(str(signal.close))
        if action == "ENTER_LONG":
            decision = ProposalDecision.BUY
            proposed_entry = entry
            proposed_exit_price = None
        elif action == "EXIT_LONG":
            decision = ProposalDecision.SELL
            proposed_entry = None
            proposed_exit_price = entry
        else:
            raise ValueError(f"Unsupported StrategySignal action: {action!r}.")
        return cls.create(
            proposer_id=proposer_id,
            proposer_version=proposer_version,
            instrument=instrument,
            decision=decision,
            confidence=None,
            rationale=rationale
            if rationale is not None
            else (
                f"Strategy signal {action} at {signal.timestamp}: close={signal.close}, "
                f"fast_sma={signal.fast_sma}, slow_sma={signal.slow_sma}."
            ),
            evidence_references=evidence_references,
            timestamp=signal.timestamp,
            data_window=(
                signal.timestamp,
                signal.timestamp,
            ),
            data_as_of=signal.timestamp,
            proposed_entry=proposed_entry,
            proposed_exit_price=proposed_exit_price,
        )


@dataclass(frozen=True, slots=True)
class ProposalValidationResult:
    """Schema and evidence validation outcome, separate from execution safety."""

    accepted: bool
    errors: tuple[str, ...] = ()
    evidence_quality_status: str = "UNKNOWN"
    rejection_reasons: tuple[str, ...] = ()
    proposal: ProposalContract | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be a boolean.")
        if not isinstance(self.evidence_quality_status, str) or (
            self.evidence_quality_status not in {"PASS", "FAIL", "UNKNOWN"}
        ):
            raise ValueError("evidence_quality_status must be PASS, FAIL or UNKNOWN.")
        for field_name in ("errors", "rejection_reasons"):
            values = getattr(self, field_name)
            if isinstance(values, str) or not isinstance(values, Sequence):
                raise TypeError(f"{field_name} must be a sequence of strings.")
            if any(not isinstance(item, str) for item in values):
                raise TypeError(f"{field_name} may only contain strings.")
            object.__setattr__(self, field_name, tuple(values))
        if self.proposal is not None and not isinstance(self.proposal, ProposalContract):
            raise TypeError("proposal must be a ProposalContract when supplied.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "errors": list(self.errors),
            "evidence_quality_status": self.evidence_quality_status,
            "rejection_reasons": list(self.rejection_reasons),
            "proposal": self.proposal.to_dict() if self.proposal is not None else None,
        }


def validate_proposal_contract(
    payload: ProposalContract | Mapping[str, Any],
    *,
    evidence_verification_status: str | None = None,
) -> ProposalValidationResult:
    """Validate schema separately from evidence and execution safety.

    Evidence PASS is only returned when the caller supplies an explicit,
    independently obtained verification result. A valid schema is not evidence
    of provenance, and no result here establishes execution eligibility.
    """

    if evidence_verification_status is not None and (
        not isinstance(evidence_verification_status, str)
        or evidence_verification_status not in {"PASS", "FAIL", "UNKNOWN"}
    ):
        raise ValueError("evidence_verification_status must be PASS, FAIL or UNKNOWN.")
    if isinstance(payload, ProposalContract):
        proposal = payload
    else:
        try:
            proposal = ProposalContract.from_dict(payload)
        except (TypeError, ValueError) as exc:
            return ProposalValidationResult(
                accepted=False,
                errors=(str(exc),),
                evidence_quality_status="UNKNOWN",
                rejection_reasons=(str(exc),),
                proposal=None,
            )
    if not proposal.evidence_references:
        evidence_status = "FAIL"
    elif evidence_verification_status is None:
        evidence_status = "UNKNOWN"
    else:
        evidence_status = evidence_verification_status
    return ProposalValidationResult(
        accepted=True,
        errors=(),
        evidence_quality_status=evidence_status,
        rejection_reasons=(),
        proposal=proposal,
    )


__all__ = [
    "PROPOSAL_CONTRACT_VERSION",
    "SUPPORTED_PROPOSAL_CONTRACT_VERSIONS",
    "ProposalContract",
    "ProposalDecision",
    "ProposalValidationResult",
    "validate_proposal_contract",
]
