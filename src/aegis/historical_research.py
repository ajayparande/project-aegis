"""Read-only, bounded access to locally stored historical research data."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aegis.database import get_readonly_connection
from aegis.market_data import (
    SUPPORTED_INTERVALS,
    list_historical_candles_in_range,
    source_use_is_eligible,
)
from aegis.market_quality import (
    CalendarConfigError,
    CalendarRegistry,
    DatasetQualityReport,
    analyze_dataset,
    load_approved_calendar_registry,
)

MAX_RESEARCH_DATE_SPAN_DAYS = 3660
MAX_INTRADAY_QUALITY_SPAN_DAYS = 92
MAX_RESEARCH_CANDLE_LIMIT = 100
MAX_RESEARCH_FINGERPRINT_ROWS = 100_000
MAX_INSTRUMENT_RESULTS = 100
MAX_DISCOVERY_SCAN = 500
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_CURSOR_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,2048}\Z")
_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_QUALITY_STATUSES = frozenset({"ANY", "PASS", "FAIL", "UNKNOWN"})
_UNVERIFIED_EXCLUSION = "Every contributing import must be declared VERIFIED_PERMITTED."


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text.")
    if not value.strip():
        raise ValueError(f"{label} must be non-empty text.")
    return value.strip()


def _instrument_identity(exchange: Any, trading_symbol: Any) -> tuple[str, str]:
    return (
        _required_text(exchange, "exchange").upper(),
        _required_text(trading_symbol, "trading_symbol").upper(),
    )


def _validate_interval(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("interval must be text.")
    if value not in SUPPORTED_INTERVALS:
        raise ValueError(f"interval must be one of {SUPPORTED_INTERVALS}.")
    return value


def _validate_limit(value: Any, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer.")
    if not 1 <= value <= maximum:
        raise ValueError(f"{label} must be between 1 and {maximum}.")
    return value


def _parse_aware_timestamp(value: Any, label: str) -> datetime:
    raw = _required_text(value, label)
    if "T" not in raw:
        raise ValueError(f"{label} must be an ISO-8601 date-time with a timezone.")
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid ISO-8601 date-time.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    precision = "microseconds" if value.microsecond else "seconds"
    return value.astimezone(UTC).isoformat(timespec=precision).replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _encode_cursor(kind: str, query: dict[str, Any], position: dict[str, Any]) -> str:
    query_hash = hashlib.sha256(_canonical_json(query).encode("utf-8")).hexdigest()
    payload = {
        "version": 1,
        "kind": kind,
        "query_hash": query_hash,
        "position": position,
    }
    payload_text = _canonical_json(payload)
    envelope = {
        "payload": payload,
        "sha256": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
    }
    encoded = base64.urlsafe_b64encode(_canonical_json(envelope).encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


def _decode_cursor(cursor: Any, *, kind: str, query: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cursor, str):
        raise TypeError("cursor must be text.")
    if _CURSOR_PATTERN.fullmatch(cursor) is None:
        raise ValueError("cursor is malformed.")
    try:
        decoded = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        envelope = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is malformed.") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
        raise ValueError("cursor is malformed.")
    if _canonical_json(envelope) != decoded:
        raise ValueError("cursor is malformed.")
    payload = envelope["payload"]
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "kind",
        "query_hash",
        "position",
    }:
        raise ValueError("cursor is malformed.")
    payload_text = _canonical_json(payload)
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    if not isinstance(envelope["sha256"], str) or not hmac.compare_digest(
        digest, envelope["sha256"]
    ):
        raise ValueError("cursor is malformed.")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("cursor version is unsupported.")
    query_hash = hashlib.sha256(_canonical_json(query).encode("utf-8")).hexdigest()
    if payload["kind"] != kind or payload["query_hash"] != query_hash:
        raise ValueError("cursor does not match the requested filters.")
    if not isinstance(payload["position"], dict):
        raise TypeError("cursor position must be an object.")
    return payload["position"]


def _validate_timestamp_range(
    start_timestamp: Any,
    end_timestamp: Any,
) -> tuple[datetime, datetime]:
    start = _parse_aware_timestamp(start_timestamp, "start_timestamp")
    end = _parse_aware_timestamp(end_timestamp, "end_timestamp")
    if start > end:
        raise ValueError("start_timestamp must not be later than end_timestamp.")
    if (end - start).days > MAX_RESEARCH_DATE_SPAN_DAYS:
        raise ValueError(
            f"Requested timestamp range cannot exceed {MAX_RESEARCH_DATE_SPAN_DAYS} days."
        )
    if end > datetime.now(UTC):
        raise ValueError("end_timestamp must not be later than the current UTC time.")
    return start, end


def _validate_optional_timestamp_range(
    start_timestamp: Any | None,
    end_timestamp: Any | None,
) -> tuple[datetime | None, datetime | None]:
    if start_timestamp is None and end_timestamp is None:
        return None, None
    if start_timestamp is None or end_timestamp is None:
        raise ValueError("start_timestamp and end_timestamp must be supplied together.")
    return _validate_timestamp_range(start_timestamp, end_timestamp)


def _sql_bounds(start: datetime | None, end: datetime | None) -> tuple[str, tuple[str, ...]]:
    conditions: list[str] = []
    values: list[str] = []
    if start is not None:
        # Stored candles have whole-second UTC precision; ceil an inclusive lower bound.
        if start.microsecond:
            start = start.replace(microsecond=0) + timedelta(seconds=1)
        else:
            start = start.replace(microsecond=0)
        conditions.append("c.candle_timestamp >= ?")
        values.append(_format_timestamp(start))
    if end is not None:
        # Floor an inclusive upper bound to avoid returning a later stored second.
        end = end.replace(microsecond=0)
        conditions.append("c.candle_timestamp <= ?")
        values.append(_format_timestamp(end))
    return (" AND " + " AND ".join(conditions) if conditions else ""), tuple(values)


def _get_instrument(
    connection: sqlite3.Connection, exchange: str, trading_symbol: str
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT id, exchange, trading_symbol, instrument_name, instrument_type,
                  segment, is_active
           FROM instruments
           WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE""",
        (exchange, trading_symbol),
    ).fetchone()


