"""Smoke tests for the Aegis Streamlit dashboard."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_FILE = PROJECT_ROOT / "app.py"


def test_dashboard_starts_without_errors() -> None:
    app = AppTest.from_file(str(APP_FILE)).run()

    assert not app.exception


def test_dashboard_displays_project_title() -> None:
    app = AppTest.from_file(str(APP_FILE)).run()

    titles = [element.value for element in app.title]

    assert "🛡️ Project Aegis" in titles


def test_dashboard_displays_research_only_warning() -> None:
    app = AppTest.from_file(str(APP_FILE)).run()

    warnings = [element.value for element in app.warning]

    assert any("RESEARCH ONLY" in warning for warning in warnings)
