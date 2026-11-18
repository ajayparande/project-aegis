"""Bounded, append-only decision evidence. Nothing here authorizes execution."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from aegis.database import get_connection, get_readonly_connection

MAX_PAYLOAD_BYTES = 16_384
MAX_DURATION_NS = 3_600_000_000_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,255}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![a-z0-9])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|secret|password|authorization|bearer)"
    r"(?:\s*[:=]\s*|\s+|%3a|%3d)\S+"
)
_STAGES = frozenset(
    {
        "candle_validation",
        "feature_preparation",
        "model_inference",
        "signal_generation",
        "risk_validation",
        "paper_execution",
        "journal_persistence",
    }
)


class JournalError(ValueError):
    """Invalid or conflicting evidence; no execution permission is implied."""


class JournalConflictError(JournalError):
    """A stable identity has already been used for materially different evidence."""


class DecisionState(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    BLOCKED = "BLOCKED"
    NO_DECISION = "NO_DECISION"


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise JournalError(f"{label} must be a bounded identifier.")
    if isinstance(value, str) and _CREDENTIAL_ASSIGNMENT.search(value):
        raise JournalError(f"{label} cannot contain credential assignments.")
    if _IDENTIFIER.fullmatch(value) is None:
        raise JournalError(f"{label} must be a bounded identifier.")
    return value


def _time(value: object, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise JournalError(f"{label} must be a timezone-aware timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise JournalError(f"{label} must be an ISO timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JournalError(f"{label} requires an explicit timezone.")
    return parsed.astimezone(UTC)


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _import_time(value: object) -> datetime:
    """SQLite CURRENT_TIMESTAMP is UTC; accept that documented legacy storage format only."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return _time(value, "import completion")


def _now() -> str:
    return _utc(datetime.now(UTC))


