"""Deterministic tests for local historical OHLCV CSV ingestion."""

import hashlib
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest

from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument, update_instrument
from aegis.market_data import (
    SUPPORTED_INTERVALS,
    _decimal_value,
    import_ohlcv_csv,
    list_historical_candles,
    list_historical_overview,
    list_quality_issues,
    list_recent_imports,
)

CSV_HEADER = "timestamp,open,high,low,close,volume\n"


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "market_data.db"
    initialize_database(path)
    register_instrument(
        path,
        InstrumentInput("NSE", "TCS", "Tata Consultancy Services", "EQUITY"),
    )
    return path


def write_csv(tmp_path: Path, filename: str, content: str) -> Path:
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8", newline="")
    return path


def candle_row(
    timestamp: str = "2026-01-05T09:15:00+05:30",
    *,
    open_price: str = "100.10",
    high: str = "101.20",
    low: str = "99.90",
    close: str = "100.50",
    volume: str = "1000",
) -> str:
    return f"{timestamp},{open_price},{high},{low},{close},{volume}\n"


def test_fresh_schema_has_exact_decimal_candle_storage(database_path: Path) -> None:
    with closing(get_connection(database_path)) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0]
        candle_info = connection.execute("PRAGMA table_info(historical_candles)").fetchall()
        market_info = connection.execute("PRAGMA table_info(market_data)").fetchall()

    candle_columns = {row["name"]: row["type"] for row in candle_info}
    market_columns = {row["name"]: row["type"] for row in market_info}
    assert version == 6
    assert {
        "open_decimal": "TEXT",
        "high_decimal": "TEXT",
        "low_decimal": "TEXT",
        "close_decimal": "TEXT",
    }.items() <= candle_columns.items()
    assert market_columns["open"] == "REAL"
    assert "open_decimal" not in market_columns


def test_csv_import_keeps_decimal_prices_exact_and_normalizes_timestamp(
    database_path: Path, tmp_path: Path
) -> None:
    content = CSV_HEADER + candle_row(
        open_price="12345678901234567890.1234567890123456789",
        high="12345678901234567891.1234567890123456789",
        low="12345678901234567889.1234567890123456789",
        close="12345678901234567890.5234567890123456789",
    )
    path = write_csv(tmp_path, "prices.csv", content)

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    candle = list_historical_candles(database_path)[0]

    assert summary.accepted_rows == 1
    assert candle["open_decimal"] == "12345678901234567890.1234567890123456789"
    assert candle["high_decimal"] == "12345678901234567891.1234567890123456789"
    assert candle["low_decimal"] == "12345678901234567889.1234567890123456789"
    assert candle["candle_timestamp"] == "2026-01-05T03:45:00Z"


def test_csv_ingestion_supports_bse_registered_instruments(
    database_path: Path, tmp_path: Path
) -> None:
    register_instrument(
        database_path,
        InstrumentInput("BSE", "TCS", "Tata Consultancy Services", "EQUITY"),
    )
    path = write_csv(tmp_path, "bse.csv", CSV_HEADER + candle_row())

    summary = import_ohlcv_csv(database_path, path, "BSE", "TCS", "1m")

    assert summary.accepted_rows == 1
    assert list_historical_candles(database_path, exchange="BSE")[0]["exchange"] == "BSE"


def test_binary_float_price_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="binary floats are rejected"):
        _decimal_value(100.1, "open")
    assert _decimal_value("100.10", "open") == (Decimal("100.10"), "100.1")


@pytest.mark.parametrize("price", ["1e2", "NaN", "Infinity", "not-a-price"])
def test_malformed_decimal_price_is_rejected(
    database_path: Path, tmp_path: Path, price: str
) -> None:
    path = write_csv(tmp_path, "malformed_price.csv", CSV_HEADER + candle_row(open_price=price))

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")

    assert summary.rejected_rows == 1
    assert summary.accepted_rows == 0
    assert "open" in summary.error_summary


