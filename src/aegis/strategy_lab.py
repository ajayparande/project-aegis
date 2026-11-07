"""Versioned, deterministic strategy specifications and research-only signals."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from aegis.database import get_connection
from aegis.market_data import SUPPORTED_INTERVALS, import_ohlcv_csv
from aegis.market_quality import (
    CalendarRegistry,
    DatasetQualityReport,
    ExchangeCalendar,
    SessionWindow,
    analyze_dataset,
    calculate_expected_candles,
)

SPEC_SCHEMA_VERSION = 1
EXPERIMENT_CONFIGURATION_SCHEMA_VERSION = 2
_SUPPORTED_EXPERIMENT_CONFIGURATION_VERSIONS = frozenset({1, 2})
_SUPPORTED_EXCHANGES = frozenset({"NSE", "BSE"})
_SUPPORTED_UNIVERSE_TYPES = frozenset({"EQUITY", "INDEX"})
_VERSION_TEXT = re.compile(r"\d+\.\d+\.\d+\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class StrategyLabError(ValueError):
    """Raised when a strategy definition or research input is invalid."""


class DataQualityGateError(StrategyLabError):
    """Raised when the dataset fails the confirmed Phase 2 quality gate."""


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """Machine-readable immutable version of the supported SMA crossover hypothesis."""

    strategy_key: str
    version: str
    name: str
    exchange: str
    trading_symbol: str
    instrument_type: str
    interval: str
    fast_window: int
    slow_window: int
    rationale: str
    hypothesis: str
    evidence_references: tuple[str, ...]
    entry_rule: str
    exit_rule: str
    position_sizing: str
    stop_logic: str
    trading_frequency: str
    costs_assumptions: str
    slippage_assumptions: str
    known_limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        exchange = self.exchange.strip().upper()
        symbol = self.trading_symbol.strip().upper()
        instrument_type = self.instrument_type.strip().upper()
        interval = self.interval.strip().lower()
        if self.strategy_key != "sma_crossover":
            raise StrategyLabError("Only the sma_crossover baseline is currently supported.")
        if _VERSION_TEXT.fullmatch(self.version) is None:
            raise StrategyLabError("Strategy version must use MAJOR.MINOR.PATCH format.")
        if not self.name.strip() or not self.rationale.strip() or not self.hypothesis.strip():
            raise StrategyLabError("Strategy name, rationale, and hypothesis are required.")
        if exchange not in _SUPPORTED_EXCHANGES or not symbol:
            raise StrategyLabError("A registered NSE/BSE exchange and trading symbol are required.")
        if instrument_type not in _SUPPORTED_UNIVERSE_TYPES:
            raise StrategyLabError(
                "Phase 3 SMA research currently supports EQUITY and INDEX instruments only."
            )
        if interval not in SUPPORTED_INTERVALS:
            raise StrategyLabError(f"Unsupported interval {interval!r}.")
        for label, value in (("fast_window", self.fast_window), ("slow_window", self.slow_window)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                raise StrategyLabError(f"{label} must be an integer of at least 2.")
        if self.fast_window >= self.slow_window:
            raise StrategyLabError("fast_window must be smaller than slow_window.")
        required_text = (
            self.entry_rule,
            self.exit_rule,
            self.position_sizing,
            self.stop_logic,
            self.trading_frequency,
            self.costs_assumptions,
            self.slippage_assumptions,
        )
        if any(not item.strip() for item in required_text):
            raise StrategyLabError("Strategy rules and execution assumptions must be documented.")
        if any(not item.strip() for item in (*self.evidence_references, *self.known_limitations)):
            raise StrategyLabError(
                "Evidence references and limitations cannot contain blank items."
            )
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "trading_symbol", symbol)
        object.__setattr__(self, "instrument_type", instrument_type)
        object.__setattr__(self, "interval", interval)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready, versioned representation of this immutable specification."""

        return {"spec_schema_version": SPEC_SCHEMA_VERSION, **asdict(self)}


@dataclass(frozen=True, slots=True)
class StrategyCandle:
    """Validated historical OHLCV input for pure signal calculation."""

    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """Signal known at a completed candle close; it is not an order or a fill."""

    timestamp: str
    action: Literal["ENTER_LONG", "EXIT_LONG"]
    close: Decimal
    fast_sma: Decimal
    slow_sma: Decimal


@dataclass(frozen=True, slots=True)
class ExperimentRegistration:
    """Persistent Phase 3 strategy and experiment identifiers."""

    strategy_id: int
    experiment_id: int
    experiment_name: str