def _approved_research_calendars() -> CalendarRegistry:
    """Reload administrator-selected evidence; caller registries confer no trust."""

    try:
        return load_approved_calendar_registry()
    except (CalendarConfigError, OSError) as exc:
        raise ValueError("Approved calendar evidence is unavailable or invalid.") from exc


def _resolve_calendar(
    calendars: CalendarRegistry | None, exchange: str, segment: str | None
) -> tuple[CalendarRegistry | None, ZoneInfo]:
    calendar = calendars.resolve(exchange, segment) if calendars is not None else None
    if calendar is None:
        raise ValueError("The requested calendar is absent from the approved configuration.")
    timezone = ZoneInfo(calendar.timezone_name)
    return calendars, timezone


def _local_dates(
    start: datetime,
    end: datetime,
    timezone: ZoneInfo,
) -> tuple[date, date]:
    return start.astimezone(timezone).date(), end.astimezone(timezone).date()


def _validate_quality_dates(start_date: Any, end_date: Any, interval: str) -> tuple[date, date]:
    start_text = _required_text(start_date, "start_date")
    end_text = _required_text(end_date, "end_date")
    if _DATE_PATTERN.fullmatch(start_text) is None or _DATE_PATTERN.fullmatch(end_text) is None:
        raise ValueError("start_date and end_date must use YYYY-MM-DD format.")
    try:
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
    except ValueError as exc:
        raise ValueError("start_date and end_date must be valid calendar dates.") from exc
    if start > end:
        raise ValueError("start_date must not be later than end_date.")
    span_limit = MAX_RESEARCH_DATE_SPAN_DAYS if interval == "1d" else MAX_INTRADAY_QUALITY_SPAN_DAYS
    if (end - start).days > span_limit:
        raise ValueError(f"Quality analysis cannot exceed a {span_limit}-day span for {interval}.")
    return start, end


def _source_use_statuses(
    connection: sqlite3.Connection,
    instrument_id: int,
    interval: str,
    start: datetime | None,
    end: datetime | None,
    snapshot_max_id: int | None = None,
) -> tuple[str, ...]:
    timestamp_filter, timestamp_values = _sql_bounds(start, end)
    snapshot_filter = " AND c.id <= ?" if snapshot_max_id is not None else ""
    snapshot_values = (snapshot_max_id,) if snapshot_max_id is not None else ()
    rows = connection.execute(
        f"""SELECT DISTINCT d.source_use_status
            FROM historical_candles AS c
            JOIN data_imports AS d ON d.id = c.import_id
            WHERE c.instrument_id = ? AND c.interval = ? {timestamp_filter}{snapshot_filter}
            ORDER BY d.source_use_status""",
        (instrument_id, interval, *timestamp_values, *snapshot_values),
    ).fetchall()
    return tuple(str(row["source_use_status"]) for row in rows)


def _dataset_fingerprint(
    connection: sqlite3.Connection,
    instrument_id: int,
    interval: str,
    start: datetime,
    end: datetime,
    snapshot_max_id: int,
) -> str | None:
    """Hash the bounded candle/provenance slice; return None if it exceeds the bound."""

    timestamp_filter, timestamp_values = _sql_bounds(start, end)
    cursor = connection.execute(
        f"""SELECT c.id, c.instrument_id, i.exchange, i.trading_symbol, c.interval,
                   c.candle_timestamp, c.open_decimal, c.high_decimal, c.low_decimal,
                   c.close_decimal, c.volume, c.import_id, d.status AS import_status,
                   d.source_id, d.original_filename, d.file_sha256, d.source_use_status,
                   s.name AS source_name
            FROM historical_candles AS c
            JOIN instruments AS i ON i.id = c.instrument_id
            JOIN data_imports AS d ON d.id = c.import_id
            LEFT JOIN sources AS s ON s.id = d.source_id
            WHERE c.instrument_id = ? AND c.interval = ? {timestamp_filter}
              AND c.id <= ?
            ORDER BY c.id
            LIMIT ?""",
        (
            instrument_id,
            interval,
            *timestamp_values,
            snapshot_max_id,
            MAX_RESEARCH_FINGERPRINT_ROWS + 1,
        ),
    )
    digest = hashlib.sha256()
    row_count = 0
    try:
        while rows := cursor.fetchmany(1000):
            for row in rows:
                row_count += 1
                if row_count > MAX_RESEARCH_FINGERPRINT_ROWS:
                    return None
                record = [
                    row["id"],
                    row["instrument_id"],
                    row["exchange"],
                    row["trading_symbol"],
                    row["interval"],
                    row["candle_timestamp"],
                    row["open_decimal"],
                    row["high_decimal"],
                    row["low_decimal"],
                    row["close_decimal"],
                    row["volume"],
                    row["import_id"],
                    row["import_status"],
                    row["source_id"],
                    row["original_filename"],
                    row["file_sha256"],
                    row["source_use_status"],
                    row["source_name"],
                ]
                digest.update(_canonical_json(record).encode("utf-8"))
                digest.update(b"\n")
    finally:
        cursor.close()
    return digest.hexdigest()


def _bind_quality_fingerprint(
    dataset_fingerprint: str,
    quality: dict[str, Any],
) -> str:
    return hashlib.sha256(
        _canonical_json({"dataset_fingerprint": dataset_fingerprint, "quality": quality}).encode(
            "utf-8"
        )
    ).hexdigest()


def _source_use_label(statuses: Sequence[str]) -> str:
    if not statuses:
        return "UNKNOWN"
    if len(statuses) == 1:
        return statuses[0]
    return "MIXED"


