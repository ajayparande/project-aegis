"""Tests for Aegis logging and secret redaction."""

import json

from aegis.logging_config import StructuredFormatter, redact_secrets


def test_api_key_is_redacted() -> None:
    result = redact_secrets("api_key=MY_SECRET_123")

    assert "MY_SECRET_123" not in result
    assert "[REDACTED]" in result


def test_password_is_redacted() -> None:
    result = redact_secrets("password:MyPassword123")

    assert "MyPassword123" not in result
    assert "[REDACTED]" in result


def test_bearer_token_is_redacted() -> None:
    result = redact_secrets("Authorization: Bearer abc123.secret456")

    assert "abc123.secret456" not in result
    assert "[REDACTED]" in result


def test_normal_message_is_preserved() -> None:
    message = "Aegis research process started"

    assert redact_secrets(message) == message


def test_structured_formatter_returns_json() -> None:
    import logging

    record = logging.LogRecord(
        name="aegis",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="api_key=TEST_SECRET",
        args=(),
        exc_info=None,
    )

    output = StructuredFormatter().format(record)
    payload = json.loads(output)

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "aegis"
    assert "TEST_SECRET" not in output
    assert "[REDACTED]" in payload["message"]