@pytest.mark.parametrize(
    ("field_values", "expected_reason"),
    [
        (("100", "99", "98", "99"), "high must be greater than or equal"),
        (("100", "105", "102", "103"), "low must be less than or equal"),
        (("0", "101", "99", "100"), "open must be finite and greater than zero"),
    ],
)
def test_invalid_ohlc_relationships_are_rejected(
    database_path: Path,
    tmp_path: Path,
    field_values: tuple[str, str, str, str],
    expected_reason: str,
) -> None:
    path = write_csv(
        tmp_path,
        "invalid_ohlc.csv",
        CSV_HEADER
        + candle_row(
            open_price=field_values[0],
            high=field_values[1],
            low=field_values[2],
            close=field_values[3],
        ),
    )

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")

    assert summary.accepted_rows == 0
    assert summary.rejected_rows == 1
    assert expected_reason in summary.error_summary
    assert list_historical_candles(database_path) == []


@pytest.mark.parametrize(
    ("volume", "reason"),
    [
        ("-1", "volume must be a non-negative integer"),
        ("1.5", "volume must be a non-negative integer"),
        ("", "volume must be a non-negative integer"),
        ("1e3", "volume must be a non-negative integer"),
        ("9223372036854775808", "volume is outside SQLite's integer range"),
    ],
)
def test_non_integer_or_negative_volume_is_rejected(
    database_path: Path, tmp_path: Path, volume: str, reason: str
) -> None:
    path = write_csv(tmp_path, "invalid_volume.csv", CSV_HEADER + candle_row(volume=volume))

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")

    assert summary.rejected_rows == 1
    assert reason in summary.error_summary


@pytest.mark.parametrize(
    ("interval", "timestamp"),
    [
        ("1m", "2026-01-05T09:15:00+05:30"),
        ("5m", "2026-01-05T09:20:00+05:30"),
        ("15m", "2026-01-05T09:30:00+05:30"),
        ("1h", "2026-01-05T10:15:00+05:30"),
        ("1d", "2026-01-05T00:00:00+05:30"),
    ],
)
def test_all_supported_intervals_accept_aligned_timestamps(
    database_path: Path, tmp_path: Path, interval: str, timestamp: str
) -> None:
    assert interval in SUPPORTED_INTERVALS
    path = write_csv(tmp_path, f"{interval}.csv", CSV_HEADER + candle_row(timestamp))

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", interval)

    assert summary.accepted_rows == 1


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-01-05T09:15:00",
        "2026-01-05T09:16:00+05:30",
        "2026-01-05T09:15:01+05:30",
        "2026-01-05T09:15:00.000001+05:30",
        "2026-01-05T08:15:00+05:30",
    ],
)
def test_invalid_or_misaligned_timestamps_are_rejected(
    database_path: Path, tmp_path: Path, timestamp: str
) -> None:
    path = write_csv(tmp_path, "bad_time.csv", CSV_HEADER + candle_row(timestamp))

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "5m")

    assert summary.rejected_rows == 1
    assert summary.accepted_rows == 0


def test_nonzero_seventh_fractional_digit_is_rejected_without_creating_candle(
    database_path: Path, tmp_path: Path
) -> None:
    path = write_csv(
        tmp_path,
        "submicrosecond.csv",
        CSV_HEADER + candle_row("2026-01-05T09:15:00.0000001+05:30"),
    )

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")

    assert summary.status == "COMPLETED_WITH_ERRORS"
    assert summary.accepted_rows == 0
    assert summary.rejected_rows == 1
    assert "beyond six-digit precision" in summary.error_summary
    assert list_historical_candles(database_path) == []


@pytest.mark.parametrize(
    ("timestamp", "expected_utc"),
    [
        ("2026-01-05T09:15:00.000000+05:30", "2026-01-05T03:45:00Z"),
        ("2026-01-05T09:15:00.000000000+05:30", "2026-01-05T03:45:00Z"),
        ("2026-01-05T03:45:00.0000000Z", "2026-01-05T03:45:00Z"),
    ],
)
def test_zero_fractional_precision_and_timezone_formats_remain_valid(
    database_path: Path, tmp_path: Path, timestamp: str, expected_utc: str
) -> None:
    path = write_csv(tmp_path, "fractional_time.csv", CSV_HEADER + candle_row(timestamp))

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")

    assert summary.accepted_rows == 1
    assert list_historical_candles(database_path)[0]["candle_timestamp"] == expected_utc


