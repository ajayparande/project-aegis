"""Bounded runner tests: every transport is injected and real sockets are forbidden."""

import json
import socket
import threading
import time
from contextlib import closing
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from aegis.database import get_connection, get_readonly_connection
from aegis.instruments import InstrumentInput
from aegis.providers.upstox_market_stream import StreamControl, StreamMode, StreamStopped
from aegis.providers.upstox_market_stream import UpstoxMarketStreamClient as Client
from aegis.upstox_observation import ObservationConfig, main, run_observation
from tests.test_forward_runtime import _calendar
from tests.test_upstox_market_stream import KEY, START, Clock, FakeSocket, _frame, _response


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr("aegis.broker_credentials.PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("AEGIS_ENABLE_LIVE_ORDERS", "false")
    monkeypatch.setattr("getpass.getpass", Mock(side_effect=AssertionError("No token prompts")))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "unused.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "unused.log"))
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "calendar.json"))
    _calendar(tmp_path / "calendar.json")

    def forbidden(*args, **kwargs):
        raise AssertionError("Real networking is forbidden in these tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def instrument():
    return InstrumentInput(
        "NSE",
        "SYNTH",
        "Synthetic protocol fixture",
        "EQUITY",
        segment="NSE",
        isin=KEY.split("|")[1],
        tick_size=Decimal("0.05"),
    )


def configured(tmp_path, **kwargs):
    return ObservationConfig(tmp_path / "run", StreamMode.OBSERVE_ONLY, **kwargs)


def transport(frames, clock, *, responses=None, sockets=None):
    session = Mock(spec=requests.Session)
    session.get.side_effect = responses or [_response()]
    peers = sockets or [FakeSocket(frames, clock)]
    connector = Mock(side_effect=peers)

    def factory(**kwargs):
        return Client(session=session, connector=connector, sleep=lambda _: None, **kwargs)

    return factory, session, peers, connector


def observe(config, clock, factory, **kwargs):
    return run_observation(
        config,
        instrument_key=KEY,
        instrument=instrument(),
        operator_authorized=True,
        credential=lambda: "fake-token-never-print",
        client_factory=factory,
        wall_clock=clock,
        **kwargs,
    )


def test_offline_default_aggregates_gap_and_never_executes(tmp_path):
    root = tmp_path / "offline"
    report = run_observation(ObservationConfig(root))
    assert report["status"] == "COMPLETED"
    assert report["counters"]["ACCEPTED"] == 6
    assert report["counters"]["MISSING_MINUTE_GAP"] == 1
    assert report["zero_execution"] == "PASSED"
    assert report["authorization"] == "NOT_ATTEMPTED"
    assert report["offline_simulation_success"] is True
    assert report["real_handshake_verified"] is False
    assert report["real_feed_observation_verified"] is False
    assert "CONNECTING" in report["lifecycle"]
    assert json.loads((root / "report.json").read_text()) == report
    with closing(get_readonly_connection(root / "observation.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM forward_runtime_candles").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_scheduler_events").fetchone()[0] == 0
        )


@pytest.mark.parametrize("approved,token", [(False, "unused"), (True, ""), (True, "bad\ntoken")])
def test_approval_and_credentials_fail_before_network_or_database(tmp_path, approved, token):
    credential = Mock(return_value=token)
    factory = Mock(side_effect=AssertionError("must not connect"))
    report = run_observation(
        configured(tmp_path),
        operator_authorized=approved,
        credential=credential,
        client_factory=factory,
    )
    assert report["status"] == "BLOCKED_FOR_AUTHORIZATION"
    assert credential.call_count == int(approved)
    factory.assert_not_called()
    assert not (tmp_path / "run" / "observation.db").exists()


def test_existing_root_is_preserved_byte_for_byte(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    sentinel = root / "report.json"
    sentinel.write_bytes(b"existing evidence")
    report = run_observation(ObservationConfig(root))
    assert report["reason_codes"] == ["EXISTING_OR_UNSAFE_OUTPUT_ROOT"]
    assert sentinel.read_bytes() == b"existing evidence"
    assert list(root.iterdir()) == [sentinel]


@pytest.mark.parametrize(
    "options",
    [
        {"max_seconds": 0},
        {"max_seconds": 61},
        {"max_observations": 301},
        {"max_reconnects": 4},
        {"max_frames": 0},
        {"mode": StreamMode.PAPER_RESEARCH},
    ],
)
def test_limits_and_paper_path_are_closed(tmp_path, options):
    with pytest.raises(ValueError):
        ObservationConfig(tmp_path / "run", **options)


def test_closed_snapshot_is_diagnostic_and_not_a_quote(tmp_path):
    clock = Clock()
    frames = [_frame(kind=2, status=0), _frame(kind=0, event_at=START - timedelta(days=1))]
    factory, session, peers, _ = transport(frames, clock)
    report = observe(configured(tmp_path, max_frames=2), clock, factory)
    assert report["status"] == "COMPLETED"
    assert report["authorization"] == report["websocket_handshake"] == "SUCCESS"
    assert report["market_status"] == report["snapshot"] == "DECODED"
    assert report["verification_scope"] == "INJECTED_TRANSPORT"
    assert not report["real_handshake_verified"]
    assert report["counters"].get("ACCEPTED", 0) == 0
    assert peers[0].closed
    assert session.get.call_count == 1
    assert json.loads(peers[0].sent[0])["data"] == {"mode": "ltpc", "instrumentKeys": [KEY]}
    assert "fake-token" not in json.dumps(report)
    assert "fake-one-use" not in json.dumps(report)


def test_configured_calendar_blocks_ingestion_before_connect(tmp_path):
    path = tmp_path / "calendar.json"
    document = json.loads(path.read_text())
    document["calendars"][0]["confidence"] = "CONFIGURED"
    path.write_text(json.dumps(document))
    factory = Mock()
    report = observe(configured(tmp_path, connectivity_only=False), Clock(), factory)
    assert report["reason_codes"] == ["CALENDAR_NOT_CONFIRMED"]
    assert report["calendar_confidence"] == "CONFIGURED"
    factory.assert_not_called()


def test_count_limit_closes_socket_without_third_observation(tmp_path):
    clock = Clock()
    frames = [_frame(kind=2), _frame(kind=0)] + [
        _frame(START + timedelta(seconds=n)) for n in (1, 2, 3)
    ]
    factory, _, peers, _ = transport(frames, clock)
    report = observe(
        configured(tmp_path, max_observations=2, connectivity_only=False), clock, factory
    )
    assert report["status"] == "COMPLETED"
    assert report["disconnect_reason"] == "OBSERVATION_LIMIT"
    assert report["counters"]["ACCEPTED"] == 2
    assert peers[0].closed
    assert report["zero_execution"] == "PASSED"


def test_deadline_and_operator_cancellation_are_enforced():
    current = [0.0]
    control = StreamControl(monotonic=lambda: current[0])
    control.start()
    current[0] = 60.0
    with pytest.raises(StreamStopped, match="DURATION_LIMIT"):
        control.check()
    control.start()
    control.stop()
    with pytest.raises(StreamStopped, match="INTERRUPTED"):
        control.check()


def test_real_transport_deadline_stops_before_processing_late_frame(tmp_path):
    clock = Clock()
    monotonic = [0.0]

    class ExpiringSocket(FakeSocket):
        def recv(self, timeout):
            monotonic[0] = 60.0
            return super().recv(timeout)

    peer = ExpiringSocket([_frame(kind=2)], clock)
    factory, _, _, _ = transport([], clock, sockets=[peer])
    report = observe(configured(tmp_path), clock, factory, monotonic=lambda: monotonic[0])
    assert report["status"] == "COMPLETED"
    assert report["disconnect_reason"] == "DURATION_LIMIT"
    assert report["market_status"] == "NOT_DECODED"
    assert peer.closed


def test_reconnect_obtains_fresh_url_and_resubscribes(tmp_path):
    clock = Clock()
    first = FakeSocket([OSError("secret URL")], clock)
    second = FakeSocket([_frame(kind=2), _frame(kind=0)], clock)
    urls = [f"wss://feed.upstox.com/market-data-feeder/v3/feeds?code={n}" for n in (1, 2)]
    factory, session, peers, connector = transport(
        [],
        clock,
        responses=[_response(u) for u in urls],
        sockets=[first, second],
    )
    report = observe(configured(tmp_path, max_frames=2), clock, factory)
    assert report["status"] == "COMPLETED"
    assert report["counters"]["RECONNECT"] == 1
    assert session.get.call_count == 2
    assert [call.args[0] for call in connector.call_args_list] == urls
    assert all(p.closed and len(p.sent) == 1 for p in peers)


def test_authentication_failure_is_sanitized_and_never_connects(tmp_path):
    clock = Clock()
    response = _response()
    response.status_code = 401
    factory, _, peers, connector = transport([], clock, responses=[response])
    report = observe(configured(tmp_path), clock, factory)
    assert report["status"] == "FAILED"
    assert report["authorization"] == "FAILED"
    assert report["websocket_handshake"] == "NOT_ATTEMPTED"
    assert report["zero_execution"] == "PASSED"
    connector.assert_not_called()
    assert not peers[0].sent


def test_keyboard_interrupt_closes_transport_and_writes_report(tmp_path):
    clock = Clock()

    class InterruptSocket(FakeSocket):
        def recv(self, timeout):
            raise KeyboardInterrupt

    peer = InterruptSocket([], clock)
    factory, _, _, _ = transport([], clock, sockets=[peer])
    report = observe(configured(tmp_path), clock, factory)
    assert report["status"] == "INTERRUPTED"
    assert peer.closed
    assert report["zero_execution"] == "PASSED"
    assert (tmp_path / "run" / "report.json").exists()


def test_financial_mutation_stops_and_preserves_evidence(tmp_path):
    clock = Clock()

    class MutatingSocket(FakeSocket):
        def recv(self, timeout):
            with closing(get_connection(tmp_path / "run" / "observation.db")) as connection:
                connection.execute("UPDATE paper_accounts SET initial_cash='9999'")
                connection.commit()
            return super().recv(timeout)

    peer = MutatingSocket([_frame(kind=2)], clock)
    factory, _, _, _ = transport([], clock, sockets=[peer])
    report = observe(configured(tmp_path), clock, factory)
    assert report["status"] == "FAILED"
    assert report["zero_execution"] == "FAILED"
    assert peer.closed
    with closing(get_readonly_connection(tmp_path / "run" / "observation.db")) as connection:
        assert connection.execute("SELECT initial_cash FROM paper_accounts").fetchone()[0] == "9999"


def test_cli_rejects_float_tick_size(tmp_path, capsys):
    path = tmp_path / "instrument.json"
    path.write_text(json.dumps({"instrument_key": KEY, "tick_size": 0.05}))
    assert main(["--root", str(tmp_path / "run"), "--instrument-config", str(path)]) == 2
    assert "INVALID_LOCAL_CONFIGURATION" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--approve-market-data-only", "--approve-observe-only"])
def test_scoped_cli_approval_requires_no_confirmation_or_token_prompt(
    tmp_path, monkeypatch, capsys, flag
):
    from aegis import upstox_observation as module

    document = asdict(instrument())
    document.update(instrument_key=KEY, tick_size="0.05")
    path = tmp_path / "instrument.json"
    path.write_text(json.dumps(document))
    monkeypatch.setenv("AEGIS_TEST_UPSTOX_ACCESS_TOKEN", "synthetic-token-not-a-real-secret")
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("No confirmation")))
    captured = {}

    def run(config, **kwargs):
        captured.update(config=config, **kwargs)
        assert kwargs["credential"]() == "synthetic-token-not-a-real-secret"
        return {"status": "COMPLETED"}

    monkeypatch.setattr(module, "run_observation", run)
    assert (
        main(
            [
                "--mode",
                "OBSERVE_ONLY",
                "--root",
                str(tmp_path / "run"),
                "--instrument-config",
                str(path),
                "--token-env",
                "AEGIS_TEST_UPSTOX_ACCESS_TOKEN",
                "--max-observations",
                "100",
                flag,
                "--confirm",
            ]
        )
        == 0
    )
    assert captured["operator_authorized"] is True
    assert captured["config"].connectivity_only is True
    assert captured["config"].mode == StreamMode.OBSERVE_ONLY
    assert captured["config"].max_seconds == 60
    assert captured["config"].max_observations == 100
    assert "approve" not in asdict(captured["config"])
    assert "synthetic-token" not in capsys.readouterr().out


