"""Offline credential initialization, dotenv isolation and secret-redaction tests."""

import os
import socket

import pytest

from aegis.broker_credentials import BrokerCredentialError, BrokerCredentials


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("No credential networking")
    )


def load(tmp_path, text="", environment=None):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return BrokerCredentials.from_env(env_file=path, environ=environment or {})


def test_only_actual_available_credentials_initialize_without_zerodha_access_token(tmp_path):
    credentials = load(
        tmp_path,
        """UPSTOX_ACCESS_TOKEN=fake-upstox
ZERODHA_API_KEY=fake-kite-key
ZERODHA_API_SECRET=fake-kite-secret
AEGIS_MODE=RESEARCH_ONLY
AEGIS_ENABLE_LIVE_ORDERS=false
""",
    )
    assert credentials.upstox_access_token == "fake-upstox"
    assert credentials.zerodha_configuration_ready
    assert not credentials.zerodha_connectivity_enabled
    assert credentials.zerodha_connectivity_blocker == "OFFICIAL_KITE_AUTHENTICATION_REQUIRED"
    assert not any(secret in repr(credentials) for secret in ("fake-upstox", "fake-kite"))


def test_upstox_token_requires_no_upstox_key_secret_or_zerodha_credentials(tmp_path):
    credentials = load(tmp_path, "UPSTOX_ACCESS_TOKEN=fake-token\n")
    assert credentials.upstox_access_token == "fake-token"
    assert not credentials.zerodha_configuration_ready
    assert not credentials.zerodha_connectivity_enabled


def test_zerodha_pair_alone_initializes_but_never_connects(tmp_path):
    credentials = load(tmp_path, "ZERODHA_API_KEY=fake-key\nZERODHA_API_SECRET=fake-secret\n")
    assert credentials.upstox_access_token is None
    assert credentials.zerodha_configuration_ready
    assert not credentials.zerodha_connectivity_enabled


def test_environment_zerodha_access_token_does_not_activate_connectivity(tmp_path):
    credentials = load(
        tmp_path,
        "ZERODHA_API_KEY=fake-key\nZERODHA_API_SECRET=fake-secret\n",
        {"ZERODHA_ACCESS_TOKEN": "unvalidated-env-token"},
    )
    assert credentials.zerodha_configuration_ready
    assert not credentials.zerodha_connectivity_enabled


def test_missing_dotenv_initializes_optional_configuration(tmp_path):
    credentials = BrokerCredentials.from_env(env_file=tmp_path / "missing.env", environ={})
    assert credentials.upstox_access_token is None
    assert not credentials.zerodha_connectivity_enabled


def test_literal_quotes_comments_and_process_precedence_without_env_mutation(tmp_path):
    before = dict(os.environ)
    credentials = load(
        tmp_path,
        "export UPSTOX_ACCESS_TOKEN='literal-$TOKEN' # comment\n",
        {"UPSTOX_ACCESS_TOKEN": "process-value"},
    )
    assert credentials.upstox_access_token == "process-value"
    literal = load(tmp_path, 'UPSTOX_ACCESS_TOKEN="literal-$TOKEN" # comment\n')
    assert literal.upstox_access_token == "literal-$TOKEN"
    assert dict(os.environ) == before
    empty = load(tmp_path, "UPSTOX_ACCESS_TOKEN=file-value\n", {"UPSTOX_ACCESS_TOKEN": ""})
    assert empty.upstox_access_token is None


@pytest.mark.parametrize(
    "text,code",
    [
        ("AEGIS_MODE=LIVE\n", "RESEARCH_ONLY_REQUIRED"),
        ("AEGIS_ENABLE_LIVE_ORDERS=true\n", "LIVE_BROKER_ORDERS_DISABLED_REQUIRED"),
        ('UPSTOX_ACCESS_TOKEN="private-secret\n', "INVALID_LOCAL_CREDENTIAL_FILE"),
        (
            "UPSTOX_ACCESS_TOKEN=private-secret\nUPSTOX_ACCESS_TOKEN=duplicate\n",
            "INVALID_LOCAL_CREDENTIAL_FILE",
        ),
        ("UPSTOX_ACCESS_TOKEN=private secret\n", "INVALID_BROKER_CREDENTIAL_FORMAT"),
    ],
)
def test_invalid_input_is_fail_closed_and_errors_never_include_secret(tmp_path, text, code):
    with pytest.raises(BrokerCredentialError) as error:
        load(tmp_path, text)
    assert error.value.code == code
    assert "private" not in str(error.value)
    assert "private" not in repr(error.value)


def test_oversized_dotenv_is_rejected_without_exposure(tmp_path):
    with pytest.raises(BrokerCredentialError, match="INVALID_LOCAL_CREDENTIAL_FILE"):
        load(tmp_path, "UPSTOX_ACCESS_TOKEN=" + "x" * 65537)
