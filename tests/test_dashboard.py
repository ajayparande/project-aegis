"""Smoke tests for the Aegis Streamlit dashboard."""

import hashlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

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
