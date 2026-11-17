"""Isolated calendar-trust regressions for the existing AI quality-summary tool."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aegis.ai_agent.tools import (
    _tool_get_dataset_quality_summary,
    create_default_tool_registry,
)
from aegis.database import initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.market_quality import CalendarRegistry, load_calendar_registry

_DAY = "2026-01-05"


@pytest.fixture
def quality_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, CalendarRegistry]:
    """Provide permitted synthetic candles and an administrator-selected calendar."""
    database = tmp_path / "quality.db"
    initialize_database(database)
    register_instrument(
        database,
        InstrumentInput("NSE", "TEST", "Synthetic equity", "EQUITY", segment="NSE"),
    )
    csv_path = tmp_path / "candles.csv"
    rows = ["timestamp,open,high,low,close,volume"]
    rows.extend(f"{_DAY}T09:{minute}:00+05:30,100,101,99,100,1000" for minute in range(15, 18))
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    imported = import_ohlcv_csv(database, csv_path, "NSE", "TEST", "1m")
    assert imported.status == "COMPLETED" and imported.accepted_rows == 3
    declare_import_source_use_status(
        database,
        imported.import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic test fixture",
        rationale="Synthetic candles only; no real source-use rights are inferred.",
    )
    calendar_path = tmp_path / "approved-calendar.json"
    calendar_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": _DAY,
                        "coverage_end": _DAY,
                        "source": "Synthetic administrator-selected calendar",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "09:18"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(calendar_path))
    return database, calendar_path, load_calendar_registry(calendar_path)


def _summary(
    database: Path,
    calendars: CalendarRegistry | None = None,
    *,
    start_date: str = _DAY,
) -> dict[str, Any]:
    return _tool_get_dataset_quality_summary(
        "TEST",
        start_date,
        _DAY,
        interval="1m",
        context={"database_path": database, "calendars": calendars},
    )


@pytest.mark.parametrize("supply_registry", [False, True])
def test_quality_summary_uses_approved_calendar_with_or_without_context_registry(
    quality_environment: tuple[Path, Path, CalendarRegistry], supply_registry: bool
) -> None:
    database, _, registry = quality_environment
    result = _summary(database, registry if supply_registry else None)
    assert result["calendar_status"] == "FULL"
    assert result["calendar_confidence"] == "CONFIRMED"
    assert result["actual_candles"] == result["expected_candles"] == 3
    assert Decimal(result["completeness_percentage"]) == Decimal("100.00")
    assert result["passes_backtest_gate"] is True


@pytest.mark.parametrize("configuration", ["missing", "malformed", "empty"])
def test_quality_summary_rejects_unavailable_approved_calendar_before_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configuration: str
) -> None:
    calendar_path = tmp_path / "unavailable-calendar.json"
    if configuration == "malformed":
        calendar_path.write_text("not JSON", encoding="utf-8")
    elif configuration == "empty":
        calendar_path.write_text('{"schema_version":1,"calendars":[]}', encoding="utf-8")
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(calendar_path))
    database = tmp_path / "must-not-be-opened.db"
    with pytest.raises(ValueError, match="Approved calendar evidence is unavailable or invalid"):
        _summary(database)
    assert not database.exists()


def test_quality_summary_rejects_caller_selected_calendar(
    quality_environment: tuple[Path, Path, CalendarRegistry], tmp_path: Path
) -> None:
    database, approved_path, _ = quality_environment
    alternate_path = tmp_path / "caller-selected-calendar.json"
    alternate_path.write_bytes(approved_path.read_bytes())
    caller_registry = load_calendar_registry(alternate_path)
    with pytest.raises(ValueError, match="Approved calendar evidence is unavailable or invalid"):
        _summary(database, caller_registry)


def test_quality_summary_rejects_calendar_changed_since_context_was_loaded(
    quality_environment: tuple[Path, Path, CalendarRegistry],
) -> None:
    database, calendar_path, original = quality_environment
    document = json.loads(calendar_path.read_text(encoding="utf-8"))
    document["calendars"][0]["source"] = "Changed synthetic calendar"
    calendar_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Approved calendar evidence is unavailable or invalid"):
        _summary(database, original)


@pytest.mark.parametrize("forge_context", [False, True])
def test_quality_summary_cannot_promote_configured_calendar_to_confirmed(
    quality_environment: tuple[Path, Path, CalendarRegistry], forge_context: bool
) -> None:
    database, calendar_path, _ = quality_environment
    document = json.loads(calendar_path.read_text(encoding="utf-8"))
    document["calendars"][0]["confidence"] = "CONFIGURED"
    calendar_path.write_text(json.dumps(document), encoding="utf-8")
    configured = load_calendar_registry(calendar_path)
    forged = replace(
        configured,
        calendars=(replace(configured.calendars[0], confidence="CONFIRMED"),),
    )
    with pytest.raises(ValueError, match="full CONFIRMED approved calendar coverage"):
        _summary(database, forged if forge_context else None)


def test_quality_summary_rejects_partial_approved_coverage(
    quality_environment: tuple[Path, Path, CalendarRegistry],
) -> None:
    database, _, _ = quality_environment
    with pytest.raises(ValueError, match="full CONFIRMED approved calendar coverage"):
        _summary(database, start_date="2026-01-04")


def test_quality_summary_reports_controlled_failure_through_public_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "missing-calendar.json"))
    database = tmp_path / "must-not-be-opened.db"
    registry = create_default_tool_registry(database)
    output = registry.execute(
        "get_dataset_quality_summary",
        {"symbol": "TEST", "start_date": _DAY, "end_date": _DAY, "interval": "1m"},
        {"database_path": database},
    )
    assert '"status": "ERROR"' in output
    assert "passes_backtest_gate" not in output
    assert not database.exists()


def test_quality_tool_fix_does_not_relax_empty_registry_validation() -> None:
    with pytest.raises(ValueError, match="at least one ExchangeCalendar"):
        CalendarRegistry(())