def test_tick_alignment_uses_only_exact_non_legacy_instrument_tick(
    database_path: Path, tmp_path: Path
) -> None:
    register_instrument(
        database_path,
        InstrumentInput("NSE", "TICKED", "Ticked instrument", "EQUITY", tick_size=Decimal("0.05")),
    )
    aligned = write_csv(tmp_path, "aligned.csv", CSV_HEADER + candle_row())
    misaligned = write_csv(
        tmp_path,
        "misaligned.csv",
        CSV_HEADER + candle_row(open_price="100.11", high="101.20", low="99.90", close="100.50"),
    )

    accepted = import_ohlcv_csv(database_path, aligned, "NSE", "TICKED", "1m")
    rejected = import_ohlcv_csv(database_path, misaligned, "NSE", "TICKED", "1m")

    assert accepted.accepted_rows == 1
    assert "validated against exact tick size" in accepted.tick_size_validation
    assert rejected.rejected_rows == 1
    assert "not aligned to the instrument tick size" in rejected.error_summary


def test_legacy_real_derived_tick_is_not_used_for_alignment(
    database_path: Path, tmp_path: Path
) -> None:
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """INSERT INTO instruments (
                   exchange, trading_symbol, instrument_name, instrument_type,
                   tick_size, tick_size_decimal
               ) VALUES ('NSE', 'LEGACY', 'Legacy instrument', 'EQUITY', 0.05, '0.05')"""
        )
        connection.commit()
    path = write_csv(
        tmp_path,
        "legacy_tick.csv",
        CSV_HEADER + candle_row(open_price="100.11", high="101.20", low="99.90", close="100.50"),
    )

    summary = import_ohlcv_csv(database_path, path, "NSE", "LEGACY", "1m")

    assert summary.accepted_rows == 1
    assert "legacy REAL-derived tick size" in summary.tick_size_validation


@pytest.mark.parametrize(
    ("exchange", "symbol", "inactive", "message"),
    [
        ("NSE", "UNKNOWN", False, "is not registered"),
        ("NSE", "TCS", True, "is inactive"),
    ],
)
def test_unknown_and_inactive_instruments_fail_without_candles(
    database_path: Path,
    tmp_path: Path,
    exchange: str,
    symbol: str,
    inactive: bool,
    message: str,
) -> None:
    if inactive:
        update_instrument(database_path, exchange, symbol, {"is_active": False})
    path = write_csv(tmp_path, "instrument.csv", CSV_HEADER + candle_row())

    summary = import_ohlcv_csv(database_path, path, exchange, symbol, "1m")

    assert summary.status == "FAILED"
    assert message in summary.error_summary
    assert list_historical_candles(database_path) == []
    assert list_quality_issues(database_path)[0]["issue_type"] == "IMPORT_REJECTED"


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("missing.csv", "timestamp,open,high,low,close\n", "missing required columns"),
        ("duplicate.csv", CSV_HEADER.replace("open,high", "open,open,high"), "duplicate column"),
        ("invalid.csv", "not utf8", "UTF-8"),
        ("empty_rows.csv", CSV_HEADER, "no data rows"),
    ],
)
def test_missing_duplicate_or_malformed_csv_is_recorded_as_failed_import(
    database_path: Path, tmp_path: Path, filename: str, content: str, message: str
) -> None:
    path = tmp_path / filename
    path.write_bytes(content.encode("utf-8") if filename != "invalid.csv" else b"\xff\xfe")

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")

    assert summary.status == "FAILED"
    assert message.lower() in summary.error_summary.lower()
    assert summary.accepted_rows == 0
    assert list_recent_imports(database_path)[0]["original_filename"] == filename


def test_unsupported_interval_is_recorded_as_failed_import(
    database_path: Path, tmp_path: Path
) -> None:
    path = write_csv(tmp_path, "interval.csv", CSV_HEADER + candle_row())

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "2m")

    assert summary.status == "FAILED"
    assert "Unsupported interval" in summary.error_summary