def _integer(value: object, label: str, *, minimum: int = 1, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise JournalError(f"{label} must be an integer in the permitted range.")
    return value


def _decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise JournalError(f"{label} requires an exact decimal value.")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise JournalError(f"{label} must be decimal.") from exc
    if (
        not number.is_finite()
        or (positive and number <= 0)
        or len(str(number)) > 128
        or abs(int(number.as_tuple().exponent)) > 100
        or len(number.as_tuple().digits) > 100
    ):
        raise JournalError(f"{label} must be finite and within its permitted range.")
    return number


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    def encode(item: object) -> object:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, Enum):
            return item.value
        raise TypeError("Unsupported journal value.")

    result = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=encode,
    )
    if len(result.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise JournalError("Journal payload exceeds the bounded storage limit.")
    return result


def _sanitize(value: object, depth: int = 0) -> Any:
    """Redact nonsecret metadata without retaining model transcripts or arbitrary objects."""
    from aegis.ai_agent.security import redact_secrets

    if depth > 5:
        raise JournalError("Journal metadata is too deeply nested.")
    if isinstance(value, Mapping):
        if len(value) > 32:
            raise JournalError("Journal metadata has too many fields.")
        result = {}
        for key, item in value.items():
            key = _identifier(key, "metadata key")
            if any(
                word in key.lower()
                for word in (
                    "token",
                    "secret",
                    "password",
                    "api_key",
                    "credential",
                    "prompt",
                    "response",
                )
            ):
                result[key] = "[REDACTED]"
            else:
                result[key] = _sanitize(item, depth + 1)
        return result
    if isinstance(value, (tuple, list)):
        if len(value) > 64:
            raise JournalError("Journal metadata list is too long.")
        return [_sanitize(item, depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > 1024:
            raise JournalError("Journal metadata text is too long.")
        return "[REDACTED]" if _CREDENTIAL_ASSIGNMENT.search(value) else redact_secrets(value)
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, Decimal):
        return str(_decimal(value, "metadata decimal"))
    raise JournalError("Journal metadata must contain bounded JSON-compatible values.")


@dataclass(frozen=True, slots=True)
class DecisionEvent:
    """Immutable original evaluation, separate from later risk/execution observations."""

    run_id: str
    event_id: str
    instrument_id: int
    strategy_id: str
    strategy_version: str
    decision_at: str
    state: DecisionState | str
    candle_completed_at: str | None = None
    information_cutoff: str | None = None
    interval: str = "1m"
    role: str = "ACTIVE"
    attempt: int = 1
    original_signal: str | None = None
    quantity: int | None = None
    confidence: Decimal | None = None
    proposed_price: Decimal | None = None
    reference_price: Decimal | None = None
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    model_id: str | None = None
    model_version: str | None = None
    feature_set_version: str | None = None
    evidence_references: tuple[str, ...] = ()
    source_import_id: int | None = None
    source_use_status: str = "UNKNOWN"
    calendar_fingerprint: str | None = None
    calendar_confidence: str = "UNAVAILABLE"
    quality_status: str = "UNKNOWN"
    risk_status: str = "NOT_EVALUATED"
    reason_codes: tuple[str, ...] = ()
    configuration_fingerprints: tuple[tuple[str, str], ...] = ()
    stage_durations_ns: tuple[tuple[str, int], ...] = ()
    paper_account_id: int | None = None
    proposal_id: str | None = None
    proposal_fingerprint: str | None = None
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        for name in ("run_id", "event_id", "strategy_id", "strategy_version"):
            _identifier(getattr(self, name), name)
        _integer(self.instrument_id, "instrument_id")
        _integer(self.attempt, "attempt", maximum=1_000_000)
        state = DecisionState(self.state)
        object.__setattr__(self, "state", state)
        decision_time = _time(self.decision_at, "decision_at")
        cutoff = _time(
            self.information_cutoff or self.candle_completed_at or self.decision_at,
            "information_cutoff",
        )
        if cutoff > decision_time:
            raise JournalError("Information cutoff cannot follow the decision.")
        if self.candle_completed_at is not None:
            completion = _time(self.candle_completed_at, "candle_completed_at")
            if completion > cutoff:
                raise JournalError("A decision cannot use an incomplete candle.")
            object.__setattr__(self, "candle_completed_at", _utc(completion))
        elif self.reference_price is not None:
            raise JournalError(
                "A reference price requires independently established candle completion."
            )
        created = _time(self.created_at, "created_at")
        if created < decision_time:
            raise JournalError("Creation timestamp cannot precede the decision.")
        object.__setattr__(self, "decision_at", _utc(decision_time))
        object.__setattr__(self, "information_cutoff", _utc(cutoff))
        object.__setattr__(self, "created_at", _utc(created))
        if self.interval not in {"1m", "5m", "15m", "1d"}:
            raise JournalError("Unsupported journal interval.")
        if self.role not in {"ACTIVE", "SHADOW"}:
            raise JournalError("Decision role must be ACTIVE or SHADOW.")
        if self.original_signal not in {None, "BUY", "SELL", "HOLD"}:
            raise JournalError("Invalid original signal.")
        if self.quantity is not None:
            _integer(self.quantity, "quantity")
        if state in {DecisionState.BUY, DecisionState.SELL} and self.quantity is None:
            raise JournalError("BUY and SELL require a proposed quantity.")
        if state in {DecisionState.HOLD, DecisionState.NO_DECISION} and self.quantity is not None:
            raise JournalError("HOLD and NO_DECISION cannot propose quantities.")
        if self.confidence is not None:
            number = _decimal(self.confidence, "confidence")
            if not 0 <= number <= 1 or state == DecisionState.NO_DECISION:
                raise JournalError("Confidence is unavailable or outside [0,1].")
            object.__setattr__(self, "confidence", number)
        for name in ("proposed_price", "reference_price", "stop_price", "target_price"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _decimal(value, name, positive=True))
        if (self.model_id is None) != (self.model_version is None):
            raise JournalError("Model identity and version must be supplied together.")
        for name in ("model_id", "model_version", "feature_set_version", "proposal_id"):
            value = getattr(self, name)
            if value is not None:
                _identifier(value, name)
        for name in ("source_import_id", "paper_account_id"):
            value = getattr(self, name)
            if value is not None:
                _integer(value, name)
        for name in ("calendar_fingerprint", "proposal_fingerprint"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or _HASH.fullmatch(value) is None):
                raise JournalError(f"{name} must be a SHA-256 fingerprint.")
        if self.source_use_status not in {
            "UNKNOWN",
            "UNVERIFIED",
            "VERIFIED_PERMITTED",
            "PROHIBITED",
        }:
            raise JournalError("Invalid source-use state.")
        if self.calendar_confidence not in {"CONFIGURED", "CONFIRMED", "UNAVAILABLE"}:
            raise JournalError("Invalid calendar confidence.")
        if self.quality_status not in {"UNKNOWN", "PASS", "FAIL"}:
            raise JournalError("Invalid quality state.")
        if self.risk_status not in {"NOT_EVALUATED", "PENDING", "PASS", "FAIL"}:
            raise JournalError("Invalid risk state.")
        for name, maximum in (("evidence_references", 32), ("reason_codes", 16)):
            value = getattr(self, name)
            if (
                isinstance(value, str)
                or not isinstance(value, (tuple, list))
                or len(value) > maximum
            ):
                raise JournalError(f"{name} must be a bounded sequence.")
            items = tuple(value)
            for item in items:
                if name == "reason_codes":
                    if not isinstance(item, str) or _REASON.fullmatch(item) is None:
                        raise JournalError("Reason codes must be bounded deterministic codes.")
                else:
                    _identifier(item, "evidence reference")
            object.__setattr__(self, name, items)
        for name in ("configuration_fingerprints", "stage_durations_ns"):
            pairs = getattr(self, name)
            pairs = tuple(sorted(pairs.items() if isinstance(pairs, Mapping) else pairs))
            if len(pairs) > 16 or len({key for key, _ in pairs}) != len(pairs):
                raise JournalError("Duplicate or excessive journal metadata keys.")
            for key, value in pairs:
                _identifier(key, name)
                if name == "configuration_fingerprints":
                    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
                        raise JournalError("Configuration values must be SHA-256 hashes.")
                elif key not in _STAGES:
                    raise JournalError("Unsupported latency stage.")
                else:
                    _integer(value, "duration", minimum=0, maximum=MAX_DURATION_NS)
            object.__setattr__(self, name, pairs)
        _json(self.to_dict())

    @property
    def decision_id(self) -> str:
        return _digest(
            _json(
                [
                    "decision-journal/v1",
                    self.run_id,
                    self.event_id,
                    self.instrument_id,
                    self.strategy_id,
                    self.strategy_version,
                    self.role,
                    self.attempt,
                ]
            )
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = 1
        # Unused stages remain unavailable, never fabricated zero-latency measurements.
        payload["processing_stage_durations_ns"] = {
            name: dict(self.stage_durations_ns).get(name) for name in sorted(_STAGES)
        }
        return payload


def _material(payload: Mapping[str, Any]) -> str:
    return _json(
        {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "created_at",
                "stage_durations_ns",
                "processing_stage_durations_ns",
                "duration_ns",
                "evaluated_at",
            }
        }
    )


@contextmanager
def _transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
    if not database_path.is_file():
        raise JournalError("Journal database must already exist and be migrated.")
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


def _existing(
    connection: sqlite3.Connection, table: str, key: str, identity: str, payload: Mapping[str, Any]
) -> bool:
    # Table/key names below are module constants, never caller-supplied SQL.
    row = connection.execute(
        f"SELECT payload_json, payload_sha256 FROM {table} WHERE {key} = ?", (identity,)
    ).fetchone()
    if row is None:
        return False
    if _digest(row["payload_json"]) != row["payload_sha256"]:
        raise JournalError("Persisted journal payload is inconsistent.")
    if _material(json.loads(row["payload_json"])) != _material(payload):
        raise JournalConflictError("Journal identity already has different immutable evidence.")
    return True


def _baseline(connection: sqlite3.Connection, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Authenticate a completed historical candle and its persisted availability, never a fill."""
    from aegis.market_quality import load_approved_calendar_registry

    cutoff = _time(payload["information_cutoff"], "cutoff")
    rows = connection.execute(
        """SELECT c.*, d.completed_at AS available_at, d.status AS import_status,
                  d.source_use_status, d.file_sha256, h.new_status, h.declared_at,
                  i.exchange, i.segment, i.tick_size_decimal
           FROM historical_candles c JOIN data_imports d ON d.id = c.import_id
           JOIN instruments i ON i.id = c.instrument_id
           LEFT JOIN import_source_use_history h ON h.id =
             (SELECT MAX(id) FROM import_source_use_history WHERE import_id = d.id)
           WHERE c.instrument_id = ? AND c.interval = ?
             AND (? IS NULL OR c.import_id = ?) AND c.candle_timestamp <= ?
           ORDER BY c.candle_timestamp DESC LIMIT 1000""",
        (
            payload["instrument_id"],
            payload["interval"],
            payload.get("source_import_id"),
            payload.get("source_import_id"),
            _utc(cutoff),
        ),
    ).fetchall()
    for row in rows:
        try:
            completion = _time(row["candle_timestamp"], "candle") + timedelta(
                minutes={"1m": 1, "5m": 5, "15m": 15, "1d": 1440}[payload["interval"]]
            )
            if (
                completion > cutoff
                or row["available_at"] is None
                or _import_time(row["available_at"]) > cutoff
                or row["declared_at"] is None
                or _import_time(row["declared_at"]) > cutoff
                or row["source_use_status"] != "VERIFIED_PERMITTED"
                or row["new_status"] != "VERIFIED_PERMITTED"
                or row["import_status"] not in {"COMPLETED", "COMPLETED_WITH_ERRORS"}
            ):
                continue
            calendar = load_approved_calendar_registry().resolve(row["exchange"], row["segment"])
            if calendar is None or calendar.confidence != "CONFIRMED":
                return None
            from aegis.market_quality import calculate_expected_candles

            day = completion.astimezone(ZoneInfo(calendar.timezone_name)).date()
            scheduled, _, _, uncovered = calculate_expected_candles(
                calendar, payload["interval"], day, day, as_of=cutoff
            )
            if uncovered or not any(
                c.timestamp == row["candle_timestamp"] and c.completes_at == completion
                for c in scheduled
            ):
                continue
            prices = [
                _decimal(row[k], k, positive=True)
                for k in ("open_decimal", "high_decimal", "low_decimal", "close_decimal")
            ]
            tick = _decimal(row["tick_size_decimal"], "tick", positive=True)
            if (
                prices[1] < max(prices)
                or prices[2] > min(prices)
                or any(p % tick for p in prices)
                or row["volume"] < 0
            ):
                continue
            return {
                "policy": "persisted-candle-cutoff/v1",
                "candle_id": row["id"],
                "import_id": row["import_id"],
                "completed_at": _utc(completion),
                "available_at": row["available_at"],
                "declared_at": row["declared_at"],
                "reference_price": str(prices[3]),
                "candle_sha256": _digest(
                    _json(
                        [
                            row["candle_timestamp"],
                            *map(str, prices),
                            row["volume"],
                            row["file_sha256"],
                        ]
                    )
                ),
            }
        except (ValueError, OSError):
            return None
    return None


def _append_decision(connection: sqlite3.Connection, event: DecisionEvent) -> str:
    started = time.perf_counter_ns()
    payload = event.to_dict()
    input_sha = _digest(_material(payload))
    prior = connection.execute(
        "SELECT payload_json, payload_sha256 FROM decision_journal_events WHERE decision_id = ?",
        (event.decision_id,),
    ).fetchone()
    if prior is not None:
        if _digest(prior["payload_json"]) != prior["payload_sha256"]:
            raise JournalError("Stored decision fingerprint is inconsistent.")
        previous = json.loads(prior["payload_json"])
        if previous.get("input_payload_sha256") == input_sha:
            return event.decision_id
        if "input_payload_sha256" not in previous and _material(
            {key: previous.get(key) for key in payload}
        ) == _material(payload):
            # Exact legacy replay remains idempotent; it does not acquire baseline authority.
            return event.decision_id
        raise JournalConflictError("Decision identity already binds different input evidence.")
    payload["input_payload_sha256"] = input_sha
    baseline = _baseline(connection, payload) if event.reference_price is not None else None
    if baseline is not None and (
        _decimal(baseline["reference_price"], "baseline") != event.reference_price
        or baseline["completed_at"] != event.candle_completed_at
    ):
        baseline = None
    payload["baseline_evidence"] = baseline
    if baseline is None and event.reference_price is not None:
        payload["unqualified_reference_sha256"] = _digest(str(event.reference_price))
        payload["reference_price"] = None
    if baseline is None:
        payload["candle_completed_at"] = None
    if _existing(connection, "decision_journal_events", "decision_id", event.decision_id, payload):
        return event.decision_id
    text = _json(payload)
    connection.execute(
        """INSERT INTO decision_journal_events
           (decision_id, logical_key, run_id, event_id, instrument_id, paper_account_id,
            state, role, decision_at, payload_json, payload_sha256, created_at, strategy_id, strategy_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.decision_id,
            event.decision_id,
            event.run_id,
            event.event_id,
            event.instrument_id,
            event.paper_account_id,
            DecisionState(event.state).value,
            event.role,
            event.decision_at,
            text,
            _digest(text),
            event.created_at,
            event.strategy_id,
            event.strategy_version,
        ),
    )
    _append_audit(
        connection,
        event.decision_id,
        "PERSISTED",
        "PENDING",
        {
            "duration_ns": min(time.perf_counter_ns() - started, MAX_DURATION_NS),
            "measurement": "journal_persistence",
        },
        created_at=event.created_at,
    )
    return event.decision_id


def append_decision(database_path: Path, event: DecisionEvent) -> str:
    """Atomically persist an evaluation and its required persistence audit."""
    if not isinstance(event, DecisionEvent):
        raise TypeError("event must be a validated DecisionEvent.")
    with _transaction(database_path) as connection:
        return _append_decision(connection, event)


def _append_audit(
    connection: sqlite3.Connection,
    decision_id: str,
    stage: str,
    status: str,
    details: Mapping[str, Any] | None = None,
    *,
    order_id: str | None = None,
    fill_id: str | None = None,
    created_at: str | None = None,
    attempt_key: str = "",
) -> str:
    if stage not in {"PERSISTED", "VALIDATION", "EXECUTION", "TIMING", "RECOVERY"}:
        raise JournalError("Unknown journal audit stage.")
    if status not in {"PENDING", "ALLOWED", "FILLED", "REJECTED", "HOLD", "BLOCKED", "NO_DECISION"}:
        raise JournalError("Unknown journal audit status.")
    _identifier(decision_id, "decision_id")
    if attempt_key:
        _identifier(attempt_key, "attempt_key")
        if len(attempt_key) > 64:
            raise JournalError("Audit attempt key is too long.")
    payload = {
        "stage": stage,
        "status": status,
        "order_id": order_id,
        "fill_id": fill_id,
        "attempt_key": attempt_key,
        **_sanitize(details or {}),
    }
    audit_id = _digest(_json([decision_id, stage, attempt_key]))
    if _existing(connection, "decision_journal_audits", "audit_id", audit_id, payload):
        return audit_id
    text = _json(payload)
    connection.execute(
        """INSERT INTO decision_journal_audits
           (audit_id, decision_id, stage, status, order_id, fill_id, payload_json,
            payload_sha256, created_at, attempt_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            audit_id,
            decision_id,
            stage,
            status,
            order_id,
            fill_id,
            text,
            _digest(text),
            _utc(_time(created_at, "created_at")) if created_at else _now(),
            attempt_key,
        ),
    )
    return audit_id


def append_validation(
    database_path: Path, decision_id: str, status: str, reason_codes: Sequence[str] = ()
) -> str:
    """Append a non-authoritative observation of deterministic validation."""
    codes = tuple(reason_codes)
    if len(codes) > 16 or any(
        not isinstance(c, str) or _REASON.fullmatch(c) is None for c in codes
    ):
        raise JournalError("Invalid validation reason codes.")
    with _transaction(database_path) as connection:
        return _append_audit(connection, decision_id, "VALIDATION", status, {"reason_codes": codes})


def record_manifest(database_path: Path, run_id: str, document: Mapping[str, Any]) -> str:
    """Freeze bounded nonsecret experiment metadata; never creates execution authority."""
    _identifier(run_id, "run_id")
    allowed = {
        "scope",
        "strategy_id",
        "strategy_version",
        "model_id",
        "model_version",
        "feature_set_version",
        "configuration_hash",
        "code_version",
        "calendar_hash",
        "cost_policy_version",
        "data_manifest",
        "authorization_reference",
        "protocol_reference",
        "prior_attempts",
    }
    if not isinstance(document, Mapping) or set(document) - allowed:
        raise JournalError("Unsupported manifest fields.")
    payload = _sanitize(document)
    text = _json(payload)
    with _transaction(database_path) as connection:
        if not _existing(connection, "decision_journal_manifests", "run_id", run_id, payload):
            connection.execute(
                """INSERT INTO decision_journal_manifests
                   (run_id, payload_json, payload_sha256, created_at) VALUES (?, ?, ?, ?)""",
                (run_id, text, _digest(text), _now()),
            )
    return run_id


def _append_health(
    connection: sqlite3.Connection,
    run_id: str,
    event_id: str,
    kind: str,
    timestamp: str,
    details: Mapping[str, Any],
) -> str:
    _identifier(run_id, "run_id")
    _identifier(event_id, "event_id")
    if _REASON.fullmatch(kind) is None:
        raise JournalError("Invalid health reason code.")
    payload = {
        "event_id": event_id,
        "kind": kind,
        "timestamp": _utc(_time(timestamp, "timestamp")),
        "details": _sanitize(details),
    }
    identity = _digest(_json([run_id, event_id, kind]))
    if not _existing(connection, "decision_journal_health", "event_id", identity, payload):
        text = _json(payload)
        connection.execute(
            """INSERT INTO decision_journal_health
               (event_id, run_id, kind, payload_json, payload_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (identity, run_id, kind, text, _digest(text), payload["timestamp"]),
        )
    return identity


def record_health(
    database_path: Path,
    run_id: str,
    event_id: str,
    kind: str,
    timestamp: str,
    details: Mapping[str, Any] | None = None,
) -> str:
    with _transaction(database_path) as connection:
        return _append_health(connection, run_id, event_id, kind, timestamp, details or {})


def get_decision(database_path: Path, decision_id: str) -> dict[str, Any]:
    """Return detached evidence and append-only observations through a query-only connection."""
    _identifier(decision_id, "decision_id")
    with closing(get_readonly_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT * FROM decision_journal_events WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise JournalError("Decision does not exist.")
        if _digest(row["payload_json"]) != row["payload_sha256"]:
            raise JournalError("Persisted decision fingerprint is inconsistent.")
        audits = connection.execute(
            "SELECT payload_json, payload_sha256 FROM decision_journal_audits WHERE decision_id = ? ORDER BY rowid",
            (decision_id,),
        ).fetchall()
        if any(_digest(a["payload_json"]) != a["payload_sha256"] for a in audits):
            raise JournalError("Persisted audit fingerprint is inconsistent.")
    return {
        "decision_id": decision_id,
        "decision": json.loads(row["payload_json"]),
        "audits": [json.loads(a["payload_json"]) for a in audits],
    }


def list_decisions(database_path: Path, run_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    _identifier(run_id, "run_id")
    _integer(limit, "limit", maximum=1000)
    with closing(get_readonly_connection(database_path)) as connection:
        rows = connection.execute(
            """SELECT decision_id FROM decision_journal_events WHERE run_id = ?
               ORDER BY decision_at, decision_id LIMIT ?""",
            (run_id, limit),
        ).fetchall()
    return [get_decision(database_path, row["decision_id"]) for row in rows]


def _runtime_decision(
    connection: sqlite3.Connection,
    event_id: str,
    proposal_json: str,
    signal_generation_ns: int | None = None,
) -> DecisionEvent:
    """Capture persisted runtime identities, never use journal labels as authorization."""
    from aegis.market_quality import CalendarConfigError, load_approved_calendar_registry

    started = time.perf_counter_ns()
    row = connection.execute(
        """SELECT e.*, r.account_id, r.config_sha256,
                  c.completed_at, c.close_decimal, ri.import_id, d.source_use_status
           FROM forward_scheduler_events e JOIN forward_runtimes r USING(runtime_id)
           LEFT JOIN forward_runtime_candles c ON c.runtime_id = e.runtime_id
             AND c.instrument_id = e.instrument_id AND c.interval = e.interval
             AND c.candle_timestamp = e.candle_timestamp
           LEFT JOIN forward_runtime_imports ri ON ri.runtime_id = e.runtime_id
             AND ri.instrument_id = e.instrument_id AND ri.interval = e.interval
           LEFT JOIN data_imports d ON d.id = ri.import_id
           WHERE e.event_id = ?""",
        (event_id,),
    ).fetchone()
    if row is None:
        raise JournalError("Runtime event does not exist.")
    proposal = json.loads(proposal_json)
    if not isinstance(proposal, dict):
        raise JournalError("Invalid journal proposal shape.")
    if (
        proposal.get("proposal_id") != row["proposal_id"]
        or proposal.get("instrument_id") != row["instrument_id"]
    ):
        raise JournalError("Journal proposal does not match the runtime event.")
    if row["proposal_json"] is not None and row["proposal_json"] != proposal_json:
        raise JournalConflictError("Journal proposal differs from the bound runtime proposal.")
    signal = proposal.get("action")
    if signal not in {"BUY", "SELL", "HOLD"}:
        raise JournalError("Unsupported runtime signal.")
    complete = row["completed_at"]
    decision_at = row["decision_timestamp"]
    # Stops act on the current accepted quote, not an unfinished candle.
    protective = str(row["proposal_id"]).startswith("stop-target-")
    available = (
        protective
        or complete is not None
        and _time(complete, "completion") <= _time(decision_at, "decision")
    )
    state = signal if available else "NO_DECISION"
    calendar_hash = None
    confidence = "UNAVAILABLE"
    try:
        calendars = load_approved_calendar_registry()
        instrument = connection.execute(
            "SELECT exchange, segment FROM instruments WHERE id = ?", (row["instrument_id"],)
        ).fetchone()
        calendar = calendars.resolve(instrument["exchange"], instrument["segment"])
        if calendar is not None:
            confidence = calendar.confidence
            calendar_hash = calendars.configuration_sha256
    except (CalendarConfigError, OSError):
        pass  # Observation only; Phase 8 still independently checks approved evidence.
    quantity = proposal.get("quantity") if state in {"BUY", "SELL"} else None
    return DecisionEvent(
        run_id=row["runtime_id"],
        event_id=event_id,
        instrument_id=row["instrument_id"],
        strategy_id="runtime-explicit-proposal",
        strategy_version="v1",
        decision_at=decision_at,
        state=state,
        interval=row["interval"],
        candle_completed_at=complete if available else None,
        original_signal=signal,
        quantity=quantity,
        confidence=(
            _decimal(proposal["confidence"], "confidence")
            if available and proposal.get("confidence") is not None
            else None
        ),
        proposed_price=(
            _decimal(proposal["entry_price"], "entry", positive=True)
            if proposal.get("entry_price") is not None
            else None
        ),
        reference_price=(
            _decimal(row["close_decimal"], "reference", positive=True)
            if available and row["close_decimal"] is not None
            else None
        ),
        stop_price=(
            _decimal(proposal["stop_loss"], "stop", positive=True)
            if proposal.get("stop_loss") is not None
            else None
        ),
        target_price=(
            _decimal(proposal["target_price"], "target", positive=True)
            if proposal.get("target_price") is not None
            else None
        ),
        evidence_references=(f"scheduler:{event_id}", f"proposal:{_digest(proposal_json)}"),
        source_import_id=row["import_id"],
        source_use_status=row["source_use_status"] or "UNKNOWN",
        calendar_fingerprint=calendar_hash,
        calendar_confidence=confidence,
        risk_status="PENDING",
        configuration_fingerprints=(("runtime", row["config_sha256"]),),
        paper_account_id=row["account_id"],
        proposal_id=row["proposal_id"],
        proposal_fingerprint=_digest(proposal_json),
        reason_codes=() if available else ("MISSING_COMPLETED_EVIDENCE",),
        stage_durations_ns=(
            ("candle_validation", min(time.perf_counter_ns() - started, MAX_DURATION_NS)),
        )
        + (
            (("signal_generation", signal_generation_ns),)
            if signal_generation_ns is not None
            else ()
        ),
    )


def capture_runtime_decision(
    database_path: Path,
    event_id: str,
    proposal_json: str,
    *,
    signal_generation_ns: int | None = None,
) -> str:
    with _transaction(database_path) as connection:
        existing = connection.execute(
            """SELECT decision_id, payload_json FROM decision_journal_events
               WHERE event_id = ? AND strategy_id = 'runtime-explicit-proposal'
                 AND strategy_version = 'v1' AND role = 'ACTIVE'""",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if json.loads(existing["payload_json"])["proposal_fingerprint"] != _digest(
                proposal_json
            ):
                raise JournalConflictError("Decision already binds different proposal evidence.")
            return str(existing["decision_id"])
        return _append_decision(
            connection, _runtime_decision(connection, event_id, proposal_json, signal_generation_ns)
        )


def finish_runtime_decision(
    database_path: Path,
    decision_id: str,
    status: str,
    *,
    reason: str | None = None,
    duration_ns: int | None = None,
) -> None:
    """Record an evaluation that did not reach an order, or its existing atomic order result."""
    with _transaction(database_path) as connection:
        existing = connection.execute(
            "SELECT 1 FROM decision_journal_audits WHERE decision_id = ? AND stage = 'EXECUTION'",
            (decision_id,),
        ).fetchone()
        if existing is None:
            if status == "FILLED":
                raise JournalError("A filled result requires an atomic execution audit.")
            outcome = "HOLD" if status == "HOLD" else "BLOCKED"
            if reason and any(
                part in reason.lower() for part in ("no completed", "evidence is missing")
            ):
                outcome = "NO_DECISION"
            _append_audit(
                connection,
                decision_id,
                "VALIDATION",
                outcome,
                {
                    "reason_codes": [
                        "NO_ORDER_HOLD"
                        if outcome == "HOLD"
                        else "MISSING_EVIDENCE"
                        if outcome == "NO_DECISION"
                        else "PROPOSAL_OR_RISK_REJECTED"
                    ]
                },
                attempt_key="PRE_ORDER",
            )
        if duration_ns is not None:
            _integer(duration_ns, "duration", minimum=0, maximum=MAX_DURATION_NS)
            _append_audit(
                connection,
                decision_id,
                "TIMING",
                "ALLOWED",
                {"duration_ns": duration_ns, "measurement": "routing_total"},
            )


def capture_proposal_decision(
    database_path: Path, account_id: int, proposal_json: str, evidence: Any
) -> str:
    """Observe a generic Phase 8 proposal; caller evidence never grants permission."""
    proposal = json.loads(proposal_json)
    with _transaction(database_path) as connection:
        existing = connection.execute(
            """SELECT decision_id, payload_json, payload_sha256 FROM decision_journal_events
               WHERE run_id = ? AND event_id = ? AND strategy_id = 'phase8-explicit-proposal'""",
            (f"paper-account:{account_id}", proposal["proposal_id"]),
        ).fetchone()
        if existing is not None:
            if _digest(existing["payload_json"]) != existing["payload_sha256"]:
                raise JournalError("Stored journal fingerprint is inconsistent.")
            if json.loads(existing["payload_json"])["proposal_fingerprint"] != _digest(
                proposal_json
            ):
                raise JournalConflictError("Proposal identity already binds different evidence.")
            return str(existing["decision_id"])
        timestamp = proposal.get("decision_timestamp") or evidence.as_of
        source = None
        candle = None
        if evidence.source_import_id is not None:
            source = connection.execute(
                "SELECT source_use_status FROM data_imports WHERE id = ?",
                (evidence.source_import_id,),
            ).fetchone()
            candle = connection.execute(
                """SELECT candle_timestamp, close_decimal FROM historical_candles WHERE import_id = ?
                   AND instrument_id = ? AND interval = ? AND candle_timestamp <= ?
                   ORDER BY candle_timestamp DESC LIMIT 1""",
                (
                    evidence.source_import_id,
                    proposal["instrument_id"],
                    proposal["timeframe"],
                    evidence.latest_candle_timestamp or "",
                ),
            ).fetchone()
        # Evidence timestamps describe candle starts; completion must precede the decision.
        minutes = {"1m": 1, "5m": 5, "15m": 15, "1d": 1440}[proposal["timeframe"]]
        completion = (
            _time(candle["candle_timestamp"], "candle") + timedelta(minutes=minutes)
            if candle
            else None
        )
        available = completion is not None and completion <= _time(timestamp, "decision")
        signal = proposal["action"]
        event = DecisionEvent(
            run_id=f"paper-account:{account_id}",
            event_id=proposal["proposal_id"],
            instrument_id=proposal["instrument_id"],
            strategy_id="phase8-explicit-proposal",
            strategy_version="v1",
            decision_at=timestamp,
            state=signal if available else "NO_DECISION",
            candle_completed_at=_utc(completion) if available and completion is not None else None,
            original_signal=signal,
            quantity=proposal["quantity"] if available and signal != "HOLD" else None,
            reference_price=_decimal(candle["close_decimal"], "reference", positive=True)
            if available and candle is not None
            else None,
            proposed_price=_decimal(proposal["entry_price"], "entry", positive=True)
            if proposal.get("entry_price") is not None
            else None,
            confidence=_decimal(proposal["confidence"], "confidence")
            if available and proposal.get("confidence") is not None
            else None,
            stop_price=_decimal(proposal["stop_loss"], "stop", positive=True)
            if proposal.get("stop_loss") is not None
            else None,
            target_price=_decimal(proposal["target_price"], "target", positive=True)
            if proposal.get("target_price") is not None
            else None,
            interval=proposal["timeframe"],
            evidence_references=(f"proposal:{_digest(proposal_json)}",),
            source_import_id=evidence.source_import_id if source else None,
            source_use_status=source["source_use_status"] if source else "UNKNOWN",
            paper_account_id=account_id,
            proposal_id=proposal["proposal_id"],
            proposal_fingerprint=_digest(proposal_json),
            risk_status="PENDING",
            reason_codes=() if available else ("MISSING_COMPLETED_EVIDENCE",),
        )
        return _append_decision(connection, event)


def finish_proposal_decision(
    database_path: Path, account_id: int, proposal_id: str, status: str, reason: str | None
) -> None:
    with closing(get_readonly_connection(database_path)) as connection:
        row = connection.execute(
            """SELECT decision_id FROM decision_journal_events WHERE run_id = ? AND event_id = ?
               AND strategy_id = 'phase8-explicit-proposal'""",
            (f"paper-account:{account_id}", proposal_id),
        ).fetchone()
    if row is not None:
        finish_runtime_decision(database_path, row["decision_id"], status, reason=reason)


def _order_intent(
    connection: sqlite3.Connection,
    account_id: int,
    idempotency_key: str,
    request: Any,
    snapshot: Any,
    event_id: str | None,
) -> str:
    if event_id is not None:
        row = connection.execute(
            "SELECT proposal_json FROM forward_scheduler_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None or row["proposal_json"] is None:
            raise JournalError("Paper order requires its bound proposal journal evidence.")
        existing = connection.execute(
            """SELECT decision_id, payload_json FROM decision_journal_events
               WHERE event_id = ? AND strategy_id = 'runtime-explicit-proposal'
                 AND strategy_version = 'v1' AND role = 'ACTIVE'""",
            (event_id,),
        ).fetchone()
        if existing is not None:
            payload = json.loads(existing["payload_json"])
            if payload["proposal_fingerprint"] != _digest(row["proposal_json"]):
                raise JournalConflictError("Order differs from its journal proposal.")
            return str(existing["decision_id"])
        event = _runtime_decision(connection, event_id, row["proposal_json"])
    else:
        existing = connection.execute(
            """SELECT decision_id, payload_json FROM decision_journal_events
               WHERE run_id = ? AND event_id = ? AND strategy_id = 'phase8-explicit-proposal'""",
            (f"paper-account:{account_id}", idempotency_key),
        ).fetchone()
        if existing is not None:
            payload = json.loads(existing["payload_json"])
            if (
                payload["instrument_id"] != request.instrument_id
                or payload["original_signal"] != request.side
                or payload["quantity"] != request.quantity
            ):
                raise JournalConflictError("Order differs from the recorded proposal.")
            return str(existing["decision_id"])
        evidence = _baseline(
            connection,
            {
                "instrument_id": request.instrument_id,
                "interval": snapshot.interval,
                "information_cutoff": request.signal_timestamp,
                "source_import_id": None,
            },
        )
        event = DecisionEvent(
            run_id=f"paper-account:{account_id}",
            event_id=idempotency_key,
            instrument_id=request.instrument_id,
            strategy_id="phase8-explicit-order",
            strategy_version="v1",
            decision_at=request.signal_timestamp,
            candle_completed_at=evidence["completed_at"] if evidence else None,
            state=request.side,
            original_signal=request.side,
            quantity=request.quantity,
            reference_price=_decimal(evidence["reference_price"], "baseline") if evidence else None,
            interval=snapshot.interval,
            source_import_id=evidence["import_id"] if evidence else None,
            paper_account_id=account_id,
            proposal_id=idempotency_key,
            evidence_references=(f"candle:{evidence['candle_id']}",) if evidence else (),
            risk_status="PENDING",
        )
    if event.role != "ACTIVE":
        raise JournalError("Shadow decisions cannot dispatch orders.")
    return _append_decision(connection, event)


def _order_result(
    connection: sqlite3.Connection,
    decision_id: str,
    order_id: str,
    status: str,
    snapshot: Any,
    *,
    risk_duration_ns: int | None,
    execution_duration_ns: int | None,
) -> None:
    order = connection.execute(
        "SELECT * FROM paper_orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    event = connection.execute(
        "SELECT * FROM decision_journal_events WHERE decision_id = ?", (decision_id,)
    ).fetchone()
    if (
        order is None
        or event is None
        or event["role"] != "ACTIVE"
        or (
            order["account_id"] != event["paper_account_id"]
            or order["requested_instrument_id"] != event["instrument_id"]
        )
    ):
        raise JournalError("Journal-to-order context is inconsistent.")
    fill = connection.execute(
        "SELECT fill_id FROM paper_fills WHERE order_id = ?", (order_id,)
    ).fetchone()
    _append_audit(
        connection,
        decision_id,
        "VALIDATION",
        "ALLOWED" if status == "FILLED" else "BLOCKED",
        {
            "reason_codes": ["PHASE8_ACCEPTED" if status == "FILLED" else "PHASE8_REJECTED"],
            "duration_ns": min(risk_duration_ns, MAX_DURATION_NS)
            if risk_duration_ns is not None
            else None,
            "measurement": "risk_validation",
        },
    )
    _append_audit(
        connection,
        decision_id,
        "EXECUTION",
        status,
        {
            "snapshot_fingerprint": snapshot.snapshot_sha256,
            "quote_event_id": snapshot.quote_event_id,
            "execution_reference_price": str(order["reference_price"]),
            "execution_timestamp": order["execution_timestamp"],
            "duration_ns": min(execution_duration_ns, MAX_DURATION_NS)
            if execution_duration_ns is not None
            else None,
            "measurement": "paper_execution",
        },
        order_id=order_id,
        fill_id=fill["fill_id"] if fill else None,
    )


def record_missing_evaluation(
    connection: sqlite3.Connection,
    runtime_id: str,
    instrument_id: int,
    event_id: str,
    timestamp: str,
    reason_code: str,
    account_id: int | None = None,
) -> str:
    event = DecisionEvent(
        run_id=runtime_id,
        event_id=event_id,
        instrument_id=instrument_id,
        strategy_id="runtime-health",
        strategy_version="v1",
        decision_at=timestamp,
        state="NO_DECISION",
        reason_codes=(reason_code,),
        paper_account_id=account_id,
    )
    identity = _append_decision(connection, event)
    _append_health(
        connection,
        runtime_id,
        event_id,
        reason_code,
        timestamp,
        {"instrument_id": instrument_id, "decision_id": identity},
    )
    return identity


def reconcile_journal(database_path: Path, run_id: str) -> dict[str, Any]:
    """Read-only linkage report; never repairs either journal or financial records."""
    decisions = list_decisions(database_path, run_id, limit=1000)
    unresolved = []
    counts: dict[str, int] = {}
    with closing(get_readonly_connection(database_path)) as connection:
        for item in decisions:
            audits = item["audits"]
            terminal = [a for a in audits if a["stage"] in {"EXECUTION", "VALIDATION"}]
            state = terminal[-1]["status"] if terminal else item["decision"]["state"]
            counts[state] = counts.get(state, 0) + 1
            for audit in audits:
                if audit["stage"] != "EXECUTION":
                    continue
                row = connection.execute(
                    "SELECT * FROM paper_orders WHERE order_id = ?", (audit["order_id"],)
                ).fetchone()
                if (
                    row is None
                    or row["status"] != audit["status"]
                    or (
                        row["account_id"] != item["decision"]["paper_account_id"]
                        or row["requested_instrument_id"] != item["decision"]["instrument_id"]
                    )
                ):
                    unresolved.append(item["decision_id"])
            if not terminal and item["decision"]["state"] in {"BUY", "SELL"}:
                unresolved.append(item["decision_id"])
        # A completely missing SELL intent is a gap too, even with a healthy financial ledger.
        missing_orders = connection.execute(
            """SELECT o.order_id FROM paper_orders o
               LEFT JOIN paper_execution_snapshot_uses s ON s.order_id = o.order_id
               WHERE (s.runtime_id = ? OR
                     (? = 'paper-account:' || o.account_id AND s.runtime_id IS NULL))
                 AND NOT EXISTS (
                     SELECT 1 FROM decision_journal_audits a JOIN decision_journal_events e
                     ON e.decision_id = a.decision_id WHERE a.order_id = o.order_id
                       AND a.stage = 'EXECUTION' AND e.run_id = ?)
               ORDER BY o.order_id LIMIT 1001""",
            (run_id, run_id, run_id),
        ).fetchall()
    return {
        "status": "UNRESOLVED" if unresolved or missing_orders else "RECONCILED",
        "counts": counts,
        "unresolved_decision_ids": sorted(set(unresolved)),
        "unresolved_order_ids": [row["order_id"] for row in missing_orders[:1000]],
        "complete": len(decisions) < 1000 and len(missing_orders) <= 1000,
    }


def recover_order_links(database_path: Path, runtime_id: str) -> tuple[str, ...]:
    """Repair supplemental links from authoritative consumed Phase 8 records only.

    This is explicit restart recovery, never order dispatch or ledger modification.
    Unavailable original quality/calendars are recorded UNKNOWN, not reconstructed as PASS.
    """
    recovered = []
    with _transaction(database_path) as connection:
        rows = connection.execute(
            """SELECT a.event_id, a.proposal_sha256, a.snapshot_sha256, a.quote_event_id,
                      o.order_id, o.status, o.reference_price, e.proposal_json
               FROM forward_execution_authorizations a
               JOIN paper_orders o ON o.order_id = a.consumed_order_id
               JOIN forward_scheduler_events e ON e.event_id = a.event_id
               WHERE a.runtime_id = ? AND a.status = 'CONSUMED'
                 AND NOT EXISTS (
                   SELECT 1 FROM decision_journal_audits j JOIN decision_journal_events d
                   ON d.decision_id = j.decision_id WHERE j.order_id = o.order_id
                     AND j.stage = 'EXECUTION' AND d.run_id = a.runtime_id
                     AND d.event_id = a.event_id)
               ORDER BY a.event_id LIMIT 1001""",
            (runtime_id,),
        ).fetchall()
        if len(rows) > 1000:
            raise JournalError("Journal restart recovery exceeds its explicit batch bound.")
        for row in rows:
            if _digest(row["proposal_json"]) != row["proposal_sha256"]:
                raise JournalError("Recovery proposal fingerprint differs from its authorization.")
            existing = connection.execute(
                """SELECT decision_id, payload_json FROM decision_journal_events WHERE event_id = ?
                   AND strategy_id = 'runtime-explicit-proposal' AND strategy_version = 'v1'
                   AND role = 'ACTIVE'""",
                (row["event_id"],),
            ).fetchone()
            if existing is None:
                event = replace(
                    _runtime_decision(connection, row["event_id"], row["proposal_json"]),
                    calendar_fingerprint=None,
                    calendar_confidence="UNAVAILABLE",
                    source_use_status="UNKNOWN",
                    quality_status="UNKNOWN",
                    reference_price=None,
                    reason_codes=("RECOVERED_FROM_PHASE8_LEDGER",),
                )
                identity = _append_decision(connection, event)
            else:
                identity = existing["decision_id"]
                if (
                    json.loads(existing["payload_json"])["proposal_fingerprint"]
                    != row["proposal_sha256"]
                ):
                    raise JournalError("Recovery conflicts with the original decision.")
            if (
                connection.execute(
                    "SELECT 1 FROM decision_journal_audits WHERE decision_id = ? AND stage = 'EXECUTION'",
                    (identity,),
                ).fetchone()
                is not None
            ):
                continue
            snapshot = SimpleNamespace(
                snapshot_sha256=row["snapshot_sha256"], quote_event_id=row["quote_event_id"]
            )
            _order_result(
                connection,
                identity,
                row["order_id"],
                row["status"],
                snapshot,
                risk_duration_ns=None,
                execution_duration_ns=None,
            )
            _append_audit(
                connection,
                identity,
                "RECOVERY",
                "ALLOWED",
                {"source": "PHASE8_LEDGER", "original_timing_unavailable": True},
            )
            recovered.append(identity)
    return tuple(recovered)


def recover_paper_order_links(database_path: Path, account_id: int) -> tuple[str, ...]:
    """Explicitly recover non-runtime journal gaps without replaying financial effects.

    Original evaluations that were not durably captured remain NO_DECISION / unknown;
    the separate execution audit describes only facts in the authoritative Phase 8 ledger.
    Runtime orders require their stronger consumed-authorization recovery path instead.
    """
    _integer(account_id, "account_id")
    recovered = []
    with _transaction(database_path) as connection:
        rows = connection.execute(
            """SELECT o.*, s.snapshot_sha256, s.import_id FROM paper_orders o
               JOIN paper_execution_snapshot_uses s ON s.order_id = o.order_id
               WHERE o.account_id = ? AND s.runtime_id IS NULL AND NOT EXISTS (
                   SELECT 1 FROM decision_journal_audits a WHERE a.order_id = o.order_id
                   AND a.stage = 'EXECUTION') ORDER BY o.order_id LIMIT 1001""",
            (account_id,),
        ).fetchall()
        if len(rows) > 1000:
            raise JournalError("Journal recovery exceeds its explicit batch bound.")
        for row in rows:
            event = connection.execute(
                """SELECT decision_id FROM decision_journal_events WHERE run_id = ? AND event_id = ?
                   AND role = 'ACTIVE' AND strategy_id IN ('phase8-explicit-order', 'phase8-explicit-proposal')""",
                (f"paper-account:{account_id}", row["idempotency_key"]),
            ).fetchone()
            if event is None:
                identity = _append_decision(
                    connection,
                    DecisionEvent(
                        run_id=f"paper-account:{account_id}",
                        event_id=row["idempotency_key"],
                        instrument_id=row["requested_instrument_id"],
                        strategy_id="phase8-explicit-order",
                        strategy_version="v1",
                        state="NO_DECISION",
                        decision_at=row["signal_timestamp"],
                        original_signal=row["side"],
                        paper_account_id=account_id,
                        source_import_id=row["import_id"],
                        evidence_references=(f"order:{row['order_id']}",),
                        reason_codes=(
                            "ORIGINAL_EVALUATION_UNAVAILABLE",
                            "RECOVERED_FROM_PHASE8_LEDGER",
                        ),
                    ),
                )
            else:
                identity = event["decision_id"]
            _order_result(
                connection,
                identity,
                row["order_id"],
                row["status"],
                SimpleNamespace(snapshot_sha256=row["snapshot_sha256"], quote_event_id=None),
                risk_duration_ns=None,
                execution_duration_ns=None,
            )
            _append_audit(
                connection,
                identity,
                "RECOVERY",
                "ALLOWED",
                {"source": "PHASE8_LEDGER", "original_timing_unavailable": True},
            )
            recovered.append(identity)
    return tuple(recovered)


def evaluate_outcome(
    database_path: Path,
    decision_id: str,
    *,
    horizon_minutes: int,
    evaluated_at: str,
    horizon_kind: str = "TRADING_MINUTES",
    evaluation_version: str = "v1",
    hypothetical_cost: Decimal | None = None,
    supersedes_outcome_id: str | None = None,
) -> str:
    """Append future observations, never rewrite decisions or label them realized P&L.

    Only approved-calendar, permitted completed one-minute candles are eligible.
    Missing observations yield censored metrics; an unelapsed horizon is rejected.
    """
    from aegis.market_quality import calculate_expected_candles, load_approved_calendar_registry

    _integer(horizon_minutes, "horizon_minutes", maximum=1440)
    _identifier(evaluation_version, "evaluation_version")
    _identifier(decision_id, "decision_id")
    if supersedes_outcome_id is not None:
        _identifier(supersedes_outcome_id, "supersedes_outcome_id")
    if horizon_kind not in {"TRADING_MINUTES", "WALL_MINUTES"}:
        raise JournalError("Unsupported horizon definition.")
    as_of = _time(evaluated_at, "evaluated_at")
    if as_of > datetime.now(UTC):
        raise JournalError("Outcome evaluation cannot use future observations.")
    decision = get_decision(database_path, decision_id)["decision"]
    start = _time(decision["decision_at"], "decision_at")
    if as_of <= start:
        raise JournalError("The outcome horizon has not elapsed.")
    calendar_registry = load_approved_calendar_registry()
    with _transaction(database_path) as connection:
        instrument = connection.execute(
            "SELECT * FROM instruments WHERE id = ?", (decision["instrument_id"],)
        ).fetchone()
        calendar = calendar_registry.resolve(instrument["exchange"], instrument["segment"])
        if calendar is None or calendar.confidence != "CONFIRMED":
            raise JournalError("Outcome evaluation requires an approved CONFIRMED calendar.")
        zone = ZoneInfo(calendar.timezone_name)
        dates = start.astimezone(zone).date()
        expected = []
        wall_end = start + timedelta(minutes=horizon_minutes)
        for day_offset in range(32):
            day = dates + timedelta(days=day_offset)
            scheduled, _, _, uncovered = calculate_expected_candles(
                calendar, "1m", day, day, as_of=as_of
            )
            if uncovered:
                raise JournalError("Outcome horizon is outside approved calendar coverage.")
            for candle in scheduled:
                if (
                    _time(candle.timestamp, "future candle") >= start
                    and candle.completes_at.astimezone(UTC) > start
                ):
                    if (
                        horizon_kind == "WALL_MINUTES"
                        and candle.completes_at.astimezone(UTC) > wall_end
                    ):
                        break
                    expected.append(candle)
                    if horizon_kind == "TRADING_MINUTES" and len(expected) == horizon_minutes:
                        break
            if (horizon_kind == "TRADING_MINUTES" and len(expected) == horizon_minutes) or (
                horizon_kind == "WALL_MINUTES" and day >= wall_end.astimezone(zone).date()
            ):
                break
        end = expected[-1].completes_at.astimezone(UTC) if expected else wall_end
        if horizon_kind == "TRADING_MINUTES" and len(expected) != horizon_minutes:
            raise JournalError("The bounded trading horizon cannot be resolved.")
        if horizon_kind == "WALL_MINUTES":
            end = wall_end
        if as_of < end:
            raise JournalError("The outcome horizon has not elapsed.")
        rows = connection.execute(
            """SELECT c.*, d.source_use_status, d.status AS import_status, d.completed_at,
                      d.file_sha256, h.new_status AS declared_status, h.declared_at
               FROM historical_candles c
               JOIN data_imports d ON d.id = c.import_id
               LEFT JOIN import_source_use_history h ON h.id =
                   (SELECT MAX(id) FROM import_source_use_history WHERE import_id = d.id)
               WHERE c.instrument_id = ? AND c.interval = '1m'
                 AND c.candle_timestamp >= ? AND c.candle_timestamp < ?
               ORDER BY c.candle_timestamp LIMIT 1441""",
            (
                decision["instrument_id"],
                _utc(start).replace(".000000Z", "Z"),
                _utc(end).replace(".000000Z", "Z"),
            ),
        ).fetchall()
        by_time = {row["candle_timestamp"]: row for row in rows}
        selected = []
        for candle in expected:
            row = by_time.get(candle.timestamp)
            if (
                row is None
                or row["source_use_status"] != "VERIFIED_PERMITTED"
                or row["declared_status"] != "VERIFIED_PERMITTED"
                or row["declared_at"] is None
                or _import_time(row["declared_at"]) > as_of
                or (
                    row["import_status"] not in {"COMPLETED", "COMPLETED_WITH_ERRORS"}
                    or row["completed_at"] is None
                    or _import_time(row["completed_at"]) > as_of
                )
            ):
                continue
            try:
                prices = [
                    _decimal(row[key], key, positive=True)
                    for key in ("open_decimal", "high_decimal", "low_decimal", "close_decimal")
                ]
                if prices[1] < max(prices) or prices[2] > min(prices):
                    continue
                tick = _decimal(instrument["tick_size_decimal"], "tick", positive=True)
                if any(price % tick != 0 for price in prices) or row["volume"] < 0:
                    continue
            except JournalError:
                continue
            selected.append(row)
        wall_covered = horizon_kind == "TRADING_MINUTES" or (
            len(expected) == horizon_minutes
            and bool(expected)
            and expected[-1].completes_at.astimezone(UTC) == wall_end
            and _time(expected[0].timestamp, "horizon start") == start
        )
        complete = (
            wall_covered
            and bool(expected)
            and len(selected) == len(expected)
            and len(by_time) == len(rows)
        )
        authenticated = (
            _baseline(connection, decision) if decision.get("baseline_evidence") else None
        )
        baseline_valid = authenticated is not None and authenticated == decision.get(
            "baseline_evidence"
        )
        baseline_valid = bool(
            baseline_valid
            and authenticated
            and decision.get("reference_price") is not None
            and _decimal(decision["reference_price"], "reference")
            == _decimal(authenticated["reference_price"], "baseline")
            and decision.get("candle_completed_at") == authenticated["completed_at"]
        )
        reference = authenticated["reference_price"] if baseline_valid and authenticated else None
        observed = favorable = adverse = hypothetical = None
        cost = (
            None if hypothetical_cost is None else _decimal(hypothetical_cost, "hypothetical_cost")
        )
        if cost is not None and cost < 0:
            raise JournalError("Hypothetical cost cannot be negative.")
        if complete and reference is not None:
            with localcontext() as context:
                context.prec = 50
                base = _decimal(reference, "reference price", positive=True)
                observed = (Decimal(selected[-1]["close_decimal"]) - base) / base
                favorable = (max(Decimal(r["high_decimal"]) for r in selected) - base) / base
                adverse = (min(Decimal(r["low_decimal"]) for r in selected) - base) / base
                if cost is not None:
                    hypothetical = observed - cost
        realized = connection.execute(
            """SELECT p.order_id, p.closed_at FROM decision_journal_audits a JOIN paper_realized_pnl p
               ON p.order_id = a.order_id WHERE a.decision_id = ? AND a.stage = 'EXECUTION'""",
            (decision_id,),
        ).fetchone()
        payload = {
            "decision_id": decision_id,
            "horizon_minutes": horizon_minutes,
            "horizon_kind": horizon_kind,
            "evaluation_version": evaluation_version,
            "as_of_end": _utc(end),
            "evaluated_at": _utc(as_of),
            "coverage_status": "UNAVAILABLE"
            if not baseline_valid
            else "COMPLETE"
            if complete
            else "INCOMPLETE",
            "censored_reason": "MISSING_QUALIFIED_DECISION_BASELINE"
            if not baseline_valid
            else None
            if complete
            else "NON_TRADING_OR_PARTIAL_HORIZON"
            if not wall_covered
            else "MISSING_OR_UNQUALIFIED_OBSERVATIONS",
            "observed_return": observed,
            "favorable_excursion": favorable,
            "adverse_excursion": adverse,
            "hypothetical_net_after_cost": hypothetical,
            "hypothetical_cost_fraction": cost,
            "hypothetical_assumption": "LONG_AT_REFERENCE_NO_FILL_MODEL"
            if cost is not None
            else None,
            "actual_paper_pnl_ref": realized["order_id"]
            if realized and _time(realized["closed_at"], "paper close") <= as_of
            else None,
            "calendar_fingerprint": calendar_registry.configuration_sha256,
            "evidence": [[r["id"], r["import_id"], r["file_sha256"]] for r in selected],
            "supersedes_outcome_id": supersedes_outcome_id,
        }
        identity = _digest(
            _json(
                [
                    decision_id,
                    horizon_minutes,
                    horizon_kind,
                    evaluation_version,
                    supersedes_outcome_id,
                ]
            )
        )
        if supersedes_outcome_id is not None:
            previous = connection.execute(
                "SELECT decision_id FROM decision_journal_outcomes WHERE outcome_id = ?",
                (supersedes_outcome_id,),
            ).fetchone()
            if previous is None or previous["decision_id"] != decision_id:
                raise JournalError("Outcome correction must reference the same decision.")
        if not _existing(connection, "decision_journal_outcomes", "outcome_id", identity, payload):
            text = _json(payload)
            connection.execute(
                """INSERT INTO decision_journal_outcomes
                   (outcome_id, decision_id, horizon_minutes, horizon_kind, evaluation_version,
                    coverage_status, as_of_end, evaluated_at, payload_json, payload_sha256,
                    supersedes_outcome_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    identity,
                    decision_id,
                    horizon_minutes,
                    horizon_kind,
                    evaluation_version,
                    payload["coverage_status"],
                    _utc(end),
                    _utc(as_of),
                    text,
                    _digest(text),
                    supersedes_outcome_id,
                ),
            )
    return identity
