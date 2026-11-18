"""Deterministic synthetic streaming and research-only forward paper runtime."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Generator, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from zoneinfo import ZoneInfo

from aegis import decision_journal
from aegis.database import (
    _authorize_scheduler_event_write,
    _SchedulerEventWrite,
    get_connection,
    get_readonly_connection,
)
from aegis.forward_paper_trading import (
    DecisionAction,
    DecisionProposal,
    ForwardPaperTradingEngine,
    MarketEvidence,
    ProposalExecutionResult,
    ProposalValidationError,
    _proposal_canonical_json,
    _proposal_sha256,
)
from aegis.market_quality import CalendarRegistry, analyze_dataset
from aegis.paper_trading import (
    ExecutionSnapshot,
    PaperHaltClass,
    PaperOrderRequest,
    PaperTradingError,
    _forward_context_error,
    get_paper_account_report,
    halt_blocks_order,
    reconcile_paper_account,
    record_paper_mark,
    submit_paper_order,
)

_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15}
_INDIA = ZoneInfo("Asia/Kolkata")
FeedStatus = Literal["STARTING", "HEALTHY", "DEGRADED", "STALE", "DISCONNECTED"]


class ForwardRuntimeError(ValueError):
    """Raised when persisted synthetic runtime configuration or state is unsafe."""


@dataclass(frozen=True, slots=True)
class Quote:
    """Provider-neutral quote event. Validation is performed at the runtime boundary."""

    instrument_id: int
    timestamp: str
    sequence: int
    price: Decimal
    quantity: int
    event_id: str
    received_at: str | None = None


class QuoteProvider(Protocol):
    """Provider-neutral stream contract for read-only quotes."""

    def quotes(self, count: int) -> Iterator[Quote]:
        """Yield one quote per configured instrument for each of ``count`` rounds."""


class DeterministicSyntheticQuoteProvider:
    """Generate reproducible independent quote sequences for multiple instruments."""

    def __init__(
        self,
        instrument_ids: tuple[int, ...],
        *,
        start_at: datetime,
        base_prices: Mapping[int, Decimal],
        price_steps: Mapping[int, Decimal] | None = None,
    ) -> None:
        if not instrument_ids or len(set(instrument_ids)) != len(instrument_ids):
            raise ForwardRuntimeError("Synthetic provider requires unique instrument IDs.")
        if start_at.tzinfo is None or start_at.utcoffset() is None:
            raise ForwardRuntimeError("start_at must be timezone-aware.")
        if set(instrument_ids) - set(base_prices):
            raise ForwardRuntimeError("Every synthetic instrument requires a base price.")
        self.instrument_ids = tuple(sorted(instrument_ids))
        self.start_at = start_at.astimezone(UTC)
        self.base_prices = dict(base_prices)
        self.price_steps = dict(price_steps or {})
        for instrument_id in self.instrument_ids:
            _decimal(self.base_prices[instrument_id], "base price")
            _decimal(self.price_steps.get(instrument_id, Decimal("0.05")), "price step")

    def quotes(self, count: int) -> Iterator[Quote]:
        """Yield deterministic one-minute quotes for each instrument in every round."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ForwardRuntimeError("count must be a non-negative integer.")
        for round_index in range(count):
            timestamp = (self.start_at + timedelta(minutes=round_index)).isoformat()
            for instrument_id in self.instrument_ids:
                sequence = round_index + 1
                price = (
                    self.base_prices[instrument_id]
                    + self.price_steps.get(instrument_id, Decimal("0.05")) * sequence
                )
                event_id = _stable_id(f"quote:{instrument_id}:{sequence}:{timestamp}:{price}")
                yield Quote(
                    instrument_id,
                    timestamp,
                    sequence,
                    price,
                    1,
                    event_id,
                    timestamp,
                )


@dataclass(frozen=True, slots=True)
class FeedHealth:
    """Observable feed state for one instrument."""

    instrument_id: int
    status: FeedStatus
    last_event_at: str | None
    age_seconds: Decimal | None
    rejected_events: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class QuoteResult:
    """Outcome of applying one quote event."""

    accepted: bool
    duplicate: bool
    reason: str | None
    completed_candles: tuple[dict[str, object], ...] = ()
    scheduled_event_ids: tuple[str, ...] = ()
    exit_result: ProposalExecutionResult | None = None


