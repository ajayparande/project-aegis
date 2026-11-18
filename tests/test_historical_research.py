from __future__ import annotations

import html
import json
from contextlib import closing
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aegis import historical_research
from aegis.ai_agent.models import SecurityViolationError, ToolDefinition, ToolPermission
from aegis.ai_agent.tools import create_default_tool_registry
from aegis.database import get_connection, initialize_database
from aegis.historical_research import (
    discover_historical_instruments,
    inspect_historical_coverage,
    inspect_historical_data_quality,
    retrieve_historical_candles,
)
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.market_quality import CalendarRegistry, load_calendar_registry
from aegis.proposals import ProposalContract, validate_proposal_contract

CSV_HEADER = "timestamp,open,high,low,close,volume\n"
DAY = date(2026, 1, 5)
FIRST_TIMESTAMP = "2026-01-05T03:45:00Z"
LAST_TIMESTAMP = "2026-01-05T03:54:00Z"
REQUEST_START = FIRST_TIMESTAMP
REQUEST_END = "2026-01-05T03:55:00Z"


@pytest.fixture
def research_database(tmp_path: Path) -> Path:
    database_path = tmp_path / "historical_research.db"
    initialize_database(database_path)
    return database_path


@pytest.fixture(autouse=True)
def approved_calendar_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "confirmed_calendar.json"
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
                        "coverage_start": DAY.isoformat(),
                        "coverage_end": DAY.isoformat(),
                        "source": "Synthetic test calendar",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:25"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(path))
    return path


@pytest.fixture
def confirmed_calendar(approved_calendar_configuration: Path) -> CalendarRegistry:
    document = json.loads(approved_calendar_configuration.read_text(encoding="utf-8"))
    document["calendars"][0]["confidence"] = "CONFIRMED"
    approved_calendar_configuration.write_text(json.dumps(document), encoding="utf-8")
    return load_calendar_registry(approved_calendar_configuration)


def _candle_row(minute: int, close: int = 100) -> str:
    return f"2026-01-05T09:{minute:02d}:00+05:30,{close},{close + 1},{close - 1},{close},1000\n"


def _import_candles(
    database_path: Path,
    tmp_path: Path,
    symbol: str,
    *,
    count: int = 10,
    duplicate_first: bool = False,
    verified: bool = True,
    start_minute: int = 15,
) -> int:
    rows = [_candle_row(start_minute + index, 100 + index) for index in range(count)]
    if duplicate_first:
        rows.insert(1, rows[0])
    csv_path = tmp_path / f"{symbol.lower()}_candles.csv"
    csv_path.write_text(CSV_HEADER + "".join(rows), encoding="utf-8", newline="")
    imported = import_ohlcv_csv(database_path, csv_path, "NSE", symbol, "1m")
    if verified:
        declare_import_source_use_status(
            database_path,
            imported.import_id,
            "VERIFIED_PERMITTED",
            declared_by="synthetic test",
            rationale="Synthetic fixture data.",
        )
    return imported.import_id


def _register(database_path: Path, symbol: str) -> None:
    register_instrument(
        database_path,
        InstrumentInput(
            "NSE",
            symbol,
            f"Synthetic {symbol}",
            "EQUITY",
            segment="NSE",
            tick_size=Decimal("0.05"),
        ),
    )


def _retrieve(
    database_path: Path,
    *,
    start_timestamp: str = REQUEST_START,
    end_timestamp: str = REQUEST_END,
    limit: int = 100,
    cursor: str | None = None,
    calendars: CalendarRegistry | None = None,
) -> dict[str, object]:
    return retrieve_historical_candles(
        database_path,
        exchange="NSE",
        trading_symbol="ALPHA",
        interval="1m",
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        limit=limit,
        cursor=cursor,
        calendars=calendars,
    )


def _populated_database(
    database_path: Path, tmp_path: Path, *, count: int = 10, duplicate_first: bool = False
) -> None:
    _register(database_path, "ALPHA")
    _import_candles(
        database_path,
        tmp_path,
        "ALPHA",
        count=count,
        duplicate_first=duplicate_first,
    )


