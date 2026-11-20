import logging
import traceback

import pytest

from aegis.config import AppMode, Settings
from aegis.logging_config import StructuredFormatter


def test_project_starts_in_research_only_mode(monkeypatch):
    monkeypatch.delenv("AEGIS_MODE", raising=False)

    settings = Settings.from_env()
    assert settings.mode == AppMode.RESEARCH_ONLY


@pytest.mark.parametrize("mode", ["RESEARCH_ONLY", "research_only"])
def test_valid_research_only_mode_is_accepted(monkeypatch, mode):
    monkeypatch.setenv("AEGIS_MODE", mode)

    settings = Settings.from_env()

    assert settings.mode == AppMode.RESEARCH_ONLY


@pytest.mark.parametrize("mode", ["LIVE", "PAPER", ""])
def test_unsupported_or_empty_mode_is_rejected(monkeypatch, mode):
    monkeypatch.setenv("AEGIS_MODE", mode)

    with pytest.raises(ValueError, match="Unsupported AEGIS_MODE"):
        Settings.from_env()


def test_invalid_mode_does_not_expose_configured_value(monkeypatch, caplog):
    sentinel = "PHASE1_INVALID_MODE_SENTINEL_7f4b2d"
    monkeypatch.setenv("AEGIS_MODE", sentinel)
    logger = logging.getLogger("aegis.config_redaction_test")

    with caplog.at_level(logging.ERROR, logger=logger.name), pytest.raises(ValueError) as error:
        try:
            Settings.from_env()
        except ValueError:
            logger.exception("Configuration rejected")
            raise

    exception = error.value
    assert exception.args == (
        "Unsupported AEGIS_MODE. Only RESEARCH_ONLY is permitted in Phase 1.",
    )
    assert exception.__cause__ is None
    assert exception.__suppress_context__

    formatted_traceback = "".join(traceback.format_exception(exception))
    formatted_log = StructuredFormatter().format(caplog.records[0])
    assert sentinel not in formatted_traceback
    assert sentinel not in caplog.text
    assert sentinel not in formatted_log


def test_invalid_log_level_does_not_echo_configured_value(monkeypatch):
    secret_value = "password=PHASE1_FAKE_SECRET"
    monkeypatch.setenv("AEGIS_LOG_LEVEL", secret_value)

    with pytest.raises(ValueError) as error:
        Settings.from_env()

    assert secret_value not in str(error.value)
    assert "PHASE1_FAKE_SECRET" not in str(error.value)


@pytest.mark.parametrize("calendar_path", ["", "../private/calendar.json"])
def test_unsafe_calendar_path_is_rejected(monkeypatch, calendar_path):
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", calendar_path)

    with pytest.raises(ValueError, match="AEGIS_CALENDAR_CONFIG_PATH"):
        Settings.from_env()


def test_configuration_representation_does_not_expose_credentials(monkeypatch):
    fake_secret = "PHASE1_FAKE_CREDENTIAL"
    monkeypatch.setenv("ZERODHA_API_SECRET", fake_secret)
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", fake_secret)

    settings = Settings.from_env()

    assert fake_secret not in repr(settings)