@contextmanager
def _transaction(database_path: Path) -> Generator[sqlite3.Connection, None, None]:
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


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ForwardRuntimeError(f"{label} must use exact Decimal or decimal text.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int)):
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise ForwardRuntimeError(f"{label} must be a valid decimal.") from exc
    else:
        raise ForwardRuntimeError(f"{label} must be a valid decimal.")
    if not result.is_finite():
        raise ForwardRuntimeError(f"{label} must be finite.")
    return result


def _timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ForwardRuntimeError(f"{label} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ForwardRuntimeError(f"{label} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForwardRuntimeError(f"{label} must include a timezone.")
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bucket(value: datetime, minutes: int) -> datetime:
    value = value.astimezone(UTC).replace(second=0, microsecond=0)
    minute = value.minute - value.minute % minutes
    return value.replace(minute=minute)


class ForwardRuntimeService:
    """Persistent synthetic quote runtime reusing Phase 8 paper controls and accounting."""

    def __init__(
        self,
        database_path: Path,
        runtime_id: str,
        account_id: int,
        instrument_ids: tuple[int, ...],
        calendars: CalendarRegistry,
        *,
        max_candidates: int,
        max_quote_delay_seconds: int,
        degraded_after_seconds: int,
        stale_after_seconds: int,
        max_execution_price_deviation: Decimal | None,
        state: dict[str, Any],
    ) -> None:
        self.database_path = Path(database_path)
        self.runtime_id = runtime_id
        self.account_id = account_id
        self.instrument_ids = instrument_ids
        self.calendars = calendars
        self.max_candidates = max_candidates
        self.max_quote_delay_seconds = max_quote_delay_seconds
        self.degraded_after_seconds = degraded_after_seconds
        self.stale_after_seconds = stale_after_seconds
        self.max_execution_price_deviation = max_execution_price_deviation
        self._state = state
        self._scheduler_dirty = False
        self._journal_recovery_error = False
        self._proposal_timings: dict[str, tuple[str, int]] = {}

    @classmethod
    def create(
        cls,
        database_path: Path,
        runtime_id: str,
        account_id: int,
        instrument_ids: tuple[int, ...],
        calendars: CalendarRegistry,
        *,
        max_candidates: int = 5,
        max_quote_delay_seconds: int = 30,
        degraded_after_seconds: int = 10,
        stale_after_seconds: int = 30,
        max_execution_price_deviation: Decimal | None = None,
        created_at: str | None = None,
    ) -> ForwardRuntimeService:
        """Persist a new immutable runtime configuration and synthetic provenance."""
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise ForwardRuntimeError("runtime_id must be non-empty.")
        runtime_id = runtime_id.strip()
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id < 1:
            raise ForwardRuntimeError("account_id must be a positive integer.")
        if not instrument_ids or len(set(instrument_ids)) != len(instrument_ids):
            raise ForwardRuntimeError("instrument_ids must be unique and non-empty.")
        if (
            isinstance(max_candidates, bool)
            or not isinstance(max_candidates, int)
            or max_candidates < 1
        ):
            raise ForwardRuntimeError("max_candidates must be a positive integer.")
        timing_limits = (
            max_quote_delay_seconds,
            degraded_after_seconds,
            stale_after_seconds,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in timing_limits
        ):
            raise ForwardRuntimeError("Feed timing limits must be non-negative integers.")
        if stale_after_seconds <= degraded_after_seconds:
            raise ForwardRuntimeError("stale_after_seconds must exceed degraded_after_seconds.")
        if max_execution_price_deviation is not None and (
            not isinstance(max_execution_price_deviation, Decimal)
            or not max_execution_price_deviation.is_finite()
            or max_execution_price_deviation < 0
        ):
            raise ForwardRuntimeError(
                "max_execution_price_deviation must be a non-negative finite Decimal."
            )
        reconciliation = reconcile_paper_account(database_path, account_id)
        if reconciliation["status"] != "RECONCILED":
            raise ForwardRuntimeError(
                "Paper account reconciliation failed before runtime creation."
            )
        config: dict[str, object] = {
            "instrument_ids": sorted(instrument_ids),
            "max_candidates": max_candidates,
            "max_quote_delay_seconds": max_quote_delay_seconds,
            "degraded_after_seconds": degraded_after_seconds,
            "stale_after_seconds": stale_after_seconds,
            "max_execution_price_deviation": (
                str(max_execution_price_deviation)
                if max_execution_price_deviation is not None
                else None
            ),
            "mode": "RESEARCH_ONLY",
            "provider": "DETERMINISTIC_SYNTHETIC",
        }
        config_json = _canonical_json(config)
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()
        state = cls._empty_state(instrument_ids)
        state_json = _canonical_json(state)
        timestamp = _timestamp_text(
            _timestamp(created_at, "created_at") if created_at else datetime.now(UTC)
        )
        with _transaction(database_path) as connection:
            account = connection.execute(
                "SELECT id, halted, halt_reason, halt_class FROM paper_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if account is None:
                raise ForwardRuntimeError("A persisted Phase 8 paper account is required.")
            if halt_blocks_order(
                account["halted"], account["halt_class"], account["halt_reason"], "SELL"
            ):
                raise ForwardRuntimeError(
                    "Paper account has an integrity or unclassified halt before runtime creation."
                )
            if connection.execute(
                "SELECT 1 FROM forward_runtimes WHERE runtime_id = ?", (runtime_id,)
            ).fetchone():
                raise ForwardRuntimeError("runtime_id already exists.")
            for instrument_id in instrument_ids:
                instrument = connection.execute(
                    "SELECT exchange, trading_symbol, is_active FROM instruments WHERE id = ?",
                    (instrument_id,),
                ).fetchone()
                if instrument is None or not instrument["is_active"]:
                    raise ForwardRuntimeError(f"Instrument {instrument_id} is unavailable.")
            connection.execute(
                """INSERT INTO forward_runtimes
                   (runtime_id, account_id, config_json, config_sha256, state_json,
                    state_sha256, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)""",
                (
                    runtime_id,
                    account_id,
                    config_json,
                    config_hash,
                    state_json,
                    hashlib.sha256(state_json.encode()).hexdigest(),
                    timestamp,
                    timestamp,
                ),
            )
            for instrument_id in instrument_ids:
                instrument = connection.execute(
                    "SELECT exchange, trading_symbol FROM instruments WHERE id = ?",
                    (instrument_id,),
                ).fetchone()
                assert instrument is not None
                for interval in _INTERVAL_MINUTES:
                    import_hash = _stable_id(f"{runtime_id}:{instrument_id}:{interval}")
                    cursor = connection.execute(
                        """INSERT INTO data_imports
                           (import_type, status, started_at, completed_at, records_received,
                            records_inserted, metadata_json, instrument_id, original_filename,
                            file_sha256, requested_exchange, requested_trading_symbol, interval,
                            total_rows, accepted_rows, rejected_rows, skipped_rows, duplicate_rows,
                            same_file_reimport, source_use_status)
                           VALUES ('SYNTHETIC_STREAM', 'COMPLETED', ?, ?, 0, 0, ?, ?, ?, ?,
                                   ?, ?, ?, 0, 0, 0, 0, 0, 0, 'VERIFIED_PERMITTED')""",
                        (
                            timestamp,
                            timestamp,
                            _canonical_json(
                                {
                                    "runtime_id": runtime_id,
                                    "provider": "DETERMINISTIC_SYNTHETIC",
                                    "source_use_rationale": "Generated in-process synthetic data.",
                                }
                            ),
                            instrument_id,
                            f"{runtime_id}-{instrument_id}-{interval}.synthetic",
                            import_hash,
                            instrument["exchange"],
                            instrument["trading_symbol"],
                            interval,
                        ),
                    )
                    if cursor.lastrowid is None:
                        raise ForwardRuntimeError(
                            "SQLite did not return synthetic import identity."
                        )
                    import_id = int(cursor.lastrowid)
                    connection.execute(
                        """INSERT INTO import_source_use_history
                           (import_id, previous_status, new_status, declared_by, rationale)
                           VALUES (?, NULL, 'VERIFIED_PERMITTED', ?, ?)""",
                        (
                            import_id,
                            "Project Aegis deterministic synthetic provider",
                            "Generated locally by the research-only test/runtime provider.",
                        ),
                    )
                    connection.execute(
                        """INSERT INTO forward_runtime_imports
                           (runtime_id, instrument_id, interval, import_id)
                           VALUES (?, ?, ?, ?)""",
                        (runtime_id, instrument_id, interval, import_id),
                    )
            cls._journal_health(
                connection,
                runtime_id,
                "startup",
                "STARTED",
                timestamp,
                {"configuration_fingerprint": config_hash, "mode": "RESEARCH_ONLY"},
            )
        return cls(
            database_path,
            runtime_id,
            account_id,
            tuple(sorted(instrument_ids)),
            calendars,
            max_candidates=max_candidates,
            max_quote_delay_seconds=max_quote_delay_seconds,
            degraded_after_seconds=degraded_after_seconds,
            stale_after_seconds=stale_after_seconds,
            max_execution_price_deviation=max_execution_price_deviation,
            state=state,
        )

    @classmethod
    def open(
        cls,
        database_path: Path,
        runtime_id: str,
        calendars: CalendarRegistry,
    ) -> ForwardRuntimeService:
        """Restore checksummed runtime state and reconcile the existing paper ledger."""
        with closing(get_connection(database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM forward_runtimes WHERE runtime_id = ?", (runtime_id,)
            ).fetchone()
            if row is None:
                raise ForwardRuntimeError("Forward runtime does not exist.")
            if hashlib.sha256(row["config_json"].encode()).hexdigest() != row["config_sha256"]:
                cls._set_halted(
                    database_path,
                    runtime_id,
                    int(row["account_id"]),
                    "Runtime config checksum mismatch.",
                )
                raise ForwardRuntimeError("Persisted runtime configuration is corrupted.")
            state_json = row["state_json"]
            if hashlib.sha256(state_json.encode()).hexdigest() != row["state_sha256"]:
                cls._set_halted(
                    database_path,
                    runtime_id,
                    int(row["account_id"]),
                    "Runtime state checksum mismatch.",
                )
                raise ForwardRuntimeError("Persisted runtime state is corrupted.")
            try:
                config = json.loads(row["config_json"])
                state = json.loads(state_json)
                instrument_ids = tuple(int(value) for value in config["instrument_ids"])
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                cls._set_halted(
                    database_path, runtime_id, int(row["account_id"]), "Runtime state is malformed."
                )
                raise ForwardRuntimeError("Persisted runtime state is malformed.") from exc
            if not isinstance(state, dict) or {str(value) for value in instrument_ids} != set(
                state.get("cursor", {})
            ):
                cls._set_halted(
                    database_path, runtime_id, int(row["account_id"]), "Runtime cursor mismatch."
                )
                raise ForwardRuntimeError("Persisted runtime cursors are corrupted.")
            account_id = int(row["account_id"])
            persisted_status = str(row["status"])
            raw_deviation = config.get("max_execution_price_deviation")
            try:
                max_execution_price_deviation = (
                    Decimal(str(raw_deviation)) if raw_deviation is not None else None
                )
            except InvalidOperation as exc:
                raise ForwardRuntimeError(
                    "Persisted execution-price deviation limit is malformed."
                ) from exc
            if max_execution_price_deviation is not None and (
                not max_execution_price_deviation.is_finite() or max_execution_price_deviation < 0
            ):
                raise ForwardRuntimeError("Persisted execution-price deviation limit is invalid.")
            config = dict(config)
            state = dict(state)
        reconciliation = reconcile_paper_account(database_path, account_id)
        if reconciliation["status"] != "RECONCILED":
            cls._set_halted(
                database_path,
                runtime_id,
                account_id,
                "Paper account reconciliation failed on restart.",
            )
            raise ForwardRuntimeError("Paper account reconciliation failed on restart.")
        with closing(get_readonly_connection(database_path)) as connection:
            account = connection.execute(
                "SELECT halted, halt_reason, halt_class FROM paper_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if account is None or halt_blocks_order(
            account["halted"], account["halt_class"], account["halt_reason"], "SELL"
        ):
            raise ForwardRuntimeError(
                "Paper account has an integrity or unclassified halt on restart."
            )
        if persisted_status == "HALTED":
            raise ForwardRuntimeError("Forward runtime remains halted after restart.")
        service = cls(
            database_path,
            runtime_id,
            account_id,
            instrument_ids,
            calendars,
            max_candidates=int(config["max_candidates"]),
            max_quote_delay_seconds=int(config["max_quote_delay_seconds"]),
            degraded_after_seconds=int(config["degraded_after_seconds"]),
            stale_after_seconds=int(config["stale_after_seconds"]),
            max_execution_price_deviation=max_execution_price_deviation,
            state=state,
        )
        try:
            service._recover_exit_controls()
            service.recover_scheduler_events()
        except (
            ForwardRuntimeError,
            PaperTradingError,
            sqlite3.Error,
        ) as exc:
            cls._set_halted(
                database_path,
                runtime_id,
                account_id,
                f"Runtime recovery failure: {exc}",
            )
            raise ForwardRuntimeError(
                "Forward runtime recovery failed; runtime remains halted."
            ) from exc
        service._journal_recovery_error = False
        try:
            decision_journal.recover_order_links(database_path, runtime_id)
            decision_journal.record_health(
                database_path,
                runtime_id,
                f"restart:{_timestamp_text(datetime.now(UTC))}",
                "RESTART_RECONCILED",
                _timestamp_text(datetime.now(UTC)),
                {"financial_ledger": "RECONCILED"},
            )
        except (decision_journal.JournalError, sqlite3.Error):
            # Supplemental recovery blocks new entries, never protective exits.
            service._journal_recovery_error = True
        with _transaction(database_path) as connection:
            connection.execute(
                "UPDATE forward_runtimes SET status = 'RUNNING', updated_at = ? WHERE runtime_id = ?",
                (_timestamp_text(datetime.now(UTC)), runtime_id),
            )
        return service

    @staticmethod
    def _empty_state(instrument_ids: tuple[int, ...]) -> dict[str, Any]:
        keys = [str(value) for value in instrument_ids]
        return {
            "cursor": {key: {"sequence": 0, "timestamp": None} for key in keys},
            "feed": {key: {"status": "STARTING", "reason": None, "rejected": 0} for key in keys},
            "current_1m": {key: None for key in keys},
            "current_aggregates": {key: {"5m": None, "15m": None} for key in keys},
            "last_prices": {},
            "scheduler_recovery": {
                "recovered_total": 0,
                "last_recovered_event_ids": [],
                "last_completed_at": None,
            },
        }

    @staticmethod
    def _journal_health(
        connection: sqlite3.Connection,
        runtime_id: str,
        event_id: str,
        kind: str,
        timestamp: str,
        details: dict[str, Any],
    ) -> None:
        """Supplemental health recording cannot undo a halt or delay a protective exit."""
        connection.execute("SAVEPOINT journal_health")
        try:
            decision_journal._append_health(
                connection, runtime_id, event_id, kind, timestamp, details
            )
        except (decision_journal.JournalError, sqlite3.Error):
            connection.execute("ROLLBACK TO journal_health")
        finally:
            connection.execute("RELEASE journal_health")

    @staticmethod
    def _halt(
        connection: sqlite3.Connection, runtime_id: str, account_id: int, reason: str
    ) -> None:
        connection.execute(
            "UPDATE forward_runtimes SET status = 'HALTED', updated_at = ? WHERE runtime_id = ?",
            (_timestamp_text(datetime.now(UTC)), runtime_id),
        )
        account = connection.execute(
            "SELECT halted, halt_reason, halt_class FROM paper_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if account is not None and not (
            account["halted"] and account["halt_class"] == PaperHaltClass.INTEGRITY.value
        ):
            connection.execute(
                """UPDATE paper_accounts SET halted = 1, halt_reason = ?,
                   halt_class = 'INTEGRITY' WHERE id = ?""",
                (reason, account_id),
            )
        timestamp = _timestamp_text(datetime.now(UTC))
        ForwardRuntimeService._journal_health(
            connection,
            runtime_id,
            f"halt:{timestamp}",
            "INTEGRITY_HALT",
            timestamp,
            {"reason_fingerprint": hashlib.sha256(reason.encode()).hexdigest()},
        )

    @classmethod
    def _set_halted(
        cls, database_path: Path, runtime_id: str, account_id: int, reason: str
    ) -> None:
        with _transaction(database_path) as connection:
            cls._halt(connection, runtime_id, account_id, reason)

    def _ensure_scheduler_recovered(self) -> None:
        if not self._scheduler_dirty:
            return
        try:
            self.recover_scheduler_events()
        except (ForwardRuntimeError, PaperTradingError, sqlite3.Error) as exc:
            self._set_halted(
                self.database_path,
                self.runtime_id,
                self.account_id,
                f"Scheduler integrity recovery failed: {exc}",
            )
            raise ForwardRuntimeError(
                "Scheduler recovery failed; runtime and paper account are halted."
            ) from exc

    def _ensure_runtime_ready(self) -> None:
        with closing(get_readonly_connection(self.database_path)) as connection:
            runtime = connection.execute(
                """SELECT status, account_id, config_json, config_sha256,
                          state_json, state_sha256
                   FROM forward_runtimes WHERE runtime_id = ?""",
                (self.runtime_id,),
            ).fetchone()
        if runtime is None:
            raise ForwardRuntimeError("Forward runtime does not exist.")
        if runtime["status"] == "HALTED":
            raise ForwardRuntimeError("Runtime is halted.")
        if (
            runtime["account_id"] != self.account_id
            or hashlib.sha256(runtime["config_json"].encode()).hexdigest()
            != runtime["config_sha256"]
            or hashlib.sha256(runtime["state_json"].encode()).hexdigest() != runtime["state_sha256"]
        ):
            self._set_halted(
                self.database_path,
                self.runtime_id,
                self.account_id,
                "Runtime configuration or state integrity check failed.",
            )
            raise ForwardRuntimeError("Runtime integrity check failed; runtime is halted.")
        if runtime["status"] not in ("RUNNING", "DEGRADED"):
            raise ForwardRuntimeError("Forward runtime status is invalid.")
        reconciliation = reconcile_paper_account(self.database_path, self.account_id)
        if reconciliation["status"] != "RECONCILED":
            self._set_halted(
                self.database_path,
                self.runtime_id,
                self.account_id,
                "Paper account reconciliation failed during runtime activity.",
            )
            raise ForwardRuntimeError("Paper account reconciliation failed; runtime is halted.")
        with closing(get_readonly_connection(self.database_path)) as connection:
            account = connection.execute(
                "SELECT halted, halt_reason, halt_class FROM paper_accounts WHERE id = ?",
                (self.account_id,),
            ).fetchone()
        if account is None or halt_blocks_order(
            account["halted"], account["halt_class"], account["halt_reason"], "SELL"
        ):
            self._set_halted(
                self.database_path,
                self.runtime_id,
                self.account_id,
                "Paper account has an integrity or unclassified halt.",
            )
            raise ForwardRuntimeError(
                "Paper account has an integrity or unclassified halt; runtime is halted."
            )

    def process_quote(self, quote: Quote) -> QuoteResult:
        """Validate one event, persist its cursor, and store only completed candles."""
        if not isinstance(quote, Quote):
            raise ForwardRuntimeError("quote must be a Quote instance.")
        try:
            self._ensure_runtime_ready()
            self._ensure_scheduler_recovered()
        except ForwardRuntimeError as exc:
            return QuoteResult(False, False, str(exc))
        try:
            now = _timestamp(quote.received_at or quote.timestamp, "received_at")
        except ForwardRuntimeError as exc:
            fallback_time = datetime.now(UTC)
            return self._reject_quote(quote, fallback_time, str(exc))
        instrument_key = str(quote.instrument_id)
        try:
            if (
                isinstance(quote.instrument_id, bool)
                or not isinstance(quote.instrument_id, int)
                or quote.instrument_id < 1
            ):
                raise ForwardRuntimeError("instrument_id must be a positive integer.")
            observed = _timestamp(quote.timestamp, "quote timestamp")
            if now > datetime.now(UTC) + timedelta(seconds=self.max_quote_delay_seconds):
                raise ForwardRuntimeError("Quote received_at cannot be in the future.")
            price = _decimal(quote.price, "quote price")
            if (
                isinstance(quote.sequence, bool)
                or not isinstance(quote.sequence, int)
                or quote.sequence < 1
            ):
                raise ForwardRuntimeError("sequence must be a positive integer.")
            if (
                isinstance(quote.quantity, bool)
                or not isinstance(quote.quantity, int)
                or quote.quantity < 0
            ):
                raise ForwardRuntimeError("quantity must be a non-negative integer.")
            if not isinstance(quote.event_id, str) or not quote.event_id.strip():
                raise ForwardRuntimeError("event_id must be non-empty.")
            if len(quote.event_id) > 256:
                raise ForwardRuntimeError("event_id exceeds the 256-character limit.")
            if price <= 0:
                raise ForwardRuntimeError("quote price must be positive.")
            if quote.instrument_id not in self.instrument_ids:
                raise ForwardRuntimeError("Quote instrument is not configured in this runtime.")
            age = (now - observed).total_seconds()
            if age < 0:
                raise ForwardRuntimeError("Quote timestamp cannot be in the future.")
            if age > self.max_quote_delay_seconds:
                raise ForwardRuntimeError("Delayed quote exceeds the configured age limit.")
        except ForwardRuntimeError as exc:
            return self._reject_quote(quote, now, str(exc))

        with _transaction(self.database_path) as connection:
            duplicate = connection.execute(
                """SELECT accepted, reason FROM forward_quotes
                   WHERE runtime_id = ? AND instrument_id = ? AND event_id = ?""",
                (self.runtime_id, quote.instrument_id, quote.event_id),
            ).fetchone()
            if duplicate is not None:
                return QuoteResult(
                    accepted=bool(duplicate["accepted"]),
                    duplicate=True,
                    reason=duplicate["reason"],
                )
            runtime = connection.execute(
                "SELECT status, state_json, state_sha256 FROM forward_runtimes WHERE runtime_id = ?",
                (self.runtime_id,),
            ).fetchone()
            if runtime is None or runtime["status"] not in ("RUNNING", "DEGRADED"):
                return QuoteResult(False, False, "Runtime is halted.")
            if (
                hashlib.sha256(runtime["state_json"].encode()).hexdigest()
                != runtime["state_sha256"]
            ):
                self._halt(
                    connection, self.runtime_id, self.account_id, "Runtime state checksum mismatch."
                )
                raise ForwardRuntimeError("Persisted runtime state is corrupted.")
            self._state = json.loads(runtime["state_json"])
            cursor = self._state["cursor"][instrument_key]
            cursor_time = cursor["timestamp"]
            reason = None
            if quote.sequence <= cursor["sequence"]:
                reason = "Out-of-order quote sequence."
            elif cursor_time is not None and observed <= _timestamp(cursor_time, "stored cursor"):
                reason = "Out-of-order quote timestamp."
            if reason is None:
                instrument = connection.execute(
                    "SELECT exchange, segment, tick_size_decimal FROM instruments WHERE id = ?",
                    (quote.instrument_id,),
                ).fetchone()
                if instrument is None:
                    reason = "Quote instrument is not registered."
                else:
                    reason = self._session_rejection(instrument, observed)
                tick_text = instrument["tick_size_decimal"] if instrument is not None else None
                if reason is None:
                    if not tick_text:
                        reason = "Instrument requires an exact decimal tick size."
                    else:
                        tick = Decimal(tick_text)
                        if tick <= 0 or price % tick != 0:
                            reason = "Quote price is not aligned to the exact instrument tick size."
            if reason is not None:
                self._record_rejection(connection, quote, now, reason)
                return QuoteResult(False, False, reason)
            completed = self._apply_quote(connection, quote, observed, now, price)
            state_json = _canonical_json(self._state)
            connection.execute(
                """UPDATE forward_runtimes SET state_json = ?, state_sha256 = ?,
                   status = 'RUNNING', last_event_at = ?, updated_at = ?
                   WHERE runtime_id = ?""",
                (
                    state_json,
                    hashlib.sha256(state_json.encode()).hexdigest(),
                    _timestamp_text(observed),
                    _timestamp_text(now),
                    self.runtime_id,
                ),
            )
        scheduled_event_ids: list[str] = []
        if completed:
            self._scheduler_dirty = True
        for completed_candle in completed:
            scheduled_id = self.schedule_event(
                int(completed_candle["instrument_id"]),
                str(completed_candle["interval"]),
                str(completed_candle["candle_timestamp"]),
            )
            if scheduled_id:
                scheduled_event_ids.append(scheduled_id)
        self._scheduler_dirty = False
        exit_result = self._monitor_position(quote, observed, now, price)
        return QuoteResult(
            True,
            False,
            None,
            tuple(completed),
            tuple(scheduled_event_ids),
            exit_result,
        )

    def _session_rejection(self, instrument: sqlite3.Row, observed: datetime) -> str | None:
        calendar = self.calendars.resolve(str(instrument["exchange"]), instrument["segment"])
        if calendar is None:
            return "Exchange calendar is unavailable for the instrument."
        local_time = observed.astimezone(ZoneInfo(calendar.timezone_name))
        sessions = calendar.sessions_for(local_time.date())
        if sessions is None:
            return "Exchange calendar does not cover the quote date."
        if not any(
            session.opens <= local_time.time().replace(tzinfo=None) < session.closes
            for session in sessions
        ):
            return "Quote timestamp is outside a configured exchange session."
        return None

    def consume_provider(self, provider: QuoteProvider, count: int) -> tuple[QuoteResult, ...]:
        """Consume a bounded deterministic quote batch through the same validation path."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ForwardRuntimeError("count must be a non-negative integer.")
        return tuple(self.process_quote(quote) for quote in provider.quotes(count))

    def _apply_quote(
        self,
        connection: sqlite3.Connection,
        quote: Quote,
        observed: datetime,
        now: datetime,
        price: Decimal,
    ) -> list[dict[str, Any]]:
        key = str(quote.instrument_id)
        self._state["cursor"][key] = {
            "sequence": quote.sequence,
            "timestamp": _timestamp_text(observed),
        }
        self._state["last_prices"][key] = _decimal_text(price)
        self._state["feed"][key] = {
            "status": "HEALTHY",
            "reason": None,
            "rejected": int(self._state["feed"][key]["rejected"]),
        }
        connection.execute(
            """INSERT INTO forward_quotes
               (runtime_id, instrument_id, event_id, sequence, quote_timestamp, received_at,
                price_decimal, quantity, accepted, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)""",
            (
                self.runtime_id,
                quote.instrument_id,
                quote.event_id,
                quote.sequence,
                _timestamp_text(observed),
                _timestamp_text(now),
                _decimal_text(price),
                quote.quantity,
            ),
        )
        import_row = connection.execute(
            """SELECT import_id FROM forward_runtime_imports
               WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m'""",
            (self.runtime_id, quote.instrument_id),
        ).fetchone()
        if import_row is None:
            raise ForwardRuntimeError("Synthetic one-minute mark provenance is missing.")
        record_paper_mark(
            connection,
            self.account_id,
            quote.instrument_id,
            price,
            _timestamp_text(observed),
            int(import_row["import_id"]),
        )
        completed: list[dict[str, Any]] = []
        quote_bucket = _bucket(observed, 1)
        current = self._state["current_1m"][key]
        if current is None:
            self._state["current_1m"][key] = _new_candle(
                quote.instrument_id, "1m", quote_bucket, price, quote.quantity
            )
        elif current["candle_timestamp"] == _timestamp_text(quote_bucket):
            _extend_candle(current, price, quote.quantity)
        else:
            prior_bucket = _timestamp(current["candle_timestamp"], "candle timestamp")
            if quote_bucket <= prior_bucket:
                raise ForwardRuntimeError("Out-of-order quote cannot close the active candle.")
            completed_1m = dict(current)
            completed_1m["completed_at"] = _timestamp_text(quote_bucket)
            self._persist_candle(connection, completed_1m)
            completed.append(completed_1m)
            self._aggregate_candle(connection, completed_1m, completed)
            self._state["current_1m"][key] = _new_candle(
                quote.instrument_id, "1m", quote_bucket, price, quote.quantity
            )
        return completed

    def _aggregate_candle(
        self,
        connection: sqlite3.Connection,
        candle: dict[str, Any],
        completed: list[dict[str, Any]],
    ) -> None:
        key = str(candle["instrument_id"])
        minute = _timestamp(str(candle["candle_timestamp"]), "candle timestamp")
        for interval, minutes in (("5m", 5), ("15m", 15)):
            aggregate = self._state["current_aggregates"][key][interval]
            start = _bucket(minute, minutes)
            start_text = _timestamp_text(start)
            if aggregate is not None and aggregate["candle_timestamp"] != start_text:
                if int(aggregate["component_count"]) == minutes:
                    result = dict(aggregate)
                    result.pop("component_count")
                    result["completed_at"] = str(candle["completed_at"])
                    self._persist_candle(connection, result)
                    completed.append(result)
                self._state["current_aggregates"][key][interval] = None
                aggregate = None
            if aggregate is None:
                aggregate = {
                    "instrument_id": int(candle["instrument_id"]),
                    "interval": interval,
                    "candle_timestamp": start_text,
                    "open_decimal": candle["open_decimal"],
                    "high_decimal": candle["high_decimal"],
                    "low_decimal": candle["low_decimal"],
                    "close_decimal": candle["close_decimal"],
                    "volume": int(candle["volume"]),
                    "component_count": 0,
                }
                self._state["current_aggregates"][key][interval] = aggregate
            else:
                aggregate["high_decimal"] = _decimal_text(
                    max(Decimal(aggregate["high_decimal"]), Decimal(candle["high_decimal"]))
                )
                aggregate["low_decimal"] = _decimal_text(
                    min(Decimal(aggregate["low_decimal"]), Decimal(candle["low_decimal"]))
                )
                aggregate["close_decimal"] = candle["close_decimal"]
                aggregate["volume"] = int(aggregate["volume"]) + int(candle["volume"])
            aggregate["component_count"] = int(aggregate["component_count"]) + 1
            if int(aggregate["component_count"]) == minutes:
                result = dict(aggregate)
                result.pop("component_count")
                result["completed_at"] = str(candle["completed_at"])
                self._persist_candle(connection, result)
                completed.append(result)
                self._state["current_aggregates"][key][interval] = None

    def _persist_candle(self, connection: sqlite3.Connection, candle: dict[str, Any]) -> None:
        instrument_id = int(candle["instrument_id"])
        interval = str(candle["interval"])
        timestamp = str(candle["candle_timestamp"])
        import_row = connection.execute(
            """SELECT import_id FROM forward_runtime_imports
               WHERE runtime_id = ? AND instrument_id = ? AND interval = ?""",
            (self.runtime_id, instrument_id, interval),
        ).fetchone()
        if import_row is None:
            raise ForwardRuntimeError("Synthetic stream provenance is missing.")
        import_id = int(import_row["import_id"])
        values = (
            self.runtime_id,
            instrument_id,
            interval,
            timestamp,
            str(candle["open_decimal"]),
            str(candle["high_decimal"]),
            str(candle["low_decimal"]),
            str(candle["close_decimal"]),
            int(candle["volume"]),
            str(candle["completed_at"]),
        )
        try:
            connection.execute(
                """INSERT INTO forward_runtime_candles
                   (runtime_id, instrument_id, interval, candle_timestamp, open_decimal,
                    high_decimal, low_decimal, close_decimal, volume, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            connection.execute(
                """INSERT INTO historical_candles
                   (instrument_id, import_id, interval, candle_timestamp, open_decimal,
                    high_decimal, low_decimal, close_decimal, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    instrument_id,
                    import_id,
                    interval,
                    timestamp,
                    str(candle["open_decimal"]),
                    str(candle["high_decimal"]),
                    str(candle["low_decimal"]),
                    str(candle["close_decimal"]),
                    int(candle["volume"]),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ForwardRuntimeError(
                "Candle identity already exists; runtime will not overwrite stored research data."
            ) from exc
        connection.execute(
            """UPDATE data_imports SET status = 'COMPLETED', completed_at = ?,
               accepted_rows = accepted_rows + 1,
               total_rows = total_rows + 1, records_received = records_received + 1,
               records_inserted = records_inserted + 1 WHERE id = ?""",
            (str(candle["completed_at"]), import_id),
        )

    def _reject_quote(self, quote: Quote, received_at: datetime, reason: str) -> QuoteResult:
        if (
            isinstance(quote.instrument_id, int)
            and not isinstance(quote.instrument_id, bool)
            and quote.instrument_id in self.instrument_ids
            and isinstance(quote.event_id, str)
            and quote.event_id.strip()
            and len(quote.event_id) <= 256
        ):
            corrupted = False
            with _transaction(self.database_path) as connection:
                runtime = connection.execute(
                    """SELECT status, state_json, state_sha256 FROM forward_runtimes
                       WHERE runtime_id = ?""",
                    (self.runtime_id,),
                ).fetchone()
                if runtime is None:
                    return QuoteResult(False, False, "Runtime does not exist.")
                if runtime["status"] == "HALTED":
                    return QuoteResult(False, False, "Runtime is halted.")
                if (
                    hashlib.sha256(runtime["state_json"].encode()).hexdigest()
                    != runtime["state_sha256"]
                ):
                    self._halt(
                        connection,
                        self.runtime_id,
                        self.account_id,
                        "Runtime state checksum mismatch.",
                    )
                    corrupted = True
                else:
                    self._state = json.loads(runtime["state_json"])
                if not corrupted:
                    prior = connection.execute(
                        """SELECT 1 FROM forward_quotes WHERE runtime_id = ?
                           AND instrument_id = ? AND event_id = ?""",
                        (self.runtime_id, quote.instrument_id, quote.event_id),
                    ).fetchone()
                    if prior is not None:
                        return QuoteResult(False, True, "Duplicate quote event.")
                    self._record_rejection(connection, quote, received_at, reason)
            if corrupted:
                raise ForwardRuntimeError("Persisted runtime state is corrupted.")
        return QuoteResult(False, False, reason)

    def _record_rejection(
        self, connection: sqlite3.Connection, quote: Quote, received_at: datetime, reason: str
    ) -> None:
        key = str(quote.instrument_id)
        feed = self._state["feed"].get(key)
        if feed is not None:
            feed["status"] = "DEGRADED"
            feed["reason"] = reason
            feed["rejected"] = int(feed["rejected"]) + 1
        connection.execute(
            """INSERT OR IGNORE INTO forward_quotes
               (runtime_id, instrument_id, event_id, sequence, quote_timestamp, received_at,
                price_decimal, quantity, accepted, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                self.runtime_id,
                quote.instrument_id,
                quote.event_id if isinstance(quote.event_id, str) else "INVALID_EVENT_ID",
                quote.sequence
                if isinstance(quote.sequence, int) and not isinstance(quote.sequence, bool)
                else None,
                quote.timestamp if isinstance(quote.timestamp, str) else None,
                _timestamp_text(received_at),
                str(quote.price) if isinstance(quote.price, (str, int, Decimal)) else None,
                quote.quantity
                if isinstance(quote.quantity, int) and not isinstance(quote.quantity, bool)
                else None,
                reason[:500],
            ),
        )
        state_json = _canonical_json(self._state)
        runtime = connection.execute(
            "SELECT status FROM forward_runtimes WHERE runtime_id = ?", (self.runtime_id,)
        ).fetchone()
        status = "HALTED" if runtime is not None and runtime["status"] == "HALTED" else "DEGRADED"
        connection.execute(
            """UPDATE forward_runtimes SET state_json = ?, state_sha256 = ?,
               status = ?, updated_at = ? WHERE runtime_id = ?""",
            (
                state_json,
                hashlib.sha256(state_json.encode()).hexdigest(),
                status,
                _timestamp_text(received_at),
                self.runtime_id,
            ),
        )
        self._journal_health(
            connection,
            self.runtime_id,
            _stable_id(f"rejection:{quote.instrument_id}:{quote.event_id}"),
            "QUOTE_REJECTED",
            _timestamp_text(received_at),
            {"reason_fingerprint": hashlib.sha256(reason.encode()).hexdigest()},
        )

    def monitor_feed_health(self, as_of: str | datetime) -> tuple[FeedHealth, ...]:
        """Evaluate per-instrument feed health using configured age thresholds."""
        current = (
            _timestamp(as_of, "as_of")
            if isinstance(as_of, str)
            else _timestamp(as_of.isoformat(), "as_of")
        )
        result = []
        for instrument_id in self.instrument_ids:
            key = str(instrument_id)
            record = self._state["feed"][key]
            cursor_time = self._state["cursor"][key]["timestamp"]
            age = (
                Decimal(
                    str((current - _timestamp(cursor_time, "cursor timestamp")).total_seconds())
                )
                if cursor_time
                else None
            )
            status_value = record["status"]
            if status_value not in {
                "STARTING",
                "HEALTHY",
                "DEGRADED",
                "STALE",
                "DISCONNECTED",
            }:
                raise ForwardRuntimeError("Persisted feed-health status is invalid.")
            status = cast(FeedStatus, status_value)
            reason = record["reason"]
            if age is not None and age > self.stale_after_seconds:
                status, reason = "STALE", "No recent quote within the stale threshold."
            elif age is not None and age > self.degraded_after_seconds and status != "DEGRADED":
                status, reason = "DEGRADED", "Quote age exceeds the healthy threshold."
            result.append(
                FeedHealth(
                    instrument_id,
                    status,
                    str(cursor_time) if cursor_time else None,
                    age,
                    int(record["rejected"]),
                    str(reason) if reason else None,
                )
            )
        with _transaction(self.database_path) as connection:
            row = connection.execute(
                "SELECT state_json, state_sha256, status FROM forward_runtimes WHERE runtime_id = ?",
                (self.runtime_id,),
            ).fetchone()
            if row is None:
                raise ForwardRuntimeError("Forward runtime does not exist.")
            if hashlib.sha256(row["state_json"].encode()).hexdigest() != row["state_sha256"]:
                self._halt(
                    connection, self.runtime_id, self.account_id, "Runtime state checksum mismatch."
                )
                raise ForwardRuntimeError("Persisted runtime state is corrupted.")
            state = json.loads(row["state_json"])
            for health in result:
                feed = state["feed"][str(health.instrument_id)]
                feed["status"] = health.status
                feed["reason"] = health.reason
            state_json = _canonical_json(state)
            runtime_status = row["status"]
            if runtime_status != "HALTED":
                runtime_status = (
                    "DEGRADED"
                    if any(health.status in {"DEGRADED", "STALE"} for health in result)
                    else "RUNNING"
                )
            connection.execute(
                """UPDATE forward_runtimes SET state_json = ?, state_sha256 = ?,
                   status = ?, updated_at = ? WHERE runtime_id = ?""",
                (
                    state_json,
                    hashlib.sha256(state_json.encode()).hexdigest(),
                    runtime_status,
                    _timestamp_text(current),
                    self.runtime_id,
                ),
            )
            self._state = state
            for health in result:
                if health.status in {"STARTING", "STALE", "DISCONNECTED", "DEGRADED"}:
                    decision_journal.record_missing_evaluation(
                        connection,
                        self.runtime_id,
                        health.instrument_id,
                        _stable_id(
                            f"health:{self.runtime_id}:{health.instrument_id}:{_timestamp_text(current)}"
                        ),
                        _timestamp_text(current),
                        "FEED_UNAVAILABLE",
                        self.account_id,
                    )
        return tuple(result)

    def select_candidates(self, as_of: str | datetime | None = None) -> tuple[int, ...]:
        """Return at most the configured number of instruments with completed 1m data."""
        cutoff = (
            _timestamp(as_of, "candidate as_of")
            if isinstance(as_of, str)
            else _timestamp(as_of.isoformat(), "candidate as_of")
            if as_of is not None
            else None
        )
        with closing(get_connection(self.database_path)) as connection:
            return self._select_candidates(connection, cutoff)

    def _select_candidates(
        self, connection: sqlite3.Connection, as_of: datetime | None
    ) -> tuple[int, ...]:
        scores: list[tuple[Decimal, int]] = []
        for instrument_id in self.instrument_ids:
            completion_filter = "AND completed_at <= ?" if as_of is not None else ""
            rows = connection.execute(
                f"""SELECT close_decimal FROM forward_runtime_candles
                    WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m'
                    {completion_filter}
                    ORDER BY completed_at DESC, candle_timestamp DESC LIMIT 2""",
                (self.runtime_id, instrument_id, _timestamp_text(as_of))
                if as_of is not None
                else (self.runtime_id, instrument_id),
            ).fetchall()
            if not rows:
                continue
            latest = Decimal(rows[0]["close_decimal"])
            if len(rows) == 1:
                score = Decimal(0)
            else:
                previous = Decimal(rows[1]["close_decimal"])
                score = abs(latest - previous) / previous if previous else Decimal(0)
            scores.append((score, instrument_id))
        scores.sort(key=lambda pair: (-pair[0], pair[1]))
        return tuple(instrument_id for _, instrument_id in scores[: self.max_candidates])

    def prepare_evidence(
        self,
        instrument_id: int,
        interval: str,
        as_of: str | datetime,
        *,
        event_id: str | None = None,
        proposal_id: str | None = None,
    ) -> MarketEvidence:
        """Prepare decision evidence only from persisted, completed candles."""
        if interval not in _INTERVAL_MINUTES or instrument_id not in self.instrument_ids:
            raise ForwardRuntimeError("Unsupported evidence instrument or interval.")
        if (event_id is None) != (proposal_id is None):
            raise ForwardRuntimeError(
                "Scheduler event and proposal identities must be supplied together."
            )
        as_of_time = (
            _timestamp(as_of, "as_of")
            if isinstance(as_of, str)
            else _timestamp(as_of.isoformat(), "as_of")
        )
        with closing(get_connection(self.database_path)) as connection:
            event = None
            if event_id is not None:
                event = connection.execute(
                    """SELECT instrument_id, interval, candle_timestamp,
                              decision_timestamp, status, proposal_id
                       FROM forward_scheduler_events
                       WHERE event_id = ? AND runtime_id = ?""",
                    (event_id, self.runtime_id),
                ).fetchone()
                if (
                    event is None
                    or event["status"] != "PENDING"
                    or event["instrument_id"] != instrument_id
                    or event["interval"] != interval
                    or event["proposal_id"] != proposal_id
                    or _timestamp(event["decision_timestamp"], "event decision timestamp")
                    != as_of_time
                ):
                    raise ForwardRuntimeError(
                        "Decision evidence does not match the exact pending scheduler event."
                    )
            imported = connection.execute(
                """SELECT d.instrument_id, d.interval, d.status, d.source_use_status,
                          d.file_sha256, d.metadata_json, d.import_type, d.completed_at,
                          i.exchange, i.trading_symbol,
                          ri.import_id
                   FROM forward_runtime_imports ri
                   JOIN data_imports d ON d.id = ri.import_id
                   JOIN instruments i ON i.id = ri.instrument_id
                   WHERE ri.runtime_id = ? AND ri.instrument_id = ? AND ri.interval = ?""",
                (self.runtime_id, instrument_id, interval),
            ).fetchone()
            if imported is None:
                raise ForwardRuntimeError("Runtime source import is missing.")
            if (
                imported["instrument_id"] != instrument_id
                or imported["interval"] != interval
                or imported["status"] != "COMPLETED"
                or imported["source_use_status"] != "VERIFIED_PERMITTED"
                or not imported["file_sha256"]
                or imported["import_type"] != "SYNTHETIC_STREAM"
                or imported["exchange"] is None
            ):
                raise ForwardRuntimeError(
                    "Runtime source provenance is not verified and permitted."
                )
            try:
                metadata = json.loads(imported["metadata_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ForwardRuntimeError(
                    "Runtime source provenance metadata is malformed."
                ) from exc
            if (
                not isinstance(metadata, dict)
                or metadata.get("runtime_id") != self.runtime_id
                or metadata.get("provider") != "DETERMINISTIC_SYNTHETIC"
                or metadata.get("source_use_rationale") != "Generated in-process synthetic data."
            ):
                raise ForwardRuntimeError(
                    "Runtime source import provenance belongs to a different runtime or provider."
                )
            if (
                not imported["completed_at"]
                or _timestamp(str(imported["completed_at"]), "source import completed_at")
                > as_of_time
            ):
                raise ForwardRuntimeError(
                    "Runtime source import is incomplete or has a future completion timestamp."
                )
            latest = connection.execute(
                """SELECT c.candle_timestamp, c.completed_at, c.close_decimal,
                          h.import_id, h.close_decimal AS imported_close
                   FROM forward_runtime_candles c
                   JOIN forward_runtime_imports ri
                     ON ri.runtime_id = c.runtime_id AND ri.instrument_id = c.instrument_id
                    AND ri.interval = c.interval
                   LEFT JOIN historical_candles h
                     ON h.instrument_id = c.instrument_id AND h.interval = c.interval
                    AND h.candle_timestamp = c.candle_timestamp
                    AND h.import_id = ri.import_id
                   WHERE c.runtime_id = ? AND c.instrument_id = ? AND c.interval = ?
                     AND c.completed_at <= ?
                   ORDER BY c.completed_at DESC, c.candle_timestamp DESC LIMIT 1""",
                (
                    self.runtime_id,
                    instrument_id,
                    interval,
                    _timestamp_text(as_of_time),
                ),
            ).fetchone()
        if latest is None:
            with closing(get_connection(self.database_path)) as connection:
                future = connection.execute(
                    """SELECT completed_at FROM forward_runtime_candles
                       WHERE runtime_id = ? AND instrument_id = ? AND interval = ?
                       ORDER BY completed_at DESC LIMIT 1""",
                    (self.runtime_id, instrument_id, interval),
                ).fetchone()
            if future is not None:
                raise ForwardRuntimeError(
                    "Decision evidence cannot include a candle completed in the future."
                )
            raise ForwardRuntimeError("No completed runtime candle is available for evidence.")
        if (
            int(latest["import_id"] or 0) != int(imported["import_id"])
            or latest["close_decimal"] != latest["imported_close"]
            or (event is not None and latest["candle_timestamp"] != event["candle_timestamp"])
        ):
            raise ForwardRuntimeError("Runtime candle does not match its exact source import.")
        evidence_id = _stable_id(
            "evidence:"
            f"{self.runtime_id}:{instrument_id}:{interval}:{imported['import_id']}:"
            f"{latest['candle_timestamp']}:{_timestamp_text(as_of_time)}"
        )
        engine = ForwardPaperTradingEngine(
            self.database_path,
            self.account_id,
            calendars=self.calendars,
            max_execution_price_deviation=self.max_execution_price_deviation,
        )
        evidence = engine.build_market_evidence(
            instrument_id,
            interval=interval,
            as_of=as_of,
            calendars=self.calendars,
            source_import_id=int(imported["import_id"]),
            source_candle_timestamp=str(latest["candle_timestamp"]),
            runtime_id=self.runtime_id,
            evidence_id=evidence_id,
            scheduler_event_id=event_id,
            proposal_id=proposal_id,
        )
        if (
            evidence.latest_candle_timestamp != latest["candle_timestamp"]
            or evidence.source_import_id != int(imported["import_id"])
            or evidence.runtime_id != self.runtime_id
            or _timestamp(evidence.as_of, "evidence as_of") != as_of_time
        ):
            raise ForwardRuntimeError("Evidence does not match the runtime's exact source candle.")
        return evidence

    def _event_id(self, instrument_id: int, interval: str, candle_timestamp: str) -> str:
        return _stable_id(
            f"scheduler:{self.runtime_id}:{instrument_id}:{interval}:{candle_timestamp}"
        )

    def _recover_exit_controls(self) -> None:
        with _transaction(self.database_path) as connection:
            controls = connection.execute(
                """SELECT instrument_id, exit_event_id FROM forward_trade_controls
                   WHERE runtime_id = ? AND active = 1 AND exit_event_id IS NOT NULL""",
                (self.runtime_id,),
            ).fetchall()
            for control in controls:
                position = connection.execute(
                    """SELECT 1 FROM paper_positions
                       WHERE account_id = ? AND instrument_id = ?""",
                    (self.account_id, control["instrument_id"]),
                ).fetchone()
                order = connection.execute(
                    """SELECT status FROM paper_orders
                       WHERE account_id = ? AND idempotency_key = ?""",
                    (self.account_id, control["exit_event_id"]),
                ).fetchone()
                if position is None:
                    connection.execute(
                        """UPDATE forward_trade_controls SET active = 0, exit_event_id = NULL
                           WHERE runtime_id = ? AND instrument_id = ?""",
                        (self.runtime_id, control["instrument_id"]),
                    )
                else:
                    connection.execute(
                        """UPDATE forward_trade_controls SET active = 1, exit_event_id = NULL
                           WHERE runtime_id = ? AND instrument_id = ?""",
                        (self.runtime_id, control["instrument_id"]),
                    )
                    if order is None:
                        connection.execute(
                            """UPDATE forward_scheduler_events SET status = 'REJECTED',
                               result_json = ?
                               WHERE event_id = ? AND status = 'PENDING'""",
                            (
                                _canonical_json(
                                    {
                                        "status": "REJECTED",
                                        "reason": "Recovered pending exit requires a fresh quote.",
                                    }
                                ),
                                control["exit_event_id"],
                            ),
                        )

    def recover_scheduler_events(self) -> tuple[str, ...]:
        """Recover missing events for completed candidate candles without routing proposals."""
        recovered: list[str] = []
        with _transaction(self.database_path) as connection:
            runtime = connection.execute(
                """SELECT state_json, state_sha256, status FROM forward_runtimes
                   WHERE runtime_id = ?""",
                (self.runtime_id,),
            ).fetchone()
            if runtime is None:
                raise ForwardRuntimeError("Forward runtime does not exist.")
            if runtime["status"] == "HALTED":
                raise ForwardRuntimeError("Forward runtime is halted.")
            if (
                hashlib.sha256(runtime["state_json"].encode()).hexdigest()
                != runtime["state_sha256"]
            ):
                raise ForwardRuntimeError("Persisted runtime state is corrupted.")
            state = json.loads(runtime["state_json"])
            existing = connection.execute(
                """SELECT e.event_id, e.instrument_id, e.interval, e.candle_timestamp,
                          e.decision_timestamp, e.proposal_id
                   FROM forward_scheduler_events e
                   LEFT JOIN forward_runtime_candles c
                     ON c.runtime_id = e.runtime_id AND c.instrument_id = e.instrument_id
                    AND c.interval = e.interval AND c.candle_timestamp = e.candle_timestamp
                   WHERE e.runtime_id = ? AND c.candle_timestamp IS NULL
                     AND e.proposal_id NOT LIKE 'stop-target-%'""",
                (self.runtime_id,),
            ).fetchall()
            if existing:
                raise ForwardRuntimeError("Scheduler event references a missing completed candle.")
            events = connection.execute(
                """SELECT event_id, instrument_id, interval, candle_timestamp,
                          decision_timestamp, proposal_id FROM forward_scheduler_events
                   WHERE runtime_id = ?""",
                (self.runtime_id,),
            ).fetchall()
            for event in events:
                if str(event["proposal_id"]).startswith("stop-target-"):
                    continue
                interval = str(event["interval"])
                if interval not in _INTERVAL_MINUTES:
                    raise ForwardRuntimeError("Scheduler event interval is invalid.")
                expected_time = _timestamp(
                    str(event["candle_timestamp"]), "scheduler candle timestamp"
                ) + timedelta(minutes=_INTERVAL_MINUTES[interval])
                expected_id = self._event_id(
                    int(event["instrument_id"]), interval, str(event["candle_timestamp"])
                )
                if (
                    event["event_id"] != expected_id
                    or event["proposal_id"] != f"synthetic-{expected_id[:40]}"
                    or _timestamp_text(_timestamp(event["decision_timestamp"], "decision time"))
                    != _timestamp_text(expected_time)
                ):
                    raise ForwardRuntimeError(
                        "Scheduler event identity or timestamp is inconsistent."
                    )
            candles = connection.execute(
                """SELECT instrument_id, interval, candle_timestamp, completed_at
                   FROM forward_runtime_candles WHERE runtime_id = ?
                   ORDER BY completed_at, candle_timestamp, instrument_id, interval""",
                (self.runtime_id,),
            ).fetchall()
            for candle in candles:
                interval = str(candle["interval"])
                if interval not in _INTERVAL_MINUTES:
                    raise ForwardRuntimeError("Completed candle interval is invalid.")
                decision_time = _timestamp(
                    str(candle["candle_timestamp"]), "candle timestamp"
                ) + timedelta(minutes=_INTERVAL_MINUTES[interval])
                if _timestamp(candle["completed_at"], "candle completion time") > decision_time:
                    raise ForwardRuntimeError(
                        "Completed candle has an inconsistent completion time."
                    )
                if int(candle["instrument_id"]) not in self._select_candidates(
                    connection, decision_time
                ):
                    continue
                event_id = self._event_id(
                    int(candle["instrument_id"]), interval, str(candle["candle_timestamp"])
                )
                event_write = _SchedulerEventWrite(
                    event_id=event_id,
                    runtime_id=self.runtime_id,
                    instrument_id=int(candle["instrument_id"]),
                    interval=interval,
                    candle_timestamp=str(candle["candle_timestamp"]),
                    decision_timestamp=_timestamp_text(decision_time),
                    status="PENDING",
                    proposal_id=f"synthetic-{event_id[:40]}",
                    proposal_sha256=None,
                    proposal_json=None,
                    proposal_binding_state="UNBOUND",
                    result_json=_canonical_json({"recovered": True}),
                    created_at=_timestamp_text(datetime.now(UTC)),
                )
                with _authorize_scheduler_event_write(
                    connection, "CREATE_SCHEDULER_EVENT", event_write
                ):
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO forward_scheduler_events
                           (event_id, runtime_id, instrument_id, interval, candle_timestamp,
                            decision_timestamp, status, proposal_id, proposal_sha256,
                            proposal_json, proposal_binding_state, result_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        event_write.values(),
                    )
                if cursor.rowcount:
                    recovered.append(event_id)
            recovery = state.setdefault(
                "scheduler_recovery",
                {
                    "recovered_total": 0,
                    "last_recovered_event_ids": [],
                    "last_completed_at": None,
                },
            )
            if recovered:
                recovery["recovered_total"] = int(recovery["recovered_total"]) + len(recovered)
                recovery["last_recovered_event_ids"] = recovered[-100:]
            recovery["last_completed_at"] = _timestamp_text(datetime.now(UTC))
            state_json = _canonical_json(state)
            connection.execute(
                """UPDATE forward_runtimes SET state_json = ?, state_sha256 = ?,
                   updated_at = ? WHERE runtime_id = ?""",
                (
                    state_json,
                    hashlib.sha256(state_json.encode()).hexdigest(),
                    _timestamp_text(datetime.now(UTC)),
                    self.runtime_id,
                ),
            )
        self._state = state
        self._scheduler_dirty = False
        return tuple(recovered)

    def schedule_event(self, instrument_id: int, interval: str, candle_timestamp: str) -> str:
        """Create a stable event once per completed candle, using the candidate cap."""
        if interval not in _INTERVAL_MINUTES:
            raise ForwardRuntimeError("Scheduler interval is unsupported.")
        decision_time = _timestamp(candle_timestamp, "candle timestamp") + timedelta(
            minutes=_INTERVAL_MINUTES[interval]
        )
        if instrument_id not in self.select_candidates(decision_time):
            return ""
        event_id = self._event_id(instrument_id, interval, candle_timestamp)
        proposal_id = f"synthetic-{event_id[:40]}"
        with _transaction(self.database_path) as connection:
            candle = connection.execute(
                """SELECT completed_at FROM forward_runtime_candles WHERE runtime_id = ?
                   AND instrument_id = ? AND interval = ? AND candle_timestamp = ?""",
                (self.runtime_id, instrument_id, interval, candle_timestamp),
            ).fetchone()
            if candle is None:
                raise ForwardRuntimeError("Scheduler requires a completed candle.")
            if _timestamp(candle["completed_at"], "candle completion time") > decision_time:
                raise ForwardRuntimeError("Scheduler cannot use a future-completed candle.")
            event_write = _SchedulerEventWrite(
                event_id=event_id,
                runtime_id=self.runtime_id,
                instrument_id=instrument_id,
                interval=interval,
                candle_timestamp=candle_timestamp,
                decision_timestamp=_timestamp_text(decision_time),
                status="PENDING",
                proposal_id=proposal_id,
                proposal_sha256=None,
                proposal_json=None,
                proposal_binding_state="UNBOUND",
                result_json=None,
                created_at=_timestamp_text(datetime.now(UTC)),
            )
            with _authorize_scheduler_event_write(
                connection, "CREATE_SCHEDULER_EVENT", event_write
            ):
                connection.execute(
                    """INSERT OR IGNORE INTO forward_scheduler_events
                       (event_id, runtime_id, instrument_id, interval, candle_timestamp,
                        decision_timestamp, status, proposal_id, proposal_sha256,
                        proposal_json, proposal_binding_state, result_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    event_write.values(),
                )
        return event_id

    def build_mock_proposal(
        self,
        event_id: str,
        action: DecisionAction | str,
        quantity: int,
        *,
        stop_loss: Decimal | None = None,
        target_price: Decimal | None = None,
    ) -> DecisionProposal:
        """Create a deterministic, non-AI proposal tied to one scheduled event."""
        started = time.perf_counter_ns()
        with closing(get_connection(self.database_path)) as connection:
            event = connection.execute(
                "SELECT * FROM forward_scheduler_events WHERE event_id = ? AND runtime_id = ?",
                (event_id, self.runtime_id),
            ).fetchone()
            candle = (
                connection.execute(
                    """SELECT close_decimal FROM forward_runtime_candles
                       WHERE runtime_id = ? AND instrument_id = ? AND interval = ?
                         AND candle_timestamp = ?""",
                    (
                        self.runtime_id,
                        event["instrument_id"],
                        event["interval"],
                        event["candle_timestamp"],
                    ),
                ).fetchone()
                if event is not None
                else None
            )
        if event is None or candle is None:
            raise ForwardRuntimeError("Mock proposal requires a persisted scheduler event.")
        proposal = DecisionProposal(
            proposal_id=str(event["proposal_id"]),
            instrument_id=int(event["instrument_id"]),
            action=DecisionAction(action),
            quantity=quantity,
            entry_price=Decimal(candle["close_decimal"]),
            stop_loss=stop_loss,
            target_price=target_price,
            confidence=Decimal(1),
            rationale="Deterministic synthetic mock intake; not an AI recommendation.",
            timeframe=str(event["interval"]),
            decision_timestamp=str(event["decision_timestamp"]),
            submitted_at=str(event["decision_timestamp"]),
            source="DETERMINISTIC_SYNTHETIC",
            expires_at=_timestamp_text(
                _timestamp(event["decision_timestamp"], "event decision timestamp")
                + timedelta(minutes=_INTERVAL_MINUTES[str(event["interval"])])
            ),
        )
        # Bounded optional measurements; proposal content and execution authority are unchanged.
        self._proposal_timings[event_id] = (
            _proposal_sha256(proposal),
            min(time.perf_counter_ns() - started, decision_journal.MAX_DURATION_NS),
        )
        if len(self._proposal_timings) > 256:
            self._proposal_timings.pop(next(iter(self._proposal_timings)))
        return proposal

    def route_event(
        self,
        event_id: str,
        proposal: DecisionProposal,
        snapshot: ExecutionSnapshot | None,
    ) -> ProposalExecutionResult:
        """Journal a bound evaluation without granting execution permission."""
        if not isinstance(proposal, DecisionProposal):
            raise ForwardRuntimeError("A persisted scheduler event requires a DecisionProposal.")
        started = time.perf_counter_ns()
        decision_id = None
        if proposal.action == DecisionAction.BUY and getattr(
            self, "_journal_recovery_error", False
        ):
            return ProposalExecutionResult(
                proposal.proposal_id, "REJECTED", reason="Decision journal recovery is unresolved."
            )
        # SELL has no additional pre-execution transaction or required analytics write.
        # Its linkage is attempted inside Phase 8's existing financial transaction.
        if proposal.action != DecisionAction.SELL:
            with closing(get_readonly_connection(self.database_path)) as connection:
                event = connection.execute(
                    "SELECT * FROM forward_scheduler_events WHERE event_id = ? AND runtime_id = ?",
                    (event_id, self.runtime_id),
                ).fetchone()
            canonical = _proposal_canonical_json(proposal)
            compatible = event is not None and (
                event["proposal_id"] == proposal.proposal_id
                and event["instrument_id"] == proposal.instrument_id
                and event["interval"] == proposal.timeframe
                and event["proposal_json"] in (None, canonical)
                and event["proposal_binding_state"] in ("UNBOUND", "BOUND")
            )
            if compatible:
                try:
                    measurement = self._proposal_timings.get(event_id)
                    signal_duration = (
                        measurement[1]
                        if measurement and measurement[0] == _proposal_sha256(proposal)
                        else None
                    )
                    decision_id = decision_journal.capture_runtime_decision(
                        self.database_path,
                        event_id,
                        canonical,
                        signal_generation_ns=signal_duration,
                    )
                except (decision_journal.JournalError, sqlite3.Error):
                    return ProposalExecutionResult(
                        proposal.proposal_id,
                        "REJECTED",
                        reason="Decision journal persistence failed; evaluation blocked.",
                    )
            elif event is not None:
                with _transaction(self.database_path) as connection:
                    decision_journal.record_missing_evaluation(
                        connection,
                        self.runtime_id,
                        int(event["instrument_id"]),
                        _stable_id(
                            f"invalid-evaluation:{event_id}:{hashlib.sha256(canonical.encode()).hexdigest()}"
                        ),
                        str(event["decision_timestamp"]),
                        "INVALID_PROPOSAL_EVIDENCE",
                        self.account_id,
                    )
        result = self._route_event(event_id, proposal, snapshot)
        if proposal.action == DecisionAction.SELL and result.order_id is None:
            try:
                decision_id = decision_journal.capture_runtime_decision(
                    self.database_path, event_id, _proposal_canonical_json(proposal)
                )
            except (decision_journal.JournalError, sqlite3.Error):
                pass  # Exit routing has already completed; financial controls remain authoritative.
        if decision_id is not None:
            try:
                decision_journal.finish_runtime_decision(
                    self.database_path,
                    decision_id,
                    result.status,
                    reason=result.reason,
                    duration_ns=min(
                        time.perf_counter_ns() - started, decision_journal.MAX_DURATION_NS
                    ),
                )
            except (decision_journal.JournalError, sqlite3.Error):
                # Mandatory BUY result linkage already committed atomically in Phase 8.
                if result.status not in {"FILLED", "REJECTED"}:
                    return ProposalExecutionResult(
                        proposal.proposal_id,
                        "REJECTED",
                        reason="Decision result could not be journaled.",
                    )
        return result

    def _route_event(
        self,
        event_id: str,
        proposal: DecisionProposal,
        snapshot: ExecutionSnapshot | None,
    ) -> ProposalExecutionResult:
        """Validate and route one scheduler event through Phase 8 execution safeguards."""
        if not isinstance(proposal, DecisionProposal):
            raise ForwardRuntimeError("A persisted scheduler event requires a DecisionProposal.")
        proposal_json = _proposal_canonical_json(proposal)
        proposal_hash = hashlib.sha256(proposal_json.encode("utf-8")).hexdigest()
        self._ensure_runtime_ready()
        self._ensure_scheduler_recovered()
        with _transaction(self.database_path) as connection:
            event = connection.execute(
                "SELECT * FROM forward_scheduler_events WHERE event_id = ? AND runtime_id = ?",
                (event_id, self.runtime_id),
            ).fetchone()
            if event is None:
                raise ForwardRuntimeError("Scheduler event does not exist.")
            if event["status"] != "PENDING":
                if (
                    proposal.proposal_id != event["proposal_id"]
                    or (
                        event["proposal_sha256"] is not None
                        and (
                            proposal_hash != event["proposal_sha256"]
                            or proposal_json != event["proposal_json"]
                        )
                    )
                    or (
                        event["proposal_json"] is not None
                        and proposal_json != event["proposal_json"]
                    )
                ):
                    return ProposalExecutionResult(
                        proposal.proposal_id,
                        "REJECTED",
                        reason="Proposal content does not match the proposal already bound to this event.",
                    )
                return _execution_result(
                    event["proposal_id"], event["status"], event["result_json"], True
                )
            if event["proposal_binding_state"] == "LEGACY_UNVERIFIABLE":
                reason = "Legacy pending event has no verifiable proposal binding."
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ? AND status = 'PENDING'
                         AND proposal_binding_state = 'LEGACY_UNVERIFIABLE'""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            if event["proposal_binding_state"] not in ("UNBOUND", "BOUND"):
                reason = "Persisted proposal binding state is invalid."
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ? AND status = 'PENDING'""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            if (
                proposal.proposal_id != event["proposal_id"]
                or proposal.instrument_id != event["instrument_id"]
                or proposal.timeframe != event["interval"]
                or proposal.decision_timestamp is None
                or _timestamp(proposal.decision_timestamp, "proposal decision timestamp")
                != _timestamp(event["decision_timestamp"], "event decision timestamp")
            ):
                reason = "Proposal identity, instrument, timeframe, or decision time mismatches the event."
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ? AND status = 'PENDING'""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            if event["proposal_sha256"] not in (None, proposal_hash) or (
                event["proposal_json"] not in (None, proposal_json)
            ):
                reason = "Proposal content does not match the proposal already bound to this event."
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ? AND status = 'PENDING'""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            if event["proposal_binding_state"] == "UNBOUND":
                if event["proposal_sha256"] is not None or event["proposal_json"] is not None:
                    reason = (
                        "Persisted proposal binding is incomplete or from an unsupported version."
                    )
                    connection.execute(
                        """UPDATE forward_scheduler_events SET status = 'REJECTED',
                           result_json = ? WHERE event_id = ? AND status = 'PENDING'""",
                        (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                    )
                    return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
                binding_write = _SchedulerEventWrite(
                    event_id=str(event["event_id"]),
                    runtime_id=str(event["runtime_id"]),
                    instrument_id=int(event["instrument_id"]),
                    interval=str(event["interval"]),
                    candle_timestamp=str(event["candle_timestamp"]),
                    decision_timestamp=str(event["decision_timestamp"]),
                    status=str(event["status"]),
                    proposal_id=str(event["proposal_id"]),
                    proposal_sha256=proposal_hash,
                    proposal_json=proposal_json,
                    proposal_binding_state="BOUND",
                    result_json=(
                        str(event["result_json"]) if event["result_json"] is not None else None
                    ),
                    created_at=str(event["created_at"]),
                )
                with _authorize_scheduler_event_write(connection, "BIND_PROPOSAL", binding_write):
                    cursor = connection.execute(
                        """UPDATE forward_scheduler_events
                           SET proposal_sha256 = ?, proposal_json = ?,
                               proposal_binding_state = 'BOUND'
                           WHERE event_id = ? AND status = 'PENDING'
                             AND proposal_binding_state = 'UNBOUND'
                             AND proposal_sha256 IS NULL AND proposal_json IS NULL""",
                        (proposal_hash, proposal_json, event_id),
                    )
                if cursor.rowcount != 1:
                    reason = "Proposal binding state changed before it could be persisted."
                    connection.execute(
                        """UPDATE forward_scheduler_events SET status = 'REJECTED',
                           result_json = ? WHERE event_id = ? AND status = 'PENDING'""",
                        (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                    )
                    return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            elif event["proposal_sha256"] is None or event["proposal_json"] is None:
                reason = "Persisted proposal binding is incomplete or from an unsupported version."
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ? AND status = 'PENDING'""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            account = connection.execute(
                "SELECT halted, halt_reason, halt_class FROM paper_accounts WHERE id = ?",
                (self.account_id,),
            ).fetchone()
            if account is None:
                reason = "Paper account is missing."
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ?""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            runtime = connection.execute(
                "SELECT status FROM forward_runtimes WHERE runtime_id = ?",
                (self.runtime_id,),
            ).fetchone()
            if runtime is None or runtime["status"] not in ("RUNNING", "DEGRADED"):
                reason = "Forward runtime is halted or unavailable."
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ?""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=reason)
            if halt_blocks_order(
                account["halted"],
                account["halt_class"],
                account["halt_reason"],
                proposal.action.value if proposal.action != DecisionAction.HOLD else "BUY",
            ):
                reason = str(account["halt_reason"] or "Paper account is halted.")
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ?""",
                    (_canonical_json({"status": "REJECTED", "reason": reason}), event_id),
                )
                return ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=str(reason))
        try:
            evidence = self.prepare_evidence(
                int(event["instrument_id"]),
                str(event["interval"]),
                str(event["decision_timestamp"]),
                event_id=event_id,
                proposal_id=proposal.proposal_id,
            )
        except (ForwardRuntimeError, ProposalValidationError) as exc:
            result = ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=str(exc))
            evidence = None
        snapshot_reason = (
            self._snapshot_validation_error(event, snapshot) if snapshot is not None else None
        )
        if proposal.action == DecisionAction.BUY:
            feed_reason = self._buy_feed_rejection(int(event["instrument_id"]), event, snapshot)
            if snapshot_reason is None:
                snapshot_reason = feed_reason
        if evidence is None:
            pass
        elif snapshot_reason is not None:
            result = ProposalExecutionResult(
                proposal.proposal_id, "REJECTED", reason=snapshot_reason
            )
        else:
            try:
                result = ForwardPaperTradingEngine(
                    self.database_path,
                    self.account_id,
                    calendars=self.calendars,
                    max_execution_price_deviation=self.max_execution_price_deviation,
                )._route_runtime_proposal(
                    proposal,
                    evidence=evidence,
                    execution_snapshot=snapshot,
                    runtime_id=self.runtime_id,
                    event_id=event_id,
                    evaluation_time=(
                        snapshot.as_of if snapshot is not None else str(event["decision_timestamp"])
                    ),
                )
            except (ProposalValidationError, PaperTradingError) as exc:
                result = ProposalExecutionResult(proposal.proposal_id, "REJECTED", reason=str(exc))
        result_json = _canonical_json(
            {
                "status": result.status,
                "order_id": result.order_id,
                "reason": result.reason,
                "fill_price": _decimal_text(result.fill_price) if result.fill_price else None,
            }
        )
        with _transaction(self.database_path) as connection:
            connection.execute(
                """UPDATE forward_scheduler_events SET status = ?, result_json = ?
                   WHERE event_id = ? AND status = 'PENDING'""",
                (result.status, result_json, event_id),
            )
            if result.status == "FILLED" and proposal.action == DecisionAction.BUY:
                connection.execute(
                    """INSERT INTO forward_trade_controls
                       (runtime_id, instrument_id, proposal_id, stop_loss, target_price, active)
                       VALUES (?, ?, ?, ?, ?, 1)
                       ON CONFLICT(runtime_id, instrument_id) DO UPDATE SET
                         proposal_id = excluded.proposal_id, stop_loss = excluded.stop_loss,
                         target_price = excluded.target_price, active = 1, exit_event_id = NULL""",
                    (
                        self.runtime_id,
                        proposal.instrument_id,
                        proposal.proposal_id,
                        _decimal_text(proposal.stop_loss) if proposal.stop_loss else None,
                        _decimal_text(proposal.target_price) if proposal.target_price else None,
                    ),
                )
            elif result.status == "FILLED" and proposal.action == DecisionAction.SELL:
                position = connection.execute(
                    """SELECT quantity FROM paper_positions
                       WHERE account_id = ? AND instrument_id = ?""",
                    (self.account_id, proposal.instrument_id),
                ).fetchone()
                if position is None:
                    connection.execute(
                        """UPDATE forward_trade_controls SET active = 0, exit_event_id = NULL
                           WHERE runtime_id = ? AND instrument_id = ?""",
                        (self.runtime_id, proposal.instrument_id),
                    )
                else:
                    connection.execute(
                        """UPDATE forward_trade_controls SET active = 1, exit_event_id = NULL
                           WHERE runtime_id = ? AND instrument_id = ?""",
                        (self.runtime_id, proposal.instrument_id),
                    )
        return result

    def _buy_feed_rejection(
        self,
        instrument_id: int,
        event: sqlite3.Row,
        snapshot: ExecutionSnapshot | None,
    ) -> str | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                """SELECT state_json, state_sha256 FROM forward_runtimes
                   WHERE runtime_id = ?""",
                (self.runtime_id,),
            ).fetchone()
            latest = connection.execute(
                """SELECT event_id, quote_timestamp, received_at
                   FROM forward_quotes WHERE runtime_id = ? AND instrument_id = ?
                     AND accepted = 1 ORDER BY sequence DESC LIMIT 1""",
                (self.runtime_id, instrument_id),
            ).fetchone()
        if (
            row is None
            or hashlib.sha256(row["state_json"].encode()).hexdigest() != row["state_sha256"]
        ):
            return "BUY rejected: persisted feed-health state is missing or invalid."
        try:
            state = json.loads(row["state_json"])
            feed = state["feed"].get(str(instrument_id))
            if not isinstance(feed, dict) or not isinstance(feed.get("status"), str):
                return "BUY rejected: instrument feed-health information is missing."
            status = feed["status"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return "BUY rejected: instrument feed-health information is invalid."
        if status != "HEALTHY":
            return f"BUY rejected: instrument feed is {status}."
        if latest is None:
            return "BUY rejected: no accepted quote establishes instrument feed health."
        if snapshot is None:
            return "BUY rejected: an independent execution snapshot is required."
        if (
            _timestamp_text(_timestamp(snapshot.timestamp, "snapshot timestamp"))
            != latest["quote_timestamp"]
            or _timestamp_text(_timestamp(snapshot.as_of, "snapshot as_of"))
            != latest["received_at"]
        ):
            return "BUY rejected: execution quote is not the latest accepted instrument quote."
        decision_time = _timestamp(event["decision_timestamp"], "decision timestamp")
        observed = _timestamp(latest["quote_timestamp"], "latest quote timestamp")
        received = _timestamp(latest["received_at"], "latest quote received_at")
        as_of = max(decision_time, _timestamp(snapshot.as_of, "snapshot as_of"))
        age_seconds = (as_of - observed).total_seconds()
        if age_seconds < 0 or age_seconds > self.degraded_after_seconds:
            return "BUY rejected: instrument feed quote is expired or degraded."
        if (received - observed).total_seconds() > self.max_quote_delay_seconds:
            return "BUY rejected: instrument feed quote exceeded the freshness limit."
        return None

    def _snapshot_validation_error(
        self, event: sqlite3.Row, snapshot: ExecutionSnapshot
    ) -> str | None:
        instrument_id = int(event["instrument_id"])
        if snapshot.interval != event["interval"]:
            return "Execution snapshot interval does not match the scheduler event."
        if snapshot.quote_event_id is None:
            return "Execution snapshot has no accepted quote identity."
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                """SELECT q.event_id, q.quote_timestamp, q.received_at, q.price_decimal,
                          q.quantity, i.import_id
                   FROM forward_quotes q JOIN forward_runtime_imports i
                     ON i.runtime_id = q.runtime_id AND i.instrument_id = q.instrument_id
                        AND i.interval = ?
                   WHERE q.runtime_id = ? AND q.instrument_id = ? AND q.accepted = 1
                     AND q.quote_timestamp = ? AND q.event_id = ?""",
                (
                    snapshot.interval,
                    self.runtime_id,
                    instrument_id,
                    _timestamp_text(_timestamp(snapshot.timestamp, "snapshot timestamp")),
                    snapshot.quote_event_id,
                ),
            ).fetchone()
        if row is None:
            return "Execution snapshot is not backed by an accepted runtime quote."
        with closing(get_connection(self.database_path)) as connection:
            latest = connection.execute(
                """SELECT event_id FROM forward_quotes WHERE runtime_id = ?
                   AND instrument_id = ? AND accepted = 1 ORDER BY sequence DESC LIMIT 1""",
                (self.runtime_id, instrument_id),
            ).fetchone()
        if latest is None or latest["event_id"] != row["event_id"]:
            return "Execution snapshot is not the latest accepted quote for this instrument."
        if (
            _timestamp_text(_timestamp(snapshot.as_of, "snapshot as_of")) != row["received_at"]
            or Decimal(row["price_decimal"]) != snapshot.reference_price
            or int(row["quantity"]) != snapshot.available_quantity
            or int(row["import_id"]) != snapshot.import_id
        ):
            return "Execution snapshot does not match the independently persisted quote."
        if _timestamp(snapshot.as_of, "snapshot as_of") > _timestamp(
            event["decision_timestamp"], "event decision timestamp"
        ) + timedelta(seconds=self.max_quote_delay_seconds):
            return "Execution snapshot is outside the scheduler event's decision window."
        if _timestamp(snapshot.timestamp, "snapshot timestamp") < _timestamp(
            event["decision_timestamp"], "event decision timestamp"
        ):
            return "Execution snapshot predates the scheduler event."
        if (
            _timestamp(snapshot.as_of, "snapshot as_of")
            - _timestamp(snapshot.timestamp, "snapshot timestamp")
        ).total_seconds() > self.max_quote_delay_seconds:
            return "Execution snapshot is expired."
        return None

    def _monitor_position(
        self, quote: Quote, observed: datetime, received: datetime, price: Decimal
    ) -> ProposalExecutionResult | None:
        with closing(get_connection(self.database_path)) as connection:
            control = connection.execute(
                """SELECT * FROM forward_trade_controls WHERE runtime_id = ?
                   AND instrument_id = ? AND active = 1""",
                (self.runtime_id, quote.instrument_id),
            ).fetchone()
            position = connection.execute(
                """SELECT quantity FROM paper_positions
                   WHERE account_id = ? AND instrument_id = ?""",
                (self.account_id, quote.instrument_id),
            ).fetchone()
            account = connection.execute(
                "SELECT halted, halt_reason, halt_class FROM paper_accounts WHERE id = ?",
                (self.account_id,),
            ).fetchone()
        if control is None or position is None or account is None:
            return None
        if halt_blocks_order(
            account["halted"], account["halt_class"], account["halt_reason"], "SELL"
        ):
            return None
        if control["exit_event_id"] is not None:
            return None
        stop = Decimal(control["stop_loss"]) if control["stop_loss"] else None
        target = Decimal(control["target_price"]) if control["target_price"] else None
        if not ((stop is not None and price <= stop) or (target is not None and price >= target)):
            return None
        trigger = "STOP" if stop is not None and price <= stop else "TARGET"
        event_id = _stable_id(
            f"exit:{self.runtime_id}:{quote.instrument_id}:{control['proposal_id']}:"
            f"{trigger}:{quote.event_id}"
        )
        exit_proposal_id = f"stop-target-{event_id}"
        exit_proposal_json = _canonical_json(
            {
                "canonicalization_version": "aegis.forward-exit/v1",
                "runtime_id": self.runtime_id,
                "event_id": event_id,
                "proposal_id": exit_proposal_id,
                "source_proposal_id": control["proposal_id"],
                "instrument_id": quote.instrument_id,
                "action": "SELL",
                "quantity": int(position["quantity"]),
                "entry_price": _decimal_text(price),
                "stop_loss": _decimal_text(stop) if stop is not None else None,
                "target_price": _decimal_text(target) if target is not None else None,
                "decision_timestamp": _timestamp_text(observed),
                "submitted_at": _timestamp_text(observed),
                "expires_at": _timestamp_text(
                    received + timedelta(seconds=self.max_quote_delay_seconds)
                ),
                "trigger": trigger,
            }
        )
        exit_proposal_sha256 = hashlib.sha256(exit_proposal_json.encode("utf-8")).hexdigest()
        snapshot = self._execution_snapshot(
            quote.instrument_id,
            observed,
            received,
            price,
            quote.quantity,
            quote_event_id=quote.event_id,
        )
        with _transaction(self.database_path) as connection:
            control_now = connection.execute(
                """SELECT active, exit_event_id FROM forward_trade_controls
                   WHERE runtime_id = ? AND instrument_id = ?""",
                (self.runtime_id, quote.instrument_id),
            ).fetchone()
            if control_now is None or not control_now["active"] or control_now["exit_event_id"]:
                return None
            connection.execute(
                """UPDATE forward_trade_controls SET exit_event_id = ?
                   WHERE runtime_id = ? AND instrument_id = ?""",
                (event_id, self.runtime_id, quote.instrument_id),
            )
            event_write = _SchedulerEventWrite(
                event_id=event_id,
                runtime_id=self.runtime_id,
                instrument_id=quote.instrument_id,
                interval="1m",
                candle_timestamp=_timestamp_text(_bucket(observed, 1)),
                decision_timestamp=_timestamp_text(observed),
                status="PENDING",
                proposal_id=exit_proposal_id,
                proposal_sha256=exit_proposal_sha256,
                proposal_json=exit_proposal_json,
                proposal_binding_state="BOUND",
                result_json=None,
                created_at=_timestamp_text(received),
            )
            with _authorize_scheduler_event_write(
                connection, "CREATE_STOP_TARGET_EXIT", event_write
            ):
                connection.execute(
                    """INSERT INTO forward_scheduler_events
                       (event_id, runtime_id, instrument_id, interval, candle_timestamp,
                        decision_timestamp, status, proposal_id, proposal_sha256,
                        proposal_json, proposal_binding_state, result_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    event_write.values(),
                )
        request = PaperOrderRequest(
            quote.instrument_id,
            "SELL",
            int(position["quantity"]),
            _timestamp_text(observed),
            _timestamp_text(observed),
        )
        try:
            with _transaction(self.database_path) as connection:
                reason = _forward_context_error(
                    connection,
                    self.account_id,
                    event_id,
                    event_id,
                    request,
                    snapshot,
                    require_authorization=False,
                )
                if reason is not None:
                    raise PaperTradingError(reason)
                event = connection.execute(
                    """SELECT runtime_id, proposal_id, proposal_sha256,
                              instrument_id, interval
                       FROM forward_scheduler_events WHERE event_id = ?""",
                    (event_id,),
                ).fetchone()
                quote_row = connection.execute(
                    """SELECT quote_timestamp, received_at FROM forward_quotes
                       WHERE runtime_id = ? AND instrument_id = ? AND event_id = ?
                         AND accepted = 1""",
                    (self.runtime_id, quote.instrument_id, snapshot.quote_event_id),
                ).fetchone()
                connection.execute(
                    """INSERT INTO forward_execution_authorizations
                       (event_id, runtime_id, account_id, proposal_id, proposal_sha256,
                        authorization_kind, instrument_id, interval, snapshot_id,
                        snapshot_sha256, quote_event_id, quote_timestamp, received_at,
                        import_id, status, authorized_at)
                       VALUES (?, ?, ?, ?, ?, 'STOP_TARGET_EXIT', ?, ?, ?, ?, ?, ?, ?, ?,
                               'AUTHORIZED', ?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    (
                        event_id,
                        event["runtime_id"],
                        self.account_id,
                        event["proposal_id"],
                        event["proposal_sha256"],
                        event["instrument_id"],
                        event["interval"],
                        snapshot.snapshot_id,
                        snapshot.snapshot_sha256,
                        snapshot.quote_event_id,
                        quote_row["quote_timestamp"],
                        quote_row["received_at"],
                        snapshot.import_id,
                        _timestamp_text(received),
                    ),
                )
            result = submit_paper_order(
                self.database_path,
                self.account_id,
                event_id,
                request,
                snapshot,
                forward_event_id=event_id,
            )
        except PaperTradingError as exc:
            with _transaction(self.database_path) as connection:
                connection.execute(
                    """UPDATE forward_runtimes SET status = 'DEGRADED', updated_at = ?
                       WHERE runtime_id = ?""",
                    (_timestamp_text(received), self.runtime_id),
                )
                connection.execute(
                    """UPDATE forward_trade_controls SET exit_event_id = NULL
                       WHERE runtime_id = ? AND instrument_id = ? AND exit_event_id = ?""",
                    (self.runtime_id, quote.instrument_id, event_id),
                )
                connection.execute(
                    """UPDATE forward_scheduler_events SET status = 'REJECTED',
                       result_json = ? WHERE event_id = ?""",
                    (
                        _canonical_json({"status": "REJECTED", "reason": str(exc)}),
                        event_id,
                    ),
                )
            return ProposalExecutionResult(f"exit-{event_id[:32]}", "REJECTED", reason=str(exc))
        with _transaction(self.database_path) as connection:
            connection.execute(
                """UPDATE forward_scheduler_events SET status = ?, result_json = ?
                   WHERE event_id = ? AND status = 'PENDING'""",
                (
                    result.status,
                    _canonical_json(
                        {
                            "status": result.status,
                            "order_id": result.order_id,
                            "reason": result.reason,
                        }
                    ),
                    event_id,
                ),
            )
            if result.status == "FILLED":
                remaining = connection.execute(
                    """SELECT quantity FROM paper_positions
                       WHERE account_id = ? AND instrument_id = ?""",
                    (self.account_id, quote.instrument_id),
                ).fetchone()
                if remaining is None:
                    connection.execute(
                        """UPDATE forward_trade_controls SET active = 0, exit_event_id = NULL
                           WHERE runtime_id = ? AND instrument_id = ? AND exit_event_id = ?""",
                        (self.runtime_id, quote.instrument_id, event_id),
                    )
                else:
                    connection.execute(
                        """UPDATE forward_trade_controls SET active = 1, exit_event_id = NULL
                           WHERE runtime_id = ? AND instrument_id = ? AND exit_event_id = ?""",
                        (self.runtime_id, quote.instrument_id, event_id),
                    )
            else:
                connection.execute(
                    """UPDATE forward_trade_controls SET exit_event_id = NULL
                       WHERE runtime_id = ? AND instrument_id = ? AND exit_event_id = ?""",
                    (self.runtime_id, quote.instrument_id, event_id),
                )
        return ProposalExecutionResult(
            f"exit-{event_id[:32]}",
            result.status,
            order_id=result.order_id,
            reason=result.reason,
            fill_price=snapshot.reference_price if result.status == "FILLED" else None,
            duplicate=result.duplicate,
        )

    def _execution_snapshot(
        self,
        instrument_id: int,
        observed: datetime,
        received: datetime,
        price: Decimal,
        available_quantity: int,
        interval: str = "1m",
        quote_event_id: str | None = None,
    ) -> ExecutionSnapshot:
        with closing(get_connection(self.database_path)) as connection:
            instrument = connection.execute(
                "SELECT exchange, trading_symbol FROM instruments WHERE id = ?",
                (instrument_id,),
            ).fetchone()
            import_row = connection.execute(
                """SELECT import_id FROM forward_runtime_imports WHERE runtime_id = ?
                   AND instrument_id = ? AND interval = ?""",
                (self.runtime_id, instrument_id, interval),
            ).fetchone()
        if instrument is None or import_row is None:
            raise ForwardRuntimeError("Synthetic quote provenance is missing.")
        local_date = received.astimezone(_INDIA).date()
        quality = analyze_dataset(
            self.database_path,
            instrument["exchange"],
            instrument["trading_symbol"],
            interval,
            local_date,
            local_date,
            as_of=received,
            calendars=self.calendars,
            source_import_id=int(import_row["import_id"]),
        )
        return ExecutionSnapshot(
            instrument_id,
            interval,
            _timestamp_text(observed),
            _timestamp_text(received),
            price,
            "execution_quote",
            available_quantity,
            int(import_row["import_id"]),
            quality,
            quote_event_id,
        )

    def execution_snapshot(
        self, instrument_id: int, quote: Quote, *, interval: str = "1m"
    ) -> ExecutionSnapshot:
        """Build a snapshot from an accepted runtime quote and existing quality gate."""
        if quote.instrument_id != instrument_id:
            raise ForwardRuntimeError("Execution quote instrument does not match the request.")
        if quote.event_id is None:
            raise ForwardRuntimeError("Execution quote must have an event identity.")
        if interval not in _INTERVAL_MINUTES:
            raise ForwardRuntimeError("Execution snapshot interval is unsupported.")
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(
                """SELECT accepted FROM forward_quotes WHERE runtime_id = ?
                   AND instrument_id = ? AND event_id = ?""",
                (self.runtime_id, instrument_id, quote.event_id),
            ).fetchone()
        if row is None or not row["accepted"]:
            raise ForwardRuntimeError("Execution snapshot requires a persisted accepted quote.")
        observed = _timestamp(quote.timestamp, "quote timestamp")
        received = _timestamp(quote.received_at or quote.timestamp, "received_at")
        price = _decimal(quote.price, "quote price")
        return self._execution_snapshot(
            instrument_id,
            observed,
            received,
            price,
            quote.quantity,
            interval,
            quote.event_id,
        )

    def portfolio_valuation(self, as_of: str | datetime | None = None) -> dict[str, object]:
        """Value the reconciled ledger using last-trade marks and expose mark freshness."""
        report = get_paper_account_report(self.database_path, self.account_id)
        reconciliation = report["reconciliation"]
        if not isinstance(reconciliation, dict) or reconciliation.get("status") != "RECONCILED":
            raise ForwardRuntimeError("Paper ledger is not reconciled; valuation is unavailable.")
        cash = Decimal(str(report["available_cash"]))
        exposure = Decimal(str(report["gross_exposure"]))
        equity = cash + exposure
        persisted_equity = Decimal(str(report["equity_before_estimated_liquidation_costs"]))
        if equity != persisted_equity:
            raise ForwardRuntimeError("Valuation does not match the persisted paper ledger.")
        as_of_time = (
            datetime.now(UTC)
            if as_of is None
            else _timestamp(as_of, "valuation as_of")
            if isinstance(as_of, str)
            else _timestamp(as_of.isoformat(), "valuation as_of")
        )
        risk = report["risk_limits"]
        if not isinstance(risk, dict):
            raise ForwardRuntimeError("Paper account risk configuration is unavailable.")
        max_age = int(risk["max_market_data_age_seconds"])
        positions = report["positions"]
        if not isinstance(positions, list):
            raise ForwardRuntimeError("Paper account positions are unavailable.")
        marks: list[dict[str, object]] = []
        for position in positions:
            if not isinstance(position, dict):
                raise ForwardRuntimeError("Paper account position data is malformed.")
            mark_timestamp = position["mark_timestamp"]
            if mark_timestamp is None:
                status = "MISSING"
                age = None
            else:
                age = Decimal(
                    str(
                        (
                            as_of_time - _timestamp(str(mark_timestamp), "position mark timestamp")
                        ).total_seconds()
                    )
                )
                status = "INVALID" if age < 0 else "FRESH" if age <= max_age else "STALE"
            marks.append(
                {
                    "exchange": position["exchange"],
                    "symbol": position["symbol"],
                    "mark_price": position["mark_price"],
                    "mark_timestamp": mark_timestamp,
                    "age_seconds": _decimal_text(age) if age is not None else None,
                    "freshness": status,
                    "price_basis": "LAST_TRADE",
                }
            )
        valuation_status = (
            "STALE" if any(mark["freshness"] != "FRESH" for mark in marks) else "CURRENT"
        )
        return {
            "simulated": True,
            "currency": "INR",
            "as_of": _timestamp_text(as_of_time),
            "cash": _decimal_text(cash),
            "market_value": _decimal_text(exposure),
            "equity": _decimal_text(equity),
            "valuation_status": valuation_status,
            "mark_convention": "Last accepted trade price; not an executable bid or liquidation quote.",
            "marks": marks,
            "realized_net_pnl": report["realized_net_pnl"],
            "unrealized_gross_pnl": report["unrealized_gross_pnl"],
            "reconciliation": "RECONCILED",
        }


def _new_candle(
    instrument_id: int,
    interval: str,
    start: datetime,
    price: Decimal,
    quantity: int,
) -> dict[str, Any]:
    price_text = _decimal_text(price)
    return {
        "instrument_id": instrument_id,
        "interval": interval,
        "candle_timestamp": _timestamp_text(start),
        "open_decimal": price_text,
        "high_decimal": price_text,
        "low_decimal": price_text,
        "close_decimal": price_text,
        "volume": quantity,
    }


def _extend_candle(candle: dict[str, Any], price: Decimal, quantity: int) -> None:
    candle["high_decimal"] = _decimal_text(max(Decimal(str(candle["high_decimal"])), price))
    candle["low_decimal"] = _decimal_text(min(Decimal(str(candle["low_decimal"])), price))
    candle["close_decimal"] = _decimal_text(price)
    candle["volume"] = int(candle["volume"]) + quantity


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        raise ForwardRuntimeError("Cannot serialize a missing Decimal value.")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _execution_result(
    proposal_id: str, status: str, result_json: str | None, duplicate: bool
) -> ProposalExecutionResult:
    result: dict[str, object] = json.loads(result_json) if result_json else {}
    allowed = cast(
        Literal["HOLD", "REJECTED", "FILLED"],
        status if status in {"HOLD", "REJECTED", "FILLED"} else "REJECTED",
    )
    fill_value = result.get("fill_price")
    return ProposalExecutionResult(
        proposal_id,
        allowed,
        order_id=str(result["order_id"]) if result.get("order_id") else None,
        reason=str(result["reason"]) if result.get("reason") else None,
        fill_price=Decimal(str(fill_value)) if fill_value else None,
        duplicate=duplicate,
    )


def list_forward_runtime_summaries(database_path: Path) -> list[dict[str, object]]:
    """Read-only dashboard summary of runtime status and event/candle counts."""
    if not Path(database_path).is_file():
        return []
    with closing(get_readonly_connection(Path(database_path))) as connection:
        rows = connection.execute(
            """SELECT r.runtime_id, r.account_id, r.status, r.last_event_at, r.state_json,
                      r.config_json,
                      (SELECT COUNT(*) FROM forward_scheduler_events e
                       WHERE e.runtime_id = r.runtime_id) AS event_count,
                      (SELECT COUNT(*) FROM forward_runtime_candles c
                       WHERE c.runtime_id = r.runtime_id) AS candle_count
               FROM forward_runtimes r ORDER BY r.created_at, r.runtime_id"""
        ).fetchall()
    summaries = []
    for row in rows:
        state = json.loads(row["state_json"])
        config = json.loads(row["config_json"])
        current = datetime.now(UTC)
        health: dict[str, str] = {}
        for instrument_id, value in state["feed"].items():
            cursor = state["cursor"].get(instrument_id, {})
            timestamp = cursor.get("timestamp")
            status = str(value.get("status", "INVALID"))
            if timestamp is None:
                status = status if status == "DISCONNECTED" else "STARTING"
            else:
                age = (current - _timestamp(timestamp, "feed cursor timestamp")).total_seconds()
                if age < 0:
                    status = "INVALID"
                elif age > int(config["stale_after_seconds"]):
                    status = "STALE"
                elif age > int(config["degraded_after_seconds"]):
                    status = "DEGRADED"
            health[instrument_id] = status
        account_report = get_paper_account_report(database_path, int(row["account_id"]))
        reconciliation = account_report["reconciliation"]
        if not isinstance(reconciliation, dict):
            raise ForwardRuntimeError("Paper account reconciliation report is malformed.")
        reconciliation_status = reconciliation.get("status")
        if not isinstance(reconciliation_status, str):
            raise ForwardRuntimeError("Paper account reconciliation status is unavailable.")
        risk = account_report["risk_limits"]
        if not isinstance(risk, dict):
            raise ForwardRuntimeError("Paper account risk configuration is unavailable.")
        max_age = int(risk["max_market_data_age_seconds"])
        mark_freshness: list[dict[str, object]] = []
        positions = account_report["positions"]
        if not isinstance(positions, list):
            raise ForwardRuntimeError("Paper account positions are unavailable.")
        for position in positions:
            if not isinstance(position, dict):
                raise ForwardRuntimeError("Paper account position data is malformed.")
            mark_timestamp = position["mark_timestamp"]
            if mark_timestamp is None:
                mark_status = "MISSING"
                mark_age = None
            else:
                mark_age = Decimal(
                    str(
                        (
                            current - _timestamp(str(mark_timestamp), "mark timestamp")
                        ).total_seconds()
                    )
                )
                mark_status = (
                    "INVALID" if mark_age < 0 else "FRESH" if mark_age <= max_age else "STALE"
                )
            mark_freshness.append(
                {
                    "symbol": position["symbol"],
                    "mark_price": position["mark_price"],
                    "mark_timestamp": mark_timestamp,
                    "age_seconds": _decimal_text(mark_age) if mark_age is not None else None,
                    "status": mark_status,
                    "price_basis": "LAST_TRADE",
                }
            )
        recovery = state.get("scheduler_recovery", {})
        summaries.append(
            {
                "runtime_id": row["runtime_id"],
                "account_id": row["account_id"],
                "status": row["status"],
                "last_event_at": row["last_event_at"],
                "feed_health": health,
                "mark_freshness": mark_freshness,
                "reconciliation_status": reconciliation_status,
                "valuation_status": (
                    "STALE"
                    if any(mark["status"] != "FRESH" for mark in mark_freshness)
                    else "CURRENT"
                ),
                "recovered_event_count": recovery.get("recovered_total", 0),
                "last_recovered_event_ids": recovery.get("last_recovered_event_ids", []),
                "candle_count": row["candle_count"],
                "scheduler_event_count": row["event_count"],
                "mode": "RESEARCH_ONLY",
                "provider": "DETERMINISTIC_SYNTHETIC",
            }
        )
    return summaries