def test_duplicate_rows_keep_first_valid_candle_without_overwrite(
    database_path: Path, tmp_path: Path
) -> None:
    duplicate = "2026-01-05T09:15:00+05:30"
    content = (
        CSV_HEADER
        + candle_row(duplicate)
        + candle_row(duplicate, open_price="200", high="201", low="199", close="200")
    )
    path = write_csv(tmp_path, "in_file_duplicates.csv", content)

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    candles = list_historical_candles(database_path)
    issues = list_quality_issues(database_path)

    assert (summary.total_rows, summary.accepted_rows, summary.skipped_rows) == (2, 1, 1)
    assert summary.duplicate_rows == 1
    assert candles[0]["open_decimal"] == "100.1"
    assert any(issue["source_row_number"] == 3 for issue in issues)


def test_same_file_reimport_is_idempotent_and_records_checksum(
    database_path: Path, tmp_path: Path
) -> None:
    content = CSV_HEADER + candle_row() + candle_row("2026-01-05T09:16:00+05:30")
    path = write_csv(tmp_path, "same.csv", content)

    first = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    second = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    imports = list_recent_imports(database_path)

    assert first.accepted_rows == 2
    assert second.accepted_rows == 0
    assert second.skipped_rows == second.duplicate_rows == 2
    assert second.same_file_reimport is True
    assert first.file_sha256 == second.file_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert Path(imports[0]["original_filename"]).name == "same.csv"
    assert imports[0]["requested_trading_symbol"] == "TCS"
    assert len(list_historical_candles(database_path)) == 2


def test_same_file_bytes_under_different_instrument_scope_are_not_same_file_reimport(
    database_path: Path, tmp_path: Path
) -> None:
    register_instrument(
        database_path,
        InstrumentInput("NSE", "RELIANCE", "Reliance Industries", "EQUITY"),
    )
    content = CSV_HEADER + candle_row(open_price="100", high="101", low="99", close="100")
    path = write_csv(tmp_path, "shared_bytes.csv", content)

    first = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    second = import_ohlcv_csv(database_path, path, "NSE", "RELIANCE", "1m")
    conflicting_path = write_csv(
        tmp_path,
        "conflicting_overlap.csv",
        CSV_HEADER + candle_row(open_price="200", high="201", low="199", close="200"),
    )
    overlapping = import_ohlcv_csv(database_path, conflicting_path, "NSE", "TCS", "1m")
    imports = list_recent_imports(database_path)
    candles = list_historical_candles(database_path)

    assert first.file_sha256 == second.file_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert first.same_file_reimport is False
    assert second.same_file_reimport is False
    assert second.exchange == "NSE"
    assert second.trading_symbol == "RELIANCE"
    assert second.interval == "1m"
    assert second.accepted_rows == 1
    assert second.skipped_rows == second.duplicate_rows == 0
    assert imports[1]["requested_trading_symbol"] == "RELIANCE"
    assert imports[1]["interval"] == "1m"
    assert imports[1]["instrument_id"] != imports[2]["instrument_id"]
    assert overlapping.accepted_rows == 0
    assert overlapping.skipped_rows == overlapping.duplicate_rows == 1
    assert overlapping.same_file_reimport is False
    candles_by_symbol = {row["trading_symbol"]: row for row in candles}
    assert set(candles_by_symbol) == {"TCS", "RELIANCE"}
    assert candles_by_symbol["TCS"]["open_decimal"] == "100"
    assert candles_by_symbol["RELIANCE"]["open_decimal"] == "100"
    assert candles_by_symbol["TCS"]["import_id"] == first.import_id
    assert candles_by_symbol["RELIANCE"]["import_id"] == second.import_id