def _eligibility_status(source_statuses: Sequence[str], quality_status: str) -> str:
    if "PROHIBITED" in source_statuses or quality_status == "FAIL":
        return "FAIL"
    if not source_use_is_eligible(source_statuses) or quality_status != "PASS":
        return "UNKNOWN"
    return "PASS"


def _source_eligibility_status(source_statuses: Sequence[str]) -> str:
    if "PROHIBITED" in source_statuses:
        return "FAIL"
    if source_use_is_eligible(source_statuses):
        return "PASS"
    return "UNKNOWN"


def _quality_label(report: DatasetQualityReport) -> str:
    if (
        report.overall_status == "COMPLETE"
        and report.calendar_status == "FULL"
        and report.calendar_confidence == "CONFIRMED"
        and report.expected_candles is not None
    ):
        return "PASS"
    if report.overall_status in {"ERROR", "INCOMPLETE", "STALE"}:
        return "FAIL"
    return "UNKNOWN"


def _quality_summary(report: DatasetQualityReport) -> dict[str, Any]:
    missing = (*report.missing_timestamps, *report.provisional_missing_timestamps)
    unexpected = (*report.unexpected_timestamps, *report.provisional_unexpected_timestamps)
    return {
        "status": _quality_label(report),
        "overall_status": report.overall_status,
        "calendar_status": report.calendar_status,
        "calendar_confidence": report.calendar_confidence,
        "expected_candles": report.expected_candles,
        "actual_candles": report.actual_candles,
        "missing_candle_count": len(missing),
        "missing_candle_examples": list(missing[:20]),
        "unexpected_candle_count": len(unexpected),
        "duplicate_identity_count": report.duplicate_identity_count,
        "duplicate_import_row_count": report.duplicate_import_row_count,
        "invalid_candle_count": report.invalid_candle_count,
        "rejected_import_row_count": report.rejected_import_row_count,
        "staleness_status": report.staleness_status,
        "completeness_percentage": (
            str(report.completeness_percentage)
            if report.completeness_percentage is not None
            else None
        ),
        "warnings": list(report.reasons[:50]),
    }


def _analyze_quality(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    as_of: datetime,
    calendars: CalendarRegistry | None,
    connection: sqlite3.Connection | None = None,
    snapshot_max_id: int | None = None,
) -> dict[str, Any]:
    report = analyze_dataset(
        database_path=database_path,
        exchange=exchange,
        trading_symbol=trading_symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        as_of=as_of,
        calendars=calendars,
        connection=connection,
        snapshot_max_id=snapshot_max_id,
    )
    return _quality_summary(report)


def _quality_for_timestamps(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    segment: str | None,
    interval: str,
    start: datetime,
    end: datetime,
    calendars: CalendarRegistry | None,
    connection: sqlite3.Connection | None = None,
    snapshot_max_id: int | None = None,
) -> dict[str, Any]:
    _, timezone = _resolve_calendar(calendars, exchange, segment)
    start_date, end_date = _local_dates(start, end, timezone)
    span_limit = MAX_RESEARCH_DATE_SPAN_DAYS if interval == "1d" else MAX_INTRADAY_QUALITY_SPAN_DAYS
    if (end_date - start_date).days > span_limit:
        return {
            "status": "UNKNOWN",
            "overall_status": "NOT_EVALUATED",
            "calendar_status": "UNAVAILABLE",
            "calendar_confidence": "UNAVAILABLE",
            "warnings": [
                f"Quality evaluation is bounded to {span_limit} days for {interval} data."
            ],
        }
    return _analyze_quality(
        database_path,
        exchange=exchange,
        trading_symbol=trading_symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        as_of=end,
        calendars=calendars,
        connection=connection,
        snapshot_max_id=snapshot_max_id,
    )


def _quality_for_full_dates(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    segment: str | None,
    interval: str,
    start_date: date,
    end_date: date,
    timezone: ZoneInfo,
    calendars: CalendarRegistry | None,
    connection: sqlite3.Connection | None = None,
    snapshot_max_id: int | None = None,
) -> dict[str, Any]:
    as_of = datetime.combine(end_date, time.max, tzinfo=timezone).astimezone(UTC)
    return _analyze_quality(
        database_path,
        exchange=exchange,
        trading_symbol=trading_symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        as_of=as_of,
        calendars=calendars,
        connection=connection,
        snapshot_max_id=snapshot_max_id,
    )


def _range_summary(
    connection: sqlite3.Connection,
    instrument_id: int,
    interval: str,
    start: datetime | None,
    end: datetime | None,
    snapshot_max_id: int | None = None,
) -> sqlite3.Row:
    timestamp_filter, timestamp_values = _sql_bounds(start, end)
    snapshot_filter = " AND c.id <= ?" if snapshot_max_id is not None else ""
    snapshot_values = (snapshot_max_id,) if snapshot_max_id is not None else ()
    return connection.execute(
        f"""SELECT COUNT(*) AS record_count,
                   COUNT(DISTINCT c.candle_timestamp) AS distinct_timestamp_count,
                   MIN(c.candle_timestamp) AS first_timestamp,
                   MAX(c.candle_timestamp) AS last_timestamp
            FROM historical_candles AS c
            WHERE c.instrument_id = ? AND c.interval = ? {timestamp_filter}{snapshot_filter}""",
        (instrument_id, interval, *timestamp_values, *snapshot_values),
    ).fetchone()


