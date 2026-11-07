"""Smoke tests for the Aegis Streamlit dashboard."""

import hashlib
from contextlib import closing
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import import_ohlcv_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_FILE = PROJECT_ROOT / "app.py"
DEVELOPMENT_FILES = (
    PROJECT_ROOT / "data" / "aegis.db",
    PROJECT_ROOT / "data" / "aegis.db-wal",
    PROJECT_ROOT / "data" / "aegis.db-shm",
    PROJECT_ROOT / "logs" / "aegis.log",
)


@pytest.fixture
def isolated_app_paths(monkeypatch, tmp_path):
    """Route dashboard database and log writes into pytest's temporary directory."""

    database_path = tmp_path / "aegis.db"
    log_path = tmp_path / "aegis.log"
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(log_path))
    return database_path, log_path


def _development_file_snapshot() -> dict[Path, tuple[bool, str | None]]:
    """Return content hashes for default development database, sidecars, and log."""

    snapshot = {}
    for path in DEVELOPMENT_FILES:
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        snapshot[path] = (path.exists(), digest)
    return snapshot


def test_dashboard_starts_without_errors(isolated_app_paths) -> None:
    app = AppTest.from_file(str(APP_FILE)).run()

    assert not app.exception
    assert any("No historical candles are stored yet" in item.value for item in app.info)


def test_dashboard_displays_project_title(isolated_app_paths) -> None:
    app = AppTest.from_file(str(APP_FILE)).run()

    titles = [element.value for element in app.title]

    assert "🛡️ Project Aegis" in titles


def test_dashboard_displays_research_only_warning(isolated_app_paths) -> None:
    app = AppTest.from_file(str(APP_FILE)).run()

    warnings = [element.value for element in app.warning]

    assert any("RESEARCH ONLY" in warning for warning in warnings)


def test_dashboard_does_not_modify_development_database_or_log(isolated_app_paths) -> None:
    database_path, _ = isolated_app_paths
    before = _development_file_snapshot()

    app = AppTest.from_file(str(APP_FILE)).run()

    assert not app.exception
    assert database_path.exists()
    assert _development_file_snapshot() == before


def test_dashboard_shows_instrument_master_summary_and_details(isolated_app_paths) -> None:
    database_path, _ = isolated_app_paths
    initialize_database(database_path)
    register_instrument(
        database_path,
        InstrumentInput(
            exchange="NSE",
            trading_symbol="TCS",
            instrument_name="Tata Consultancy Services",
            instrument_type="EQUITY",
        ),
    )

    app = AppTest.from_file(str(APP_FILE)).run(timeout=10)

    assert not app.exception
    assert "Instrument Master" in [item.value for item in app.subheader]
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["Total Instruments"] == "1"
    assert metrics["Active Instruments"] == "1"
    assert metrics["Inactive Instruments"] == "0"
    assert len(app.dataframe) == 1


def test_dashboard_shows_historical_csv_import_and_candle_overview(
    isolated_app_paths, tmp_path
) -> None:
    database_path, _ = isolated_app_paths
    initialize_database(database_path)
    register_instrument(
        database_path,
        InstrumentInput(
            exchange="NSE",
            trading_symbol="TCS",
            instrument_name="Tata Consultancy Services",
            instrument_type="EQUITY",
        ),
    )
    csv_path = tmp_path / "historical.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-05T09:15:00+05:30,100.10,101.20,99.90,100.50,1000\n",
        encoding="utf-8",
    )
    result = import_ohlcv_csv(database_path, csv_path, "NSE", "TCS", "1m")
    assert result.accepted_rows == 1
    with closing(get_connection(database_path)) as connection:
        instrument_id = connection.execute(
            "SELECT id FROM instruments WHERE trading_symbol = 'TCS'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO market_data (
                   instrument_id, interval, candle_timestamp, open, high, low, close
               ) VALUES (?, '1d', '2026-01-04T00:00:00Z', 100, 101, 99, 100)""",
            (instrument_id,),
        )
        connection.commit()

    app = AppTest.from_file(str(APP_FILE)).run(timeout=10)

    assert not app.exception
    assert "Historical Data / CSV Import" in [item.value for item in app.subheader]
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["Historical Candles"] == "1"
    assert metrics["Instrument / Interval Series"] == "1"
    assert metrics["Legacy REAL Rows Preserved"] == "1"
    assert any("excluded from the exact-decimal CSV view" in item.value for item in app.warning)
    assert any("Recent CSV import history" in item.value for item in app.markdown)
    assert len(app.dataframe) >= 3


def test_dashboard_shows_unconfigured_calendar_without_claiming_completeness(
    isolated_app_paths,
) -> None:
    database_path, _ = isolated_app_paths
    initialize_database(database_path)
    register_instrument(
        database_path,
        InstrumentInput(
            exchange="NSE",
            trading_symbol="TCS",
            instrument_name="Tata Consultancy Services",
            instrument_type="EQUITY",
            segment="NSE",
        ),
    )
    app = AppTest.from_file(str(APP_FILE)).run(timeout=10)

    assert not app.exception
    assert "Market Data Quality and Exchange Sessions" in [item.value for item in app.subheader]
    assert any("Exchange calendars are unavailable" in item.value for item in app.warning)
    analyze_button = next(
        item for item in app.button if item.label == "Analyze market-data quality"
    )
    analyze_button.click().run(timeout=10)
    assert not app.exception
    assert any("NSE:TCS" in item.value for item in app.markdown)
    assert any(
        item.label == "Completeness" and item.value == "Insufficient information"
        for item in app.metric
    )
