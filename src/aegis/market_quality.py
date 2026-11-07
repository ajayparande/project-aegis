"""Local exchange-session calendars and deterministic historical-data quality reports."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DEFAULT_TIMEZONE = "Asia/Kolkata"
_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
_SUPPORTED_INTERVALS = frozenset((*_INTERVAL_MINUTES, "1d"))
_TIME_TEXT = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class CalendarConfigError(ValueError):
    """Raised when local calendar configuration is malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """One same-day local exchange session, with an exclusive close boundary."""

    opens: time
    closes: time


@dataclass(frozen=True, slots=True)
class SpecialSession:
    """Date-specific replacement sessions; an empty tuple means a confirmed closure."""

    sessions: tuple[SessionWindow, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ExchangeCalendar:
    """Calendar rules for one exchange and one segment (or its explicit wildcard)."""

    exchange: str
    segment: str
    timezone_name: str
    confidence: Literal["CONFIGURED", "CONFIRMED"]
    coverage_start: date
    coverage_end: date
    source: str
    weekdays: frozenset[int]
    sessions: tuple[SessionWindow, ...]
    holidays: dict[date, str]
    special_sessions: dict[date, SpecialSession]

    def covers(self, value: date) -> bool:
        """Return whether this calendar claims coverage for a date."""

        return self.coverage_start <= value <= self.coverage_end

    def sessions_for(self, value: date) -> tuple[SessionWindow, ...] | None:
        """Return configured sessions, an empty closure, or None when uncovered."""

        if not self.covers(value):
            return None
        special = self.special_sessions.get(value)
        if special is not None:
            return special.sessions
        if value in self.holidays or value.weekday() not in self.weekdays:
            return ()
        return self.sessions


@dataclass(frozen=True, slots=True)
class StalenessPolicy:
    """Thresholds measured in missing completed expected candles, not wall-clock time."""

    potential_after_missing: int = 1
    stale_after_missing: int = 3

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (self.potential_after_missing, self.stale_after_missing)
        ):
            raise ValueError("staleness thresholds must be integers.")
        if self.potential_after_missing < 1:
            raise ValueError("potential_after_missing must be at least 1.")
        if self.stale_after_missing <= self.potential_after_missing:
            raise ValueError("stale_after_missing must exceed potential_after_missing.")