@dataclass(frozen=True, slots=True)
class StrategyRunResult:
    """Outcome of one research-only signal-generation run."""

    run_id: int
    status: str
    signals: tuple[StrategySignal, ...]
    dataset_sha256: str | None
    reason: str | None


def create_sma_crossover_spec(
    *,
    exchange: str,
    trading_symbol: str,
    instrument_type: str,
    interval: str,
    fast_window: int,
    slow_window: int,
    hypothesis: str,
    version: str = "1.0.0",
    evidence_references: tuple[str, ...] = (),
) -> StrategySpec:
    """Build the documented baseline without adding execution or profitability claims."""

    return StrategySpec(
        strategy_key="sma_crossover",
        version=version,
        name="Simple moving-average crossover",
        exchange=exchange,
        trading_symbol=trading_symbol,
        instrument_type=instrument_type,
        interval=interval,
        fast_window=fast_window,
        slow_window=slow_window,
        rationale="Compare short- and long-window close-price averages as a transparent baseline.",
        hypothesis=hypothesis,
        evidence_references=evidence_references,
        entry_rule="Emit ENTER_LONG when the fast SMA crosses above the slow SMA at candle close.",
        exit_rule="Emit EXIT_LONG when the fast SMA crosses below the slow SMA at candle close.",
        position_sizing="Not modeled in Phase 3; signals do not specify quantity.",
        stop_logic="Not modeled in Phase 3; no stop or order is created.",
        trading_frequency="At most one long-entry or long-exit transition per completed candle.",
        costs_assumptions="Not modeled in Phase 3; no return or performance metric is produced.",
        slippage_assumptions="Not modeled in Phase 3; no simulated fill is produced.",
        known_limitations=(
            "The experiment uses a point-in-time instrument selection and does not correct survivorship bias.",
            "Corporate actions and adjusted-price series are not modeled.",
            "Signals are informational and do not model next-bar execution.",
            "A fully confirmed calendar and complete, fresh data are required to run an experiment.",
        ),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_hash(value: object) -> str:
    return _sha256(_canonical_json(value))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _market_quality_source_path() -> Path:
    return Path(inspect.getfile(analyze_dataset)).resolve()


def _market_data_source_path() -> Path:
    return Path(inspect.getfile(import_ohlcv_csv)).resolve()


def _source_revision() -> dict[str, str | None]:
    """Return a commit only when Git is available and the complete worktree is clean."""

    root = Path(__file__).resolve().parents[2]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "status": "UNAVAILABLE"}
    if status:
        return {"commit": None, "status": "UNCOMMITTED"}
    return {"commit": revision or None, "status": "CLEAN" if revision else "UNAVAILABLE"}


def _session_document(session: SessionWindow) -> dict[str, str]:
    return {
        "opens": session.opens.isoformat(),
        "closes": session.closes.isoformat(),
    }


def _calendar_document(calendar: ExchangeCalendar) -> dict[str, object]:
    return {
        "exchange": calendar.exchange,
        "segment": calendar.segment,
        "timezone": calendar.timezone_name,
        "confidence": calendar.confidence,
        "coverage_start": calendar.coverage_start.isoformat(),
        "coverage_end": calendar.coverage_end.isoformat(),
        "source": calendar.source,
        "weekdays": sorted(calendar.weekdays),
        "sessions": [_session_document(item) for item in calendar.sessions],
        "holidays": [
            {"date": day.isoformat(), "reason": reason}
            for day, reason in sorted(calendar.holidays.items())
        ],
        "special_sessions": [
            {
                "date": day.isoformat(),
                "reason": special.reason,
                "sessions": [_session_document(item) for item in special.sessions],
            }
            for day, special in sorted(calendar.special_sessions.items())
        ],
    }


def _calendar_registry_hash(calendars: CalendarRegistry) -> str:
    if calendars.configuration_sha256 is not None:
        return calendars.configuration_sha256
    return _canonical_hash(
        {
            "calendars": [
                _calendar_document(item)
                for item in sorted(
                    calendars.calendars, key=lambda item: (item.exchange, item.segment)
                )
            ],
            "staleness_policy": {
                "potential_after_missing": calendars.staleness_policy.potential_after_missing,
                "stale_after_missing": calendars.staleness_policy.stale_after_missing,
            },
        }
    )


def _effective_calendar_hash(calendar: ExchangeCalendar, calendars: CalendarRegistry) -> str:
    return _canonical_hash(
        {
            "calendar": _calendar_document(calendar),
            "staleness_policy": {
                "potential_after_missing": calendars.staleness_policy.potential_after_missing,
                "stale_after_missing": calendars.staleness_policy.stale_after_missing,
            },
        }
    )


