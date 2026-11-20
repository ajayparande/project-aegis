"""Offline Upstox V3 protocol, validation, transport and safety tests."""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import traceback
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from aegis.database import get_readonly_connection
from aegis.forward_runtime import ForwardRuntimeService, Quote
from aegis.instruments import update_instrument
from aegis.providers._upstox_v3_descriptor import DESCRIPTOR_BYTES
from aegis.providers.upstox_market_stream import (
    AUTHORIZE_URL,
    ConnectionState,
    StreamMode,
    UpstoxMarketStreamAdapter,
    UpstoxMarketStreamClient,
    UpstoxStreamError,
    _connect_socket,
    feed_message_class,
)
from tests.test_forward_runtime import _setup

KEY = "NSE_EQ|INE002A01018"
START = datetime(2026, 1, 5, 3, 45, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


def _frame(at=START, *, kind=1, price=100.1, quantity=2, key=KEY, mode=0, status=2, event_at=None):
    message = feed_message_class()()
    message.type = kind
    message.currentTs = (at - EPOCH) // timedelta(milliseconds=1)
    if kind == 2:
        message.marketInfo.segmentStatus["NSE_EQ"] = status
    else:
        feed = message.feeds[key]
        feed.requestMode = mode
        feed.ltpc.ltp = price
        feed.ltpc.ltq = quantity
        feed.ltpc.ltt = ((event_at or at) - EPOCH) // timedelta(milliseconds=1)
    return message.SerializeToString()


def _setup_observer(tmp_path, monkeypatch, **options):
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "observe.log"))
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "runtime_calendar.json"))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    database, calendars, (instrument,), runtime = _setup(
        tmp_path, initial_cash=Decimal(10000), observation_only=True
    )
    update_instrument(
        database, "NSE", "SYNTH", {"isin": KEY.split("|")[1], "instrument_token": KEY}
    )
    clock = Clock()
    adapter = UpstoxMarketStreamAdapter(runtime, {KEY: instrument}, clock=clock, **options)
    return database, calendars, instrument, runtime, clock, adapter