def test_discovery_uses_actual_history_and_supports_bounded_filters(
    research_database: Path, tmp_path: Path, confirmed_calendar: CalendarRegistry
) -> None:
    _register(research_database, "ALPHA")
    _register(research_database, "BETA")
    _register(research_database, "EMPTY")
    _import_candles(research_database, tmp_path, "ALPHA", count=10)
    _import_candles(research_database, tmp_path, "BETA", count=3)

    result = discover_historical_instruments(
        research_database,
        interval="1m",
        exchange="NSE",
        minimum_candles=5,
        quality_status="PASS",
        calendars=confirmed_calendar,
    )

    assert [item["trading_symbol"] for item in result["instruments"]] == ["ALPHA"]
    assert result["instruments_without_historical_data"] == 1
    alpha = result["instruments"][0]
    assert alpha["eligible_for_research"] is True
    assert alpha["source_use_statuses"] == ["VERIFIED_PERMITTED"]


def test_discovery_can_filter_instrument_identity_and_timestamp_range(
    research_database: Path, tmp_path: Path, confirmed_calendar: CalendarRegistry
) -> None:
    _register(research_database, "ALPHA")
    _register(research_database, "BETA")
    _import_candles(research_database, tmp_path, "ALPHA", count=10)
    _import_candles(research_database, tmp_path, "BETA", count=10)

    result = discover_historical_instruments(
        research_database,
        interval="1m",
        trading_symbol="alpha",
        start_timestamp="2026-01-05T03:47:00Z",
        end_timestamp="2026-01-05T03:49:00Z",
        minimum_candles=3,
        calendars=confirmed_calendar,
    )

    assert [item["trading_symbol"] for item in result["instruments"]] == ["ALPHA"]
    assert result["instruments"][0]["candle_count"] == 3


def test_discovery_pages_cover_each_candidate_once_and_bind_filters(
    research_database: Path, tmp_path: Path
) -> None:
    for symbol in ("ALPHA", "BETA", "GAMMA"):
        _register(research_database, symbol)
        _import_candles(research_database, tmp_path, symbol, count=1)

    collected: list[str] = []
    cursor = None
    while True:
        page = discover_historical_instruments(
            research_database,
            interval="1m",
            limit=1,
            cursor=cursor,
        )
        collected.extend(item["trading_symbol"] for item in page["instruments"])
        if not page["has_more"]:
            assert page["complete"] is True
            assert page["completeness_status"] == "COMPLETE"
            assert page["next_cursor"] is None
            break
        assert page["complete"] is False
        assert isinstance(page["next_cursor"], str)
        cursor = page["next_cursor"]

    assert collected == ["ALPHA", "BETA", "GAMMA"]

    first = discover_historical_instruments(
        research_database,
        interval="1m",
        limit=1,
    )
    with pytest.raises(ValueError, match="does not match"):
        discover_historical_instruments(
            research_database,
            interval="1m",
            exchange="BSE",
            limit=1,
            cursor=first["next_cursor"],
        )


