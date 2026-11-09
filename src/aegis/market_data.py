"""Local CSV ingestion and read-only access for exact historical OHLCV candles."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from aegis.database import get_connection

SUPPORTED_INTERVALS = ("1m", "5m", "15m", "1h", "1d")
REQUIRED_CSV_COLUMNS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})
_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_INTEGER_TEXT = re.compile(r"[0-9]+\Z")
_SQLITE_INTEGER_MAX = 9_223_372_036_854_775_807
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?P<fraction>\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_INDIA_TZ = ZoneInfo("Asia/Kolkata")


class CSVImportError(ValueError):
    """Raised when a CSV file cannot be read as a valid import input."""


class SourceUseStatusError(ValueError):
    """Raised when a source-use declaration is invalid or targets no import."""


SourceUseStatus = Literal["VERIFIED_PERMITTED", "UNVERIFIED", "PROHIBITED"]
_SOURCE_USE_STATUSES = frozenset({"VERIFIED_PERMITTED", "UNVERIFIED", "PROHIBITED"})


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Result and persisted provenance for one CSV import attempt."""

    import_id: int
    original_filename: str
    file_sha256: str | None
    exchange: str
    trading_symbol: str
    interval: str
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    skipped_rows: int
    duplicate_rows: int
    same_file_reimport: bool
    status: str
    error_summary: str | None
    tick_size_validation: str


@dataclass(frozen=True, slots=True)
class _Candle:
    timestamp: str
    open_decimal: str
    high_decimal: str
    low_decimal: str
    close_decimal: str
    volume: int


class _RowValidationError(ValueError):
    def __init__(self, reason: str, timestamp: str | None = None) -> None:
        super().__init__(reason)
        self.timestamp = timestamp


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