def _source_summaries(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    sources: dict[int, dict[str, object]] = {}
    for row in rows:
        import_id = row["import_id"]
        if isinstance(import_id, int):
            sources[import_id] = {
                "import_id": import_id,
                "source_id": row.get("source_id"),
                "source_name": row.get("source_name"),
                "file_sha256": row.get("file_sha256"),
                "import_status": row.get("import_status"),
                "source_use_status": row.get("source_use_status"),
            }
    return [sources[key] for key in sorted(sources)]


def _evidence_reference(
    *,
    exchange: str,
    trading_symbol: str,
    interval: str,
    requested_start: str,
    requested_end: str,
    candles: Sequence[dict[str, object]],
    sources: Sequence[dict[str, object]],
    returned_first_timestamp: str | None,
    returned_last_timestamp: str | None,
    returned_count: int,
    available_record_count: int,
    page_limit: int,
    page_cursor: str | None,
    snapshot_max_id: int,
    has_more: bool,
    quality_status: str,
    quality_warnings: Sequence[str],
) -> str:
    identity = {
        "exchange": exchange,
        "trading_symbol": trading_symbol,
        "interval": interval,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "returned_first_timestamp": returned_first_timestamp,
        "returned_last_timestamp": returned_last_timestamp,
        "returned_count": returned_count,
        "available_record_count": available_record_count,
        "page_limit": page_limit,
        "page_cursor": page_cursor,
        "snapshot_max_id": snapshot_max_id,
        "has_more": has_more,
        "quality_status": quality_status,
        "quality_warnings": list(quality_warnings),
        "candles": [
            [
                row["candle_timestamp"],
                row["open_decimal"],
                row["high_decimal"],
                row["low_decimal"],
                row["close_decimal"],
                row["volume"],
            ]
            for row in candles
        ],
        "sources": [
            {
                "import_id": source["import_id"],
                "source_id": source["source_id"],
                "file_sha256": source["file_sha256"],
                "source_name": source["source_name"],
                "import_status": source["import_status"],
                "source_use_status": source["source_use_status"],
            }
            for source in sources
        ],
    }
    canonical = _canonical_json(identity)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"aegis:historical-candles:v1:{digest}"


def discover_historical_instruments(
    database_path: Path,
    *,
    interval: str,
    exchange: str | None = None,
    trading_symbol: str | None = None,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    minimum_candles: int = 1,
    quality_status: str = "ANY",
    limit: int = 50,
    cursor: str | None = None,
    calendars: CalendarRegistry | None = None,
) -> dict[str, Any]:
    """Discover bounded history using approved calendars, ignoring caller calendar evidence."""

    interval = _validate_interval(interval)
    result_limit = _validate_limit(limit, "limit", MAX_INSTRUMENT_RESULTS)
    if isinstance(minimum_candles, bool) or not isinstance(minimum_candles, int):
        raise TypeError("minimum_candles must be an integer.")
    if minimum_candles < 1:
        raise ValueError("minimum_candles must be positive.")
    if not isinstance(quality_status, str) or quality_status not in _QUALITY_STATUSES:
        raise ValueError(f"quality_status must be one of {sorted(_QUALITY_STATUSES)}.")
    normalized_exchange = None if exchange is None else _required_text(exchange, "exchange").upper()
    normalized_symbol = (
        None if trading_symbol is None else _required_text(trading_symbol, "trading_symbol").upper()
    )
    start, end = _validate_optional_timestamp_range(start_timestamp, end_timestamp)
    query = {
        "interval": interval,
        "exchange": normalized_exchange,
        "trading_symbol": normalized_symbol,
        "start_timestamp": _format_timestamp(start) if start else None,
        "end_timestamp": _format_timestamp(end) if end else None,
        "minimum_candles": minimum_candles,
        "quality_status": quality_status,
        "limit": result_limit,
    }
    position = _decode_cursor(cursor, kind="discovery", query=query) if cursor is not None else None
    calendars = _approved_research_calendars()
    if position is not None:
        if set(position) != {"exchange", "trading_symbol", "instrument_id", "snapshot_max_id"}:
            raise ValueError("cursor position is malformed.")
        cursor_exchange = _required_text(position["exchange"], "cursor exchange")
        cursor_symbol = _required_text(position["trading_symbol"], "cursor trading_symbol")
        instrument_id = position["instrument_id"]
        snapshot_max_id = position["snapshot_max_id"]
        if (
            isinstance(instrument_id, bool)
            or not isinstance(instrument_id, int)
            or instrument_id < 1
        ):
            raise ValueError("cursor instrument_id is invalid.")
        if (
            isinstance(snapshot_max_id, bool)
            or not isinstance(snapshot_max_id, int)
            or snapshot_max_id < 0
        ):
            raise ValueError("cursor snapshot is invalid.")
    else:
        cursor_exchange = ""
        cursor_symbol = ""
        instrument_id = 0
        snapshot_max_id = 0

    where: list[str] = ["c.interval = ?"]
    parameters: list[object] = [interval]
    if normalized_exchange is not None:
        where.append("i.exchange = ? COLLATE NOCASE")
        parameters.append(normalized_exchange)
    if normalized_symbol is not None:
        where.append("i.trading_symbol = ? COLLATE NOCASE")
        parameters.append(normalized_symbol)
    timestamp_filter, timestamp_values = _sql_bounds(start, end)
    if timestamp_filter:
        where.append(timestamp_filter.removeprefix(" AND "))
        parameters.extend(timestamp_values)

    with closing(get_readonly_connection(database_path)) as connection:
        if position is None:
            snapshot_max_id = int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM historical_candles"
                ).fetchone()[0]
            )
        else:
            valid_cursor = connection.execute(
                f"""SELECT i.id
                    FROM instruments AS i
                    JOIN historical_candles AS c ON c.instrument_id = i.id
                    WHERE {" AND ".join(where)}
                      AND c.id <= ?
                      AND i.id = ?
                      AND i.exchange = ? COLLATE NOCASE
                      AND i.trading_symbol = ? COLLATE NOCASE
                    GROUP BY i.id, c.interval
                    HAVING COUNT(*) >= ?
                    LIMIT 1""",
                (
                    *parameters,
                    snapshot_max_id,
                    instrument_id,
                    cursor_exchange,
                    cursor_symbol,
                    minimum_candles,
                ),
            ).fetchone()
            if valid_cursor is None:
                raise ValueError("cursor position is outside the requested discovery set.")

        bounded_where = [*where, "c.id <= ?"]
        bounded_parameters = [*parameters, snapshot_max_id]
        if position is not None:
            bounded_where.append(
                """(
                    i.exchange COLLATE NOCASE > ? COLLATE NOCASE
                    OR (
                        i.exchange = ? COLLATE NOCASE
                        AND i.trading_symbol COLLATE NOCASE > ? COLLATE NOCASE
                    )
                    OR (
                        i.exchange = ? COLLATE NOCASE
                        AND i.trading_symbol = ? COLLATE NOCASE
                        AND i.id > ?
                    )
                )"""
            )
            bounded_parameters.extend(
                (
                    cursor_exchange,
                    cursor_exchange,
                    cursor_symbol,
                    cursor_exchange,
                    cursor_symbol,
                    instrument_id,
                )
            )
        rows = connection.execute(
            f"""SELECT i.id, i.exchange, i.trading_symbol, i.instrument_name,
                       i.instrument_type, i.segment, i.is_active, c.interval,
                       COUNT(*) AS candle_count,
                       MIN(c.candle_timestamp) AS first_timestamp,
                       MAX(c.candle_timestamp) AS last_timestamp,
                       GROUP_CONCAT(DISTINCT d.source_use_status) AS source_statuses
                FROM historical_candles AS c
                JOIN instruments AS i ON i.id = c.instrument_id
                JOIN data_imports AS d ON d.id = c.import_id
                WHERE {" AND ".join(bounded_where)}
                GROUP BY i.id, c.interval
                HAVING COUNT(*) >= ?
                ORDER BY i.exchange COLLATE NOCASE, i.trading_symbol COLLATE NOCASE,
                         i.id
                LIMIT ?""",
            (*bounded_parameters, minimum_candles, MAX_DISCOVERY_SCAN + 1),
        ).fetchall()
        no_history = connection.execute(
            """SELECT COUNT(*) FROM instruments AS i
               WHERE NOT EXISTS (
                   SELECT 1 FROM historical_candles AS c
                   WHERE c.instrument_id = i.id
               )"""
        ).fetchone()[0]

    candidates = rows[:MAX_DISCOVERY_SCAN]
    instruments: list[dict[str, Any]] = []
    scanned_count = 0
    last_scanned: sqlite3.Row | None = None
    for row in candidates:
        scanned_count += 1
        last_scanned = row
        first = _parse_aware_timestamp(row["first_timestamp"], "first_timestamp")
        last = _parse_aware_timestamp(row["last_timestamp"], "last_timestamp")
        if start is not None and end is not None:
            quality_start, quality_end = start, end
        else:
            quality_start, quality_end = first, last
        quality = _quality_for_timestamps(
            database_path,
            exchange=row["exchange"],
            trading_symbol=row["trading_symbol"],
            segment=row["segment"],
            interval=interval,
            start=quality_start,
            end=quality_end,
            calendars=calendars,
        )
        source_statuses = tuple(sorted(filter(None, str(row["source_statuses"] or "").split(","))))
        eligibility = _eligibility_status(source_statuses, quality["status"])
        if quality_status != "ANY" and quality["status"] != quality_status:
            continue
        exclusion_reasons: list[str] = []
        if not source_use_is_eligible(source_statuses):
            exclusion_reasons.append(_UNVERIFIED_EXCLUSION)
        if quality["status"] != "PASS":
            exclusion_reasons.extend(quality.get("warnings", []))
            if not exclusion_reasons:
                exclusion_reasons.append("Data-quality eligibility is not confirmed.")
        instruments.append(
            {
                "instrument_id": row["id"],
                "exchange": row["exchange"],
                "trading_symbol": row["trading_symbol"],
                "instrument_name": row["instrument_name"],
                "instrument_type": row["instrument_type"],
                "segment": row["segment"],
                "is_active": bool(row["is_active"]),
                "interval": interval,
                "candle_count": row["candle_count"],
                "first_available_candle": row["first_timestamp"],
                "last_available_candle": row["last_timestamp"],
                "source_use_statuses": list(source_statuses),
                "data_quality_status": quality["status"],
                "eligibility_status": eligibility,
                "eligible_for_research": eligibility == "PASS",
                "exclusion_reasons": exclusion_reasons,
                "evidence_verification_status": "UNKNOWN",
            }
        )
        if len(instruments) >= result_limit:
            break
    has_more = scanned_count < len(rows)
    scan_limit_reached = scanned_count == MAX_DISCOVERY_SCAN and has_more
    next_cursor = None
    if has_more and last_scanned is not None:
        next_cursor = _encode_cursor(
            "discovery",
            query,
            {
                "exchange": last_scanned["exchange"],
                "trading_symbol": last_scanned["trading_symbol"],
                "instrument_id": int(last_scanned["id"]),
                "snapshot_max_id": snapshot_max_id,
            },
        )
    completeness_status = (
        "COMPLETE"
        if not has_more
        else "SCAN_LIMIT_REACHED"
        if scan_limit_reached
        else "PAGE_HAS_MORE"
    )
    return {
        "status": "OK",
        "interval": interval,
        "requested_start_timestamp": _format_timestamp(start) if start else None,
        "requested_end_timestamp": _format_timestamp(end) if end else None,
        "minimum_candles": minimum_candles,
        "quality_status_filter": quality_status,
        "instruments_without_historical_data": int(no_history),
        "candidate_scan_limit": MAX_DISCOVERY_SCAN,
        "candidate_scan_truncated": scan_limit_reached,
        "scanned_candidates": scanned_count,
        "result_limit": result_limit,
        "instruments": instruments,
        "eligible_count": sum(item["eligible_for_research"] for item in instruments),
        "complete": not has_more,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "completeness_status": completeness_status,
    }


