"""Offline-only tests for read-only Upstox HTTP integration."""

from datetime import date
from unittest.mock import Mock

import pytest
import requests

from aegis.providers.upstox_http import (
    UpstoxHistoricalHTTPClient,
    UpstoxHistoricalHTTPError,
)

KEY = "NSE_EQ|INE002A01018"
START = date(2026, 11, 11)
END = date(2026, 11, 13)


def response(status=200, payload=None):
    result = Mock()
    result.status_code = status
    if payload is None:
        payload = {
            "status": "success",
            "data": {
                "candles": [
                    [
                        "2026-11-11T09:15:00+05:30",
                        100,
                        101,
                        99,
                        100,
                        10,
                        0,
                    ]
                ]
            },
        }
    import json

    result.iter_content.return_value = [json.dumps(payload).encode()]
    return result


def client(responses, **kwargs):
    session = Mock(spec=requests.Session)
    session.get.side_effect = responses
    return (
        UpstoxHistoricalHTTPClient(
            "test-token-do-not-log",
            session=session,
            sleep=lambda _: None,
            min_request_interval=0,
            **kwargs,
        ),
        session,
    )


def fetch(instance, **changes):
    params = {
        "instrument_key": KEY,
        "interval": "1m",
        "from_date": START,
        "to_date": END,
    }
    params.update(changes)
    return instance.fetch(**params)


def test_success_uses_v3_get_and_normalizes():
    instance, session = client([response()])
    batch = fetch(instance)
    assert batch.provider_id == "upstox"
    assert len(batch.candles) == 1
    args, kwargs = session.get.call_args
    assert "/v3/historical-candle/" in args[0]
    assert "NSE_EQ%7CINE002A01018" in args[0]
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["headers"]["Authorization"] == "Bearer test-token-do-not-log"


@pytest.mark.parametrize("status", [401, 403, 404, 302])
def test_non_retryable_status_is_sanitized(status):
    instance, session = client([response(status)])
    with pytest.raises(UpstoxHistoricalHTTPError) as exc:
        fetch(instance)
    assert "test-token" not in str(exc.value)
    assert session.get.call_count == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_status(status):
    instance, session = client([response(status), response()])
    assert len(fetch(instance).candles) == 1
    assert session.get.call_count == 2


def test_network_error_is_sanitized():
    instance, session = client(
        [requests.ConnectionError("secret"), requests.Timeout("secret")],
        max_attempts=2,
    )
    with pytest.raises(UpstoxHistoricalHTTPError) as exc:
        fetch(instance)
    assert "secret" not in str(exc.value)
    assert session.get.call_count == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"interval": "5m"},
        {"instrument_key": "bad/key"},
        {"from_date": date(2026, 10, 1)},
        {"to_date": date(2026, 11, 10)},
        {"from_date": "2026-11-11"},
    ],
)
def test_invalid_request_never_calls_network(changes):
    instance, session = client([])
    with pytest.raises((ValueError, TypeError)):
        fetch(instance, **changes)
    session.get.assert_not_called()


def test_daily_interval():
    instance, session = client([response()])
    fetch(instance, interval="1d")
    assert "/days/1/" in session.get.call_args.args[0]


def test_invalid_json_fails_closed():
    bad = response()
    bad.iter_content.return_value = [b"not-json"]
    instance, _ = client([bad])
    with pytest.raises(UpstoxHistoricalHTTPError):
        fetch(instance)


def test_oversized_response_fails_closed():
    bad = response()
    bad.iter_content.return_value = [b"x" * 8_000_001]
    instance, _ = client([bad])
    with pytest.raises(UpstoxHistoricalHTTPError, match="size limit"):
        fetch(instance)


def test_invalid_candle_fails_closed():
    instance, _ = client([response(payload={"status": "success", "data": {"candles": [[1]]}})])
    with pytest.raises(UpstoxHistoricalHTTPError, match="validation"):
        fetch(instance)


def test_m2a_rejects_out_of_range_candle():
    payload = {
        "status": "success",
        "data": {"candles": [["2026-11-10T09:15:00+05:30", 100, 101, 99, 100, 10, 0]]},
    }
    instance, _ = client([response(payload=payload)])
    with pytest.raises(UpstoxHistoricalHTTPError, match="validation"):
        fetch(instance)


def test_m2a_rejects_duplicate_candles():
    row = ["2026-11-11T09:15:00+05:30", 100, 101, 99, 100, 10, 0]
    payload = {
        "status": "success",
        "data": {"candles": [row, row]},
    }
    instance, _ = client([response(payload=payload)])
    with pytest.raises(UpstoxHistoricalHTTPError, match="validation"):
        fetch(instance)


def test_m2a_rejects_future_candle():
    payload = {
        "status": "success",
        "data": {"candles": [["2099-11-11T09:15:00+05:30", 100, 101, 99, 100, 10, 0]]},
    }
    instance, _ = client([response(payload=payload)])
    with pytest.raises(UpstoxHistoricalHTTPError, match="validation"):
        fetch(
            instance,
            from_date=date(2099, 11, 11),
            to_date=date(2099, 11, 11),
        )


