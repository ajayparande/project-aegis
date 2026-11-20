"""Four isolated checks for the offline readiness command."""

import json
from unittest.mock import Mock

import pytest

from aegis.broker_status import main


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr("aegis.broker_credentials.PROJECT_ROOT", tmp_path)
    for name in ("UPSTOX_ACCESS_TOKEN", "ZERODHA_API_KEY", "ZERODHA_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setenv("AEGIS_ENABLE_LIVE_ORDERS", "false")
    guards = []
    for target in (
        "socket.socket.connect",
        "socket.create_connection",
        "requests.Session.request",
        "sqlite3.connect",
        "builtins.input",
        "getpass.getpass",
    ):
        guard = Mock(side_effect=AssertionError("Readiness must be offline and noninteractive"))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
    return guards


def test_credentials_present(tmp_path, capsys):
    (tmp_path / ".env").write_text(
        "UPSTOX_ACCESS_TOKEN=fake-token\nZERODHA_API_KEY=fake-key\nZERODHA_API_SECRET=fake-secret\n"
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "upstox_access_token": "PRESENT",
        "zerodha_api_key": "PRESENT",
        "zerodha_api_secret": "PRESENT",
        "upstox_market_data_adapter": "AVAILABLE",
        "zerodha_market_data_adapter": "NOT_IMPLEMENTED",
        "aegis_mode": "RESEARCH_ONLY",
        "live_broker_orders": "DISABLED",
    }


def test_credentials_missing_and_unavailable_adapter(monkeypatch, capsys):
    monkeypatch.setattr("aegis.broker_status.import_module", Mock(side_effect=ImportError))
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    for name in ("upstox_access_token", "zerodha_api_key", "zerodha_api_secret"):
        assert report[name] == "MISSING"
    assert report["upstox_market_data_adapter"] == "UNAVAILABLE"


def test_secret_values_never_printed_even_on_configuration_error(tmp_path, capsys):
    secrets = ("synthetic-private-token", "synthetic-private-key", "synthetic-private-secret")
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            f"{name}={value}"
            for name, value in zip(
                ("UPSTOX_ACCESS_TOKEN", "ZERODHA_API_KEY", "ZERODHA_API_SECRET"),
                secrets,
            )
        )
    )
    assert main() == 0
    path.write_text('UPSTOX_ACCESS_TOKEN="synthetic-private-token\n')
    assert main() == 2
    captured = capsys.readouterr()
    assert all(value not in captured.out + captured.err for value in secrets)
    assert "INVALID_LOCAL_CREDENTIAL_FILE" in captured.out
    assert not captured.err


def test_no_network_prompts_or_database_access(isolated, tmp_path, monkeypatch, capsys):
    database = tmp_path / "existing.db"
    database.write_bytes(b"untouched database sentinel")
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(database))
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["live_broker_orders"] == "DISABLED"
    for guard in isolated:
        guard.assert_not_called()
    assert database.read_bytes() == b"untouched database sentinel"