def inspect_historical_coverage(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    calendars: CalendarRegistry | None = None,
) -> dict[str, Any]:
    """Inspect stored coverage and use the existing quality analyzer for continuity."""

    normalized_exchange, normalized_symbol = _instrument_identity(exchange, trading_symbol)
    interval = _validate_interval(interval)
    start, end = _validate_optional_timestamp_range(start_timestamp, end_timestamp)
    calendars = _approved_research_calendars()
    with closing(get_readonly_connection(database_path)) as connection:
        instrument = _get_instrument(connection, normalized_exchange, normalized_symbol)
        if instrument is None:
            raise ValueError(
                f"Instrument {normalized_exchange}:{normalized_symbol} is not registered."
            )
        _resolve_calendar(calendars, normalized_exchange, instrument["segment"])
        summary = _range_summary(connection, instrument["id"], interval, start, end)
        source_statuses = _source_use_statuses(connection, instrument["id"], interval, start, end)

    count = int(summary["record_count"])
    first_timestamp = summary["first_timestamp"]
    last_timestamp = summary["last_timestamp"]
    quality: dict[str, Any]
    if count == 0:
        quality = {
            "status": "UNKNOWN",
            "overall_status": "NO_DATA",
            "calendar_status": "UNAVAILABLE",
            "calendar_confidence": "UNAVAILABLE",
            "warnings": ["No historical candles exist in the requested range."],
        }
    else:
        quality_start = start or _parse_aware_timestamp(first_timestamp, "first_timestamp")
        quality_end = end or _parse_aware_timestamp(last_timestamp, "last_timestamp")
        quality = _quality_for_timestamps(
            database_path,
            exchange=normalized_exchange,
            trading_symbol=normalized_symbol,
            segment=instrument["segment"],
            interval=interval,
            start=quality_start,
            end=quality_end,
            calendars=calendars,
        )
    eligibility = _eligibility_status(source_statuses, quality["status"])
    exclusion_reason = None
    if count == 0:
        exclusion_reason = "No historical candles exist in the requested range."
    elif "PROHIBITED" in source_statuses:
        exclusion_reason = "At least one contributing import is declared PROHIBITED."
    elif not source_use_is_eligible(source_statuses):
        exclusion_reason = _UNVERIFIED_EXCLUSION
    elif quality["status"] != "PASS":
        exclusion_reason = "Data-quality eligibility is not confirmed."
    return {
        "status": "OK" if count else "NO_DATA",
        "instrument": f"{normalized_exchange}:{normalized_symbol}",
        "interval": interval,
        "requested_start_timestamp": _format_timestamp(start) if start else None,
        "requested_end_timestamp": _format_timestamp(end) if end else None,
        "first_available_candle": first_timestamp,
        "last_available_candle": last_timestamp,
        "record_count": count,
        "distinct_timestamp_count": int(summary["distinct_timestamp_count"]),
        "duplicate_record_count": count - int(summary["distinct_timestamp_count"]),
        "source_use_statuses": list(source_statuses),
        "data_quality": quality,
        "eligibility_status": eligibility,
        "exclusion_reason": exclusion_reason,
        "evidence_verification_status": "UNKNOWN",
    }