def _synchronize(adapter, clock):
    adapter.connected()
    assert adapter.receive_frame(_frame(clock.now, kind=2), received_at=clock.now) == ()
    assert adapter.receive_frame(_frame(clock.now, kind=0), received_at=clock.now) == ()


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "runtime_calendar.json"))
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "observe.log"))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")

    def forbidden(*args, **kwargs):
        raise AssertionError("A test attempted real network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_binary_decoding_precision_three_clocks_and_immutable_wire_evidence(tmp_path, monkeypatch):
    assert (
        hashlib.sha256(DESCRIPTOR_BYTES).hexdigest()
        == "6765c281ddba1c0bec39c262e3231736f01036b76c4031bf2e4dd81fea6acf5a"
    )
    database, _, _, runtime, clock, adapter = _setup_observer(tmp_path, monkeypatch)
    _synchronize(adapter, clock)
    received = START + timedelta(milliseconds=2)
    clock.now = START + timedelta(milliseconds=3)
    (outcome,) = adapter.receive_frame(_frame(), received_at=received)
    assert outcome.runtime_result.accepted and not outcome.runtime_result.duplicate
    assert outcome.observation.price == Decimal("100.1")
    assert outcome.observation.price_binary64_hex == (100.1).hex()
    assert outcome.observation.event_at == START
    assert outcome.observation.received_at == received
    assert outcome.observation.processing_started_at == clock.now
    with closing(get_readonly_connection(database)) as connection:
        row = connection.execute(
            "SELECT quote_timestamp,received_at,price_decimal FROM forward_quotes"
        ).fetchone()
        assert row["price_decimal"] == "100.1" and row["quote_timestamp"] != row["received_at"]
        evidence = json.loads(
            connection.execute(
                "SELECT payload_json FROM decision_journal_health WHERE kind='UPSTOX_V3_OBSERVATION'"
            ).fetchone()[0]
        )["details"]
        assert evidence["price_binary64_hex"] == (100.1).hex()
        assert evidence["price_conversion"] == "BINARY64_SHORTEST_REPR_V1"
        assert evidence["transport_mode"] == "OFFLINE_TEST"
        assert evidence["processing_started_at"] != evidence["received_at"]
    assert runtime.observation_only


def test_runtime_rejects_provider_quotes_without_receipt_and_wire_evidence(tmp_path, monkeypatch):
    database, _, instrument, runtime, _, _ = _setup_observer(tmp_path, monkeypatch)
    result = runtime.process_quote(
        Quote(instrument, START.isoformat(), 1, Decimal("100.1"), 2, "missing-evidence")
    )
    assert not result.accepted and "Observation evidence" in result.reason
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted=1").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"price": float("nan")},
        {"price": float("inf")},
        {"price": -1},
        {"price": 0},
        {"price": 100.10000000000001},
        {"price": 100.11},
        {"quantity": -1},
        {"key": "NSE_EQ|INE009A01021"},
        {"mode": 1},
        {"kind": 9},
        {"at": START + timedelta(seconds=1)},
        {"event_at": START - timedelta(seconds=31)},
    ],
)
def test_invalid_frames_never_reach_runtime(tmp_path, monkeypatch, changes):
    database, _, _, _, clock, adapter = _setup_observer(tmp_path, monkeypatch)
    _synchronize(adapter, clock)
    with pytest.raises(UpstoxStreamError):
        adapter.receive_frame(_frame(**changes), received_at=clock.now)
    with closing(get_readonly_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM forward_quotes").fetchone()[0] == 0


@pytest.mark.parametrize("frame", [b"", b"not-protobuf", b"\x12\xff", "{}", b"x" * 1_048_577])
def test_invalid_wire_framing(tmp_path, monkeypatch, frame):
    _, _, _, _, clock, adapter = _setup_observer(tmp_path, monkeypatch)
    _synchronize(adapter, clock)
    with pytest.raises(UpstoxStreamError):
        adapter.receive_frame(frame, received_at=clock.now)


def test_duplicate_out_of_order_and_restart_use_durable_runtime_cursor(tmp_path, monkeypatch):
    database, calendars, instrument, runtime, clock, adapter = _setup_observer(
        tmp_path, monkeypatch
    )
    _synchronize(adapter, clock)
    assert adapter.receive_frame(_frame(), received_at=clock.now)[0].runtime_result.accepted
    duplicate = adapter.receive_frame(_frame(), received_at=clock.now)[0]
    assert duplicate.runtime_result.duplicate
    clock.now += timedelta(seconds=2)
    stale_order = adapter.receive_frame(
        _frame(clock.now, event_at=START - timedelta(seconds=1)), received_at=clock.now
    )[0]
    assert not stale_order.runtime_result.accepted
    runtime = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    restored = UpstoxMarketStreamAdapter(runtime, {KEY: instrument}, clock=clock)
    _synchronize(restored, clock)
    replay = restored.receive_frame(_frame(clock.now, event_at=START), received_at=clock.now)[0]
    assert replay.runtime_result.duplicate
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_quotes WHERE accepted=1").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_scheduler_events").fetchone()[0] == 0
        )


def test_stale_disconnected_closed_market_and_exchange_session_are_not_holds(tmp_path, monkeypatch):
    database, _, _, _, clock, adapter = _setup_observer(tmp_path, monkeypatch)
    _synchronize(adapter, clock)
    adapter.receive_frame(_frame(), received_at=clock.now)
    clock.now += timedelta(seconds=31)
    assert adapter.poll_health() == ConnectionState.STALE
    with pytest.raises(UpstoxStreamError, match="resynchronization"):
        adapter.receive_frame(_frame(clock.now), received_at=clock.now)
    adapter.disconnected()
    assert adapter.state == ConnectionState.DISCONNECTED
    _synchronize(adapter, clock)
    adapter.receive_frame(_frame(clock.now, kind=2, status=3), received_at=clock.now)
    with pytest.raises(UpstoxStreamError, match="normal market"):
        adapter.receive_frame(_frame(clock.now), received_at=clock.now)
    clock.now = START + timedelta(minutes=31)
    _synchronize(adapter, clock)
    rejected = adapter.receive_frame(_frame(clock.now), received_at=clock.now)[0]
    assert not rejected.runtime_result.accepted and "session" in rejected.runtime_result.reason
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM decision_journal_events WHERE state='HOLD'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


