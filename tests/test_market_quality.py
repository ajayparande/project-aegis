"""Deterministic tests for local exchange calendars and market-data quality."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import import_ohlcv_csv
from aegis.market_quality import (
    CalendarConfigError,
    CalendarRegistry,
    StalenessPolicy,
    analyze_dataset,
    calculate_expected_candles,
    load_calendar_registry,
)

CSV_HEADER = "timestamp,open,high,low,close,volume\n"
_AS_OF = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "quality.db"
    initialize_database(path)
    register_instrument(
        path,
        InstrumentInput("NSE", "TCS", "Tata Consultancy Services", "EQUITY", segment="NSE"),
    )
    return path


def calendar_entry(
    *,
    exchange: str = "NSE",
    segment: str = "NSE",
    timezone: str = "Asia/Kolkata",
    confidence: str = "CONFIRMED",
    start: str = "2026-01-05",
    end: str = "2026-01-05",
    weekdays: list[int] | None = None,
    sessions: list[dict[str, str]] | None = None,
    holidays: list[dict[str, str]] | None = None,
    special_sessions: list[dict[str, object]] | None = None,
    source: str = "Test fixture; administrator verified",
) -> dict[str, object]:
    return {
        "exchange": exchange,
        "segment": segment,
        "timezone": timezone,
        "confidence": confidence,
        "coverage_start": start,
        "coverage_end": end,
        "source": source,
        "weekdays": [0, 1, 2, 3, 4] if weekdays is None else weekdays,
        "sessions": [{"open": "09:15", "close": "15:30"}] if sessions is None else sessions,
        "holidays": [] if holidays is None else holidays,
        "special_sessions": [] if special_sessions is None else special_sessions,
    }


def write_registry(tmp_path: Path, *entries: dict[str, object], policy=None) -> CalendarRegistry:
    path = tmp_path / "exchange_calendars.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": policy
                or {"potential_after_missing": 1, "stale_after_missing": 3},
                "calendars": list(entries),
            }
        ),
        encoding="utf-8",
    )
    return load_calendar_registry(path)


def write_csv(tmp_path: Path, filename: str, *rows: str) -> Path:
    path = tmp_path / filename
    path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8", newline="")
    return path


def candle_row(timestamp: str, *, price: str = "100") -> str:
    return f"{timestamp},{price},{int(price) + 1},{int(price) - 1},{price},10\n"


def import_rows(database_path: Path, tmp_path: Path, *rows: str) -> None:
    path = write_csv(tmp_path, "quality_data.csv", *rows)
    result = import_ohlcv_csv(database_path, path, "NSE", "TCS", "1m")
    assert result.accepted_rows == len(rows)


def test_default_empty_calendar_is_unavailable_and_never_claims_missing_data(
    database_path: Path, tmp_path: Path
) -> None:
    import_rows(database_path, tmp_path, candle_row("2026-01-05T09:15:00+05:30"))
    config_path = tmp_path / "empty.json"
    config_path.write_text('{"schema_version": 1, "calendars": []}', encoding="utf-8")

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=_AS_OF,
        calendars=load_calendar_registry(config_path),
    )

    assert report.calendar_status == "UNAVAILABLE"
    assert report.calendar_confidence == "UNAVAILABLE"
    assert report.expected_candles is None
    assert report.completeness_percentage is None
    assert report.missing_timestamps == ()
    assert report.staleness_status == "INSUFFICIENT_DATA"
    assert report.overall_status == "INSUFFICIENT_INFORMATION"


def test_exchange_and_segment_calendar_rules_do_not_leak_between_entries(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            exchange="NSE", segment="NSE", sessions=[{"open": "09:15", "close": "10:00"}]
        ),
        calendar_entry(
            exchange="BSE", segment="BSE", sessions=[{"open": "11:00", "close": "12:00"}]
        ),
    )

    nse = registry.resolve("NSE", "NSE")
    bse = registry.resolve("BSE", "BSE")
    assert nse is not None and bse is not None
    assert nse.sessions[0].opens.isoformat(timespec="minutes") == "09:15"
    assert bse.sessions[0].opens.isoformat(timespec="minutes") == "11:00"
    assert registry.resolve("NSE", "BSE") is None


@pytest.mark.parametrize(
    ("interval", "expected_starts"),
    [
        ("1m", ["2026-01-05T03:45:00Z", "2026-01-05T03:46:00Z", "2026-01-05T03:47:00Z"]),
        ("5m", ["2026-01-05T03:45:00Z"]),
        ("15m", ["2026-01-05T03:45:00Z"]),
        ("1h", ["2026-01-05T03:45:00Z"]),
        ("1d", ["2026-01-04T18:30:00Z"]),
    ],
)
def test_expected_candles_respect_stage3_starts_and_partial_close(
    tmp_path: Path, interval: str, expected_starts: list[str]
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(sessions=[{"open": "09:15", "close": "09:18"}]),
    )
    calendar = registry.resolve("NSE", "NSE")
    assert calendar is not None

    scheduled, completed, trading_days, uncovered_days = calculate_expected_candles(
        calendar,
        interval,
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 18, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert [item.timestamp for item in scheduled] == expected_starts
    assert [item.timestamp for item in completed] == expected_starts
    assert trading_days == 1
    assert uncovered_days == 0


def test_in_progress_partial_candle_is_not_expected_as_completed(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(sessions=[{"open": "09:15", "close": "09:37"}]),
    )
    calendar = registry.resolve("NSE", "NSE")
    assert calendar is not None

    scheduled, completed, _, _ = calculate_expected_candles(
        calendar,
        "15m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 36, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert [item.timestamp for item in scheduled] == [
        "2026-01-05T03:45:00Z",
        "2026-01-05T04:00:00Z",
    ]
    assert [item.timestamp for item in completed] == ["2026-01-05T03:45:00Z"]
    assert scheduled[-1].completes_at.isoformat() == "2026-01-05T09:37:00+05:30"


def test_multiple_sessions_breaks_and_early_close_produce_bounded_slots(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            sessions=[{"open": "09:15", "close": "09:25"}, {"open": "10:00", "close": "10:12"}]
        ),
    )
    calendar = registry.resolve("NSE", "NSE")
    assert calendar is not None

    scheduled, completed, _, _ = calculate_expected_candles(
        calendar,
        "5m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 10, 11, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert [item.timestamp for item in scheduled] == [
        "2026-01-05T03:45:00Z",
        "2026-01-05T03:50:00Z",
        "2026-01-05T04:30:00Z",
        "2026-01-05T04:35:00Z",
        "2026-01-05T04:40:00Z",
    ]
    assert len(completed) == 4
    assert scheduled[-1].completes_at.isoformat() == "2026-01-05T10:12:00+05:30"


def test_holidays_and_special_sessions_override_weekday_rules(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            start="2026-01-05",
            end="2026-01-10",
            holidays=[{"date": "2026-01-06", "reason": "verified closure"}],
            special_sessions=[
                {
                    "date": "2026-01-07",
                    "reason": "verified early close",
                    "sessions": [{"open": "09:15", "close": "09:17"}],
                },
                {"date": "2026-01-08", "reason": "verified exceptional closure", "closed": True},
            ],
        ),
    )
    calendar = registry.resolve("NSE", "NSE")
    assert calendar is not None

    monday = calendar.sessions_for(date(2026, 1, 5))
    holiday = calendar.sessions_for(date(2026, 1, 6))
    special = calendar.sessions_for(date(2026, 1, 7))
    exceptional_closure = calendar.sessions_for(date(2026, 1, 8))
    weekend = calendar.sessions_for(date(2026, 1, 10))

    assert monday
    assert holiday == ()
    assert special is not None and special[0].closes.isoformat(timespec="minutes") == "09:17"
    assert exceptional_closure == ()
    assert weekend == ()


def test_unverified_calendar_reports_provisional_not_confirmed_missing(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(confidence="CONFIGURED", sessions=[{"open": "09:15", "close": "09:18"}]),
    )
    import_rows(database_path, tmp_path, candle_row("2026-01-05T09:15:00+05:30"))

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 18, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.calendar_confidence == "CONFIGURED"
    assert report.expected_candles is None
    assert report.provisional_expected_candles == 3
    assert report.missing_timestamps == ()
    assert len(report.provisional_missing_timestamps) == 2
    assert report.completeness_percentage is None
    assert report.overall_status == "INSUFFICIENT_INFORMATION"


def test_complete_confirmed_dataset_receives_transparent_full_completeness(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(sessions=[{"open": "09:15", "close": "09:18"}]),
    )
    import_rows(
        database_path,
        tmp_path,
        candle_row("2026-01-05T09:15:00+05:30"),
        candle_row("2026-01-05T09:16:00+05:30"),
        candle_row("2026-01-05T09:17:00+05:30"),
    )

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 18, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.expected_candles == report.actual_candles == 3
    assert report.actual_trading_days == report.expected_trading_days == 1
    assert report.missing_timestamps == ()
    assert report.completeness_percentage == Decimal("100.00")
    assert report.staleness_status == "FRESH"
    assert report.overall_status == "COMPLETE"


def test_quality_report_finds_missing_unexpected_invalid_and_is_read_only(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(sessions=[{"open": "09:15", "close": "09:18"}]),
    )
    import_rows(
        database_path,
        tmp_path,
        candle_row("2026-01-05T09:15:00+05:30"),
        candle_row("2026-01-05T09:20:00+05:30", price="200"),
    )
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE historical_candles SET high_decimal = '99' WHERE candle_timestamp = ?",
            ("2026-01-05T03:45:00Z",),
        )
        connection.commit()
        before_candles = connection.execute(
            "SELECT id, candle_timestamp, open_decimal, high_decimal, import_id "
            "FROM historical_candles ORDER BY id"
        ).fetchall()
        before_imports = connection.execute(
            "SELECT id, status FROM data_imports ORDER BY id"
        ).fetchall()

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 20, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    with closing(get_connection(database_path)) as connection:
        after_candles = connection.execute(
            "SELECT id, candle_timestamp, open_decimal, high_decimal, import_id "
            "FROM historical_candles ORDER BY id"
        ).fetchall()
        after_imports = connection.execute(
            "SELECT id, status FROM data_imports ORDER BY id"
        ).fetchall()
    assert [tuple(row) for row in after_candles] == [tuple(row) for row in before_candles]
    assert [tuple(row) for row in after_imports] == [tuple(row) for row in before_imports]
    assert report.calendar_status == "FULL"
    assert report.expected_trading_days == 1
    assert report.expected_candles == 3
    assert report.actual_candles == 2
    assert report.missing_timestamps == (
        "2026-01-05T03:45:00Z",
        "2026-01-05T03:46:00Z",
        "2026-01-05T03:47:00Z",
    )
    assert report.unexpected_timestamps == ("2026-01-05T03:50:00Z",)
    assert report.invalid_candle_count == 1
    assert report.completeness_percentage == Decimal("0.00")
    assert report.overall_status == "ERROR"


def test_calendar_holiday_candle_is_a_confirmed_error_even_with_zero_denominator(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            start="2026-01-05",
            end="2026-01-06",
            holidays=[{"date": "2026-01-06", "reason": "verified closure"}],
        ),
    )
    import_rows(database_path, tmp_path, candle_row("2026-01-06T09:15:00+05:30"))

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 6),
        date(2026, 1, 6),
        as_of=datetime(2026, 1, 6, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.expected_candles == 0
    assert report.completeness_percentage is None
    assert report.unexpected_timestamps == ("2026-01-06T03:45:00Z",)
    assert any("configured holiday: verified closure" in reason for reason in report.reasons)
    assert report.overall_status == "ERROR"


@pytest.mark.parametrize(
    ("column", "value"),
    [("candle_timestamp", "invalid-time"), ("volume", -1), ("high_decimal", "99")],
)
def test_quality_report_detects_invalid_stored_candle_values(
    database_path: Path, tmp_path: Path, column: str, value: object
) -> None:
    registry = write_registry(tmp_path, calendar_entry())
    import_rows(database_path, tmp_path, candle_row("2026-01-05T09:15:00+05:30"))
    with closing(get_connection(database_path)) as connection:
        if column == "volume":
            connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(f"UPDATE historical_candles SET {column} = ? WHERE id = 1", (value,))
        connection.commit()

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.invalid_candle_count == 1
    assert report.overall_status == "ERROR"


def test_quality_report_includes_duplicate_and_rejected_import_history(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(tmp_path, calendar_entry())
    duplicate_path = write_csv(
        tmp_path,
        "duplicate.csv",
        candle_row("2026-01-05T09:15:00+05:30"),
        candle_row("2026-01-05T09:15:00+05:30", price="101"),
    )
    import_ohlcv_csv(database_path, duplicate_path, "NSE", "TCS", "1m")
    rejected_path = write_csv(
        tmp_path,
        "rejected.csv",
        "2026-01-05T09:16:00+05:30,0,1,0.5,0.75,10\n",
    )
    import_ohlcv_csv(database_path, rejected_path, "NSE", "TCS", "1m")
    invalid_time_path = write_csv(
        tmp_path,
        "invalid_time.csv",
        candle_row("not-a-timestamp"),
    )
    import_ohlcv_csv(database_path, invalid_time_path, "NSE", "TCS", "1m")

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.duplicate_identity_count == 0
    assert report.duplicate_import_row_count == 1
    assert report.rejected_import_row_count == 1
    assert report.unlocated_rejected_import_row_count == 1


def test_calendar_holiday_data_is_not_inferred_from_unavailable_dates(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(start="2026-01-05", end="2026-01-05"),
    )

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 6),
        as_of=_AS_OF,
        calendars=registry,
    )

    assert report.calendar_status == "PARTIAL"
    assert report.expected_candles is None
    assert report.overall_status == "INSUFFICIENT_INFORMATION"
    assert any("does not cover 1 requested date" in reason for reason in report.reasons)


def test_future_requested_dates_do_not_receive_a_completeness_score(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            start="2026-01-05",
            end="2026-01-06",
            sessions=[{"open": "09:15", "close": "09:18"}],
        ),
    )
    import_rows(database_path, tmp_path, candle_row("2026-01-05T09:15:00+05:30"))

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 6),
        as_of=datetime(2026, 1, 5, 9, 18, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.calendar_status == "FULL"
    assert report.expected_candles is None
    assert report.completeness_percentage is None
    assert report.overall_status == "INSUFFICIENT_INFORMATION"
    assert any("extends beyond as_of" in reason for reason in report.reasons)


def test_staleness_uses_expected_sessions_not_overnight_weekend_or_holiday_time(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            start="2026-01-05",
            end="2026-01-08",
            sessions=[{"open": "09:15", "close": "09:18"}],
            holidays=[{"date": "2026-01-08", "reason": "verified closure"}],
        ),
    )
    import_rows(
        database_path,
        tmp_path,
        candle_row("2026-01-07T09:15:00+05:30"),
        candle_row("2026-01-07T09:16:00+05:30"),
        candle_row("2026-01-07T09:17:00+05:30"),
    )

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 8),
        as_of=datetime(2026, 1, 8, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.staleness_status == "FRESH"
    assert report.missing_completed_candles_since_latest == 0


def test_report_freshness_is_fresh_immediately_before_session_open(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            start="2026-01-05",
            end="2026-01-06",
            sessions=[{"open": "09:15", "close": "09:18"}],
        ),
    )
    import_rows(
        database_path,
        tmp_path,
        candle_row("2026-01-05T09:15:00+05:30"),
        candle_row("2026-01-05T09:16:00+05:30"),
        candle_row("2026-01-05T09:17:00+05:30"),
    )

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 6),
        as_of=datetime(2026, 1, 6, 9, 14, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.expected_candles == 3
    assert report.missing_timestamps == ()
    assert report.staleness_status == "FRESH"
    assert report.missing_completed_candles_since_latest == 0


def test_incomplete_current_candle_is_not_missing_or_stale(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(sessions=[{"open": "09:15", "close": "09:18"}]),
    )
    import_rows(
        database_path,
        tmp_path,
        candle_row("2026-01-05T09:15:00+05:30"),
        candle_row("2026-01-05T09:16:00+05:30"),
    )

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 5, 9, 16, 30, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.expected_candles == 1
    assert report.actual_candles == 2
    assert report.missing_timestamps == ()
    assert report.staleness_status == "FRESH"
    assert report.missing_completed_candles_since_latest == 0


def test_date_range_prefilter_preserves_local_daily_timestamp_boundary(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            start="2026-01-04",
            end="2026-01-05",
            sessions=[{"open": "09:15", "close": "09:18"}],
        ),
    )
    csv_path = write_csv(
        tmp_path,
        "daily_boundary.csv",
        candle_row("2026-01-04T00:00:00+05:30"),
        candle_row("2026-01-05T00:00:00+05:30"),
    )
    result = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1d")
    assert result.accepted_rows == 2

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1d",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=datetime(2026, 1, 6, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )

    assert report.actual_candles == 1
    assert report.first_candle_timestamp == "2026-01-04T18:30:00Z"
    assert report.last_candle_timestamp == "2026-01-04T18:30:00Z"


def test_staleness_thresholds_are_configurable_and_naive_as_of_is_rejected(
    database_path: Path, tmp_path: Path
) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            start="2026-01-05",
            end="2026-01-06",
            sessions=[{"open": "09:15", "close": "09:18"}],
        ),
        policy={"potential_after_missing": 2, "stale_after_missing": 4},
    )
    import_rows(database_path, tmp_path, candle_row("2026-01-05T09:15:00+05:30"))

    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 6),
        as_of=datetime(2026, 1, 6, 9, 16, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )
    assert report.staleness_status == "POTENTIALLY_STALE"
    assert report.missing_completed_candles_since_latest == 3

    stale_report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 6),
        as_of=datetime(2026, 1, 6, 9, 18, tzinfo=ZoneInfo("Asia/Kolkata")),
        calendars=registry,
    )
    assert stale_report.staleness_status == "STALE"
    assert stale_report.missing_completed_candles_since_latest == 5

    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        analyze_dataset(
            database_path,
            "NSE",
            "TCS",
            "1m",
            date(2026, 1, 5),
            date(2026, 1, 6),
            as_of=datetime(2026, 1, 6, 9, 18),
            calendars=registry,
        )


def test_ambiguous_local_calendar_session_is_rejected(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            timezone="America/New_York",
            start="2026-11-01",
            end="2026-11-01",
            weekdays=[6],
            sessions=[{"open": "01:15", "close": "02:30"}],
        ),
    )
    calendar = registry.resolve("NSE", "NSE")
    assert calendar is not None

    with pytest.raises(CalendarConfigError, match="ambiguous"):
        calculate_expected_candles(
            calendar,
            "1m",
            date(2026, 11, 1),
            date(2026, 11, 1),
            as_of=datetime(2026, 11, 1, 12, tzinfo=UTC),
        )


def test_nonexistent_local_calendar_session_is_rejected(tmp_path: Path) -> None:
    registry = write_registry(
        tmp_path,
        calendar_entry(
            timezone="America/New_York",
            start="2026-03-08",
            end="2026-03-08",
            weekdays=[6],
            sessions=[{"open": "02:30", "close": "03:30"}],
        ),
    )
    calendar = registry.resolve("NSE", "NSE")
    assert calendar is not None

    with pytest.raises(CalendarConfigError, match="does not exist"):
        calculate_expected_candles(
            calendar,
            "1m",
            date(2026, 3, 8),
            date(2026, 3, 8),
            as_of=datetime(2026, 3, 8, 12, tzinfo=UTC),
        )


def test_missing_database_and_bad_calendar_config_fail_clearly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Database does not exist"):
        analyze_dataset(
            tmp_path / "missing.db",
            "NSE",
            "TCS",
            "1m",
            date(2026, 1, 5),
            date(2026, 1, 5),
            as_of=_AS_OF,
            calendars=CalendarRegistry(()),
        )

    bad_config = tmp_path / "bad.json"
    bad_config.write_text('{"schema_version": 2, "calendars": []}', encoding="utf-8")
    with pytest.raises(CalendarConfigError, match="schema_version must be 1"):
        load_calendar_registry(bad_config)


def test_staleness_policy_rejects_ambiguous_thresholds() -> None:
    with pytest.raises(ValueError, match="must exceed"):
        StalenessPolicy(potential_after_missing=2, stale_after_missing=2)


def test_read_only_analysis_connection_cannot_write(database_path: Path, tmp_path: Path) -> None:
    import_rows(database_path, tmp_path, candle_row("2026-01-05T09:15:00+05:30"))
    registry = write_registry(tmp_path, calendar_entry())
    report = analyze_dataset(
        database_path,
        "NSE",
        "TCS",
        "1m",
        date(2026, 1, 5),
        date(2026, 1, 5),
        as_of=_AS_OF,
        calendars=registry,
    )
    assert report.actual_candles == 1
    with (
        closing(
            sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        ) as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("DELETE FROM historical_candles")