@dataclass(frozen=True, slots=True)
class CalendarRegistry:
    """Validated local calendar records and their staleness policy."""

    calendars: tuple[ExchangeCalendar, ...]
    staleness_policy: StalenessPolicy = StalenessPolicy()
    configuration_sha256: str | None = None

    def resolve(self, exchange: str, segment: str | None) -> ExchangeCalendar | None:
        """Resolve by exact exchange/segment, then that exchange's explicit wildcard."""

        normalized_exchange = exchange.strip().upper()
        normalized_segment = (segment or "*").strip().upper()
        exact = next(
            (
                item
                for item in self.calendars
                if item.exchange == normalized_exchange and item.segment == normalized_segment
            ),
            None,
        )
        if exact is not None:
            return exact
        return next(
            (
                item
                for item in self.calendars
                if item.exchange == normalized_exchange and item.segment == "*"
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class ExpectedCandle:
    """An expected candle start and the instant its interval is considered complete."""

    timestamp: str
    completes_at: datetime
    trading_date: date


@dataclass(frozen=True, slots=True)
class DatasetQualityReport:
    """Read-only completeness, integrity and freshness assessment for one dataset slice."""

    exchange: str
    trading_symbol: str
    segment: str | None
    interval: str
    requested_start: date
    requested_end: date
    as_of: datetime
    calendar_status: str
    calendar_confidence: str
    calendar_source: str | None
    calendar_coverage_start: date | None
    calendar_coverage_end: date | None
    expected_trading_days: int | None
    actual_trading_days: int
    expected_candles: int | None
    provisional_expected_candles: int | None
    actual_candles: int
    missing_timestamps: tuple[str, ...]
    provisional_missing_timestamps: tuple[str, ...]
    unexpected_timestamps: tuple[str, ...]
    provisional_unexpected_timestamps: tuple[str, ...]
    duplicate_identity_count: int
    duplicate_import_row_count: int
    invalid_candle_count: int
    rejected_import_row_count: int
    unlocated_rejected_import_row_count: int
    first_candle_timestamp: str | None
    last_candle_timestamp: str | None
    staleness_status: str
    missing_completed_candles_since_latest: int | None
    completeness_percentage: Decimal | None
    overall_status: str
    reasons: tuple[str, ...]


def _required_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CalendarConfigError(f"{label} must be a JSON object.")
    return value


def _required_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise CalendarConfigError(f"{label} must be a JSON array.")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalendarConfigError(f"{label} must be non-empty text.")
    return value.strip()


def _parse_date(value: object, label: str) -> date:
    raw = _text(value, label)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise CalendarConfigError(f"{label} must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != raw:
        raise CalendarConfigError(f"{label} must use YYYY-MM-DD format.")
    return parsed


def _parse_time(value: object, label: str) -> time:
    raw = _text(value, label)
    if _TIME_TEXT.fullmatch(raw) is None:
        raise CalendarConfigError(f"{label} must use 24-hour HH:MM format.")
    return time.fromisoformat(raw)


def _parse_sessions(value: object, label: str) -> tuple[SessionWindow, ...]:
    result: list[SessionWindow] = []
    for index, item in enumerate(_required_list(value, label)):
        data = _required_mapping(item, f"{label}[{index}]")
        opens = _parse_time(data.get("open"), f"{label}[{index}].open")
        closes = _parse_time(data.get("close"), f"{label}[{index}].close")
        if opens >= closes:
            raise CalendarConfigError(f"{label}[{index}] must close later on the same day.")
        current = SessionWindow(opens, closes)
        if result and current.opens < result[-1].closes:
            raise CalendarConfigError(f"{label} must be ordered and sessions must not overlap.")
        result.append(current)
    return tuple(result)


def _parse_calendar(value: object, index: int) -> ExchangeCalendar:
    label = f"calendars[{index}]"
    data = _required_mapping(value, label)
    exchange = _text(data.get("exchange"), f"{label}.exchange").upper()
    segment = _text(data.get("segment"), f"{label}.segment").upper()
    timezone_name = _text(data.get("timezone", _DEFAULT_TIMEZONE), f"{label}.timezone")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarConfigError(f"{label}.timezone is not a supported IANA timezone.") from exc
    confidence = _text(data.get("confidence"), f"{label}.confidence").upper()
    if confidence not in {"CONFIGURED", "CONFIRMED"}:
        raise CalendarConfigError(f"{label}.confidence must be CONFIGURED or CONFIRMED.")
    coverage_start = _parse_date(data.get("coverage_start"), f"{label}.coverage_start")
    coverage_end = _parse_date(data.get("coverage_end"), f"{label}.coverage_end")
    if coverage_start > coverage_end:
        raise CalendarConfigError(f"{label} coverage_start must not exceed coverage_end.")
    source = _text(data.get("source"), f"{label}.source")

    raw_weekdays = _required_list(data.get("weekdays", [0, 1, 2, 3, 4]), f"{label}.weekdays")
    weekdays: set[int] = set()
    for weekday in raw_weekdays:
        if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise CalendarConfigError(f"{label}.weekdays entries must be integers from 0 to 6.")
        weekdays.add(weekday)

    sessions = _parse_sessions(data.get("sessions", []), f"{label}.sessions")
    if not sessions:
        raise CalendarConfigError(f"{label}.sessions must contain at least one regular session.")

    holidays: dict[date, str] = {}
    for holiday_index, item in enumerate(
        _required_list(data.get("holidays", []), f"{label}.holidays")
    ):
        holiday_data = _required_mapping(item, f"{label}.holidays[{holiday_index}]")
        holiday = _parse_date(holiday_data.get("date"), f"{label}.holidays[{holiday_index}].date")
        if not coverage_start <= holiday <= coverage_end:
            raise CalendarConfigError(f"{label} holiday {holiday} is outside declared coverage.")
        holidays[holiday] = _text(
            holiday_data.get("reason"), f"{label}.holidays[{holiday_index}].reason"
        )

    special_sessions: dict[date, SpecialSession] = {}
    for special_index, item in enumerate(
        _required_list(data.get("special_sessions", []), f"{label}.special_sessions")
    ):
        special_data = _required_mapping(item, f"{label}.special_sessions[{special_index}]")
        special_date = _parse_date(
            special_data.get("date"), f"{label}.special_sessions[{special_index}].date"
        )
        if not coverage_start <= special_date <= coverage_end:
            raise CalendarConfigError(
                f"{label} special session {special_date} is outside declared coverage."
            )
        special_note = _text(
            special_data.get("reason"), f"{label}.special_sessions[{special_index}].reason"
        )
        closed = special_data.get("closed", False)
        if not isinstance(closed, bool):
            raise CalendarConfigError(
                f"{label}.special_sessions[{special_index}].closed must be a boolean."
            )
        if closed:
            special_windows: tuple[SessionWindow, ...] = ()
        else:
            special_windows = _parse_sessions(
                special_data.get("sessions"), f"{label}.special_sessions[{special_index}].sessions"
            )
            if not special_windows:
                raise CalendarConfigError(
                    f"{label} special session must have sessions or set closed to true."
                )
        if special_date in special_sessions:
            raise CalendarConfigError(f"{label} has duplicate special session date {special_date}.")
        special_sessions[special_date] = SpecialSession(special_windows, special_note)

    return ExchangeCalendar(
        exchange=exchange,
        segment=segment,
        timezone_name=timezone_name,
        confidence=confidence,  # type: ignore[arg-type]
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        source=source,
        weekdays=frozenset(weekdays),
        sessions=sessions,
        holidays=holidays,
        special_sessions=special_sessions,
    )


def load_calendar_registry(config_path: Path) -> CalendarRegistry:
    """Load a validated, local JSON calendar without fetching or inferring holidays."""

    if not config_path.exists():
        return CalendarRegistry(())
    try:
        config_bytes = config_path.read_bytes()
        document = json.loads(config_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalendarConfigError(f"Cannot read calendar configuration: {exc}") from exc
    root = _required_mapping(document, "calendar configuration")
    schema_version = root.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise CalendarConfigError("Calendar configuration schema_version must be 1.")
    calendars = tuple(
        _parse_calendar(item, index)
        for index, item in enumerate(_required_list(root.get("calendars", []), "calendars"))
    )
    identities = [(item.exchange, item.segment) for item in calendars]
    if len(identities) != len(set(identities)):
        raise CalendarConfigError("Calendar exchange/segment identities must be unique.")
    policy_data = _required_mapping(root.get("staleness_policy", {}), "staleness_policy")
    potential_after = policy_data.get("potential_after_missing", 1)
    stale_after = policy_data.get("stale_after_missing", 3)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (potential_after, stale_after)):
        raise CalendarConfigError("Staleness thresholds must be integers.")
    try:
        policy = StalenessPolicy(cast(int, potential_after), cast(int, stale_after))
    except ValueError as exc:
        raise CalendarConfigError(str(exc)) from exc
    return CalendarRegistry(
        calendars,
        policy,
        hashlib.sha256(config_bytes).hexdigest(),
    )


def _strict_local_datetime(day: date, local_time: time, timezone: ZoneInfo) -> datetime:
    """Attach a timezone only when the local wall time is unique and exists."""

    naive = datetime.combine(day, local_time)
    first = naive.replace(tzinfo=timezone, fold=0)
    second = naive.replace(tzinfo=timezone, fold=1)
    first_roundtrip = first.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
    second_roundtrip = second.astimezone(UTC).astimezone(timezone).replace(tzinfo=None)
    if first_roundtrip != naive or second_roundtrip != naive:
        raise CalendarConfigError(f"Local calendar time {naive} does not exist in {timezone.key}.")
    if first.utcoffset() != second.utcoffset():
        raise CalendarConfigError(f"Local calendar time {naive} is ambiguous in {timezone.key}.")
    return first


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")
    return value.astimezone(UTC)


def _interval_duration(interval: str) -> timedelta:
    if interval not in _SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval {interval!r}.")
    return timedelta(minutes=_INTERVAL_MINUTES[interval]) if interval != "1d" else timedelta(days=1)


def _day_schedule(
    calendar: ExchangeCalendar, day: date, interval: str
) -> tuple[tuple[ExpectedCandle, ...], bool] | None:
    """Return all scheduled slots and whether the date is a trading day."""

    windows = calendar.sessions_for(day)
    if windows is None:
        return None
    if not windows:
        return (), False
    timezone = ZoneInfo(calendar.timezone_name)
    for window in windows:
        _strict_local_datetime(day, window.opens, timezone)
        _strict_local_datetime(day, window.closes, timezone)
    if interval == "1d":
        start = _strict_local_datetime(day, time.min, timezone)
        close_times = [_strict_local_datetime(day, window.closes, timezone) for window in windows]
        completed_at = max(close_times)
        return (ExpectedCandle(_utc_text(start), completed_at, day),), True

    duration = _interval_duration(interval)
    anchor_naive = datetime.combine(day, time(9, 15))
    generated: list[ExpectedCandle] = []
    for window in windows:
        if window.opens < time(9, 15):
            raise CalendarConfigError(
                "Intraday sessions before 09:15 are unsupported by the Stage 3 timestamp convention."
            )
        open_naive = datetime.combine(day, window.opens)
        close_naive = datetime.combine(day, window.closes)
        opening_offset = open_naive - anchor_naive
        offset_minutes = max(0, opening_offset.days * 1440 + opening_offset.seconds // 60)
        interval_minutes = _INTERVAL_MINUTES[interval]
        steps = (offset_minutes + interval_minutes - 1) // interval_minutes
        start_naive = anchor_naive + timedelta(minutes=steps * interval_minutes)
        while start_naive < close_naive:
            start_time = start_naive.time()
            end_naive = min(start_naive + duration, close_naive)
            start = _strict_local_datetime(day, start_time, timezone)
            completes_at = _strict_local_datetime(day, end_naive.time(), timezone)
            generated.append(ExpectedCandle(_utc_text(start), completes_at, day))
            start_naive += duration
    return tuple(generated), True


def calculate_expected_candles(
    calendar: ExchangeCalendar,
    interval: str,
    start_date: date,
    end_date: date,
    *,
    as_of: datetime,
) -> tuple[tuple[ExpectedCandle, ...], tuple[ExpectedCandle, ...], int, int]:
    """Return scheduled and completed candles plus known/uncovered trading-day counts.

    Timestamps retain Stage 3's interval start convention, anchored at 09:15
    Asia/Kolkata for intraday bars. The close boundary is exclusive. A final
    partial interval ends at the session close and is complete only at that close.
    """

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date.")
    _interval_duration(interval)
    as_of_utc = _aware_utc(as_of, "as_of")
    all_scheduled: list[ExpectedCandle] = []
    completed: list[ExpectedCandle] = []
    expected_days = 0
    uncovered_days = 0
    for offset in range((end_date - start_date).days + 1):
        day = start_date + timedelta(days=offset)
        result = _day_schedule(calendar, day, interval)
        if result is None:
            uncovered_days += 1
            continue
        day_candles, is_trading_day = result
        if is_trading_day:
            expected_days += 1
        all_scheduled.extend(day_candles)
        completed.extend(
            candle for candle in day_candles if candle.completes_at.astimezone(UTC) <= as_of_utc
        )
    return tuple(all_scheduled), tuple(completed), expected_days, uncovered_days


def _parse_stored_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise ValueError("stored timestamp is not canonical UTC ISO text")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("stored timestamp is not UTC")
    return parsed.astimezone(UTC)


def _valid_candle_values(row: sqlite3.Row) -> bool:
    try:
        prices = [
            Decimal(row[name])
            for name in ("open_decimal", "high_decimal", "low_decimal", "close_decimal")
        ]
        volume = row["volume"]
        if any(not price.is_finite() or price <= 0 for price in prices):
            return False
        open_price, high, low, close = prices
        return (
            high >= max(open_price, low, close)
            and low <= min(open_price, high, close)
            and isinstance(volume, int)
            and volume >= 0
        )
    except (InvalidOperation, TypeError, ValueError):
        return False


def _calendar_coverage(
    calendar: ExchangeCalendar | None, start_date: date, end_date: date
) -> tuple[str, int]:
    if calendar is None:
        return "UNAVAILABLE", 0
    covered = sum(
        calendar.covers(start_date + timedelta(days=offset))
        for offset in range((end_date - start_date).days + 1)
    )
    total = (end_date - start_date).days + 1
    return ("FULL" if covered == total else "PARTIAL" if covered else "UNAVAILABLE"), covered


def _utc_prefilter_bounds(start_date: date, end_date: date) -> tuple[str | None, str | None]:
    """Return conservative UTC bounds for filtering canonical candle timestamps in SQL.

    A local date can map to UTC instants on the previous or following UTC date.
    One full day of padding on the lower bound and two days after the inclusive end
    therefore retain every possible local-date match. The exact calendar-timezone
    date check remains in Python, so these bounds only reduce the candidate rows.
    """

    lower_bound: str | None
    upper_bound: str | None
    try:
        lower = datetime.combine(start_date, time.min, UTC) - timedelta(days=1)
    except OverflowError:
        lower_bound = None
    else:
        lower_bound = _utc_text(lower)
    try:
        upper = datetime.combine(end_date, time.min, UTC) + timedelta(days=2)
    except OverflowError:
        upper_bound = None
    else:
        upper_bound = _utc_text(upper)
    return lower_bound, upper_bound


def _readonly_connection(database_path: Path) -> sqlite3.Connection:
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def analyze_dataset(
    database_path: Path,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    *,
    as_of: datetime,
    calendars: CalendarRegistry,
) -> DatasetQualityReport:
    """Analyze a date slice without writing to SQLite or modifying stored candles."""

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date.")
    _interval_duration(interval)
    as_of_utc = _aware_utc(as_of, "as_of")
    normalized_exchange = exchange.strip().upper()
    normalized_symbol = trading_symbol.strip().upper()
    lower_bound, upper_bound = _utc_prefilter_bounds(start_date, end_date)
    timestamp_conditions = []
    timestamp_parameters: list[str] = []
    if lower_bound is not None:
        timestamp_conditions.append("candle_timestamp >= ?")
        timestamp_parameters.append(lower_bound)
    if upper_bound is not None:
        timestamp_conditions.append("candle_timestamp < ?")
        timestamp_parameters.append(upper_bound)
    timestamp_filter = " AND " + " AND ".join(timestamp_conditions) if timestamp_conditions else ""
    with closing(_readonly_connection(database_path)) as connection:
        instrument = connection.execute(
            "SELECT id, segment FROM instruments "
            "WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE",
            (normalized_exchange, normalized_symbol),
        ).fetchone()
        if instrument is None:
            raise ValueError(
                f"Instrument {normalized_exchange}:{normalized_symbol} is not registered."
            )
        rows = connection.execute(
            f"""SELECT id, interval, candle_timestamp, open_decimal, high_decimal,
                      low_decimal, close_decimal, volume, import_id
               FROM historical_candles WHERE instrument_id = ? AND interval = ?
                 {timestamp_filter}
               ORDER BY candle_timestamp, id""",
            (instrument["id"], interval, *timestamp_parameters),
        ).fetchall()
        duplicate_groups = connection.execute(
            f"""SELECT candle_timestamp FROM historical_candles
               WHERE instrument_id = ? AND interval = ?
                 {timestamp_filter}
               GROUP BY instrument_id, interval, candle_timestamp HAVING COUNT(*) > 1""",
            (instrument["id"], interval, *timestamp_parameters),
        ).fetchall()
        # Keep detecting malformed timestamps anywhere in this instrument/interval.
        # They cannot be assigned to a requested date range, so this narrow covering
        # index scan preserves the existing report semantics without materializing
        # every historical candle and its OHLC values in memory.
        invalid_timestamp_count = 0
        for timestamp_row in connection.execute(
            "SELECT candle_timestamp FROM historical_candles "
            "WHERE instrument_id = ? AND interval = ?",
            (instrument["id"], interval),
        ):
            try:
                _parse_stored_timestamp(timestamp_row["candle_timestamp"])
            except ValueError:
                invalid_timestamp_count += 1
        issue_rows = connection.execute(
            """SELECT issue_type, affected_timestamp FROM data_quality_issues
               WHERE instrument_id = ? AND interval = ?
                 AND issue_type IN ('DUPLICATE_CANDLE', 'CSV_ROW_REJECTED')""",
            (instrument["id"], interval),
        ).fetchall()

    calendar = calendars.resolve(normalized_exchange, instrument["segment"])
    timezone_name = calendar.timezone_name if calendar is not None else _DEFAULT_TIMEZONE
    timezone = ZoneInfo(timezone_name)
    day_range_rows: list[sqlite3.Row] = []
    invalid_count = invalid_timestamp_count
    actual_days: set[date] = set()
    timestamps_by_id: list[tuple[int, str, date]] = []
    valid_times: set[str] = set()
    sorted_valid: list[tuple[datetime, str]] = []
    reasons: list[str] = []
    for row in rows:
        try:
            parsed = _parse_stored_timestamp(row["candle_timestamp"])
        except ValueError:
            continue
        local_day = parsed.astimezone(timezone).date()
        if not start_date <= local_day <= end_date:
            continue
        day_range_rows.append(row)
        actual_days.add(local_day)
        timestamps_by_id.append((int(row["id"]), row["candle_timestamp"], local_day))
        if _valid_candle_values(row):
            valid_times.add(row["candle_timestamp"])
            sorted_valid.append((parsed, row["candle_timestamp"]))
        else:
            invalid_count += 1

    coverage, _covered_days = _calendar_coverage(calendar, start_date, end_date)
    calendar_confidence = calendar.confidence if calendar is not None else "UNAVAILABLE"
    scheduled: tuple[ExpectedCandle, ...] = ()
    completed: tuple[ExpectedCandle, ...] = ()
    expected_days: int | None = None
    provisional_expected: int | None = None
    missing: tuple[str, ...] = ()
    provisional_missing: tuple[str, ...] = ()
    unexpected: list[str] = []
    provisional_unexpected: list[str] = []
    if calendar is None:
        reasons.append(
            "No exchange/segment calendar is configured; session-based checks are unavailable."
        )
    else:
        scheduled, completed, expected_days, uncovered_by_schedule = calculate_expected_candles(
            calendar, interval, start_date, end_date, as_of=as_of_utc
        )
        provisional_expected = len(completed)
        if uncovered_by_schedule:
            reasons.append(f"Calendar does not cover {uncovered_by_schedule} requested date(s).")
        scheduled_by_day: dict[date, set[str]] = {}
        for item in scheduled:
            scheduled_by_day.setdefault(item.trading_date, set()).add(item.timestamp)
        for _, timestamp, local_day in timestamps_by_id:
            if not calendar.covers(local_day):
                continue
            allowed = scheduled_by_day.get(local_day, set())
            if timestamp not in allowed:
                special = calendar.special_sessions.get(local_day)
                if special is not None and not special.sessions:
                    detail = f"configured exceptional closure: {special.reason}"
                elif local_day in calendar.holidays:
                    detail = f"configured holiday: {calendar.holidays[local_day]}"
                elif local_day.weekday() not in calendar.weekdays:
                    detail = "configured non-trading weekday"
                else:
                    detail = "timestamp is outside configured session candle starts"
                if calendar.confidence == "CONFIRMED":
                    unexpected.append(timestamp)
                else:
                    provisional_unexpected.append(timestamp)
                confidence_text = (
                    "confirmed" if calendar.confidence == "CONFIRMED" else "provisional"
                )
                reasons.append(f"{confidence_text} unexpected candle {timestamp}: {detail}.")
        completed_set = {item.timestamp for item in completed}
        found = valid_times & completed_set
        absent = tuple(sorted(completed_set - found))
        if calendar.confidence == "CONFIRMED":
            missing = absent
        else:
            provisional_missing = absent
            reasons.append(
                "Calendar rules are configured but not verified; counts are provisional."
            )
        if calendar.confidence == "CONFIRMED" and coverage != "FULL":
            reasons.append(
                "Calendar confidence is confirmed only for the covered portion of this range."
            )

    duplicate_identity_count = 0
    for duplicate in duplicate_groups:
        try:
            duplicate_time = _parse_stored_timestamp(duplicate["candle_timestamp"])
        except ValueError:
            continue
        duplicate_day = duplicate_time.astimezone(timezone).date()
        if start_date <= duplicate_day <= end_date:
            duplicate_identity_count += 1
    duplicate_import_count = 0
    rejected_import_count = 0
    unlocated_rejected_import_count = 0
    for issue in issue_rows:
        try:
            issue_time = _parse_stored_timestamp(issue["affected_timestamp"])
        except ValueError:
            if issue["issue_type"] == "CSV_ROW_REJECTED":
                unlocated_rejected_import_count += 1
            continue
        if start_date <= issue_time.astimezone(timezone).date() <= end_date:
            if issue["issue_type"] == "DUPLICATE_CANDLE":
                duplicate_import_count += 1
            elif issue["issue_type"] == "CSV_ROW_REJECTED":
                rejected_import_count += 1

    actual_count = len(day_range_rows)
    first_timestamp = min(sorted_valid)[1] if sorted_valid else None
    last_timestamp = max(sorted_valid)[1] if sorted_valid else None
    trusted_calendar = calendar is not None and calendar.confidence == "CONFIRMED"
    as_of_local_date = as_of_utc.astimezone(timezone).date()
    confirmed_full_coverage = trusted_calendar and coverage == "FULL"
    assessable_as_of_range = confirmed_full_coverage and end_date <= as_of_local_date
    staleness_calendar_ready = confirmed_full_coverage and start_date <= as_of_local_date
    staleness_status = "INSUFFICIENT_DATA"
    missing_since_latest: int | None = None
    if end_date < as_of_local_date or start_date > as_of_local_date:
        reasons.append("Requested date range does not include the supplied as_of date.")
    elif not staleness_calendar_ready or not valid_times:
        staleness_status = "INSUFFICIENT_DATA"
    else:
        completed_set = {item.timestamp for item in completed}
        present_expected = valid_times & completed_set
        completed_order = [item.timestamp for item in completed]
        latest_present_index = max(
            (
                index
                for index, timestamp in enumerate(completed_order)
                if timestamp in present_expected
            ),
            default=-1,
        )
        missing_since_latest = len(completed_order) - latest_present_index - 1
        if missing_since_latest < calendars.staleness_policy.potential_after_missing:
            staleness_status = "FRESH"
        elif missing_since_latest >= calendars.staleness_policy.stale_after_missing:
            staleness_status = "STALE"
        else:
            staleness_status = "POTENTIALLY_STALE"

    completeness: Decimal | None = None
    if assessable_as_of_range and provisional_expected:
        completed_set = {item.timestamp for item in completed}
        present = len(valid_times & completed_set)
        completeness = (Decimal(present) * Decimal(100) / Decimal(provisional_expected)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    if invalid_timestamp_count:
        reasons.append(
            f"{invalid_timestamp_count} stored candle(s) have invalid timestamps and cannot be assigned to the requested dates."
        )
    if staleness_status == "INSUFFICIENT_DATA" and not valid_times:
        reasons.append(
            "No valid stored candles in the requested range; freshness cannot be determined."
        )
    if invalid_count:
        reasons.append(f"{invalid_count} invalid stored candle(s) were detected.")
    if rejected_import_count:
        reasons.append(
            f"{rejected_import_count} rejected import row(s) are recorded in quality history."
        )
    if unlocated_rejected_import_count:
        reasons.append(
            f"{unlocated_rejected_import_count} rejected import row(s) have no usable timestamp and cannot be assigned to requested dates."
        )
    if unexpected or provisional_unexpected:
        count = len(unexpected) + len(provisional_unexpected)
        reasons.append(f"{count} candle(s) fall outside configured session boundaries.")
    if missing:
        reasons.append(f"{len(missing)} completed expected candle(s) are missing.")
    if provisional_missing:
        reasons.append(f"{len(provisional_missing)} provisional expected candle(s) are absent.")

    if invalid_count or invalid_timestamp_count or unexpected or duplicate_identity_count:
        overall = "ERROR"
    elif not assessable_as_of_range or not provisional_expected:
        overall = "INSUFFICIENT_INFORMATION"
    elif missing:
        overall = "INCOMPLETE"
    elif staleness_status == "STALE":
        overall = "STALE"
    else:
        overall = "COMPLETE"
    if end_date > as_of_local_date:
        reasons.append(
            "Requested date range extends beyond as_of; completeness covers completed candles only up to as_of."
        )

    return DatasetQualityReport(
        exchange=normalized_exchange,
        trading_symbol=normalized_symbol,
        segment=instrument["segment"],
        interval=interval,
        requested_start=start_date,
        requested_end=end_date,
        as_of=as_of_utc,
        calendar_status=coverage,
        calendar_confidence=calendar_confidence,
        calendar_source=calendar.source if calendar is not None else None,
        calendar_coverage_start=calendar.coverage_start if calendar is not None else None,
        calendar_coverage_end=calendar.coverage_end if calendar is not None else None,
        expected_trading_days=expected_days,
        actual_trading_days=len(actual_days),
        expected_candles=len(completed) if assessable_as_of_range else None,
        provisional_expected_candles=provisional_expected,
        actual_candles=actual_count,
        missing_timestamps=missing,
        provisional_missing_timestamps=provisional_missing,
        unexpected_timestamps=tuple(sorted(set(unexpected))),
        provisional_unexpected_timestamps=tuple(sorted(set(provisional_unexpected))),
        duplicate_identity_count=duplicate_identity_count,
        duplicate_import_row_count=duplicate_import_count,
        invalid_candle_count=invalid_count,
        rejected_import_row_count=rejected_import_count,
        unlocated_rejected_import_row_count=unlocated_rejected_import_count,
        first_candle_timestamp=first_timestamp,
        last_candle_timestamp=last_timestamp,
        staleness_status=staleness_status,
        missing_completed_candles_since_latest=missing_since_latest,
        completeness_percentage=completeness,
        overall_status=overall,
        reasons=tuple(dict.fromkeys(reasons)),
    )
