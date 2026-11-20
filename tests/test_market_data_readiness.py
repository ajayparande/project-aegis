"""Deterministic tests for Phase 2 market-data evidence-readiness assessment."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import aegis.market_data_readiness as readiness_module
from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.market_data_readiness import (
    assess_evidence_readiness,
    assess_multiple_instruments,
)
from aegis.market_quality import (
    CalendarConfigError,
    CalendarRegistry,
    load_calendar_registry,
)

CSV_HEADER = "timestamp,open,high,low,close,volume\n"
_AS_OF = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_START = date(2026, 1, 5)
_END = date(2026, 1, 5)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "readiness.db"
    initialize_database(path)
    register_instrument(
        path,
        InstrumentInput("NSE", "TCS", "Tata Consultancy Services", "EQUITY", segment="NSE"),
    )
    return path


@pytest.fixture
def valid_calendar_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CalendarRegistry:
    """Create a CONFIRMED calendar for NSE covering the test date."""
    path = tmp_path / "calendars.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {"potential_after_missing": 1, "stale_after_missing": 3},
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": "2026-01-05",
                        "coverage_end": "2026-01-05",
                        "source": "Test fixture; administrator verified",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "15:30"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(path))
    return load_calendar_registry(path)


@pytest.fixture
def provisional_calendar_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> CalendarRegistry:
    """Create a CONFIGURED calendar for NSE covering the test date."""
    path = tmp_path / "calendars_provisional.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {"potential_after_missing": 1, "stale_after_missing": 3},
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIGURED",
                        "coverage_start": "2026-01-05",
                        "coverage_end": "2026-01-05",
                        "source": "Test fixture; not verified",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "15:30"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(path))
    return load_calendar_registry(path)


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


def write_csv(tmp_path: Path, filename: str, *rows: str) -> Path:
    path = tmp_path / filename
    path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8", newline="")
    return path


def write_calendar_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    filename: str,
    exchange: str = "NSE",
    confidence: str = "CONFIRMED",
    coverage_start: str = "2026-01-05",
    coverage_end: str = "2026-01-05",
) -> CalendarRegistry:
    path = tmp_path / filename
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {"potential_after_missing": 1, "stale_after_missing": 3},
                "calendars": [
                    {
                        "exchange": exchange,
                        "segment": exchange,
                        "timezone": "Asia/Kolkata",
                        "confidence": confidence,
                        "coverage_start": coverage_start,
                        "coverage_end": coverage_end,
                        "source": "Synthetic test fixture",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "15:30"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(path))
    return load_calendar_registry(path)


def full_session_rows(*, last_minute: int = 374) -> tuple[str, ...]:
    start = datetime(2026, 1, 5, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    return tuple(
        candle_row((start + timedelta(minutes=minute)).isoformat())
        for minute in range(last_minute + 1)
    )


def full_session_import(
    database_path: Path,
    tmp_path: Path,
    *,
    filename: str = "full_session.csv",
    last_minute: int = 374,
    declare_permitted: bool = True,
) -> int:
    csv_path = write_csv(tmp_path, filename, *full_session_rows(last_minute=last_minute))
    summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
    if declare_permitted:
        declare_import_source_use_status(
            database_path,
            summary.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test",
            rationale="Synthetic test fixture only",
        )
    return summary.import_id


class TestEvidenceReadinessBasic:
    """Basic readiness assessment behavior."""

    def test_instrument_not_registered(self, database_path: Path) -> None:
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "UNKNOWN",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
        )
        assert result.readiness_status == "BLOCKED"
        assert "INSTRUMENT_NOT_REGISTERED" in result.reason_codes
        assert result.dataset_quality is None

    def test_instrument_inactive(self, database_path: Path) -> None:
        from aegis.instruments import update_instrument

        update_instrument(database_path, "NSE", "TCS", {"is_active": False})
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
        )
        assert result.readiness_status == "BLOCKED"
        assert "INSTRUMENT_INACTIVE" in result.reason_codes

    def test_calendar_unavailable(
        self, database_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calendars = write_calendar_registry(
            tmp_path, monkeypatch, filename="bse_only.json", exchange="BSE"
        )
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=calendars,
        )
        assert result.readiness_status == "BLOCKED"
        assert "CALENDAR_UNAVAILABLE" in result.reason_codes

    def test_no_historical_data(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry
    ) -> None:
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )
        assert result.readiness_status == "BLOCKED"
        assert "NO_HISTORICAL_DATA" in result.reason_codes
        assert result.dataset_quality is not None
        assert result.dataset_quality.actual_candles == 0

    def test_fully_qualified_dataset(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        import_id = full_session_import(database_path, tmp_path)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=import_id,
        )
        assert result.readiness_status == "READY"
        assert result.reason_codes == ()
        assert result.dataset_quality is not None
        assert result.dataset_quality.actual_candles == 375


class TestEvidenceReadinessCalendarStatus:
    """Calendar confidence and coverage checks."""

    def test_calendar_not_confirmed(
        self, database_path: Path, provisional_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        import_id = full_session_import(database_path, tmp_path)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=provisional_calendar_registry,
            source_import_id=import_id,
        )
        assert result.readiness_status == "BLOCKED"
        assert "CALENDAR_NOT_CONFIRMED" in result.reason_codes

    def test_calendar_incomplete_coverage(
        self,
        database_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calendars = write_calendar_registry(
            tmp_path,
            monkeypatch,
            filename="partial.json",
            coverage_start="2026-01-06",
            coverage_end="2026-01-06",
        )

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=calendars,
        )
        assert result.readiness_status == "BLOCKED"
        assert "CALENDAR_INCOMPLETE_COVERAGE" in result.reason_codes

    def test_calendar_loading_failure_blocks(
        self,
        database_path: Path,
        valid_calendar_registry: CalendarRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_loading(*, expected_registry: CalendarRegistry | None = None) -> CalendarRegistry:
            raise CalendarConfigError("synthetic calendar load failure")

        monkeypatch.setattr(
            "aegis.market_data_readiness.load_approved_calendar_registry", fail_loading
        )
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )

        assert result.readiness_status == "BLOCKED"
        assert result.reason_codes == ("CALENDAR_LOAD_ERROR",)


class TestEvidenceReadinessDataQuality:
    """Dataset quality checks."""

    def test_invalid_candles_detected(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        csv_content = CSV_HEADER + "2026-01-05T09:15:00+05:30,100.10,99.90,101.20,100.50,1000\n"
        csv_path = tmp_path / "invalid.csv"
        csv_path.write_text(csv_content, encoding="utf-8", newline="")

        summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
        declare_import_source_use_status(
            database_path,
            summary.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test",
            rationale="Test data",
        )

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )
        assert result.readiness_status == "BLOCKED"
        assert "NO_HISTORICAL_DATA" in result.reason_codes
        assert "REJECTED_IMPORT_ROWS" in result.reason_codes

    def test_single_valid_candle_in_full_session_is_blocked(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        csv_path = write_csv(tmp_path, "sparse.csv", candle_row())
        summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
        declare_import_source_use_status(
            database_path,
            summary.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test",
            rationale="Test data",
        )

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=datetime(2026, 1, 5, 20, 0, tzinfo=UTC),
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )
        assert result.readiness_status == "BLOCKED"
        assert "INCOMPLETE_CANDLES" in result.reason_codes
        assert result.dataset_quality is not None
        assert result.dataset_quality.expected_candles == 375
        assert result.dataset_quality.actual_candles == 1

    def test_stale_candles_block(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        import_id = full_session_import(database_path, tmp_path, last_minute=371)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=import_id,
        )

        assert result.readiness_status == "BLOCKED"
        assert result.dataset_quality is not None
        assert result.dataset_quality.staleness_status == "STALE"
        assert "DATASET_STALE" in result.reason_codes

    def test_duplicate_import_rows_block(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        rows = full_session_rows()
        csv_path = write_csv(tmp_path, "duplicate.csv", *rows, rows[0])
        summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
        declare_import_source_use_status(
            database_path,
            summary.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test",
            rationale="Synthetic test fixture only",
        )

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )

        assert result.readiness_status == "BLOCKED"
        assert "DUPLICATE_CANDLES_PRESENT" in result.reason_codes


class TestEvidenceReadinessSourceUse:
    """Source-use permission checks."""

    def test_missing_source_import_id_is_blocked(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        import_id = full_session_import(database_path, tmp_path)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=import_id + 100,
        )

        assert result.readiness_status == "BLOCKED"
        assert "IMPORT_ID_NOT_FOUND" in result.reason_codes

    def test_invalid_source_import_id_is_blocked(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        full_session_import(database_path, tmp_path)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=0,
        )

        assert result.readiness_status == "BLOCKED"
        assert "DATASET_ANALYSIS_ERROR" in result.reason_codes

    def test_unverified_source_use(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        csv_path = write_csv(tmp_path, "test.csv", candle_row())
        summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )
        assert result.readiness_status == "BLOCKED"
        assert "SOURCE_USE_NOT_PERMITTED" in result.reason_codes

    def test_prohibited_source_use(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        csv_path = write_csv(tmp_path, "test.csv", candle_row())
        summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
        declare_import_source_use_status(
            database_path,
            summary.import_id,
            "PROHIBITED",
            declared_by="test",
            rationale="Restricted data",
        )

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )
        assert result.readiness_status == "BLOCKED"
        assert "SOURCE_USE_NOT_PERMITTED" in result.reason_codes

    def test_verified_source_use_allows_readiness(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        import_id = full_session_import(database_path, tmp_path)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=import_id,
        )
        assert result.readiness_status == "READY"
        assert result.reason_codes == ()

    def test_omitting_import_id_does_not_bypass_source_use_validation(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        full_session_import(database_path, tmp_path, declare_permitted=False)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )

        assert result.readiness_status == "BLOCKED"
        assert "SOURCE_USE_NOT_PERMITTED" in result.reason_codes
        assert "SOURCE_PROVENANCE_INVALID" in result.reason_codes

    def test_verified_status_without_matching_declaration_is_blocked(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        import_id = full_session_import(database_path, tmp_path, declare_permitted=False)
        with closing(get_connection(database_path)) as connection:
            connection.execute(
                "UPDATE data_imports SET source_use_status = 'VERIFIED_PERMITTED' WHERE id = ?",
                (import_id,),
            )
            connection.commit()

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )

        assert result.readiness_status == "BLOCKED"
        assert "SOURCE_PROVENANCE_INVALID" in result.reason_codes

    def test_complete_permitted_test_provenance_qualifies_without_import_filter(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        full_session_import(database_path, tmp_path)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )

        assert result.readiness_status == "READY"
        assert result.reason_codes == ()

    def test_concurrent_source_update_uses_one_read_snapshot(
        self,
        database_path: Path,
        valid_calendar_registry: CalendarRegistry,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import_id = full_session_import(database_path, tmp_path)
        with closing(get_connection(database_path)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"

        readonly_connections: list[sqlite3.Connection] = []
        original_get_readonly_connection = readiness_module.get_readonly_connection

        def track_readonly_connection(path: Path) -> sqlite3.Connection:
            connection = original_get_readonly_connection(path)
            readonly_connections.append(connection)
            return connection

        transaction_states: list[bool] = []
        original_read_source_imports = readiness_module._read_source_imports

        def interleave_source_update(
            connection: sqlite3.Connection,
            *,
            instrument_id: int,
            interval: str,
            completed_timestamps: frozenset[str],
            first_timestamp: str,
            last_timestamp: str,
        ) -> tuple[dict[str, object], ...]:
            transaction_states.append(connection.in_transaction)
            declare_import_source_use_status(
                database_path,
                import_id,
                "PROHIBITED",
                declared_by="test",
                rationale="Deterministic concurrent-update regression",
            )
            return original_read_source_imports(
                connection,
                instrument_id=instrument_id,
                interval=interval,
                completed_timestamps=completed_timestamps,
                first_timestamp=first_timestamp,
                last_timestamp=last_timestamp,
            )

        monkeypatch.setattr(readiness_module, "get_readonly_connection", track_readonly_connection)
        monkeypatch.setattr(readiness_module, "_read_source_imports", interleave_source_update)

        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=import_id,
        )

        assert result.readiness_status == "READY"
        assert result.reason_codes == ()
        assert transaction_states == [True]
        assert len(readonly_connections) == 1
        with closing(get_connection(database_path)) as connection:
            current_status = connection.execute(
                "SELECT source_use_status FROM data_imports WHERE id = ?", (import_id,)
            ).fetchone()[0]
        assert current_status == "PROHIBITED"
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            readonly_connections[0].execute("SELECT 1")


class TestEvidenceReadinessBatchAssessment:
    """Multiple-instrument assessment."""

    def test_assess_multiple_instruments(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        register_instrument(
            database_path,
            InstrumentInput("NSE", "INFY", "Infosys", "EQUITY", segment="NSE"),
        )

        csv_path_1 = write_csv(tmp_path, "tcs.csv", candle_row())
        summary_1 = import_ohlcv_csv(database_path, csv_path_1, "NSE", "TCS", "1m")
        declare_import_source_use_status(
            database_path,
            summary_1.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test",
            rationale="Test",
        )

        specs = [
            ("NSE", "TCS", "1m", _START, _END),
            ("NSE", "INFY", "1m", _START, _END),
        ]

        results = assess_multiple_instruments(
            database_path,
            specs,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )

        assert len(results) == 2
        assert results[0].readiness_status == "BLOCKED"
        assert "INCOMPLETE_CANDLES" in results[0].reason_codes
        assert results[1].readiness_status == "BLOCKED"
        assert "NO_HISTORICAL_DATA" in results[1].reason_codes


class TestEvidenceReadinessInputValidation:
    """Input parameter validation."""

    def test_invalid_date_range(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry
    ) -> None:
        with pytest.raises(ValueError, match="start_date must not be after end_date"):
            assess_evidence_readiness(
                database_path,
                "NSE",
                "TCS",
                "1m",
                date(2026, 1, 10),
                date(2026, 1, 5),
                as_of=_AS_OF,
                calendars=valid_calendar_registry,
            )

    def test_missing_timezone_in_as_of(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry
    ) -> None:
        with pytest.raises(ValueError, match="as_of must be timezone-aware"):
            assess_evidence_readiness(
                database_path,
                "NSE",
                "TCS",
                "1m",
                _START,
                _END,
                as_of=datetime(2026, 1, 5, 10, 0),
                calendars=valid_calendar_registry,
            )

    def test_non_datetime_as_of(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry
    ) -> None:
        with pytest.raises(TypeError, match="as_of must be a timezone-aware datetime"):
            assess_evidence_readiness(
                database_path,
                "NSE",
                "TCS",
                "1m",
                _START,
                _END,
                as_of="2026-01-05T10:00:00Z",  # type: ignore
                calendars=valid_calendar_registry,
            )


class TestEvidenceReadinessReportStructure:
    """Evidence report structure and immutability."""

    def test_report_is_frozen_dataclass(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry
    ) -> None:
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )
        with pytest.raises(AttributeError):
            result.readiness_status = "READY"  # type: ignore

    def test_report_contains_all_required_fields(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry
    ) -> None:
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )
        assert hasattr(result, "exchange")
        assert hasattr(result, "trading_symbol")
        assert hasattr(result, "interval")
        assert hasattr(result, "start_date")
        assert hasattr(result, "end_date")
        assert hasattr(result, "as_of")
        assert hasattr(result, "readiness_status")
        assert hasattr(result, "reason_codes")
        assert hasattr(result, "reason_descriptions")
        assert hasattr(result, "dataset_quality")

    def test_reason_codes_and_descriptions_aligned(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry
    ) -> None:
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )
        assert len(result.reason_codes) == len(result.reason_descriptions)


class TestEvidenceReadinessFailures:
    def test_analysis_error_fails_closed(
        self,
        database_path: Path,
        valid_calendar_registry: CalendarRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_analysis(*args: object, **kwargs: object) -> object:
            raise RuntimeError("synthetic analysis failure")

        readonly_connections: list[sqlite3.Connection] = []
        original_get_readonly_connection = readiness_module.get_readonly_connection

        def track_readonly_connection(path: Path) -> sqlite3.Connection:
            connection = original_get_readonly_connection(path)
            readonly_connections.append(connection)
            return connection

        monkeypatch.setattr(readiness_module, "get_readonly_connection", track_readonly_connection)
        monkeypatch.setattr(readiness_module, "analyze_dataset", fail_analysis)
        result = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
        )

        assert result.readiness_status == "BLOCKED"
        assert result.dataset_quality is None
        assert "DATASET_ANALYSIS_ERROR" in result.reason_codes
        assert any("RuntimeError" in detail for detail in result.reason_descriptions)
        assert len(readonly_connections) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            readonly_connections[0].execute("SELECT 1")


class TestEvidenceReadinessDeterminism:
    """Deterministic and idempotent behavior."""

    def test_repeated_assessment_is_identical(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        csv_path = write_csv(tmp_path, "test.csv", candle_row())
        summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
        declare_import_source_use_status(
            database_path,
            summary.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test",
            rationale="Test",
        )

        result_1 = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )

        result_2 = assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )

        assert result_1.readiness_status == result_2.readiness_status
        assert result_1.reason_codes == result_2.reason_codes
        assert result_1.reason_descriptions == result_2.reason_descriptions

    def test_no_database_mutation(
        self, database_path: Path, valid_calendar_registry: CalendarRegistry, tmp_path: Path
    ) -> None:
        csv_path = write_csv(tmp_path, "test.csv", candle_row())
        summary = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
        declare_import_source_use_status(
            database_path,
            summary.import_id,
            "VERIFIED_PERMITTED",
            declared_by="test",
            rationale="Test",
        )

        tables = (
            "instruments",
            "data_imports",
            "historical_candles",
            "data_quality_issues",
            "import_source_use_history",
        )
        with closing(get_connection(database_path)) as conn:
            counts_before = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
            import_status_before = conn.execute(
                "SELECT id, source_use_status FROM data_imports ORDER BY id"
            ).fetchall()

        assess_evidence_readiness(
            database_path,
            "NSE",
            "TCS",
            "1m",
            _START,
            _END,
            as_of=_AS_OF,
            calendars=valid_calendar_registry,
            source_import_id=summary.import_id,
        )

        with closing(get_connection(database_path)) as conn:
            counts_after = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }
            import_status_after = conn.execute(
                "SELECT id, source_use_status FROM data_imports ORDER BY id"
            ).fetchall()

        assert counts_before == counts_after
        assert import_status_before == import_status_after