def test_bounded_discovery_reports_incomplete_even_when_page_has_no_matches(
    research_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(historical_research, "MAX_DISCOVERY_SCAN", 2)
    for symbol in ("ALPHA", "BETA", "GAMMA"):
        _register(research_database, symbol)
        _import_candles(research_database, tmp_path, symbol, count=1)

    first = discover_historical_instruments(
        research_database,
        interval="1m",
        quality_status="PASS",
        limit=100,
    )
    assert first["instruments"] == []
    assert first["has_more"] is True
    assert first["complete"] is False
    assert first["candidate_scan_truncated"] is True
    assert first["completeness_status"] == "SCAN_LIMIT_REACHED"
    assert isinstance(first["next_cursor"], str)

    final = discover_historical_instruments(
        research_database,
        interval="1m",
        quality_status="PASS",
        limit=100,
        cursor=first["next_cursor"],
    )
    assert final["instruments"] == []
    assert final["has_more"] is False
    assert final["complete"] is True
    assert final["completeness_status"] == "COMPLETE"


def test_coverage_reports_history_and_continuity(
    research_database: Path, tmp_path: Path, confirmed_calendar: CalendarRegistry
) -> None:
    _populated_database(research_database, tmp_path)

    result = inspect_historical_coverage(
        research_database,
        exchange="NSE",
        trading_symbol="ALPHA",
        interval="1m",
        calendars=confirmed_calendar,
    )

    assert result["status"] == "OK"
    assert result["record_count"] == 10
    assert result["first_available_candle"] == FIRST_TIMESTAMP
    assert result["last_available_candle"] == LAST_TIMESTAMP
    assert result["data_quality"]["status"] == "PASS"
    assert result["source_use_statuses"] == ["VERIFIED_PERMITTED"]


def test_empty_range_is_distinct_from_unknown_instrument(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path)
    empty = _retrieve(
        research_database,
        start_timestamp="2026-01-06T03:45:00Z",
        end_timestamp="2026-01-06T03:55:00Z",
    )

    assert empty["status"] == "EMPTY"
    assert empty["record_count"] == 0
    assert empty["candles"] == []
    with pytest.raises(ValueError, match="not registered"):
        retrieve_historical_candles(
            research_database,
            exchange="NSE",
            trading_symbol="MISSING",
            interval="1m",
            start_timestamp=REQUEST_START,
            end_timestamp=REQUEST_END,
        )


def test_retrieval_is_chronological_bounded_and_limited(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path)

    result = _retrieve(
        research_database,
        start_timestamp="2026-01-05T03:46:00Z",
        end_timestamp="2026-01-05T03:49:00Z",
        limit=2,
    )
    candles = result["candles"]

    assert [candle["timestamp"] for candle in candles] == [
        "2026-01-05T03:46:00Z",
        "2026-01-05T03:47:00Z",
    ]
    assert result["available_record_count"] == 4
    assert result["record_count"] == 2
    assert result["truncated"] is True
    assert result["returned_range"] == {
        "start_timestamp": "2026-01-05T03:46:00Z",
        "end_timestamp": "2026-01-05T03:47:00Z",
    }


def test_candle_pages_are_gap_free_stable_and_evidence_bound(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path)
    first = _retrieve(research_database, limit=4)
    retry = _retrieve(research_database, limit=4)
    assert retry["evidence_reference"] == first["evidence_reference"]
    assert retry["next_cursor"] == first["next_cursor"]
    assert first["has_more"] is True
    assert first["truncated"] is True
    assert first["complete"] is False

    _import_candles(
        research_database,
        tmp_path,
        "ALPHA",
        count=1,
        start_minute=55,
    )
    pages = [first]
    cursor = first["next_cursor"]
    while True:
        page = _retrieve(research_database, limit=4, cursor=cursor)
        pages.append(page)
        assert page["truncated"] is page["has_more"]
        assert page["complete"] is not page["has_more"]
        if not page["has_more"]:
            assert page["next_cursor"] is None
            assert page["complete"] is True
            assert page["truncated"] is False
            break
        assert page["complete"] is False
        assert isinstance(page["next_cursor"], str)
        cursor = page["next_cursor"]

    timestamps = [candle["timestamp"] for page in pages for candle in page["candles"]]
    assert timestamps == [f"2026-01-05T03:{minute:02d}:00Z" for minute in range(45, 55)]
    assert len(timestamps) == len(set(timestamps))
    assert all(page["available_record_count"] == 10 for page in pages)
    assert all(
        pages[index]["evidence_reference"] != pages[index + 1]["evidence_reference"]
        for index in range(len(pages) - 1)
    )


def test_candle_cursor_rejects_malformed_and_filter_mismatched_values(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path)
    first = _retrieve(research_database, limit=2)
    cursor = first["next_cursor"]
    assert isinstance(cursor, str)

    with pytest.raises(ValueError, match="malformed"):
        _retrieve(research_database, limit=2, cursor="not a cursor")
    with pytest.raises(ValueError, match="does not match"):
        _retrieve(
            research_database,
            start_timestamp="2026-01-05T03:46:00Z",
            limit=2,
            cursor=cursor,
        )


def test_candle_cursor_rejects_position_outside_requested_range(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path)
    query = {
        "exchange": "NSE",
        "trading_symbol": "ALPHA",
        "interval": "1m",
        "start_timestamp": REQUEST_START,
        "end_timestamp": REQUEST_END,
        "limit": 2,
    }
    cursor = historical_research._encode_cursor(
        "candles",
        query,
        {
            "timestamp": "2026-01-05T03:44:00Z",
            "candle_id": 1,
            "snapshot_max_id": 10,
            "dataset_fingerprint": "0" * 64,
        },
    )

    with pytest.raises(ValueError, match="outside the requested range"):
        _retrieve(research_database, limit=2, cursor=cursor)


def test_source_use_is_rechecked_for_every_candle_page(
    research_database: Path, tmp_path: Path
) -> None:
    _register(research_database, "ALPHA")
    _import_candles(research_database, tmp_path, "ALPHA", count=2)
    later_import_id = _import_candles(
        research_database,
        tmp_path,
        "ALPHA",
        count=2,
        start_minute=17,
    )
    first = _retrieve(research_database, limit=2)
    assert first["status"] == "OK"
    assert first["has_more"] is True

    with closing(get_connection(research_database)) as connection:
        connection.execute(
            "UPDATE data_imports SET source_use_status = 'UNVERIFIED' WHERE id = ?",
            (later_import_id,),
        )
        connection.commit()

    later = _retrieve(
        research_database,
        limit=2,
        cursor=first["next_cursor"],
    )
    assert later["status"] == "DATA_CHANGED"
    assert later["candles"] == []
    assert later["complete"] is False


def test_deleted_rows_after_cursor_return_explicit_dataset_changed_status(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path)
    first = _retrieve(research_database, limit=2)
    assert first["has_more"] is True
    with closing(get_connection(research_database)) as connection:
        connection.execute(
            "DELETE FROM historical_candles WHERE candle_timestamp > ?",
            ("2026-01-05T03:46:00Z",),
        )
        connection.commit()

    later = _retrieve(
        research_database,
        limit=2,
        cursor=first["next_cursor"],
    )
    assert later["status"] == "DATA_CHANGED"
    assert later["candles"] == []
    assert later["evidence_reference"] is None
    assert later["complete"] is False


@pytest.mark.parametrize(
    ("statement", "parameters", "message"),
    [
        (
            "DELETE FROM historical_candles WHERE candle_timestamp = ?",
            ("2026-01-05T03:46:00Z",),
            "cursor candle",
        ),
        (
            "UPDATE historical_candles SET close_decimal = '999' WHERE candle_timestamp = ?",
            ("2026-01-05T03:47:00Z",),
            "changed",
        ),
        (
            "DELETE FROM historical_candles WHERE candle_timestamp = ?",
            ("2026-01-05T03:49:00Z",),
            "changed",
        ),
    ],
)
def test_cursor_fingerprint_detects_deletion_and_candle_edits(
    research_database: Path,
    tmp_path: Path,
    statement: str,
    parameters: tuple[str, ...],
    message: str,
) -> None:
    _populated_database(research_database, tmp_path)
    first = _retrieve(research_database, limit=2)
    assert first["has_more"] is True
    with closing(get_connection(research_database)) as connection:
        connection.execute(statement, parameters)
        connection.commit()

    later = _retrieve(research_database, limit=2, cursor=first["next_cursor"])

    assert later["status"] == "DATA_CHANGED"
    assert later["candles"] == []
    assert later["complete"] is False
    assert message in later["message"]


def test_fingerprint_bound_fails_closed(
    research_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populated_database(research_database, tmp_path)
    monkeypatch.setattr(historical_research, "MAX_RESEARCH_FINGERPRINT_ROWS", 2)

    result = _retrieve(research_database, limit=2)

    assert result["status"] == "VERIFICATION_INCOMPLETE"
    assert result["candles"] == []
    assert result["truncated"] is True
    assert result["complete"] is False
    assert result["next_cursor"] is None


def test_retrieval_uses_one_explicit_read_transaction(
    research_database: Path,
    tmp_path: Path,
    confirmed_calendar: CalendarRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _populated_database(research_database, tmp_path)
    observed_connections: list[int] = []
    list_rows = historical_research.list_historical_candles_in_range
    analyze_quality = historical_research._quality_for_timestamps

    def observe_rows(*args: Any, **kwargs: Any) -> list[dict[str, object]]:
        connection = kwargs["connection"]
        assert connection.in_transaction
        observed_connections.append(id(connection))
        return list_rows(*args, **kwargs)

    def observe_quality(*args: Any, **kwargs: Any) -> dict[str, Any]:
        connection = kwargs["connection"]
        assert connection.in_transaction
        observed_connections.append(id(connection))
        return analyze_quality(*args, **kwargs)

    monkeypatch.setattr(historical_research, "list_historical_candles_in_range", observe_rows)
    monkeypatch.setattr(historical_research, "_quality_for_timestamps", observe_quality)

    result = _retrieve(research_database, limit=4, calendars=confirmed_calendar)

    assert result["status"] == "OK"
    assert len(observed_connections) == 2
    assert len(set(observed_connections)) == 1


def test_candle_evidence_identity_binds_data_provenance_and_page_context() -> None:
    candle = {
        "candle_timestamp": FIRST_TIMESTAMP,
        "open_decimal": "100",
        "high_decimal": "101",
        "low_decimal": "99",
        "close_decimal": "100",
        "volume": 1000,
    }
    source = {
        "import_id": 1,
        "source_id": 2,
        "file_sha256": "a" * 64,
        "source_name": "synthetic",
        "import_status": "COMPLETED",
        "source_use_status": "VERIFIED_PERMITTED",
    }
    identity = {
        "exchange": "NSE",
        "trading_symbol": "ALPHA",
        "interval": "1m",
        "requested_start": REQUEST_START,
        "requested_end": REQUEST_END,
        "candles": [candle],
        "sources": [source],
        "returned_first_timestamp": FIRST_TIMESTAMP,
        "returned_last_timestamp": FIRST_TIMESTAMP,
        "returned_count": 1,
        "available_record_count": 10,
        "page_limit": 2,
        "page_cursor": None,
        "snapshot_max_id": 10,
        "has_more": True,
        "quality_status": "PASS",
        "quality_warnings": [],
    }
    reference = historical_research._evidence_reference(**identity)

    changed_candle = {**candle, "close_decimal": "100.01"}
    assert reference != historical_research._evidence_reference(
        **(identity | {"candles": [changed_candle]})
    )
    changed_import = {**source, "import_id": 3}
    assert reference != historical_research._evidence_reference(
        **(identity | {"sources": [changed_import]})
    )
    changed_status = {**source, "source_use_status": "UNVERIFIED"}
    assert reference != historical_research._evidence_reference(
        **(identity | {"sources": [changed_status]})
    )
    assert reference != historical_research._evidence_reference(
        **(identity | {"page_cursor": "next-page"})
    )
    assert reference != historical_research._evidence_reference(**(identity | {"has_more": False}))
    assert reference != historical_research._evidence_reference(
        **(identity | {"quality_status": "UNKNOWN"})
    )
    assert reference != historical_research._evidence_reference(
        **(identity | {"quality_warnings": ["Calendar coverage unavailable."]})
    )


def test_duplicate_csv_rows_never_produce_duplicate_candles(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path, count=2, duplicate_first=True)
    result = _retrieve(research_database)

    timestamps = [candle["timestamp"] for candle in result["candles"]]
    assert len(timestamps) == 2
    assert len(set(timestamps)) == 2


def test_unverified_source_imports_are_not_returned_as_research_candles(
    research_database: Path, tmp_path: Path
) -> None:
    _register(research_database, "ALPHA")
    _import_candles(research_database, tmp_path, "ALPHA", verified=False)

    result = _retrieve(research_database)

    assert result["status"] == "INELIGIBLE_SOURCE_USE"
    assert result["candles"] == []
    assert result["evidence_reference"] is None


def test_quality_status_pass_fail_and_unknown(
    research_database: Path,
    tmp_path: Path,
    confirmed_calendar: CalendarRegistry,
    approved_calendar_configuration: Path,
) -> None:
    _populated_database(research_database, tmp_path)
    passed = inspect_historical_data_quality(
        research_database,
        exchange="NSE",
        trading_symbol="ALPHA",
        interval="1m",
        start_date=DAY.isoformat(),
        end_date=DAY.isoformat(),
        calendars=confirmed_calendar,
    )
    assert passed["data_quality"]["status"] == "PASS"

    with closing(get_connection(research_database)) as connection:
        connection.execute("UPDATE historical_candles SET high_decimal = '0'")
        connection.commit()
    failed = inspect_historical_data_quality(
        research_database,
        exchange="NSE",
        trading_symbol="ALPHA",
        interval="1m",
        start_date=DAY.isoformat(),
        end_date=DAY.isoformat(),
        calendars=confirmed_calendar,
    )
    assert failed["data_quality"]["status"] == "FAIL"

    with closing(get_connection(research_database)) as connection:
        connection.execute("UPDATE historical_candles SET high_decimal = '111'")
        connection.commit()
    document = json.loads(approved_calendar_configuration.read_text(encoding="utf-8"))
    document["calendars"][0]["confidence"] = "CONFIGURED"
    approved_calendar_configuration.write_text(json.dumps(document), encoding="utf-8")
    unknown = inspect_historical_data_quality(
        research_database,
        exchange="NSE",
        trading_symbol="ALPHA",
        interval="1m",
        start_date=DAY.isoformat(),
        end_date=DAY.isoformat(),
        calendars=None,
    )
    assert unknown["data_quality"]["status"] == "UNKNOWN"


def test_quality_for_no_data_is_unknown_not_pass(
    research_database: Path, confirmed_calendar: CalendarRegistry
) -> None:
    _register(research_database, "ALPHA")
    result = inspect_historical_data_quality(
        research_database,
        exchange="NSE",
        trading_symbol="ALPHA",
        interval="1m",
        start_date=DAY.isoformat(),
        end_date=DAY.isoformat(),
        calendars=confirmed_calendar,
    )
    assert result["status"] == "NO_DATA"
    assert result["data_quality"]["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"interval": "3m"}, "interval"),
        ({"start_timestamp": "2026-01-05T03:45:00", "end_timestamp": REQUEST_END}, "timezone"),
        ({"start_timestamp": REQUEST_END, "end_timestamp": REQUEST_START}, "later"),
        ({"limit": 0}, "between 1"),
        ({"limit": True}, "integer"),
        ({"exchange": 42}, "exchange"),
    ],
)
def test_invalid_research_inputs_are_rejected(
    research_database: Path,
    tmp_path: Path,
    kwargs: dict[str, object],
    error: str,
) -> None:
    _populated_database(research_database, tmp_path)
    arguments: dict[str, object] = {
        "exchange": "NSE",
        "trading_symbol": "ALPHA",
        "interval": "1m",
        "start_timestamp": REQUEST_START,
        "end_timestamp": REQUEST_END,
        "limit": 100,
    }
    arguments.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=error):
        retrieve_historical_candles(research_database, **arguments)


def test_evidence_reference_is_deterministic_reproducible_and_proposal_compatible(
    research_database: Path, tmp_path: Path, confirmed_calendar: CalendarRegistry
) -> None:
    _populated_database(research_database, tmp_path)
    first = _retrieve(research_database, calendars=confirmed_calendar)
    retry = _retrieve(research_database, calendars=confirmed_calendar)

    assert first["evidence_reference"] == retry["evidence_reference"]
    assert first["evidence_verification_status"] == "UNKNOWN"
    proposal = ProposalContract.from_dict(
        {
            "proposal_id": "research-proposal",
            "contract_version": "1.0.0",
            "proposer_id": "test",
            "proposer_version": "1.0",
            "instrument": "NSE:ALPHA",
            "decision": "BUY",
            "confidence": None,
            "rationale": "Synthetic research test.",
            "evidence_references": [first["evidence_reference"]],
            "timestamp": "2026-01-05T03:55:00Z",
            "data_window": f"{REQUEST_START}/{LAST_TIMESTAMP}",
            "data_as_of": LAST_TIMESTAMP,
            "proposed_entry": "100",
        }
    )
    validation = validate_proposal_contract(proposal)
    assert validation.accepted is True
    assert validation.evidence_quality_status == "UNKNOWN"


def test_default_registry_exposes_bounded_read_only_research_tools(
    research_database: Path,
) -> None:
    registry = create_default_tool_registry(research_database)
    definitions = {definition.name: definition for definition in registry.get_definitions()}
    expected = {
        "discover_historical_instruments",
        "inspect_historical_coverage",
        "retrieve_historical_candles",
        "inspect_historical_data_quality",
    }

    assert expected <= definitions.keys()
    assert all(definitions[name].permission == ToolPermission.READ_ONLY for name in expected)
    assert definitions["retrieve_historical_candles"].to_dict()["parameters"]


def test_tool_permission_denial_and_malicious_arguments_fail_closed(
    research_database: Path, tmp_path: Path
) -> None:
    _populated_database(research_database, tmp_path)
    registry = create_default_tool_registry(research_database)
    with pytest.raises(SecurityViolationError, match="non-permitted permission level"):
        registry.register(
            ToolDefinition("mutate_data", "not permitted", (), ToolPermission.MUTATION),
            lambda *, context: {"ok": True},
        )

    rejected = registry.execute(
        "retrieve_historical_candles",
        {
            "exchange": "NSE",
            "trading_symbol": "ALPHA",
            "interval": "1m",
            "start_timestamp": "2026-01-05T03:45:00; DROP TABLE instruments",
            "end_timestamp": REQUEST_END,
        },
        {"database_path": research_database, "calendars": None},
    )
    assert '"status": "REJECTED"' in rejected
    assert "start_timestamp" in rejected
    with closing(get_connection(research_database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1


def test_registry_historical_read_does_not_change_database(
    research_database: Path, tmp_path: Path, confirmed_calendar: CalendarRegistry
) -> None:
    _populated_database(research_database, tmp_path)
    registry = create_default_tool_registry(research_database, calendars=confirmed_calendar)
    context = {"database_path": research_database, "calendars": confirmed_calendar}
    with closing(get_connection(research_database)) as connection:
        before = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("instruments", "historical_candles", "data_imports", "schema_metadata")
        )

    output = registry.execute(
        "retrieve_historical_candles",
        {
            "exchange": "NSE",
            "trading_symbol": "ALPHA",
            "interval": "1m",
            "start_timestamp": REQUEST_START,
            "end_timestamp": REQUEST_END,
            "limit": 10,
        },
        context,
    )
    assert '"status": "OK"' in output
    with closing(get_connection(research_database)) as connection:
        after = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("instruments", "historical_candles", "data_imports", "schema_metadata")
        )
    assert before == after


_HISTORICAL_TOOLS = (
    "discover_historical_instruments",
    "inspect_historical_coverage",
    "retrieve_historical_candles",
    "inspect_historical_data_quality",
)


def _historical_tool_response(
    database_path: Path, tool_name: str, injected: CalendarRegistry
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "exchange": "NSE",
        "trading_symbol": "ALPHA",
        "interval": "1m",
    }
    if tool_name == "inspect_historical_data_quality":
        arguments.update(start_date=DAY.isoformat(), end_date=DAY.isoformat())
    else:
        arguments.update(start_timestamp=REQUEST_START, end_timestamp=REQUEST_END)
    registry = create_default_tool_registry(database_path, calendars=injected)
    output = registry.execute(
        tool_name, arguments, {"database_path": database_path, "calendars": injected}
    )
    content = html.unescape(output.split("\n", 2)[2].rsplit("\n</data_block>", 1)[0])
    return json.loads(content)


@pytest.mark.parametrize("tool_name", _HISTORICAL_TOOLS)
@pytest.mark.parametrize("confidence", ["CONFIGURED", "CONFIRMED"])
def test_historical_tools_use_approved_confidence_not_injected_registry(
    research_database: Path,
    tmp_path: Path,
    approved_calendar_configuration: Path,
    tool_name: str,
    confidence: str,
) -> None:
    _populated_database(research_database, tmp_path)
    document = json.loads(approved_calendar_configuration.read_text(encoding="utf-8"))
    document["calendars"][0]["confidence"] = confidence
    approved_calendar_configuration.write_text(json.dumps(document), encoding="utf-8")
    approved = load_calendar_registry(approved_calendar_configuration)
    opposite = "CONFIRMED" if confidence == "CONFIGURED" else "CONFIGURED"
    injected = replace(approved, calendars=(replace(approved.calendars[0], confidence=opposite),))

    response = _historical_tool_response(research_database, tool_name, injected)

    assert response["status"] == "OK"
    eligible = confidence == "CONFIRMED"
    if tool_name == "discover_historical_instruments":
        assert response["instruments"][0]["eligible_for_research"] is eligible
        assert response["eligible_count"] == int(eligible)
        assert (response["instruments"][0]["data_quality_status"] == "PASS") is eligible
    elif tool_name == "retrieve_historical_candles":
        assert (response["quality_status"] == "PASS") is eligible
        assert response["source_use_statuses"] == ["VERIFIED_PERMITTED"]
    else:
        assert response["data_quality"]["calendar_confidence"] == confidence
        assert (response["data_quality"]["status"] == "PASS") is eligible
        assert (response["eligibility_status"] == "PASS") is eligible


@pytest.mark.parametrize("tool_name", _HISTORICAL_TOOLS)
@pytest.mark.parametrize(
    "configuration", ["missing", "malformed", "inaccessible", "absent", "UNVERIFIED"]
)
def test_historical_tools_fail_closed_without_approved_calendar(
    research_database: Path,
    tmp_path: Path,
    confirmed_calendar: CalendarRegistry,
    approved_calendar_configuration: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    configuration: str,
) -> None:
    _populated_database(research_database, tmp_path)
    if configuration == "missing":
        monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "missing.json"))
    elif configuration == "malformed":
        approved_calendar_configuration.write_text("not JSON", encoding="utf-8")
    elif configuration == "inaccessible":
        monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path))
    else:
        document = json.loads(approved_calendar_configuration.read_text(encoding="utf-8"))
        if configuration == "absent":
            document["calendars"][0]["exchange"] = "BSE"
        else:
            # UNVERIFIED is not a valid calendar confidence. Keep constructor validation strict.
            document["calendars"][0]["confidence"] = "UNVERIFIED"
        approved_calendar_configuration.write_text(json.dumps(document), encoding="utf-8")

    response = _historical_tool_response(research_database, tool_name, confirmed_calendar)

    assert response["status"] == "REJECTED"
    assert "approved" in response["error"].lower()
    assert str(tmp_path) not in response["error"]
    assert "candles" not in response
    assert "eligible_for_research" not in response
    assert "data_quality" not in response


