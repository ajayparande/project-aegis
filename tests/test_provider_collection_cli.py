"""Offline tests for the historical collection planning CLI."""

import json

import pytest

from aegis.providers.collection_cli import main

_KEY = "NSE_EQ|INE002A01018"


def _args(*extra):
    return [
        "--instrument-key",
        _KEY,
        "--interval",
        "1m",
        "--from-date",
        "2026-11-01",
        "--to-date",
        "2026-11-18",
        *extra,
    ]


def test_cli_plans_contiguous_minute_windows(capsys):
    assert main(_args()) == 0
    document = json.loads(capsys.readouterr().out)

    assert document["mode"] == "RESEARCH_ONLY"
    assert document["network_enabled"] is False
    assert document["storage_enabled"] is False
    assert document["source_use_status"] == "UNVERIFIED"
    assert document["calendar_quality_status"] == "UNASSESSED"
    assert document["planned_requests"] == 3
    assert document["windows"] == [
        {"from_date": "2026-11-01", "to_date": "2026-11-07"},
        {"from_date": "2026-11-08", "to_date": "2026-11-14"},
        {"from_date": "2026-11-15", "to_date": "2026-11-18"},
    ]


def test_cli_daily_window(capsys):
    arguments = [
        "--instrument-key",
        _KEY,
        "--interval",
        "1d",
        "--from-date",
        "2024-01-01",
        "--to-date",
        "2024-12-31",
    ]

    assert main(arguments) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["planned_requests"] == 1


def test_cli_rejects_insufficient_budget():
    with pytest.raises(SystemExit) as captured:
        main(_args("--max-windows", "2"))
    assert captured.value.code == 2


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--instrument-key", "INVALID"),
        ("--interval", "5m"),
        ("--from-date", "2026/11/01"),
        ("--from-date", "20261101"),
        ("--to-date", "2026-10-01"),
        ("--max-windows", "0"),
        ("--max-windows", "21"),
    ],
)
def test_cli_rejects_invalid_requests(flag, value):
    arguments = _args()

    if flag in arguments:
        index = arguments.index(flag)
        arguments[index + 1] = value
    else:
        arguments.extend([flag, value])

    with pytest.raises(SystemExit) as captured:
        main(arguments)

    assert captured.value.code == 2


def test_cli_planning_does_not_call_network(monkeypatch, capsys):
    import requests

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Offline CLI attempted network access")

    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        forbidden_network,
    )

    assert main(_args()) == 0
    assert json.loads(capsys.readouterr().out)["network_enabled"] is False