def test_m2a_rejects_unaligned_minute():
    payload = {
        "status": "success",
        "data": {"candles": [["2026-11-11T09:15:30+05:30", 100, 101, 99, 100, 10, 0]]},
    }
    instance, _ = client([response(payload=payload)])
    with pytest.raises(UpstoxHistoricalHTTPError, match="validation"):
        fetch(instance)


def test_m2a_rejects_out_of_range_daily_candle():
    payload = {
        "status": "success",
        "data": {"candles": [["2026-11-14T00:00:00+05:30", 100, 101, 99, 100, 10, 0]]},
    }
    instance, _ = client([response(payload=payload)])
    with pytest.raises(UpstoxHistoricalHTTPError, match="validation"):
        fetch(instance, interval="1d")


def test_m2a_valid_candle_still_passes():
    instance, _ = client([response()])
    batch = fetch(instance)
    assert len(batch.candles) == 1


def test_m2a_retry_after_three_seconds():
    waits = []
    first = response(429)
    first.headers = {"Retry-After": "3"}
    session = Mock(spec=requests.Session)
    session.get.side_effect = [first, response()]
    instance = UpstoxHistoricalHTTPClient(
        "test-token-do-not-log",
        session=session,
        sleep=waits.append,
        min_request_interval=0,
    )
    assert len(fetch(instance).candles) == 1
    assert 3.0 in waits
    assert first.close.call_count == 1


@pytest.mark.parametrize("header", ["nan", "inf", "-1", "999999", "invalid"])
def test_m2a_invalid_retry_after_is_bounded(header):
    waits = []
    first = response(429)
    first.headers = {"Retry-After": header}
    session = Mock(spec=requests.Session)
    session.get.side_effect = [first, response()]
    instance = UpstoxHistoricalHTTPClient(
        "test-token-do-not-log",
        session=session,
        sleep=waits.append,
        min_request_interval=0,
    )
    assert len(fetch(instance).candles) == 1
    assert waits == [1.0]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_m2a_invalid_request_interval_rejected(value):
    with pytest.raises(ValueError):
        UpstoxHistoricalHTTPClient(
            "test-token",
            min_request_interval=value,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0])
def test_m2a_invalid_timeout_rejected(value):
    with pytest.raises(ValueError):
        UpstoxHistoricalHTTPClient(
            "test-token",
            timeout=(value, 15),
        )


def test_m2a_network_error_does_not_expose_token():
    token = "SENSITIVE_TEST_TOKEN_123"
    session = Mock(spec=requests.Session)
    session.get.side_effect = requests.ConnectionError(token)
    instance = UpstoxHistoricalHTTPClient(
        token,
        session=session,
        sleep=lambda _: None,
        min_request_interval=0,
        max_attempts=1,
    )
    with pytest.raises(UpstoxHistoricalHTTPError) as exc:
        fetch(instance)
    assert token not in str(exc.value)
    assert exc.value.__cause__ is None


def test_m2a_response_close_transport_error_is_sanitized():
    bad = response(401)
    bad.close.side_effect = requests.ConnectionError("secret")
    instance, _ = client([bad])
    with pytest.raises(UpstoxHistoricalHTTPError) as exc:
        fetch(instance)
    assert "secret" not in str(exc.value)
    assert "401" in str(exc.value)


def test_m2a_concurrent_fetches_are_serialized():
    import threading

    first_started = threading.Event()
    release_first = threading.Event()
    calls = []
    errors = []

    def guarded_get(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            first_started.set()
            if not release_first.wait(timeout=5):
                raise RuntimeError("test synchronization timeout")
        return response()

    session = Mock(spec=requests.Session)
    session.get.side_effect = guarded_get
    instance = UpstoxHistoricalHTTPClient(
        "test-token",
        session=session,
        min_request_interval=0,
    )

    def worker():
        try:
            fetch(instance)
        except Exception as exc:  # noqa: BLE001 - capture worker failures for main-thread assertions
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    try:
        threads[0].start()
        assert first_started.wait(timeout=5)
        threads[1].start()
        assert len(calls) == 1
    finally:
        release_first.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(calls) == 2


def test_m2a_actual_elapsed_request_pacing(monkeypatch):
    """Consecutive requests must be separated by the configured interval."""
    import aegis.providers.upstox_http as http_module

    clock = [100.0]
    request_times = []

    def monotonic():
        return clock[0]

    def sleep(seconds):
        assert 0 <= seconds <= 60
        clock[0] += seconds

    def get(*args, **kwargs):
        request_times.append(clock[0])
        return response()

    monkeypatch.setattr(http_module.time, "monotonic", monotonic)

    session = Mock(spec=requests.Session)
    session.get.side_effect = get

    instance = UpstoxHistoricalHTTPClient(
        "test-token-do-not-log",
        session=session,
        sleep=sleep,
        min_request_interval=2.0,
    )

    first = fetch(instance)
    second = fetch(instance)

    assert len(first.candles) == 1
    assert len(second.candles) == 1
    assert len(request_times) == 2
    assert request_times[1] - request_times[0] >= 2.0
    assert session.get.call_count == 2
