"""Read-only Phase 2 evidence-readiness assessment for market datasets."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from aegis.database import get_readonly_connection
from aegis.market_data import source_use_is_eligible
from aegis.market_quality import (
    CalendarConfigError,
    CalendarRegistry,
    DatasetQualityReport,
    analyze_dataset,
    calculate_expected_candles,
    load_approved_calendar_registry,
)
from aegis.strategy_lab import DataQualityGateError, _validate_source_imports

ReadinessStatus = Literal["READY", "BLOCKED"]


@dataclass(frozen=True, slots=True)
class EvidenceReadinessReport:
    """Read-only assessment of market-data readiness for research use."""

    exchange: str
    trading_symbol: str
    interval: str
    start_date: date
    end_date: date
    as_of: datetime
    readiness_status: ReadinessStatus
    reason_codes: tuple[str, ...]
    reason_descriptions: tuple[str, ...]
    dataset_quality: DatasetQualityReport | None


def _read_source_imports(
    connection: sqlite3.Connection,
    *,
    instrument_id: int,
    interval: str,
    completed_timestamps: frozenset[str],
    first_timestamp: str,
    last_timestamp: str,
) -> tuple[dict[str, object], ...]:
    rows = connection.execute(
        """SELECT c.candle_timestamp, c.import_id,
                  d.id AS source_import_id, d.file_sha256,
                  d.instrument_id AS source_instrument_id,
                  d.requested_exchange AS source_exchange,
                  d.requested_trading_symbol AS source_trading_symbol,
                  d.interval AS source_interval, d.status AS source_import_status,
                  d.source_use_status,
                  h.id AS source_use_declaration_id,
                  h.import_id AS source_use_declaration_import_id,
                  h.previous_status AS source_use_declaration_previous_status,
                  h.new_status AS source_use_declaration_new_status,
                  h.declared_by, h.rationale AS source_use_rationale, h.declared_at
           FROM historical_candles AS c
           LEFT JOIN data_imports AS d ON d.id = c.import_id
           LEFT JOIN import_source_use_history AS h
             ON h.id = (
                 SELECT MAX(history.id)
                 FROM import_source_use_history AS history
                 WHERE history.import_id = d.id
             )
           WHERE c.instrument_id = ? AND c.interval = ?
             AND c.candle_timestamp >= ? AND c.candle_timestamp <= ?
           ORDER BY c.candle_timestamp, c.id""",
        (instrument_id, interval, first_timestamp, last_timestamp),
    ).fetchall()
    imports: dict[int, dict[str, object]] = {}
    for row in rows:
        if row["candle_timestamp"] not in completed_timestamps:
            continue
        import_id = row["source_import_id"]
        if not isinstance(import_id, int) or isinstance(import_id, bool):
            continue
        imports[import_id] = {
            "import_id": import_id,
            "file_sha256": row["file_sha256"],
            "instrument_id": row["source_instrument_id"],
            "exchange": row["source_exchange"],
            "trading_symbol": row["source_trading_symbol"],
            "interval": row["source_interval"],
            "import_status": row["source_import_status"],
            "source_use_status": row["source_use_status"] or "UNVERIFIED",
            "declaration_id": row["source_use_declaration_id"],
            "declaration_import_id": row["source_use_declaration_import_id"],
            "declaration_previous_status": row["source_use_declaration_previous_status"],
            "declaration_new_status": row["source_use_declaration_new_status"],
            "declared_by": row["declared_by"],
            "rationale": row["source_use_rationale"],
            "declared_at": row["declared_at"],
        }
    return tuple(imports[key] for key in sorted(imports))


def assess_evidence_readiness(
    database_path: Path,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    *,
    as_of: datetime,
    calendars: CalendarRegistry | None = None,
    source_import_id: int | None = None,
) -> EvidenceReadinessReport:
    """Assess whether a market dataset is ready for research use.

    Returns READY only when:
    - Instrument is registered and active
    - Calendar exists and covers the date range
    - Historical OHLCV data is complete and valid
    - Source-use permission is explicitly VERIFIED_PERMITTED
    - Dataset quality meets established thresholds

    Any supplied calendar registry is reloaded and checked against the
    administrator-approved configuration. A source_import_id narrows the quality
    analysis but never waives completeness or source-provenance checks.
    All blocking conditions return stable reason codes. Read-only operation.
    """

    if not isinstance(as_of, datetime):
        raise TypeError("as_of must be a timezone-aware datetime.")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware.")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date.")

    normalized_exchange = exchange.strip().upper()
    normalized_symbol = trading_symbol.strip().upper()
    with closing(get_readonly_connection(database_path)) as connection:
        connection.execute("BEGIN")
        return _assess_evidence_readiness(
            connection,
            database_path,
            normalized_exchange,
            normalized_symbol,
            interval,
            start_date,
            end_date,
            as_of=as_of,
            calendars=calendars,
            source_import_id=source_import_id,
        )


def _assess_evidence_readiness(
    connection: sqlite3.Connection,
    database_path: Path,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    *,
    as_of: datetime,
    calendars: CalendarRegistry | None,
    source_import_id: int | None,
) -> EvidenceReadinessReport:
    """Assess readiness using the caller-owned read-only database snapshot."""

    reason_codes: list[str] = []
    reason_descriptions: list[str] = []
    dataset_quality: DatasetQualityReport | None = None

    instrument_row = connection.execute(
        "SELECT id, segment, is_active FROM instruments "
        "WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE",
        (exchange, trading_symbol),
    ).fetchone()

    if instrument_row is None:
        reason_codes.append("INSTRUMENT_NOT_REGISTERED")
        reason_descriptions.append(f"Instrument {exchange}:{trading_symbol} is not registered.")
        return EvidenceReadinessReport(
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
            readiness_status="BLOCKED",
            reason_codes=tuple(reason_codes),
            reason_descriptions=tuple(reason_descriptions),
            dataset_quality=None,
        )

    if not instrument_row["is_active"]:
        reason_codes.append("INSTRUMENT_INACTIVE")
        reason_descriptions.append(f"Instrument {exchange}:{trading_symbol} is marked inactive.")
        return EvidenceReadinessReport(
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
            readiness_status="BLOCKED",
            reason_codes=tuple(reason_codes),
            reason_descriptions=tuple(reason_descriptions),
            dataset_quality=None,
        )

    try:
        calendars = load_approved_calendar_registry(expected_registry=calendars)
    except (CalendarConfigError, OSError, ValueError) as exc:
        reason_codes.append("CALENDAR_LOAD_ERROR")
        reason_descriptions.append(f"Approved calendar configuration error: {exc}")
        return EvidenceReadinessReport(
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
            readiness_status="BLOCKED",
            reason_codes=tuple(reason_codes),
            reason_descriptions=tuple(reason_descriptions),
            dataset_quality=None,
        )

    calendar = calendars.resolve(exchange, instrument_row["segment"])
    if calendar is None:
        reason_codes.append("CALENDAR_UNAVAILABLE")
        reason_descriptions.append(
            f"No exchange/segment calendar is configured for "
            f"{exchange}/{instrument_row['segment'] or 'default'}."
        )
        return EvidenceReadinessReport(
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
            readiness_status="BLOCKED",
            reason_codes=tuple(reason_codes),
            reason_descriptions=tuple(reason_descriptions),
            dataset_quality=None,
        )

    if calendar.confidence != "CONFIRMED":
        reason_codes.append("CALENDAR_NOT_CONFIRMED")
        reason_descriptions.append(f"Calendar confidence is {calendar.confidence}, not CONFIRMED.")

    calendar_covers = sum(
        1
        for offset in range((end_date - start_date).days + 1)
        if calendar.covers(start_date + timedelta(days=offset))
    )
    total_days = (end_date - start_date).days + 1
    if calendar_covers < total_days:
        reason_codes.append("CALENDAR_INCOMPLETE_COVERAGE")
        reason_descriptions.append(
            f"Calendar covers {calendar_covers} of {total_days} requested days."
        )

    try:
        dataset_quality = analyze_dataset(
            database_path,
            exchange,
            trading_symbol,
            interval,
            start_date,
            end_date,
            as_of=as_of,
            calendars=calendars,
            source_import_id=source_import_id,
            connection=connection,
        )
    # Keep unexpected analyzer failures explicit and fail closed at the report boundary.
    except Exception as exc:  # noqa: BLE001
        reason_codes.append("DATASET_ANALYSIS_ERROR")
        reason_descriptions.append(f"Dataset analysis failed ({type(exc).__name__}): {exc}")
        return EvidenceReadinessReport(
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
            readiness_status="BLOCKED",
            reason_codes=tuple(reason_codes),
            reason_descriptions=tuple(reason_descriptions),
            dataset_quality=None,
        )

    if dataset_quality.expected_candles is None:
        reason_codes.append("EXPECTED_SCHEDULE_UNAVAILABLE")
        reason_descriptions.append(
            "A complete expected-candle schedule is unavailable for the requested range."
        )
    elif dataset_quality.expected_candles == 0:
        reason_codes.append("NO_EXPECTED_CANDLES")
        reason_descriptions.append(
            "The requested range contains no completed expected candles as of the assessment time."
        )

    if dataset_quality.actual_candles == 0:
        reason_codes.append("NO_HISTORICAL_DATA")
        reason_descriptions.append(
            "No historical candles found for this instrument/interval in the requested period."
        )

    if dataset_quality.invalid_candle_count > 0:
        reason_codes.append("INVALID_CANDLES_PRESENT")
        reason_descriptions.append(
            f"{dataset_quality.invalid_candle_count} candles have invalid OHLCV values."
        )

    if (
        dataset_quality.duplicate_identity_count > 0
        or dataset_quality.duplicate_import_row_count > 0
    ):
        reason_codes.append("DUPLICATE_CANDLES_PRESENT")
        reason_descriptions.append(
            f"{dataset_quality.duplicate_identity_count} duplicate stored candles and "
            f"{dataset_quality.duplicate_import_row_count} duplicate import rows detected."
        )

    if dataset_quality.completeness_percentage is None:
        reason_codes.append("COMPLETENESS_UNAVAILABLE")
        reason_descriptions.append("Dataset completeness could not be established.")
    elif dataset_quality.completeness_percentage < 100:
        reason_codes.append("INCOMPLETE_CANDLES")
        reason_descriptions.append(
            f"Dataset is {dataset_quality.completeness_percentage}% complete; "
            f"{len(dataset_quality.missing_timestamps)} completed expected candles are missing."
        )

    if dataset_quality.staleness_status == "STALE":
        reason_codes.append("DATASET_STALE")
        reason_descriptions.append(
            "The latest completed expected candles exceed the stale threshold."
        )
    elif dataset_quality.staleness_status == "POTENTIALLY_STALE":
        reason_codes.append("DATASET_POTENTIALLY_STALE")
        reason_descriptions.append(
            "The latest completed expected candles exceed the potential-staleness threshold."
        )

    if (
        dataset_quality.rejected_import_row_count
        or dataset_quality.unlocated_rejected_import_row_count
    ):
        reason_codes.append("REJECTED_IMPORT_ROWS")
        reason_descriptions.append(
            f"{dataset_quality.rejected_import_row_count} rejected import rows and "
            f"{dataset_quality.unlocated_rejected_import_row_count} unlocated rejected rows detected."
        )

    if dataset_quality.unexpected_timestamps or dataset_quality.provisional_unexpected_timestamps:
        reason_codes.append("UNEXPECTED_CANDLES_PRESENT")
        reason_descriptions.append("Candles exist outside the expected session schedule.")

    if dataset_quality.overall_status != "COMPLETE":
        reason_codes.append("DATASET_QUALITY_NOT_COMPLETE")
        reason_descriptions.append(
            f"Existing dataset qualification reports {dataset_quality.overall_status}."
        )

    try:
        _, completed, _, _ = calculate_expected_candles(
            calendar,
            interval,
            start_date,
            end_date,
            as_of=as_of,
        )
        source_imports = (
            _read_source_imports(
                connection,
                instrument_id=instrument_row["id"],
                interval=interval,
                completed_timestamps=frozenset(item.timestamp for item in completed),
                first_timestamp=completed[0].timestamp,
                last_timestamp=completed[-1].timestamp,
            )
            if completed
            else ()
        )

        if source_import_id is not None:
            import_row = connection.execute(
                "SELECT id FROM data_imports WHERE id = ?", (source_import_id,)
            ).fetchone()
            if import_row is None:
                reason_codes.append("IMPORT_ID_NOT_FOUND")
                reason_descriptions.append(f"Source import ID {source_import_id} not found.")

        if not source_imports:
            reason_codes.append("SOURCE_PROVENANCE_UNAVAILABLE")
            reason_descriptions.append(
                "No completed expected candle has resolvable source-import provenance."
            )
        else:
            statuses = [
                str(source_import.get("source_use_status") or "UNVERIFIED")
                for source_import in source_imports
            ]
            if not source_use_is_eligible(statuses):
                reason_codes.append("SOURCE_USE_NOT_PERMITTED")
                reason_descriptions.append(
                    "Every contributing import must have VERIFIED_PERMITTED source-use status."
                )
            try:
                _validate_source_imports(source_imports)
            except DataQualityGateError as exc:
                reason_codes.append("SOURCE_PROVENANCE_INVALID")
                reason_descriptions.append(f"Source-import provenance is invalid: {exc}")
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        reason_codes.append("SOURCE_PROVENANCE_ERROR")
        reason_descriptions.append(
            f"Source-provenance analysis failed ({type(exc).__name__}): {exc}"
        )

    if reason_codes:
        return EvidenceReadinessReport(
            exchange=exchange,
            trading_symbol=trading_symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
            readiness_status="BLOCKED",
            reason_codes=tuple(reason_codes),
            reason_descriptions=tuple(reason_descriptions),
            dataset_quality=dataset_quality,
        )

    return EvidenceReadinessReport(
        exchange=exchange,
        trading_symbol=trading_symbol,
        interval=interval,
        start_date=start_date,
        end_date=end_date,
        as_of=as_of,
        readiness_status="READY",
        reason_codes=(),
        reason_descriptions=(),
        dataset_quality=dataset_quality,
    )


def assess_multiple_instruments(
    database_path: Path,
    specs: list[tuple[str, str, str, date, date]],
    *,
    as_of: datetime,
    calendars: CalendarRegistry | None = None,
) -> tuple[EvidenceReadinessReport, ...]:
    """Assess readiness for multiple instruments in one pass.

    Returns a tuple of readiness reports in the same order as the input specs.
    Each spec is (exchange, trading_symbol, interval, start_date, end_date).
    """

    return tuple(
        assess_evidence_readiness(
            database_path,
            exchange,
            trading_symbol,
            interval,
            start_date,
            end_date,
            as_of=as_of,
            calendars=calendars,
        )
        for exchange, trading_symbol, interval, start_date, end_date in specs
    )