def _validate_experiment_configuration(
    configuration: object, spec: StrategySpec
) -> tuple[dict[str, object], date, date, str]:
    if not isinstance(configuration, dict):
        raise StrategyLabError("Experiment configuration must be a JSON object.")
    configuration_schema_version = configuration.get("configuration_schema_version")
    if (
        isinstance(configuration_schema_version, bool)
        or not isinstance(configuration_schema_version, int)
        or configuration_schema_version not in _SUPPORTED_EXPERIMENT_CONFIGURATION_VERSIONS
    ):
        raise StrategyLabError("Experiment configuration has an unsupported schema version.")
    try:
        start_date = date.fromisoformat(configuration["start_date"])
        end_date = date.fromisoformat(configuration["end_date"])
        expected_spec_hash = configuration["strategy_spec_sha256"]
    except (KeyError, TypeError, ValueError) as exc:
        raise StrategyLabError("Experiment configuration has invalid dates or spec hash.") from exc
    if (
        start_date.isoformat() != configuration["start_date"]
        or end_date.isoformat() != configuration["end_date"]
    ):
        raise StrategyLabError("Experiment dates must use canonical ISO format.")
    if start_date > end_date:
        raise StrategyLabError("Experiment start_date must not be after end_date.")
    if not isinstance(expected_spec_hash, str) or expected_spec_hash != _canonical_hash(
        spec.to_dict()
    ):
        raise StrategyLabError("Experiment strategy snapshot failed its checksum validation.")
    if configuration.get("interval") != spec.interval:
        raise StrategyLabError("Experiment interval does not match its strategy specification.")

    instrument = configuration.get("instrument")
    if not isinstance(instrument, dict) or any(
        instrument.get(field) != expected
        for field, expected in (
            ("exchange", spec.exchange),
            ("trading_symbol", spec.trading_symbol),
            ("instrument_type", spec.instrument_type),
        )
    ):
        raise StrategyLabError(
            "Experiment instrument identity does not match its strategy specification."
        )
    strategy_identity = configuration.get("strategy_identity")
    if configuration_schema_version >= 2 and strategy_identity is None:
        raise StrategyLabError("Experiment configuration is missing its strategy identity.")
    if strategy_identity is not None and (
        not isinstance(strategy_identity, dict)
        or any(
            strategy_identity.get(field) != expected
            for field, expected in (
                ("strategy_key", spec.strategy_key),
                ("name", spec.name),
                ("version", spec.version),
            )
        )
    ):
        raise StrategyLabError("Experiment strategy identity does not match its specification.")
    return configuration, start_date, end_date, expected_spec_hash


def _strategy_record_name(spec: StrategySpec) -> str:
    """Keep the same strategy/version independently registrable for different universes."""

    return f"{spec.name} — {spec.exchange}:{spec.trading_symbol} {spec.interval}"


def _validate_legacy_strategy_reference(
    connection: sqlite3.Connection, strategy_id: int, experiment_spec: StrategySpec
) -> None:
    """Ensure a legacy configuration still matches its separately persisted strategy row."""

    strategy = connection.execute(
        "SELECT name, version, description FROM strategies WHERE id = ?", (strategy_id,)
    ).fetchone()
    if strategy is None:
        raise StrategyLabError(
            f"Legacy experiment references missing strategy record {strategy_id}."
        )
    try:
        persisted_document = json.loads(strategy["description"])
        persisted_spec = _parse_spec(persisted_document)
    except (TypeError, json.JSONDecodeError, StrategyLabError) as exc:
        raise StrategyLabError(
            f"Legacy experiment references invalid strategy record {strategy_id}."
        ) from exc

    if (
        _canonical_json(persisted_spec.to_dict()) != _canonical_json(experiment_spec.to_dict())
        or strategy["version"] != experiment_spec.version
        or strategy["name"] != _strategy_record_name(experiment_spec)
    ):
        raise StrategyLabError(
            "Legacy experiment strategy snapshot does not match its persisted strategy record."
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_spec(document: object) -> StrategySpec:
    if not isinstance(document, dict) or document.get("spec_schema_version") != SPEC_SCHEMA_VERSION:
        raise StrategyLabError("Stored strategy specification has an unsupported schema version.")
    values = dict(document)
    values.pop("spec_schema_version")
    refs = values.get("evidence_references")
    limitations = values.get("known_limitations")
    if not isinstance(refs, list) or not isinstance(limitations, list):
        raise StrategyLabError("Stored evidence references and limitations must be JSON arrays.")
    values["evidence_references"] = tuple(refs)
    values["known_limitations"] = tuple(limitations)
    try:
        return StrategySpec(**values)
    except TypeError as exc:
        raise StrategyLabError(f"Stored strategy specification is malformed: {exc}") from exc


def _validate_candles(candles: tuple[StrategyCandle, ...] | list[StrategyCandle]) -> None:
    previous_timestamp: str | None = None
    for candle in candles:
        if not isinstance(candle, StrategyCandle):
            raise StrategyLabError("Signal input must contain StrategyCandle values.")
        if _UTC_TIMESTAMP.fullmatch(candle.timestamp) is None:
            raise StrategyLabError("Candle timestamps must use canonical UTC ISO text.")
        try:
            datetime.fromisoformat(candle.timestamp)
        except ValueError as exc:
            raise StrategyLabError("Candle timestamp is not a valid UTC date-time.") from exc
        if previous_timestamp is not None and candle.timestamp <= previous_timestamp:
            raise StrategyLabError(
                "Candles must have unique timestamps in strictly increasing order."
            )
        previous_timestamp = candle.timestamp
        prices = (candle.open, candle.high, candle.low, candle.close)
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in prices
        ):
            raise StrategyLabError("Candle OHLC values must be finite positive Decimal values.")
        if candle.high < max(candle.open, candle.low, candle.close):
            raise StrategyLabError("Candle high must be at least open, low, and close.")
        if candle.low > min(candle.open, candle.high, candle.close):
            raise StrategyLabError("Candle low must be no greater than open, high, and close.")
        if (
            isinstance(candle.volume, bool)
            or not isinstance(candle.volume, int)
            or candle.volume < 0
        ):
            raise StrategyLabError("Candle volume must be a non-negative integer.")