@pytest.mark.parametrize(
    "mapping",
    [
        {"NSE_INDEX|Nifty 50": 1},
        {"BSE_EQ|INE002A01018": 1},
        {KEY: 999},
        {KEY: True},
        {KEY: 1, "NSE_EQ|INE009A01021": 1},
    ],
)
def test_instrument_mapping_is_exact(tmp_path, monkeypatch, mapping):
    _, _, _, runtime, clock, _ = _setup_observer(tmp_path, monkeypatch)
    with pytest.raises(UpstoxStreamError):
        UpstoxMarketStreamAdapter(runtime, mapping, clock=clock)


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "bad"},
        {"mode": "PAPER_RESEARCH"},
        {"subscription_mode": "full"},
        {"subscription_mode": "option_greeks"},
    ],
)
def test_unsupported_modes_are_disabled(tmp_path, monkeypatch, options):
    with pytest.raises(UpstoxStreamError):
        _setup_observer(tmp_path, monkeypatch, **options)


def test_observer_refuses_synthetic_runtime_and_default_transport_is_offline(tmp_path, monkeypatch):
    _, _, instruments, runtime = _setup(tmp_path)
    with pytest.raises(UpstoxStreamError, match="dedicated"):
        UpstoxMarketStreamAdapter(runtime, {KEY: instruments[0]})


def _response(
    uri="wss://feed.upstox.com/market-data-feeder/v3/upstox-developer-api/feeds?code=fake-one-use",
):
    response = Mock()
    response.status_code = 200
    response.iter_content.return_value = [
        json.dumps({"status": "success", "data": {"authorized_redirect_uri": uri}}).encode()
    ]
    return response


class FakeSocket:
    def __init__(self, frames, clock):
        self.frames = iter(frames)
        self.sent = []
        self.closed = False
        self.clock = clock

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout):
        value = next(self.frames)
        if isinstance(value, Exception):
            self.clock.now += timedelta(seconds=40)
            raise value
        msg = feed_message_class()()
        msg.ParseFromString(value)
        self.clock.now = EPOCH + timedelta(milliseconds=msg.currentTs)
        return value

    def close(self):
        self.closed = True


def test_reconnect_reauthorizes_resubscribes_and_deduplicates_offline(
    tmp_path, monkeypatch, caplog
):
    _, _, _, _, clock, adapter = _setup_observer(tmp_path, monkeypatch, mode="OBSERVE_ONLY")
    first = FakeSocket(
        [_frame(kind=2), _frame(kind=0), _frame(), OSError("fake-token secret URL")], clock
    )
    # The replay is older than the default age limit; use a two-second reconnect
    # so the replay is eligible for durable duplicate detection, not stale acceptance.
    later = START + timedelta(seconds=2)
    second = FakeSocket(
        [
            _frame(later, kind=2),
            _frame(later, kind=0),
            _frame(later, event_at=START),
            _frame(later),
        ],
        clock,
    )
    session = Mock(spec=requests.Session)
    session.get.side_effect = [_response(), _response()]
    connector = Mock(side_effect=[first, second])
    sleep = Mock()
    client = UpstoxMarketStreamClient(
        network_enabled=True, session=session, connector=connector, sleep=sleep
    )
    results = client.run(adapter, access_token="fake-token", operator_authorized=True, max_frames=7)
    assert [r.runtime_result.duplicate for r in results] == [False, True, False]
    assert session.get.call_count == connector.call_count == 2
    for request_call in session.get.call_args_list:
        assert request_call.args == (AUTHORIZE_URL,)
        assert request_call.kwargs["allow_redirects"] is False
        assert request_call.kwargs["timeout"] == (5, 5)
    assert first.closed and second.closed
    assert sleep.call_args.args == (1.0,)
    for sock in (first, second):
        request = json.loads(sock.sent[0])
        assert isinstance(sock.sent[0], bytes) and request["method"] == "sub"
        assert request["data"] == {"mode": "ltpc", "instrumentKeys": [KEY]}
    assert "fake-token" not in caplog.text and "fake-one-use" not in caplog.text