def test_cli_token_alone_never_grants_market_data_approval(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AEGIS_TEST_UPSTOX_ACCESS_TOKEN", "synthetic-token-not-a-real-secret")
    assert (
        main(
            [
                "--mode",
                "OBSERVE_ONLY",
                "--root",
                str(tmp_path / "run"),
                "--token-env",
                "AEGIS_TEST_UPSTOX_ACCESS_TOKEN",
            ]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["reason_codes"] == ["PER_INVOCATION_APPROVAL_REQUIRED"]
    assert report["authorization"] == "NOT_ATTEMPTED"


def test_scoped_cli_approval_loads_dotenv_without_any_prompt(tmp_path, monkeypatch, capsys):
    from aegis import upstox_observation as module

    document = asdict(instrument())
    document.update(instrument_key=KEY, tick_size="0.05")
    path = tmp_path / "instrument.json"
    path.write_text(json.dumps(document))
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("No confirmation")))
    (tmp_path / ".env").write_text("UPSTOX_ACCESS_TOKEN=synthetic-token-not-a-real-secret\n")

    def run(config, **kwargs):
        assert kwargs["operator_authorized"] is True
        assert kwargs["credential"]() == "synthetic-token-not-a-real-secret"
        return {"status": "FAILED", "reason_codes": ["SIMULATED_AUTHORIZATION_REJECTION"]}

    monkeypatch.setattr(module, "run_observation", run)
    assert (
        main(
            [
                "--mode",
                "OBSERVE_ONLY",
                "--root",
                str(tmp_path / "run"),
                "--instrument-config",
                str(path),
                "--approve-market-data-only",
            ]
        )
        == 2
    )
    assert "synthetic-token" not in capsys.readouterr().out


def test_approved_cli_missing_dotenv_token_is_noninteractive_blocker(tmp_path, capsys):
    document = asdict(instrument())
    document.update(instrument_key=KEY, tick_size="0.05")
    path = tmp_path / "instrument.json"
    path.write_text(json.dumps(document))
    assert (
        main(
            [
                "--mode",
                "OBSERVE_ONLY",
                "--root",
                str(tmp_path / "run"),
                "--instrument-config",
                str(path),
                "--approve-market-data-only",
            ]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["reason_codes"] == ["OPERATOR_CREDENTIAL_UNAVAILABLE"]
    assert report["authorization"] == "NOT_ATTEMPTED"


def test_bad_dotenv_safety_configuration_is_sanitized(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AEGIS_ENABLE_LIVE_ORDERS")
    (tmp_path / ".env").write_text(
        "UPSTOX_ACCESS_TOKEN=synthetic-secret\nAEGIS_ENABLE_LIVE_ORDERS=true\n"
    )
    assert (
        main(
            [
                "--mode",
                "OBSERVE_ONLY",
                "--root",
                str(tmp_path / "run"),
                "--approve-market-data-only",
            ]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().out)
    assert report["reason_codes"] == ["LIVE_BROKER_ORDERS_DISABLED_REQUIRED"]
    assert "synthetic-secret" not in json.dumps(report)


@pytest.mark.parametrize(
    "changes",
    [
        {"isin": "INE000000001"},
        {"exchange": "BSE"},
        {"instrument_type": "INDEX"},
        {"is_active": False},
    ],
)
def test_bad_instrument_allowlist_never_connects(tmp_path, changes):
    factory = Mock()
    report = run_observation(
        configured(tmp_path),
        instrument_key=KEY,
        instrument=replace(instrument(), **changes),
        operator_authorized=True,
        credential=lambda: "fake",
        client_factory=factory,
    )
    assert report["status"] == "BLOCKED"
    assert report["reason_codes"] == ["INVALID_EXACT_INSTRUMENT_MAPPING"]
    factory.assert_not_called()


@pytest.mark.parametrize("document", [[], {}, {"instrument_key": KEY, "tick_size": "bad"}])
def test_malformed_local_configuration_is_controlled(tmp_path, capsys, document):
    path = tmp_path / "instrument.json"
    path.write_text(json.dumps(document))
    assert main(["--root", str(tmp_path / "run"), "--instrument-config", str(path)]) == 2
    assert "INVALID_LOCAL_CONFIGURATION" in capsys.readouterr().out


def test_configured_calendar_allows_only_diagnostic_connectivity(tmp_path):
    path = tmp_path / "calendar.json"
    document = json.loads(path.read_text())
    document["calendars"][0]["confidence"] = "CONFIGURED"
    path.write_text(json.dumps(document))
    clock = Clock()
    factory, _, _, _ = transport([_frame(kind=2, status=0), _frame(kind=0)], clock)
    report = observe(configured(tmp_path, max_frames=2), clock, factory)
    assert report["status"] == "COMPLETED"
    assert report["calendar_confidence"] == "CONFIGURED"
    assert report["data_quality_eligibility"] == "UNVERIFIED"
    assert report["trading_readiness"] == "NOT_AUTHORIZED"
    assert report["zero_execution"] == "PASSED"


def test_timer_actually_unblocks_waiting_socket(tmp_path):
    clock = Clock()

    class WaitingSocket(FakeSocket):
        waiting = threading.Event()

        def recv(self, timeout):
            assert self.waiting.wait(3), "Deadline failed to close the socket"
            raise OSError("closed by deadline")

        def close(self):
            self.waiting.set()
            super().close()

    peer = WaitingSocket([], clock)
    factory, _, _, _ = transport([], clock, sockets=[peer])
    start = time.monotonic()
    report = observe(configured(tmp_path, max_seconds=1), clock, factory)
    assert time.monotonic() - start < 3
    assert report["status"] == "COMPLETED"
    assert report["disconnect_reason"] == "DURATION_LIMIT"
    assert peer.closed


def test_duplicate_out_of_order_and_stale_metrics(tmp_path):
    clock = Clock()
    one = START + timedelta(seconds=1)
    two = START + timedelta(seconds=2)
    frames = [
        _frame(kind=2),
        _frame(kind=0),
        _frame(one),
        _frame(two, event_at=START),
        _frame(two, event_at=one),
    ]
    factory, _, _, _ = transport(frames, clock)
    report = observe(configured(tmp_path, connectivity_only=False, max_frames=5), clock, factory)
    assert report["status"] == "COMPLETED"
    assert report["counters"]["ACCEPTED"] == 1
    assert report["counters"]["DUPLICATE"] == 1
    assert report["rejected_by_reason"]["OUT_OF_ORDER"] == 1
    assert report["provider_latency"]["count"] == 3


def test_stale_feed_reconnects_and_synchronizes_again(tmp_path):
    clock = Clock()
    later = START + timedelta(seconds=40)
    first = FakeSocket([_frame(kind=2), _frame(later, kind=0)], clock)
    second = FakeSocket([_frame(later, kind=2), _frame(later, kind=0)], clock)
    factory, _, _, _ = transport(
        [], clock, sockets=[first, second], responses=[_response(), _response()]
    )
    report = observe(configured(tmp_path, max_frames=3), clock, factory)
    assert report["status"] == "COMPLETED"
    assert report["counters"]["STALE"] == 1
    assert report["counters"]["RECONNECT"] == 1
    assert report["counters"]["SUBSCRIBED"] == 2
    assert report["counters"]["SYNCHRONIZED"] == 1