def simple_moving_average(
    values: list[Decimal] | tuple[Decimal, ...], window: int
) -> tuple[Decimal | None, ...]:
    """Calculate trailing close averages with Decimal arithmetic and no future values."""

    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise StrategyLabError("SMA window must be a positive integer.")
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise StrategyLabError("SMA inputs must be finite Decimal values.")
    if not values:
        return ()

    integer_places = max(max(value.adjusted() + 1, 0) for value in values)
    exponents = [value.as_tuple().exponent for value in values]
    if any(not isinstance(exponent, int) for exponent in exponents):
        raise StrategyLabError("SMA inputs must use finite Decimal exponents.")
    fractional_places = max(
        max(-exponent, 0) for exponent in exponents if isinstance(exponent, int)
    )
    precision = max(28, integer_places + fractional_places + len(str(len(values))) + 12)
    result: list[Decimal | None] = []
    with localcontext() as context:
        context.prec = precision
        context.rounding = ROUND_HALF_EVEN
        rolling_sum = Decimal(0)
        for index, value in enumerate(values):
            rolling_sum += value
            if index >= window:
                rolling_sum -= values[index - window]
            result.append(rolling_sum / Decimal(window) if index + 1 >= window else None)
    return tuple(result)


def generate_sma_crossover_signals(
    candles: list[StrategyCandle] | tuple[StrategyCandle, ...],
    *,
    fast_window: int,
    slow_window: int,
) -> tuple[StrategySignal, ...]:
    """Emit long-only crossover signals at candle close; never simulates execution."""

    if (
        isinstance(fast_window, bool)
        or not isinstance(fast_window, int)
        or isinstance(slow_window, bool)
        or not isinstance(slow_window, int)
        or fast_window < 2
        or slow_window <= fast_window
    ):
        raise StrategyLabError("Require 2 <= fast_window < slow_window.")
    _validate_candles(candles)
    closes = tuple(candle.close for candle in candles)
    fast = simple_moving_average(closes, fast_window)
    slow = simple_moving_average(closes, slow_window)
    signals: list[StrategySignal] = []
    in_long_state = False

    for index in range(slow_window, len(candles)):
        previous_fast, previous_slow = fast[index - 1], slow[index - 1]
        current_fast, current_slow = fast[index], slow[index]
        if (
            previous_fast is None
            or previous_slow is None
            or current_fast is None
            or current_slow is None
        ):
            continue
        action: Literal["ENTER_LONG", "EXIT_LONG"] | None = None
        if not in_long_state and previous_fast <= previous_slow and current_fast > current_slow:
            action = "ENTER_LONG"
            in_long_state = True
        elif in_long_state and previous_fast >= previous_slow and current_fast < current_slow:
            action = "EXIT_LONG"
            in_long_state = False
        if action is not None:
            candle = candles[index]
            signals.append(
                StrategySignal(candle.timestamp, action, candle.close, current_fast, current_slow)
            )
    return tuple(signals)


@contextmanager
def _write_transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
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