def _validate_retrieval_cursor_position(
    position: dict[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> tuple[datetime, int, int, str]:
    if set(position) != {"timestamp", "candle_id", "snapshot_max_id", "dataset_fingerprint"}:
        raise ValueError("cursor position is malformed.")
    timestamp_text = _required_text(position["timestamp"], "cursor timestamp")
    timestamp = _parse_aware_timestamp(timestamp_text, "cursor timestamp")
    if _format_timestamp(timestamp) != timestamp_text:
        raise ValueError("cursor timestamp is not canonical.")
    if not start <= timestamp <= end:
        raise ValueError("cursor timestamp is outside the requested range.")
    candle_id = position["candle_id"]
    snapshot_max_id = position["snapshot_max_id"]
    if isinstance(candle_id, bool) or not isinstance(candle_id, int) or candle_id < 1:
        raise ValueError("cursor candle_id is invalid.")
    if (
        isinstance(snapshot_max_id, bool)
        or not isinstance(snapshot_max_id, int)
        or snapshot_max_id < candle_id
    ):
        raise ValueError("cursor snapshot is invalid.")
    fingerprint = position["dataset_fingerprint"]
    if not isinstance(fingerprint, str) or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise ValueError("cursor dataset fingerprint is invalid.")
    return timestamp, candle_id, snapshot_max_id, fingerprint


def _retrieval_failure(
    base: dict[str, Any],
    *,
    status: str,
    message: str,
    quality_status: str = "UNKNOWN",
    has_more: bool = False,
) -> dict[str, Any]:
    return {
        **base,
        "status": status,
        "record_count": 0,
        "returned_range": None,
        "truncated": True,
        "quality_status": quality_status,
        "quality_warnings": [],
        "sources": [],
        "candles": [],
        "evidence_reference": None,
        "has_more": has_more,
        "next_cursor": None,
        "complete": False,
        "message": message,
    }


def retrieve_historical_candles(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_timestamp: str,
    end_timestamp: str,
    limit: int = 100,
    cursor: str | None = None,
    calendars: CalendarRegistry | None = None,
) -> dict[str, Any]:
    """Retrieve one internally consistent page from verified-permitted imports.

    Each request uses one explicit read transaction. Cursors fingerprint the bounded
    queried dataset, but each continuation opens a new transaction and is not an
    immutable cross-request database snapshot.
    The legacy calendars argument is non-authoritative; approved evidence is reloaded.
    """

    normalized_exchange, normalized_symbol = _instrument_identity(exchange, trading_symbol)
    interval = _validate_interval(interval)
    row_limit = _validate_limit(limit, "limit", MAX_RESEARCH_CANDLE_LIMIT)
    start, end = _validate_timestamp_range(start_timestamp, end_timestamp)
    requested_start = _format_timestamp(start)
    requested_end = _format_timestamp(end)
    query = {
        "exchange": normalized_exchange,
        "trading_symbol": normalized_symbol,
        "interval": interval,
        "start_timestamp": requested_start,
        "end_timestamp": requested_end,
        "limit": row_limit,
    }
    position = _decode_cursor(cursor, kind="candles", query=query) if cursor is not None else None
    calendars = _approved_research_calendars()
    if position is None:
        after_timestamp = None
        after_id = None
        snapshot_max_id = 0
        expected_fingerprint = None
    else:
        (
            cursor_timestamp,
            after_id,
            snapshot_max_id,
            expected_fingerprint,
        ) = _validate_retrieval_cursor_position(position, start=start, end=end)
        after_timestamp = _format_timestamp(cursor_timestamp)

    with closing(get_readonly_connection(database_path)) as connection:
        connection.execute("BEGIN")
        instrument = _get_instrument(connection, normalized_exchange, normalized_symbol)
        if instrument is None:
            raise ValueError(
                f"Instrument {normalized_exchange}:{normalized_symbol} is not registered."
            )
        _resolve_calendar(calendars, normalized_exchange, instrument["segment"])
        if position is None:
            snapshot_max_id = int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM historical_candles"
                ).fetchone()[0]
            )
        summary = _range_summary(
            connection, instrument["id"], interval, start, end, snapshot_max_id
        )
        source_statuses = _source_use_statuses(
            connection, instrument["id"], interval, start, end, snapshot_max_id
        )
        available_count = int(summary["record_count"])
        base: dict[str, Any] = {
            "instrument": f"{normalized_exchange}:{normalized_symbol}",
            "interval": interval,
            "requested_start_timestamp": requested_start,
            "requested_end_timestamp": requested_end,
            "available_record_count": available_count,
            "source_use_statuses": list(source_statuses),
            "page_cursor": cursor,
            "snapshot_max_id": snapshot_max_id,
            "result_limit": row_limit,
            "evidence_verification_status": "UNKNOWN",
        }

        if position is not None:
            cursor_row = connection.execute(
                """SELECT 1 FROM historical_candles
                   WHERE id = ? AND instrument_id = ? AND interval = ?
                     AND candle_timestamp = ? AND candle_timestamp >= ?
                     AND candle_timestamp <= ? AND id <= ?""",
                (
                    after_id,
                    instrument["id"],
                    interval,
                    after_timestamp,
                    requested_start,
                    requested_end,
                    snapshot_max_id,
                ),
            ).fetchone()
            if cursor_row is None:
                return _retrieval_failure(
                    base,
                    status="DATA_CHANGED",
                    message="The cursor candle was deleted or no longer matches this query.",
                )

        candle_fingerprint = _dataset_fingerprint(
            connection,
            instrument["id"],
            interval,
            start,
            end,
            snapshot_max_id,
        )
        if candle_fingerprint is None:
            if expected_fingerprint is not None:
                return _retrieval_failure(
                    base,
                    status="DATA_CHANGED",
                    message=(
                        "The dataset exceeded the cursor verification bound; restart retrieval."
                    ),
                )
            return _retrieval_failure(
                base,
                status="VERIFICATION_INCOMPLETE",
                message=(
                    "The matching dataset exceeds the bounded cursor-verification limit; "
                    "no candles were returned."
                ),
                has_more=available_count > 0,
            )
        quality = _quality_for_timestamps(
            database_path,
            exchange=normalized_exchange,
            trading_symbol=normalized_symbol,
            segment=instrument["segment"],
            interval=interval,
            start=start,
            end=end,
            calendars=calendars,
            connection=connection,
            snapshot_max_id=snapshot_max_id,
        )
        dataset_fingerprint = _bind_quality_fingerprint(candle_fingerprint, quality)
        if expected_fingerprint is not None and not hmac.compare_digest(
            dataset_fingerprint, expected_fingerprint
        ):
            return _retrieval_failure(
                base,
                status="DATA_CHANGED",
                message=(
                    "Candle data, provenance, or source-use status changed after this cursor "
                    "was issued; restart retrieval."
                ),
            )

        if available_count == 0:
            return {
                **base,
                "status": "EMPTY",
                "record_count": 0,
                "returned_range": None,
                "truncated": False,
                "candles": [],
                "quality_status": "UNKNOWN",
                "evidence_reference": None,
                "has_more": False,
                "next_cursor": None,
                "complete": True,
                "message": "No stored candles match the requested instrument, interval, and range.",
            }
        if not source_use_is_eligible(source_statuses):
            return _retrieval_failure(
                base,
                status="INELIGIBLE_SOURCE_USE",
                message=_UNVERIFIED_EXCLUSION,
            )

        repository_rows = list_historical_candles_in_range(
            database_path,
            exchange=normalized_exchange,
            trading_symbol=normalized_symbol,
            interval=interval,
            start_timestamp=requested_start,
            end_timestamp=requested_end,
            limit=row_limit + 1,
            after_timestamp=after_timestamp,
            after_id=after_id,
            snapshot_max_id=snapshot_max_id,
            connection=connection,
        )
        if len({row["candle_timestamp"] for row in repository_rows}) != len(repository_rows):
            return _retrieval_failure(
                base,
                status="DATA_INTEGRITY_ERROR",
                quality_status="FAIL",
                message="Duplicate candle timestamps were detected; no candles were returned.",
            )
        has_more = len(repository_rows) > row_limit
        returned_rows = repository_rows[:row_limit]
        if not returned_rows:
            return _retrieval_failure(
                base,
                status="DATA_CHANGED",
                message=(
                    "The dataset changed after this cursor was issued; restart retrieval "
                    "to obtain a consistent result."
                ),
            )

    next_cursor = None
    if has_more:
        last_row = returned_rows[-1]
        last_timestamp = last_row["candle_timestamp"]
        last_candle_id = last_row["id"]
        if (
            not isinstance(last_timestamp, str)
            or isinstance(last_candle_id, bool)
            or not isinstance(last_candle_id, int)
        ):
            return _retrieval_failure(
                base,
                status="DATA_INTEGRITY_ERROR",
                quality_status="FAIL",
                message="A candle cursor could not be formed from invalid stored data.",
            )
        next_cursor = _encode_cursor(
            "candles",
            query,
            {
                "timestamp": last_timestamp,
                "candle_id": last_candle_id,
                "snapshot_max_id": snapshot_max_id,
                "dataset_fingerprint": dataset_fingerprint,
            },
        )
    result_candles = [
        {
            "timestamp": row["candle_timestamp"],
            "open": row["open_decimal"],
            "high": row["high_decimal"],
            "low": row["low_decimal"],
            "close": row["close_decimal"],
            "volume": row["volume"],
        }
        for row in returned_rows
    ]
    returned_first_timestamp = result_candles[0]["timestamp"]
    returned_last_timestamp = result_candles[-1]["timestamp"]
    if not isinstance(returned_first_timestamp, str) or not isinstance(
        returned_last_timestamp, str
    ):
        return _retrieval_failure(
            base,
            status="DATA_INTEGRITY_ERROR",
            quality_status="FAIL",
            message="A candle page contains an invalid stored timestamp.",
        )
    sources = _source_summaries(returned_rows)
    reference = _evidence_reference(
        exchange=normalized_exchange,
        trading_symbol=normalized_symbol,
        interval=interval,
        requested_start=requested_start,
        requested_end=requested_end,
        candles=returned_rows[:row_limit],
        sources=sources,
        returned_first_timestamp=returned_first_timestamp,
        returned_last_timestamp=returned_last_timestamp,
        returned_count=len(result_candles),
        available_record_count=available_count,
        page_limit=row_limit,
        page_cursor=cursor,
        snapshot_max_id=snapshot_max_id,
        has_more=has_more,
        quality_status=quality["status"],
        quality_warnings=quality.get("warnings", []),
    )
    return {
        **base,
        "status": "OK",
        "record_count": len(result_candles),
        "returned_range": {
            "start_timestamp": returned_first_timestamp,
            "end_timestamp": returned_last_timestamp,
        },
        "truncated": has_more,
        "result_limit": row_limit,
        "quality_status": quality["status"],
        "quality_warnings": quality.get("warnings", []),
        "sources": sources,
        "candles": result_candles,
        "evidence_reference": reference,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "complete": not has_more,
    }