@pytest.mark.parametrize(
    "uri",
    [
        "ws://feed.upstox.com/market-data-feeder/v3/feeds",
        "wss://upstox.com.evil.test/market-data-feeder/v3/feeds",
        "wss://feed.upstox.com:444/market-data-feeder/v3/feeds",
        "wss://u:p@feed.upstox.com/market-data-feeder/v3/feeds",
        "wss://feed.upstox.com/v2/order/place",
    ],
)
def test_authorization_redirects_fail_closed_and_hide_secrets(tmp_path, monkeypatch, uri):
    _, _, _, _, _, adapter = _setup_observer(tmp_path, monkeypatch, mode="OBSERVE_ONLY")
    session = Mock(spec=requests.Session)
    session.get.return_value = _response(uri)
    connector = Mock()
    client = UpstoxMarketStreamClient(network_enabled=True, session=session, connector=connector)
    token = "fake-" + "token"
    with pytest.raises(UpstoxStreamError) as error:
        client.run(adapter, access_token=token, operator_authorized=True)
    assert "fake-token" not in "".join(traceback.format_exception(error.value))
    assert uri not in str(error.value)
    connector.assert_not_called()


def test_transport_requires_both_explicit_gates_and_correct_mode(tmp_path, monkeypatch):
    _, _, _, _, _, adapter = _setup_observer(tmp_path, monkeypatch, mode="OBSERVE_ONLY")
    session = Mock(spec=requests.Session)
    with pytest.raises(UpstoxStreamError, match="operator authorization"):
        UpstoxMarketStreamClient(session=session).run(
            adapter, access_token="fake-token", operator_authorized=True
        )
    with pytest.raises(UpstoxStreamError, match="operator authorization"):
        UpstoxMarketStreamClient(network_enabled=True, session=session).run(
            adapter, access_token="fake-token"
        )
    adapter.mode = StreamMode.OFFLINE_TEST
    with pytest.raises(UpstoxStreamError, match="operator authorization"):
        UpstoxMarketStreamClient(network_enabled=True, session=session).run(
            adapter, access_token="fake-token", operator_authorized=True
        )
    session.get.assert_not_called()


def test_bounded_retries_stale_evidence_and_sanitized_transport_errors(tmp_path, monkeypatch):
    database, _, _, _, clock, adapter = _setup_observer(tmp_path, monkeypatch, mode="OBSERVE_ONLY")
    session = Mock(spec=requests.Session)
    session.get.side_effect = [_response(), _response()]
    connector = Mock(
        side_effect=[FakeSocket([TimeoutError("fake-secret")], clock) for _ in range(2)]
    )
    sleep = Mock()
    client = UpstoxMarketStreamClient(
        network_enabled=True, session=session, connector=connector, sleep=sleep, max_reconnects=1
    )
    with pytest.raises(UpstoxStreamError, match="reconnect limit") as error:
        client.run(adapter, access_token="fake-token", operator_authorized=True)
    assert "fake-secret" not in "".join(traceback.format_exception(error.value))
    assert connector.call_count == 2 and sleep.call_count == 1
    assert adapter.state == ConnectionState.EXHAUSTED
    with closing(get_readonly_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM decision_journal_health WHERE kind='OBSERVE_STALE'"
            ).fetchone()[0]
            == 2
        )
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_websocket_boundary_retains_tls_defaults_limits_and_private_logging(monkeypatch, caplog):
    import websockets.sync.client

    caplog.set_level(logging.DEBUG)
    transport = Mock()

    def connect(uri, **options):
        assert options["proxy"] is None and "ssl" not in options
        assert options["max_size"] == 1_048_576 and options["open_timeout"] == 5
        assert options["ping_interval"] == options["ping_timeout"] == 20
        assert options["logger"].disabled
        options["logger"].debug("secret URI: %s", uri)
        return transport

    monkeypatch.setattr(websockets.sync.client, "connect", connect)
    assert (
        _connect_socket("wss://feed.upstox.com/market-data-feeder/v3/feeds?code=fake-secret")
        is transport
    )
    assert "fake-secret" not in caplog.text