def test_historical_service_reloads_approved_calendar_for_each_call(
    research_database: Path,
    tmp_path: Path,
    confirmed_calendar: CalendarRegistry,
    approved_calendar_configuration: Path,
) -> None:
    _populated_database(research_database, tmp_path)
    arguments = {
        "exchange": "NSE",
        "trading_symbol": "ALPHA",
        "interval": "1m",
        "start_date": DAY.isoformat(),
        "end_date": DAY.isoformat(),
    }
    document = json.loads(approved_calendar_configuration.read_text(encoding="utf-8"))
    document["calendars"][0]["confidence"] = "CONFIGURED"
    approved_calendar_configuration.write_text(json.dumps(document), encoding="utf-8")
    unapproved_path = tmp_path / "unapproved.json"
    unapproved_document = {
        **document,
        "calendars": [{**document["calendars"][0], "confidence": "CONFIRMED"}],
    }
    unapproved_path.write_text(json.dumps(unapproved_document), encoding="utf-8")
    forged = load_calendar_registry(unapproved_path)
    for injected in (confirmed_calendar, forged, None):
        result = inspect_historical_data_quality(research_database, **arguments, calendars=injected)
        assert result["data_quality"]["calendar_confidence"] == "CONFIGURED"
        assert result["data_quality"]["status"] != "PASS"
        assert result["eligibility_status"] != "PASS"
        discovery = discover_historical_instruments(
            research_database, interval="1m", calendars=injected
        )
        assert discovery["eligible_count"] == 0
    document["calendars"][0]["confidence"] = "CONFIRMED"
    approved_calendar_configuration.write_text(json.dumps(document), encoding="utf-8")
    assert (
        inspect_historical_data_quality(research_database, **arguments)["eligibility_status"]
        == "PASS"
    )