def inspect_historical_data_quality(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: str,
    end_date: str,
    calendars: CalendarRegistry | None = None,
) -> dict[str, Any]:
    """Expose the existing dataset-quality report and source-use declarations."""

    normalized_exchange, normalized_symbol = _instrument_identity(exchange, trading_symbol)
    interval = _validate_interval(interval)
    start, end = _validate_quality_dates(start_date, end_date, interval)
    calendars = _approved_research_calendars()
    with closing(get_readonly_connection(database_path)) as connection:
        connection.execute("BEGIN")
        instrument = _get_instrument(connection, normalized_exchange, normalized_symbol)
        if instrument is None:
            raise ValueError(
                f"Instrument {normalized_exchange}:{normalized_symbol} is not registered."
            )
        _, timezone = _resolve_calendar(calendars, normalized_exchange, instrument["segment"])
        lower = datetime.combine(start, time.min, tzinfo=timezone).astimezone(UTC)
        upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone).astimezone(UTC)
        timestamp_filter = "c.candle_timestamp >= ? AND c.candle_timestamp < ?"
        count = connection.execute(
            f"""SELECT COUNT(*) FROM historical_candles AS c
                WHERE c.instrument_id = ? AND c.interval = ? AND {timestamp_filter}""",
            (instrument["id"], interval, _format_timestamp(lower), _format_timestamp(upper)),
        ).fetchone()[0]
        source_statuses = _source_use_statuses(
            connection, instrument["id"], interval, lower, upper - timedelta(seconds=1)
        )
        if count == 0:
            quality: dict[str, Any] = {
                "status": "UNKNOWN",
                "overall_status": "NO_DATA",
                "calendar_status": "UNAVAILABLE",
                "calendar_confidence": "UNAVAILABLE",
                "expected_candles": None,
                "warnings": ["No historical candles exist in the requested date range."],
            }
        else:
            quality = _quality_for_full_dates(
                database_path,
                exchange=normalized_exchange,
                trading_symbol=normalized_symbol,
                segment=instrument["segment"],
                interval=interval,
                start_date=start,
                end_date=end,
                timezone=timezone,
                calendars=calendars,
                connection=connection,
            )
    source_eligibility = _source_eligibility_status(source_statuses)
    return {
        "status": "OK" if count else "NO_DATA",
        "instrument": f"{normalized_exchange}:{normalized_symbol}",
        "interval": interval,
        "requested_start_date": start.isoformat(),
        "requested_end_date": end.isoformat(),
        "record_count": int(count),
        "source_use_statuses": list(source_statuses),
        "source_use_eligibility_status": source_eligibility,
        "eligibility_status": _eligibility_status(source_statuses, quality["status"]),
        "data_quality": quality,
        "evidence_verification_status": "UNKNOWN",
    }


__all__ = [
    "MAX_INSTRUMENT_RESULTS",
    "MAX_INTRADAY_QUALITY_SPAN_DAYS",
    "MAX_RESEARCH_CANDLE_LIMIT",
    "MAX_RESEARCH_DATE_SPAN_DAYS",
    "discover_historical_instruments",
    "inspect_historical_coverage",
    "inspect_historical_data_quality",
    "retrieve_historical_candles",
]