def test_overlapping_different_file_skips_existing_and_inserts_new_candles(
    database_path: Path, tmp_path: Path
) -> None:
    shared_timestamp = "2026-01-05T09:15:00+05:30"
    first_path = write_csv(tmp_path, "first.csv", CSV_HEADER + candle_row(shared_timestamp))
    second_path = write_csv(
        tmp_path,
        "overlap.csv",
        CSV_HEADER
        + candle_row(shared_timestamp, open_price="200", high="201", low="199", close="200")
        + candle_row("2026-01-05T09:16:00+05:30"),
    )

    import_ohlcv_csv(database_path, first_path, "NSE", "TCS", "1m")
    overlap = import_ohlcv_csv(database_path, second_path, "NSE", "TCS", "1m")

    assert overlap.accepted_rows == 1
    assert overlap.skipped_rows == overlap.duplicate_rows == 1
    assert overlap.same_file_reimport is False
    assert len(list_historical_candles(database_path)) == 2
    assert list_historical_overview(database_path)[0]["candle_count"] == 2


def test_rejected_rows_are_counted_and_recorded_with_row_context(
    database_path: Path, tmp_path: Path
) -> None:
    content = CSV_HEADER + candle_row() + "bad,row\n" + candle_row("2026-01-05T09:16:00+05:30")
    path = write_csv(tmp_path, "partial.csv", content)

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    issue = next(
        issue for issue in list_quality_issues(database_path) if issue["source_row_number"]
    )

    assert summary.status == "COMPLETED_WITH_ERRORS"
    assert summary.total_rows == 3
    assert summary.accepted_rows == 2
    assert summary.rejected_rows == 1
    assert issue["source_row_number"] == 3
    assert issue["issue_type"] == "CSV_ROW_REJECTED"
    assert "timestamp" in issue["description"]


def test_unexpected_batch_failure_rolls_back_all_candles_and_records_failure(
    database_path: Path, tmp_path: Path
) -> None:
    path = write_csv(
        tmp_path,
        "rollback.csv",
        CSV_HEADER + candle_row() + candle_row("2026-01-05T09:16:00+05:30"),
    )
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            CREATE TRIGGER abort_whole_import
            BEFORE INSERT ON historical_candles
            WHEN NEW.candle_timestamp = '2026-01-05T03:46:00Z'
            BEGIN
                SELECT RAISE(ROLLBACK, 'injected batch transaction failure');
            END
            """
        )
        connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected batch transaction failure"):
        import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")

    imports = list_recent_imports(database_path)
    assert list_historical_candles(database_path) == []
    assert imports[0]["status"] == "FAILED"
    assert "Unexpected import failure" in imports[0]["error_summary"]
    with closing(get_connection(database_path)) as connection:
        assert not connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1


def test_row_level_integrity_failure_is_rejected_without_aborting_valid_rows(
    database_path: Path, tmp_path: Path
) -> None:
    path = write_csv(
        tmp_path,
        "row_failure.csv",
        CSV_HEADER
        + candle_row()
        + candle_row("2026-01-05T09:16:00+05:30")
        + candle_row("2026-01-05T09:17:00+05:30"),
    )
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_one_candle
            BEFORE INSERT ON historical_candles
            WHEN NEW.candle_timestamp = '2026-01-05T03:46:00Z'
            BEGIN
                SELECT RAISE(ABORT, 'injected row integrity failure');
            END
            """
        )
        connection.commit()

    summary = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    candles = list_historical_candles(database_path)
    issues = list_quality_issues(database_path)

    assert summary.accepted_rows == 2
    assert summary.rejected_rows == 1
    assert [row["candle_timestamp"] for row in candles] == [
        "2026-01-05T03:47:00Z",
        "2026-01-05T03:45:00Z",
    ]
    assert any(
        issue["source_row_number"] == 3
        and issue["issue_type"] == "CSV_ROW_REJECTED"
        and "injected row integrity failure" in issue["description"]
        for issue in issues
    )


def test_failed_file_read_is_recorded_without_absolute_path(
    database_path: Path, tmp_path: Path
) -> None:
    missing_path = tmp_path / "does_not_exist.csv"

    summary = import_ohlcv_csv(database_path, missing_path, "NSE", "TCS", "1m")

    assert summary.status == "FAILED"
    assert summary.original_filename == "does_not_exist.csv"
    assert summary.file_sha256 is None
    assert str(tmp_path) not in summary.error_summary
    assert list_recent_imports(database_path)[0]["original_filename"] == "does_not_exist.csv"