def _decimal_value(value: object, field: str) -> tuple[Decimal, str]:
    if isinstance(value, (float, bool)):
        raise _RowValidationError(f"{field} must be decimal text; binary floats are rejected.")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (str, int)):
        text = str(value).strip()
        if not text or _DECIMAL_TEXT.fullmatch(text) is None:
            raise _RowValidationError(f"{field} must be a plain base-10 decimal value.")
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            raise _RowValidationError(f"{field} must be a valid decimal value.") from None
    else:
        raise _RowValidationError(f"{field} must be decimal text.")

    if not parsed.is_finite() or parsed <= 0:
        raise _RowValidationError(f"{field} must be finite and greater than zero.")
    canonical = format(parsed, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return parsed, canonical


def _is_tick_aligned(price: Decimal, tick_size: Decimal) -> bool:
    """Test exact divisibility using integer coefficients, independent of Decimal context."""

    price_tuple = price.as_tuple()
    tick_tuple = tick_size.as_tuple()
    if not isinstance(price_tuple.exponent, int) or not isinstance(tick_tuple.exponent, int):
        return False
    common_exponent = min(price_tuple.exponent, tick_tuple.exponent)
    price_units = int("".join(map(str, price_tuple.digits))) * 10 ** (
        price_tuple.exponent - common_exponent
    )
    tick_units = int("".join(map(str, tick_tuple.digits))) * 10 ** (
        tick_tuple.exponent - common_exponent
    )
    return tick_units != 0 and price_units % tick_units == 0


def _parse_timestamp(value: object, interval: str) -> str:
    if not isinstance(value, str):
        raise _RowValidationError(
            "timestamp must be ISO-8601 date-time text with an explicit UTC offset."
        )
    raw = value.strip()
    timestamp_match = _ISO_TIMESTAMP.fullmatch(raw)
    if timestamp_match is None:
        raise _RowValidationError(
            "timestamp must be ISO-8601 date-time text with an explicit UTC offset."
        )
    fraction = timestamp_match.group("fraction")
    if fraction is not None and len(fraction) > 7 and any(digit != "0" for digit in fraction[7:]):
        raise _RowValidationError(
            "timestamp has nonzero fractional-second digits beyond six-digit precision."
        )
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        raise _RowValidationError("timestamp is not a valid ISO-8601 date-time.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _RowValidationError("timestamp must include a timezone offset.")
    if parsed.second != 0 or parsed.microsecond != 0:
        raise _RowValidationError("timestamp must be aligned to a whole minute.")

    local = parsed.astimezone(_INDIA_TZ)
    if interval == "1d":
        if local.hour != 0 or local.minute != 0:
            raise _RowValidationError(
                "1d candles must start at midnight Asia/Kolkata local time.",
                local.isoformat(),
            )
    else:
        minutes_since_session_anchor = local.hour * 60 + local.minute - (9 * 60 + 15)
        duration = _INTERVAL_MINUTES[interval]
        if minutes_since_session_anchor < 0 or minutes_since_session_anchor % duration != 0:
            raise _RowValidationError(
                f"timestamp is not aligned to a {interval} candle start from 09:15 Asia/Kolkata.",
                local.isoformat(),
            )

    utc_timestamp = parsed.astimezone(UTC).isoformat(timespec="seconds")
    return utc_timestamp.replace("+00:00", "Z")


def _parse_candle(
    row: dict[str | None, str | None], interval: str, tick_size: Decimal | None
) -> _Candle:
    if None in row:
        raise _RowValidationError("row contains more values than the CSV header.")
    timestamp = _parse_timestamp(row.get("timestamp"), interval)

    values: dict[str, Decimal] = {}
    canonical: dict[str, str] = {}
    for field in ("open", "high", "low", "close"):
        try:
            values[field], canonical[field] = _decimal_value(row.get(field), field)
        except _RowValidationError as exc:
            raise _RowValidationError(str(exc), timestamp) from None

    if values["high"] < max(values["open"], values["low"], values["close"]):
        raise _RowValidationError(
            "high must be greater than or equal to open, low, and close.", timestamp
        )
    if values["low"] > min(values["open"], values["high"], values["close"]):
        raise _RowValidationError(
            "low must be less than or equal to open, high, and close.", timestamp
        )

    raw_volume = row.get("volume")
    if not isinstance(raw_volume, str) or _INTEGER_TEXT.fullmatch(raw_volume.strip()) is None:
        raise _RowValidationError("volume must be a non-negative integer.", timestamp)
    try:
        volume = int(raw_volume.strip())
    except ValueError:
        raise _RowValidationError("volume is outside SQLite's integer range.", timestamp) from None
    if volume > _SQLITE_INTEGER_MAX:
        raise _RowValidationError("volume is outside SQLite's integer range.", timestamp)

    if tick_size is not None:
        for field, price in values.items():
            if not _is_tick_aligned(price, tick_size):
                raise _RowValidationError(
                    f"{field} is not aligned to the instrument tick size {tick_size}.", timestamp
                )

    return _Candle(
        timestamp,
        canonical["open"],
        canonical["high"],
        canonical["low"],
        canonical["close"],
        volume,
    )


def _create_import_attempt(
    database_path: Path, filename: str, exchange: str, symbol: str, interval: str
) -> int:
    with _transaction(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO data_imports (
                import_type, status, original_filename, requested_exchange,
                requested_trading_symbol, interval
            ) VALUES ('CSV_OHLCV', 'STARTED', ?, ?, ?, ?)
            """,
            (filename, exchange, symbol, interval),
        )
        import_id = cursor.lastrowid
        if import_id is None:
            raise RuntimeError("SQLite did not return an import identifier.")
        return import_id


def _record_import_failure(
    database_path: Path,
    import_id: int,
    *,
    checksum: str | None,
    total_rows: int,
    message: str,
    instrument_id: int | None = None,
) -> ImportSummary:
    with _transaction(database_path) as connection:
        row = connection.execute(
            """SELECT original_filename, requested_exchange, requested_trading_symbol, interval
               FROM data_imports WHERE id = ?""",
            (import_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE data_imports
            SET instrument_id = ?, file_sha256 = ?, total_rows = ?, records_received = ?, status = 'FAILED',
                completed_at = CURRENT_TIMESTAMP, error_summary = ?
            WHERE id = ?
            """,
            (instrument_id, checksum, total_rows, total_rows, message, import_id),
        )
        connection.execute(
            """
            INSERT INTO data_quality_issues (
                import_id, instrument_id, interval, issue_type, severity, description
            ) VALUES (?, ?, ?, 'IMPORT_REJECTED', 'ERROR', ?)
            """,
            (
                import_id,
                instrument_id,
                row["interval"],
                message,
            ),
        )
    return ImportSummary(
        import_id=import_id,
        original_filename=row["original_filename"],
        file_sha256=checksum,
        exchange=row["requested_exchange"],
        trading_symbol=row["requested_trading_symbol"],
        interval=row["interval"],
        total_rows=total_rows,
        accepted_rows=0,
        rejected_rows=0,
        skipped_rows=0,
        duplicate_rows=0,
        same_file_reimport=False,
        status="FAILED",
        error_summary=message,
        tick_size_validation="not applied",
    )


def _read_csv(
    content: bytes,
) -> list[tuple[int, dict[str | None, str | None]]]:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVImportError("CSV must be UTF-8 encoded.") from exc
    try:
        reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
        if reader.fieldnames is None:
            raise CSVImportError("CSV is empty or has no header row.")
        normalized_headers = [header.strip().lower() for header in reader.fieldnames]
        if len(set(normalized_headers)) != len(normalized_headers):
            raise CSVImportError("CSV contains duplicate column names after normalization.")
        missing = REQUIRED_CSV_COLUMNS - set(normalized_headers)
        if missing:
            raise CSVImportError(f"CSV is missing required columns: {', '.join(sorted(missing))}.")
        reader.fieldnames = normalized_headers
        rows = [(line_number, row) for line_number, row in enumerate(reader, start=2)]
        return rows
    except csv.Error as exc:
        raise CSVImportError(f"CSV parsing failed: {exc}") from exc


def _insert_issue(
    connection: sqlite3.Connection,
    *,
    import_id: int,
    instrument_id: int,
    interval: str,
    row_number: int,
    issue_type: str,
    severity: str,
    description: str,
    timestamp: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO data_quality_issues (
            import_id, instrument_id, interval, issue_type, severity,
            description, affected_timestamp, source_row_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_id,
            instrument_id,
            interval,
            issue_type,
            severity,
            description,
            timestamp,
            row_number,
        ),
    )


def import_ohlcv_csv(
    database_path: Path,
    csv_path: Path,
    exchange: str,
    trading_symbol: str,
    interval: str,
) -> ImportSummary:
    """Import one local CSV, preserving accepted candles and recording all row outcomes.

    CSV timestamps must be ISO-8601 values with an explicit offset. They are aligned in
    Asia/Kolkata and stored in UTC. CSV columns are timestamp, open, high, low, close,
    and volume; exchange, symbol, and interval are supplied for the whole file.
    """

    path = Path(csv_path)
    normalized_exchange = exchange.strip().upper()
    normalized_symbol = trading_symbol.strip().upper()
    normalized_interval = interval.strip().lower()
    import_id = _create_import_attempt(
        database_path,
        path.name,
        normalized_exchange,
        normalized_symbol,
        normalized_interval,
    )

    try:
        content = path.read_bytes()
    except OSError as exc:
        return _record_import_failure(
            database_path,
            import_id,
            checksum=None,
            total_rows=0,
            message=f"Could not read CSV file: {exc.strerror or type(exc).__name__}.",
        )

    checksum = hashlib.sha256(content).hexdigest()
    try:
        rows = _read_csv(content)
    except CSVImportError as exc:
        return _record_import_failure(
            database_path,
            import_id,
            checksum=checksum,
            total_rows=0,
            message=str(exc),
        )

    if not rows:
        return _record_import_failure(
            database_path,
            import_id,
            checksum=checksum,
            total_rows=0,
            message="CSV contains a header but no data rows.",
        )

    if normalized_interval not in SUPPORTED_INTERVALS:
        return _record_import_failure(
            database_path,
            import_id,
            checksum=checksum,
            total_rows=len(rows),
            message=(
                f"Unsupported interval {normalized_interval!r}; supported intervals are "
                f"{', '.join(SUPPORTED_INTERVALS)}."
            ),
        )

    accepted = rejected = skipped = duplicates = 0
    error_messages: list[str] = []
    tick_mode = "not available"
    same_file = False
    try:
        with _transaction(database_path) as connection:
            instrument = connection.execute(
                """
                SELECT id, is_active, tick_size, tick_size_decimal
                FROM instruments
                WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE
                """,
                (normalized_exchange, normalized_symbol),
            ).fetchone()
            if instrument is None:
                message = f"Instrument {normalized_exchange}:{normalized_symbol} is not registered."
                connection.execute(
                    """UPDATE data_imports SET file_sha256 = ?, total_rows = ?,
                       records_received = ?, status = 'FAILED', completed_at = CURRENT_TIMESTAMP,
                       error_summary = ? WHERE id = ?""",
                    (checksum, len(rows), len(rows), message, import_id),
                )
                connection.execute(
                    """INSERT INTO data_quality_issues
                       (import_id, interval, issue_type, severity, description)
                       VALUES (?, ?, 'IMPORT_REJECTED', 'ERROR', ?)""",
                    (import_id, normalized_interval, message),
                )
                return ImportSummary(
                    import_id,
                    path.name,
                    checksum,
                    normalized_exchange,
                    normalized_symbol,
                    normalized_interval,
                    len(rows),
                    0,
                    0,
                    0,
                    0,
                    False,
                    "FAILED",
                    message,
                    "not applied",
                )
            instrument_id = int(instrument["id"])
            if not instrument["is_active"]:
                message = f"Instrument {normalized_exchange}:{normalized_symbol} is inactive."
                connection.execute(
                    """UPDATE data_imports SET file_sha256 = ?, total_rows = ?,
                       instrument_id = ?, records_received = ?, status = 'FAILED', completed_at = CURRENT_TIMESTAMP,
                       error_summary = ? WHERE id = ?""",
                    (checksum, len(rows), instrument_id, len(rows), message, import_id),
                )
                connection.execute(
                    """INSERT INTO data_quality_issues
                       (import_id, instrument_id, interval, issue_type, severity, description)
                       VALUES (?, ?, ?, 'IMPORT_REJECTED', 'ERROR', ?)""",
                    (import_id, instrument_id, normalized_interval, message),
                )
                return ImportSummary(
                    import_id,
                    path.name,
                    checksum,
                    normalized_exchange,
                    normalized_symbol,
                    normalized_interval,
                    len(rows),
                    0,
                    0,
                    0,
                    0,
                    False,
                    "FAILED",
                    message,
                    "not applied",
                )

            tick_size: Decimal | None = None
            if instrument["tick_size_decimal"] is not None and instrument["tick_size"] is None:
                try:
                    tick_size = Decimal(instrument["tick_size_decimal"])
                except InvalidOperation:
                    tick_size = None
            if tick_size is not None and tick_size.is_finite() and tick_size > 0:
                tick_mode = f"validated against exact tick size {tick_size}"
            elif instrument["tick_size"] is not None:
                tick_mode = (
                    "not applied: legacy REAL-derived tick size requires authoritative verification"
                )

            same_file = (
                connection.execute(
                    """
                SELECT 1 FROM data_imports
                WHERE id != ? AND file_sha256 = ? AND requested_exchange = ?
                  AND requested_trading_symbol = ? AND interval = ?
                  AND status IN ('COMPLETED', 'COMPLETED_WITH_ERRORS')
                LIMIT 1
                """,
                    (
                        import_id,
                        checksum,
                        normalized_exchange,
                        normalized_symbol,
                        normalized_interval,
                    ),
                ).fetchone()
                is not None
            )

            connection.execute(
                "UPDATE data_imports SET file_sha256 = ?, instrument_id = ? WHERE id = ?",
                (checksum, instrument_id, import_id),
            )
            seen: dict[tuple[int, str, str], int] = {}
            for row_number, row in rows:
                try:
                    candle = _parse_candle(row, normalized_interval, tick_size)
                except _RowValidationError as exc:
                    rejected += 1
                    error_messages.append(f"row {row_number}: {exc}")
                    _insert_issue(
                        connection,
                        import_id=import_id,
                        instrument_id=instrument_id,
                        interval=normalized_interval,
                        row_number=row_number,
                        issue_type="CSV_ROW_REJECTED",
                        severity="ERROR",
                        description=f"CSV row {row_number}: {exc}",
                        timestamp=exc.timestamp,
                    )
                    continue

                identity = (instrument_id, normalized_interval, candle.timestamp)
                prior_row = seen.get(identity)
                if prior_row is not None:
                    skipped += 1
                    duplicates += 1
                    _insert_issue(
                        connection,
                        import_id=import_id,
                        instrument_id=instrument_id,
                        interval=normalized_interval,
                        row_number=row_number,
                        issue_type="DUPLICATE_CANDLE",
                        severity="WARNING",
                        description=(
                            f"CSV row {row_number} duplicates row {prior_row}; first valid row wins."
                        ),
                        timestamp=candle.timestamp,
                    )
                    continue

                connection.execute("SAVEPOINT csv_candle_row")
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO historical_candles (
                            instrument_id, import_id, interval, candle_timestamp,
                            open_decimal, high_decimal, low_decimal, close_decimal, volume
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(instrument_id, interval, candle_timestamp) DO NOTHING
                        """,
                        (
                            instrument_id,
                            import_id,
                            normalized_interval,
                            candle.timestamp,
                            candle.open_decimal,
                            candle.high_decimal,
                            candle.low_decimal,
                            candle.close_decimal,
                            candle.volume,
                        ),
                    )
                    connection.execute("RELEASE csv_candle_row")
                except sqlite3.IntegrityError as exc:
                    if not connection.in_transaction:
                        raise
                    connection.execute("ROLLBACK TO csv_candle_row")
                    connection.execute("RELEASE csv_candle_row")
                    rejected += 1
                    error_messages.append(f"row {row_number}: {exc}")
                    _insert_issue(
                        connection,
                        import_id=import_id,
                        instrument_id=instrument_id,
                        interval=normalized_interval,
                        row_number=row_number,
                        issue_type="CSV_ROW_REJECTED",
                        severity="ERROR",
                        description=f"CSV row {row_number}: database rejected candle: {exc}",
                        timestamp=candle.timestamp,
                    )
                    continue

                seen[identity] = row_number
                if cursor.rowcount == 0:
                    skipped += 1
                    duplicates += 1
                    _insert_issue(
                        connection,
                        import_id=import_id,
                        instrument_id=instrument_id,
                        interval=normalized_interval,
                        row_number=row_number,
                        issue_type="DUPLICATE_CANDLE",
                        severity="WARNING",
                        description=(
                            f"CSV row {row_number} matches an existing candle; existing data retained."
                        ),
                        timestamp=candle.timestamp,
                    )
                else:
                    accepted += 1

            status = "COMPLETED_WITH_ERRORS" if rejected else "COMPLETED"
            error_summary = "; ".join(error_messages[:20]) if error_messages else None
            if len(error_messages) > 20:
                error_summary = (error_summary or "") + (
                    f"; and {len(error_messages) - 20} more rejected rows"
                )
            connection.execute(
                """
                UPDATE data_imports
                SET total_rows = ?, accepted_rows = ?, rejected_rows = ?, skipped_rows = ?,
                    duplicate_rows = ?, same_file_reimport = ?, records_received = ?,
                    records_inserted = ?, status = ?, completed_at = CURRENT_TIMESTAMP,
                    error_summary = ?
                WHERE id = ?
                """,
                (
                    len(rows),
                    accepted,
                    rejected,
                    skipped,
                    duplicates,
                    int(same_file),
                    len(rows),
                    accepted,
                    status,
                    error_summary,
                    import_id,
                ),
            )
    except Exception as exc:
        _record_import_failure(
            database_path,
            import_id,
            checksum=checksum,
            total_rows=len(rows),
            message=f"Unexpected import failure: {exc}",
            instrument_id=instrument_id if "instrument_id" in locals() else None,
        )
        raise

    return ImportSummary(
        import_id,
        path.name,
        checksum,
        normalized_exchange,
        normalized_symbol,
        normalized_interval,
        len(rows),
        accepted,
        rejected,
        skipped,
        duplicates,
        same_file,
        status,
        error_summary,
        tick_mode,
    )


def declare_import_source_use_status(
    database_path: Path,
    import_id: int,
    status: SourceUseStatus,
    *,
    declared_by: str,
    rationale: str,
) -> int:
    """Record an explicit user/admin source-use declaration for one imported dataset.

    This records a declaration; it does not determine legal rights or verify a licence.
    No status is inferred from a filename, URL, or successful import.
    """

    if isinstance(import_id, bool) or not isinstance(import_id, int) or import_id < 1:
        raise SourceUseStatusError("import_id must be a positive integer.")
    if not isinstance(status, str) or status not in _SOURCE_USE_STATUSES:
        raise SourceUseStatusError("Unsupported source-use status.")
    if not isinstance(declared_by, str) or not declared_by.strip():
        raise SourceUseStatusError("declared_by must identify the user or administrator.")
    if not isinstance(rationale, str) or not rationale.strip():
        raise SourceUseStatusError("rationale must explain the explicit declaration.")

    with _transaction(database_path) as connection:
        row = connection.execute(
            "SELECT source_use_status FROM data_imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if row is None:
            raise SourceUseStatusError(f"Data import {import_id} was not found.")
        cursor = connection.execute(
            """INSERT INTO import_source_use_history
               (import_id, previous_status, new_status, declared_by, rationale)
               VALUES (?, ?, ?, ?, ?)""",
            (import_id, row["source_use_status"], status, declared_by.strip(), rationale.strip()),
        )
        connection.execute(
            "UPDATE data_imports SET source_use_status = ? WHERE id = ?",
            (status, import_id),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a source-use declaration identifier.")
        return int(cursor.lastrowid)


def list_historical_overview(database_path: Path) -> list[dict[str, object]]:
    """Return exact candle counts and UTC date coverage per instrument and interval."""

    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT i.exchange, i.trading_symbol, i.instrument_name, c.interval,
                   COUNT(*) AS candle_count, MIN(c.candle_timestamp) AS first_timestamp,
                   MAX(c.candle_timestamp) AS last_timestamp
            FROM historical_candles AS c
            JOIN instruments AS i ON i.id = c.instrument_id
            GROUP BY i.exchange, i.trading_symbol, i.instrument_name, c.interval
            ORDER BY i.exchange, i.trading_symbol, c.interval
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_legacy_market_data_count(database_path: Path) -> int:
    """Count pre-Stage-3 REAL-based rows preserved outside the exact-decimal view."""

    with closing(get_connection(database_path)) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM market_data").fetchone()[0])


def list_recent_imports(database_path: Path, limit: int = 20) -> list[dict[str, object]]:
    """Return recent CSV import provenance and outcome summaries."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer.")
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT id, instrument_id, original_filename, file_sha256, requested_exchange,
                   requested_trading_symbol, interval, started_at, completed_at, status,
                   total_rows, accepted_rows, rejected_rows, skipped_rows, duplicate_rows,
                   same_file_reimport, error_summary, source_use_status
            FROM data_imports
            WHERE import_type = 'CSV_OHLCV'
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_quality_issues(database_path: Path, limit: int = 100) -> list[dict[str, object]]:
    """Return recent row-level rejects, duplicate notices, and failed import issues."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer.")
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT q.id, q.import_id, d.original_filename, q.instrument_id,
                   i.exchange, i.trading_symbol, q.interval, q.issue_type, q.severity,
                   q.description, q.source_row_number, q.affected_timestamp, q.created_at
            FROM data_quality_issues AS q
            LEFT JOIN data_imports AS d ON d.id = q.import_id
            LEFT JOIN instruments AS i ON i.id = q.instrument_id
            WHERE q.import_id IS NOT NULL
            ORDER BY q.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_historical_candles(
    database_path: Path,
    *,
    exchange: str | None = None,
    trading_symbol: str | None = None,
    interval: str | None = None,
    limit: int = 500,
) -> list[dict[str, object]]:
    """Return stored candles with decimal values as text, newest first."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise ValueError("limit must be an integer between 1 and 5000.")
    clauses: list[str] = []
    parameters: list[object] = []
    if exchange is not None:
        clauses.append("i.exchange = ? COLLATE NOCASE")
        parameters.append(exchange.strip().upper())
    if trading_symbol is not None:
        clauses.append("i.trading_symbol = ? COLLATE NOCASE")
        parameters.append(trading_symbol.strip().upper())
    if interval is not None:
        clauses.append("c.interval = ?")
        parameters.append(interval.strip().lower())
    query = """
        SELECT c.id, i.exchange, i.trading_symbol, c.interval, c.candle_timestamp,
               c.open_decimal, c.high_decimal, c.low_decimal, c.close_decimal,
               c.volume, c.import_id
        FROM historical_candles AS c
        JOIN instruments AS i ON i.id = c.instrument_id
    """
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY c.candle_timestamp DESC, i.exchange, i.trading_symbol LIMIT ?"
    parameters.append(limit)
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]