def _readonly_connection(database_path: Path) -> sqlite3.Connection:
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def register_experiment(
    database_path: Path,
    spec: StrategySpec,
    *,
    start_date: date,
    end_date: date,
) -> ExperimentRegistration:
    """Persist a new experiment with immutable strategy and configuration snapshots."""

    if start_date > end_date:
        raise StrategyLabError("Experiment start_date must not be after end_date.")
    spec_json = _canonical_json(spec.to_dict())
    spec_hash = _sha256(spec_json)
    with _write_transaction(database_path) as connection:
        instrument = connection.execute(
            """SELECT id, instrument_type, is_active FROM instruments
               WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE""",
            (spec.exchange, spec.trading_symbol),
        ).fetchone()
        if instrument is None:
            raise StrategyLabError(
                f"Instrument {spec.exchange}:{spec.trading_symbol} is not registered."
            )
        if not instrument["is_active"]:
            raise StrategyLabError("Strategy experiments require an active registered instrument.")
        if instrument["instrument_type"].upper() != spec.instrument_type:
            raise StrategyLabError("Strategy instrument type does not match the Instrument Master.")

        strategy_name = _strategy_record_name(spec)
        strategy = connection.execute(
            "SELECT id, description FROM strategies WHERE name = ? AND version = ? ORDER BY id LIMIT 1",
            (strategy_name, spec.version),
        ).fetchone()
        if strategy is not None:
            if strategy["description"] != spec_json:
                raise StrategyLabError(
                    "A strategy version is immutable; register changed rules under a new version."
                )
            strategy_id = int(strategy["id"])
        else:
            cursor = connection.execute(
                "INSERT INTO strategies (name, description, version, status) VALUES (?, ?, ?, 'RESEARCH')",
                (strategy_name, spec_json, spec.version),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a strategy identifier.")
            strategy_id = int(cursor.lastrowid)

        hypothesis_description = _canonical_json(
            {
                "hypothesis": spec.hypothesis,
                "evidence_references": list(spec.evidence_references),
                "rationale": spec.rationale,
            }
        )
        hypothesis_cursor = connection.execute(
            "INSERT INTO hypotheses (title, description, status) VALUES (?, ?, 'TESTABLE')",
            (f"{spec.name} — {spec.trading_symbol}", hypothesis_description),
        )
        if hypothesis_cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a hypothesis identifier.")

        experiment_name = (
            f"{spec.name} v{spec.version} — {spec.exchange}:{spec.trading_symbol} "
            f"{spec.interval} {start_date} to {end_date}"
        )
        configuration = {
            "configuration_schema_version": EXPERIMENT_CONFIGURATION_SCHEMA_VERSION,
            "strategy_identity": {
                "strategy_key": spec.strategy_key,
                "name": spec.name,
                "version": spec.version,
            },
            "strategy_spec": spec.to_dict(),
            "strategy_spec_sha256": spec_hash,
            "instrument": {
                "exchange": spec.exchange,
                "trading_symbol": spec.trading_symbol,
                "instrument_type": spec.instrument_type,
                "universe_policy": "Current registered instrument snapshot; historical membership is not modeled.",
            },
            "interval": spec.interval,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        configuration_sha256 = _canonical_hash(configuration)
        experiment_cursor = connection.execute(
            """INSERT INTO experiments
               (name, strategy_id, hypothesis_id, configuration_json, status,
                configuration_sha256, configuration_hash_origin)
               VALUES (?, ?, ?, ?, 'REGISTERED', ?, 'REGISTRATION')""",
            (
                experiment_name,
                strategy_id,
                int(hypothesis_cursor.lastrowid),
                _canonical_json(configuration),
                configuration_sha256,
            ),
        )
        if experiment_cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return an experiment identifier.")
        return ExperimentRegistration(
            strategy_id, int(experiment_cursor.lastrowid), experiment_name
        )


def list_strategy_experiments(database_path: Path) -> list[dict[str, object]]:
    """List Phase 3 experiments using their frozen JSON configuration snapshots."""

    with closing(_readonly_connection(database_path)) as connection:
        rows = connection.execute(
            """SELECT id, name, status, configuration_json, configuration_sha256,
                      configuration_hash_origin, created_at
               FROM experiments WHERE strategy_id IS NOT NULL ORDER BY id DESC"""
        ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        try:
            configuration = json.loads(row["configuration_json"])
            spec = _parse_spec(configuration["strategy_spec"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise StrategyLabError(f"Experiment {row['id']} has malformed configuration.") from exc
        result.append(
            {
                "id": int(row["id"]),
                "name": row["name"],
                "status": row["status"],
                "created_at": row["created_at"],
                "exchange": spec.exchange,
                "trading_symbol": spec.trading_symbol,
                "instrument_type": spec.instrument_type,
                "interval": spec.interval,
                "fast_window": spec.fast_window,
                "slow_window": spec.slow_window,
                "start_date": configuration["start_date"],
                "end_date": configuration["end_date"],
                "strategy_spec_sha256": configuration["strategy_spec_sha256"],
                "configuration_sha256": row["configuration_sha256"],
                "configuration_hash_origin": row["configuration_hash_origin"],
            }
        )
    return result


def _write_run_result(
    database_path: Path,
    run_id: int,
    *,
    status: str,
    result_json: str,
    error_message: str | None,
) -> None:
    with _write_transaction(database_path) as connection:
        connection.execute(
            """UPDATE runs SET status = ?, completed_at = CURRENT_TIMESTAMP,
               result_json = ?, error_message = ? WHERE id = ? AND status = 'RUNNING'""",
            (status, result_json, error_message, run_id),
        )


def _gate_reason(report: DatasetQualityReport) -> str | None:
    if report.calendar_status != "FULL" or report.calendar_confidence != "CONFIRMED":
        return "A fully covered CONFIRMED exchange calendar is required."
    if report.overall_status != "COMPLETE":
        return (
            f"Data quality status is {report.overall_status}; only COMPLETE data can be evaluated."
        )
    if report.completeness_percentage != Decimal("100.00"):
        return "The selected dataset does not have 100% confirmed candle completeness."
    if report.staleness_status != "FRESH":
        return (
            f"Data freshness status is {report.staleness_status}; only FRESH data can be evaluated."
        )
    if report.invalid_candle_count or report.duplicate_identity_count:
        return "Invalid or duplicate stored candles block strategy evaluation."
    return None


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _load_completed_candles(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    timezone_name: str,
    completed_timestamps: set[str],
) -> tuple[list[StrategyCandle], str, tuple[str, ...]]:
    """Read exact rows from the selected local-date range and fingerprint their sources."""

    try:
        lower = datetime.combine(start_date, time.min, UTC) - timedelta(days=1)
    except OverflowError:
        lower_text = None
    else:
        lower_text = lower.isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        upper = datetime.combine(end_date, time.min, UTC) + timedelta(days=2)
    except OverflowError:
        upper_text = None
    else:
        upper_text = upper.isoformat(timespec="seconds").replace("+00:00", "Z")

    clauses = [
        "i.exchange = ? COLLATE NOCASE",
        "i.trading_symbol = ? COLLATE NOCASE",
        "c.interval = ?",
    ]
    parameters: list[object] = [exchange, trading_symbol, interval]
    if lower_text is not None:
        clauses.append("c.candle_timestamp >= ?")
        parameters.append(lower_text)
    if upper_text is not None:
        clauses.append("c.candle_timestamp < ?")
        parameters.append(upper_text)
    query = f"""SELECT c.candle_timestamp, c.open_decimal, c.high_decimal,
                       c.low_decimal, c.close_decimal, c.volume, d.file_sha256
                FROM historical_candles AS c
                JOIN instruments AS i ON i.id = c.instrument_id
                LEFT JOIN data_imports AS d ON d.id = c.import_id
                WHERE {" AND ".join(clauses)}
                ORDER BY c.candle_timestamp"""
    timezone = ZoneInfo(timezone_name)
    candles: list[StrategyCandle] = []
    records: list[dict[str, object]] = []
    import_hashes: set[str] = set()
    with closing(_readonly_connection(database_path)) as connection:
        rows = connection.execute(query, parameters).fetchall()
    for row in rows:
        timestamp = row["candle_timestamp"]
        try:
            if not isinstance(timestamp, str) or _UTC_TIMESTAMP.fullmatch(timestamp) is None:
                raise StrategyLabError("Stored candle has an invalid timestamp.")
            parsed = datetime.fromisoformat(timestamp)
        except (AttributeError, ValueError) as exc:
            raise StrategyLabError("Stored candle has an invalid timestamp.") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise StrategyLabError("Stored candle timestamp is not timezone-aware.")
        if not start_date <= parsed.astimezone(timezone).date() <= end_date:
            continue
        if timestamp not in completed_timestamps:
            continue
        try:
            candle = StrategyCandle(
                timestamp=timestamp,
                open=Decimal(row["open_decimal"]),
                high=Decimal(row["high_decimal"]),
                low=Decimal(row["low_decimal"]),
                close=Decimal(row["close_decimal"]),
                volume=row["volume"],
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise StrategyLabError("Stored candle contains invalid OHLCV values.") from exc
        candles.append(candle)
        records.append(
            {
                "timestamp": candle.timestamp,
                "open": _decimal_text(candle.open),
                "high": _decimal_text(candle.high),
                "low": _decimal_text(candle.low),
                "close": _decimal_text(candle.close),
                "volume": candle.volume,
            }
        )
        file_hash = row["file_sha256"]
        if isinstance(file_hash, str) and file_hash:
            import_hashes.add(file_hash)
    dataset_sha = _sha256(_canonical_json(records))
    return candles, dataset_sha, tuple(sorted(import_hashes))


def run_strategy_experiment(
    database_path: Path,
    experiment_id: int,
    *,
    calendars: CalendarRegistry,
    as_of: datetime,
) -> StrategyRunResult:
    """Run signal generation only when Phase 2 reports complete, confirmed, fresh data."""

    if isinstance(experiment_id, bool) or not isinstance(experiment_id, int) or experiment_id < 1:
        raise StrategyLabError("experiment_id must be a positive integer.")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise StrategyLabError("as_of must be timezone-aware.")
    configuration_hash_origin: str
    with _write_transaction(database_path) as connection:
        experiment = connection.execute(
            """SELECT id, strategy_id, configuration_json, configuration_sha256,
                      configuration_hash_origin
               FROM experiments WHERE id = ?""",
            (experiment_id,),
        ).fetchone()
        if experiment is None or experiment["strategy_id"] is None:
            raise StrategyLabError(f"Strategy experiment {experiment_id} does not exist.")
        try:
            configuration = json.loads(experiment["configuration_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise StrategyLabError(
                f"Experiment {experiment_id} has malformed configuration JSON."
            ) from exc
        if not isinstance(configuration, dict):
            raise StrategyLabError(f"Experiment {experiment_id} configuration must be an object.")
        actual_configuration_sha256 = _canonical_hash(configuration)
        stored_configuration_sha256 = experiment["configuration_sha256"]
        if (
            stored_configuration_sha256 is not None
            and stored_configuration_sha256 != actual_configuration_sha256
        ):
            raise StrategyLabError(
                f"Experiment {experiment_id} configuration integrity check failed; "
                "the registered experiment may have been modified."
            )
        try:
            spec = _parse_spec(configuration["strategy_spec"])
            configuration, start_date, end_date, expected_spec_hash = (
                _validate_experiment_configuration(configuration, spec)
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, StrategyLabError):
                raise
            raise StrategyLabError(
                f"Experiment {experiment_id} has malformed or inconsistent configuration."
            ) from exc
        if stored_configuration_sha256 is None:
            _validate_legacy_strategy_reference(connection, int(experiment["strategy_id"]), spec)
            configuration_hash_origin = "FIRST_RUN_BACKFILL"
            connection.execute(
                """UPDATE experiments
                   SET configuration_sha256 = ?, configuration_hash_origin = ?
                   WHERE id = ? AND configuration_sha256 IS NULL""",
                (actual_configuration_sha256, configuration_hash_origin, experiment_id),
            )
        else:
            configuration_hash_origin = str(
                experiment["configuration_hash_origin"] or "UNKNOWN_ORIGIN"
            )
        run_cursor = connection.execute(
            "INSERT INTO runs (experiment_id, status) VALUES (?, 'RUNNING')", (experiment_id,)
        )
        if run_cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a run identifier.")
        run_id = int(run_cursor.lastrowid)

    try:
        report = analyze_dataset(
            database_path,
            spec.exchange,
            spec.trading_symbol,
            spec.interval,
            start_date,
            end_date,
            as_of=as_of,
            calendars=calendars,
        )
        gate_reason = _gate_reason(report)
        if gate_reason is not None:
            result = {
                "result_schema_version": 1,
                "status": "BLOCKED",
                "strategy_spec_sha256": expected_spec_hash,
                "experiment_configuration_sha256": actual_configuration_sha256,
                "configuration_hash_origin": configuration_hash_origin,
                "provenance_status": "PARTIAL_BLOCKED",
                "quality_status": report.overall_status,
                "calendar_status": report.calendar_status,
                "calendar_confidence": report.calendar_confidence,
                "completeness_percentage": (
                    str(report.completeness_percentage)
                    if report.completeness_percentage is not None
                    else None
                ),
                "staleness_status": report.staleness_status,
                "reason": gate_reason,
            }
            _write_run_result(
                database_path,
                run_id,
                status="BLOCKED",
                result_json=_canonical_json(result),
                error_message=gate_reason,
            )
            return StrategyRunResult(run_id, "BLOCKED", (), None, gate_reason)

        calendar = calendars.resolve(spec.exchange, report.segment)
        if calendar is None:
            raise DataQualityGateError("No exchange/segment calendar is configured.")
        _, completed_candles, _, uncovered = calculate_expected_candles(
            calendar, spec.interval, start_date, end_date, as_of=as_of
        )
        if uncovered:
            raise DataQualityGateError("Calendar coverage is incomplete for the experiment range.")
        completed_timestamps = {item.timestamp for item in completed_candles}
        candles, dataset_sha, source_hashes = _load_completed_candles(
            database_path,
            exchange=spec.exchange,
            trading_symbol=spec.trading_symbol,
            interval=spec.interval,
            start_date=start_date,
            end_date=end_date,
            timezone_name=calendar.timezone_name,
            completed_timestamps=completed_timestamps,
        )
        _validate_candles(candles)
        if len(candles) != report.expected_candles:
            raise DataQualityGateError(
                "Stored completed candles changed during analysis; rerun after verifying the dataset."
            )
        signals = generate_sma_crossover_signals(
            candles, fast_window=spec.fast_window, slow_window=spec.slow_window
        )
        implementation_sha = _sha256_file(Path(__file__))
        market_quality_sha = _sha256_file(_market_quality_source_path())
        resolved_calendar_sha = _effective_calendar_hash(calendar, calendars)
        source_revision = _source_revision()
        metadata = {
            "provenance_status": "COMPLETE",
            "experiment_configuration_sha256": actual_configuration_sha256,
            "configuration_hash_origin": configuration_hash_origin,
            "strategy_spec_sha256": expected_spec_hash,
            "strategy_implementation_sha256": implementation_sha,
            "market_quality_implementation_sha256": market_quality_sha,
            "market_data_ingestion_implementation_sha256": _sha256_file(_market_data_source_path()),
            "calendar_configuration_sha256": _calendar_registry_hash(calendars),
            "calendar_configuration_hash_method": (
                "SOURCE_FILE_SHA256"
                if calendars.configuration_sha256 is not None
                else "EFFECTIVE_REGISTRY_SHA256"
            ),
            "effective_calendar_configuration_sha256": resolved_calendar_sha,
            "source_revision": source_revision["commit"],
            "source_revision_status": source_revision["status"],
            "dataset_sha256": dataset_sha,
            "source_file_sha256s": list(source_hashes),
            "exchange": spec.exchange,
            "trading_symbol": spec.trading_symbol,
            "interval": spec.interval,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "as_of": as_of.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "candle_count": len(candles),
            "quality_status": report.overall_status,
            "calendar_confidence": report.calendar_confidence,
            "calendar_source": report.calendar_source,
            "calendar_coverage_start": (
                report.calendar_coverage_start.isoformat()
                if report.calendar_coverage_start is not None
                else None
            ),
            "calendar_coverage_end": (
                report.calendar_coverage_end.isoformat()
                if report.calendar_coverage_end is not None
                else None
            ),
            "completeness_percentage": str(report.completeness_percentage),
            "staleness_status": report.staleness_status,
            "fast_window": spec.fast_window,
            "slow_window": spec.slow_window,
            "signals_are_execution_orders": False,
        }
        result_document = {
            "result_schema_version": 1,
            "metadata": metadata,
            "signals": [
                {
                    "timestamp": signal.timestamp,
                    "action": signal.action,
                    "close": _decimal_text(signal.close),
                    "fast_sma": _decimal_text(signal.fast_sma),
                    "slow_sma": _decimal_text(signal.slow_sma),
                }
                for signal in signals
            ],
        }
        _write_run_result(
            database_path,
            run_id,
            status="COMPLETED",
            result_json=_canonical_json(result_document),
            error_message=None,
        )
        return StrategyRunResult(run_id, "COMPLETED", signals, dataset_sha, None)
    except DataQualityGateError as exc:
        _write_run_result(
            database_path,
            run_id,
            status="BLOCKED",
            result_json=_canonical_json(
                {
                    "result_schema_version": 1,
                    "status": "BLOCKED",
                    "provenance_status": "PARTIAL_BLOCKED",
                    "strategy_spec_sha256": expected_spec_hash,
                    "experiment_configuration_sha256": actual_configuration_sha256,
                    "reason": str(exc),
                }
            ),
            error_message=str(exc),
        )
        return StrategyRunResult(run_id, "BLOCKED", (), None, str(exc))
    except Exception as exc:
        _write_run_result(
            database_path,
            run_id,
            status="FAILED",
            result_json=_canonical_json(
                {
                    "result_schema_version": 1,
                    "status": "FAILED",
                    "provenance_status": "PARTIAL_FAILED",
                    "strategy_spec_sha256": expected_spec_hash,
                    "experiment_configuration_sha256": actual_configuration_sha256,
                    "error_type": type(exc).__name__,
                }
            ),
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise
