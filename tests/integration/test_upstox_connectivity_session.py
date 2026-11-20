"""Isolated HTTP/protobuf/import/qualification/causal-execution evidence, never real data."""

import json
import socket
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from aegis.database import get_readonly_connection
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.market_quality import calculate_expected_candles, load_calendar_registry
from aegis.upstox_observation import run_observation
from aegis.upstox_research_session import qualified_historical_simulation
from tests.test_forward_runtime import _calendar
from tests.test_upstox_http import client as historical_client
from tests.test_upstox_http import response
from tests.test_upstox_market_stream import START, Clock, _frame
from tests.test_upstox_observation import configured, observe, transport

DAY = date(2026, 1, 5)
AS_OF = START + timedelta(minutes=31)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "unused.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "unused.log"))
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "calendar.json"))
    _calendar(tmp_path / "calendar.json")

    def forbidden(*args, **kwargs):
        raise AssertionError("Real networking is forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def candles(*, gap=False, duplicate=False):
    prices = [
        "100",
        "100",
        "100",
        "100",
        "99.9",
        "99.8",
        "100.1",
        "100.1",
        "100.4",
        "100.2",
        "99.9",
        "99.9",
    ] + ["99.9"] * 18
    rows = []
    for index, price in enumerate(prices):
        if gap and index == 15:
            continue
        at = START + timedelta(minutes=index)
        rows.append([at.isoformat(), price, price, price, price, 100, 0])
    if duplicate:
        rows.append(rows[-1])
    return {"status": "success", "data": {"candles": rows}}


def run_sample(tmp_path, *, gap=False, duplicate=False, reviewed=True):
    clock = Clock()
    factory, stream_http, peers, _ = transport(
        [_frame(kind=2), _frame(kind=0), _frame(START + timedelta(seconds=1))],
        clock,
    )
    history, history_http = historical_client(
        [response(payload=candles(gap=gap, duplicate=duplicate))]
    )

    def history_factory(*args, **kwargs):
        clock.now = AS_OF  # Entire fixture session is now completed.
        return history

    config = configured(
        tmp_path,
        max_frames=3,
        historical_from=DAY,
        historical_to=DAY,
        data_use_reviewed=reviewed,
    )
    # Connectivity remains diagnostic: no stream quote reaches execution.
    report = observe(config, clock, factory, historical_factory=history_factory)
    assert peers[0].closed
    return report, stream_http, history_http


def test_complete_mocked_connectivity_collection_then_explicitly_qualified_replay(tmp_path):
    report, stream_http, history_http = run_sample(tmp_path)
    assert report["status"] == "COMPLETED"
    assert report["zero_execution"] == "PASSED"
    assert not report["real_handshake_verified"]
    assert report["counters"]["LTPC_UPDATE_DECODED"] == 1
    assert report["counters"].get("ACCEPTED", 0) == 0
    history = report["historical"]
    assert history["status"] == "RETRIEVED"
    assert history["accepted_rows"] == history["candles_retrieved"] == 30
    assert history["duplicate_rows"] == history["rejected_rows"] == 0
    assert history["source_use_status"] == "UNVERIFIED"
    assert history["simulation"]["status"] == "BLOCKED"
    assert "SOURCE_USE_UNVERIFIED" in history["simulation"]["reason_codes"]
    assert history["simulation"]["fills"] == 0
    source_evidence = history["simulation"]["source_imports"]
    assert len(source_evidence) == 1
    assert source_evidence[0]["contributing_candles"] == 30
    assert source_evidence[0]["declaration_count"] == 0
    assert source_evidence[0]["latest_declaration_id"] is None
    assert stream_http.get.call_count == history_http.get.call_count == 1
    assert (
        "/v3/historical-candle/NSE_EQ%7CINE002A01018/minutes/1/"
        in history_http.get.call_args.args[0]
    )
    root = tmp_path / "run"
    database = root / "research.db"
    with closing(get_readonly_connection(database)) as connection:
        imported = connection.execute("SELECT * FROM data_imports").fetchone()
        assert imported["source_use_status"] == "UNVERIFIED"
        metadata = json.loads(imported["metadata_json"])
        assert metadata["raw_candle_array_sha256"] == history["raw_candle_array_sha256"]
        assert imported["file_sha256"]
        assert (
            connection.execute("SELECT COUNT(*) FROM import_source_use_history").fetchone()[0] == 0
        )
        import_id = imported["id"]
    assert list((root / "historical").rglob("*.candles.json"))
    # This declaration exists only for this synthetic disposable fixture. The
    # runner has no auto-declaration path and never asserts real data permission.
    declare_import_source_use_status(
        database,
        import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic test owner",
        rationale="Synthetic in-memory HTTP fixture; no external market-data rights claimed.",
    )
    result = qualified_historical_simulation(
        database,
        symbol="SYNTH",
        from_date=DAY,
        to_date=DAY,
        calendar_path=tmp_path / "calendar.json",
        as_of=AS_OF,
    )
    assert result["status"] == "COMPLETED"
    assert result["label"] == "HISTORICAL_SIMULATION"
    assert result["execution_engine"] == "EXISTING_M1B_HISTORICAL"
    assert result["decisions"] == {
        "status": "EVALUATED",
        "BUY": 1,
        "SELL": 1,
        "HOLD": 25,
        "NO_DECISION": 3,
    }
    assert result["orders"] == result["fills"] == 2
    assert result["rejected_or_unfilled_orders"] == 0
    assert result["expected_candles"] == 30
    assert result["confirmed_completeness_percentage"] == "100.00"
    assert result["source_imports"][0]["declaration_count"] == 1
    assert result["source_imports"][0]["latest_declaration_status"] == "VERIFIED_PERMITTED"
    assert result["position"] is False
    fees = Decimal(result["fees"])
    ending = Decimal(result["ending_cash"])
    pnl = Decimal(result["realized_net_pnl"])
    assert fees > 0
    assert fees == Decimal("0.3")
    assert pnl == Decimal("-0.7")
    assert ending == Decimal("9999.3")
    assert ending == Decimal(10000) + pnl
    orders = result["order_evidence"]
    assert [o["side"] for o in orders] == ["BUY", "SELL"]
    for order in orders:
        signal = datetime.fromisoformat(order["signal_candle_timestamp"])
        execution = datetime.fromisoformat(order["execution_bar_timestamp"])
        assert execution == signal + timedelta(minutes=1)
        assert order["status"] == "FILLED"
    # An unverified import outside the requested day must not appear as a
    # contributing source or alter the qualified snapshot's evidence.
    other_day = tmp_path / "other_day.csv"
    other_day.write_text(
        "timestamp,open,high,low,close,volume\n2026-01-06T09:15:00+05:30,100,100,100,100,100\n"
    )
    other_import = import_ohlcv_csv(database, other_day, "NSE", "SYNTH", "1m")
    assert other_import.accepted_rows == 1
    assert (
        qualified_historical_simulation(
            database,
            symbol="SYNTH",
            from_date=DAY,
            to_date=DAY,
            calendar_path=tmp_path / "calendar.json",
            as_of=AS_OF,
        )
        == result
    )  # Read-only deterministic replay does not duplicate financial records.
    (root / "qualified-fixture-result.json").write_text(
        json.dumps({"evidence_scope": "SYNTHETIC_HTTP_FIXTURE", "result": result}, indent=2),
        encoding="utf-8",
    )
    with closing(get_readonly_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    # Surviving public CLI report remains the truthful unverified initial attempt.
    assert json.loads((root / "report.json").read_text())["historical"]["simulation"]["fills"] == 0


def test_gap_preserved_and_blocks_even_after_fixture_source_declaration(tmp_path):
    report, _, _ = run_sample(tmp_path, gap=True)
    history = report["historical"]
    assert history["accepted_rows"] == 29
    assert history["simulation"]["missing_candles"] == 1
    database = tmp_path / "run" / "research.db"
    with closing(get_readonly_connection(database)) as connection:
        import_id = connection.execute("SELECT id FROM data_imports").fetchone()[0]
    declare_import_source_use_status(
        database,
        import_id,
        "VERIFIED_PERMITTED",
        declared_by="fixture",
        rationale="Synthetic fixture only",
    )
    result = qualified_historical_simulation(
        database,
        symbol="SYNTH",
        from_date=DAY,
        to_date=DAY,
        calendar_path=tmp_path / "calendar.json",
        as_of=AS_OF,
    )
    assert result["status"] == "BLOCKED"
    assert result["fills"] == 0
    assert result["expected_candles"] == 30
    assert result["actual_candles"] == 29
    assert "MISSING_COMPLETED_CANDLES" in result["reason_codes"]


def test_duplicate_provider_candles_are_rejected_without_fabrication(tmp_path):
    report, _, _ = run_sample(tmp_path, duplicate=True)
    assert report["historical"]["status"] == "FAILED_AUTHORIZATION_ENTITLEMENT_OR_DATA_VALIDATION"
    with closing(get_readonly_connection(tmp_path / "run" / "research.db")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM historical_candles").fetchone()[0] == 0


def test_historical_permission_is_separate_and_never_inferred(tmp_path):
    report, _, history_http = run_sample(tmp_path, reviewed=False)
    assert report["historical"]["status"] == "BLOCKED_FOR_DATA_USE_REVIEW"
    history_http.get.assert_not_called()


def test_no_operator_approval_offline_implementation_does_not_contact_service(tmp_path):
    report = run_observation(configured(tmp_path))
    assert report["status"] == "BLOCKED_FOR_AUTHORIZATION"
    assert report["authorization"] == report["websocket_handshake"] == "NOT_ATTEMPTED"
    assert report["historical"]["status"] == "NOT_REQUESTED"


def test_provisional_calendar_stays_provisional_after_historical_retrieval(tmp_path):
    path = tmp_path / "calendar.json"
    document = json.loads(path.read_text())
    document["calendars"][0]["confidence"] = "CONFIGURED"
    path.write_text(json.dumps(document))
    report, _, _ = run_sample(tmp_path)
    history = report["historical"]
    assert history["status"] == "RETRIEVED"
    assert history["simulation"]["status"] == "BLOCKED"
    assert history["simulation"]["calendar_confidence"] == "CONFIGURED"
    assert "CALENDAR_NOT_CONFIRMED" in history["simulation"]["reason_codes"]
    assert "SOURCE_USE_UNVERIFIED" in history["simulation"]["reason_codes"]
    assert history["simulation"]["expected_candles"] is None
    assert history["simulation"]["provisional_expected_candles"] == 30
    assert history["simulation"]["actual_candles"] == 30
    assert history["simulation"]["confirmed_completeness_percentage"] is None
    assert history["simulation"]["invalid_candles"] == 0
    assert history["simulation"]["duplicate_candle_identities"] == 0
    assert len(history["simulation"]["calendar_configuration_sha256"]) == 64
    assert history["simulation"]["quality_reasons"]
    assert json.loads(path.read_text()) == document


@pytest.mark.parametrize("segment,expected_count", [("SYNTH_CAS", 360), ("SYNTH_NON_CAS", 375)])
def test_session_classes_require_explicit_calendar_mapping(tmp_path, segment, expected_count):
    # Synthetic administrative fixtures exercise existing segment selection.
    # They do not classify RELIANCE or approve the real calendar.
    path = tmp_path / "calendar.json"
    document = json.loads(path.read_text())
    base = document["calendars"][0]
    document["calendars"] = [
        {**base, "segment": "SYNTH_CAS", "sessions": [{"open": "09:15", "close": "15:15"}]},
        {**base, "segment": "SYNTH_NON_CAS", "sessions": [{"open": "09:15", "close": "15:30"}]},
    ]
    path.write_text(json.dumps(document))
    registry = load_calendar_registry(path)
    assert registry.resolve("NSE", "NSE") is None  # No implicit security classification.
    calendar = registry.resolve("NSE", segment)
    assert calendar is not None
    cutoff = START.replace(hour=23, minute=59)
    scheduled, completed, _, uncovered = calculate_expected_candles(
        calendar,
        "1m",
        DAY,
        DAY,
        as_of=cutoff,
    )
    assert len(scheduled) == len(completed) == expected_count
    assert uncovered == 0
