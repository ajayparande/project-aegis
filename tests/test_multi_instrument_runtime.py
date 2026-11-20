"""Isolated three-instrument scheduling and restart regressions."""

import json
import socket
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aegis import database as database_module
from aegis.backtesting import zero_cost_assumptions
from aegis.database import get_connection, get_readonly_connection, initialize_database
from aegis.forward_runtime import ForwardRuntimeError, ForwardRuntimeService, Quote
from aegis.instruments import InstrumentInput, register_instrument
from aegis.multi_instrument_runtime import _MultiInstrumentCoordinator
from aegis.paper_trading import create_paper_account, reconcile_paper_account
from tests.test_forward_runtime import _LIMITS, _START, _calendar

SYMBOLS = ("RELIANCE", "TCS", "INFY")


@pytest.fixture
def session(tmp_path, monkeypatch, request):
    options = getattr(request, "param", {})
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "unused.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "unused.log"))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("No network"))
    database = tmp_path / "synthetic.db"
    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(database_module, "SCHEMA_VERSION", options.get("schema", 18))
        initialize_database(database)
    path = tmp_path / "calendar.json"
    calendars = _calendar(path)
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(path))
    ids = tuple(
        register_instrument(
            database,
            InstrumentInput(
                "NSE",
                symbol,
                symbol + " SYNTHETIC",
                "EQUITY",
                segment="NSE",
                tick_size=Decimal("0.05"),
            ),
        ).id
        for symbol in SYMBOLS
    )
    account = create_paper_account(
        database,
        name="m5-synthetic",
        initial_cash=Decimal(10000),
        risk_limits=_LIMITS,
        costs=zero_cost_assumptions(),
        created_at=_START.isoformat(),
    )
    runtime = ForwardRuntimeService.create(
        database,
        "m5-synthetic",
        account,
        ids,
        calendars,
        max_candidates=options.get("cap", 5),
        scheduling_only=True,
        created_at=_START.isoformat(),
    )
    return database, calendars, ids, runtime


def observations(ids, gap=True):
    quotes = []
    sequences = dict.fromkeys(ids, 0)
    for minute in range(12):
        for index, instrument in enumerate(ids):
            if gap and index == 2 and minute == 2:
                continue
            sequences[instrument] += 1
            at = (_START + timedelta(minutes=minute)).isoformat()
            quotes.append(
                Quote(
                    instrument,
                    at,
                    sequences[instrument],
                    Decimal(100 + index * 100) + Decimal("0.05") * minute,
                    10,
                    f"synthetic-{instrument}-{minute}",
                    at,
                )
            )
    return tuple(quotes)


def evidence(database):
    with closing(get_readonly_connection(database)) as conn:
        candles = [
            tuple(r)
            for r in conn.execute(
                "SELECT instrument_id,interval,candle_timestamp,close_decimal FROM forward_runtime_candles ORDER BY instrument_id,interval,candle_timestamp"
            )
        ]
        events = [
            tuple(r)
            for r in conn.execute(
                "SELECT event_id,instrument_id,interval,candle_timestamp FROM forward_scheduler_events ORDER BY event_id"
            )
        ]
        for table in (
            "paper_orders",
            "paper_fills",
            "paper_positions",
            "paper_marks",
            "paper_ledger_entries",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    return candles, events


@pytest.mark.parametrize("cut", [1, 3, 18, 34])
def test_restart_replay_isolation(session, cut):
    database, calendars, ids, runtime = session
    quotes = observations(ids)
    coordinator = _MultiInstrumentCoordinator(runtime, SYMBOLS)
    assert all(r.accepted for r in coordinator.process(quotes[:cut]))
    coordinator = _MultiInstrumentCoordinator.open(database, runtime.runtime_id, calendars, SYMBOLS)
    assert all(r.accepted for r in coordinator.process(quotes[cut:]))
    before = evidence(database)
    replay = coordinator.process(quotes)
    assert all(r.duplicate or not r.accepted for r in replay)
    assert evidence(database) == before
    candles, events = before
    for instrument in ids:
        assert any(c[0] == instrument and c[1] == "1m" for c in candles)
        assert any(c[0] == instrument and c[1] == "5m" for c in candles)
    for index, instrument in enumerate(ids):
        own = [c for c in candles if c[0] == instrument]
        assert all(
            Decimal(100 + index * 100) <= Decimal(c[3]) < Decimal(101 + index * 100) for c in own
        )
        assert len([c for c in own if c[1] == "1m"]) == (10 if index == 2 else 11)
        assert len([e for e in events if e[1] == instrument and e[2] == "1m"]) == (
            9 if index == 2 else 11
        )
    with closing(get_readonly_connection(database)) as conn:
        import json

        state = json.loads(
            conn.execute(
                "SELECT state_json FROM forward_runtimes WHERE runtime_id=?", (runtime.runtime_id,)
            ).fetchone()[0]
        )
        assert [state["cursor"][str(i)]["sequence"] for i in ids] == [12, 12, 11]
    assert len({e[0] for e in events}) == len(events)
    assert all(any(c[:3] == e[1:] for c in candles) for e in events)
    assert reconcile_paper_account(database, runtime.account_id)["status"] == "RECONCILED"


def test_execution_and_foreign_bindings_blocked(session):
    database, _calendars, ids, runtime = session
    coordinator = _MultiInstrumentCoordinator(runtime, SYMBOLS)
    quotes = observations(ids)
    coordinator.process(quotes)
    with pytest.raises(ForwardRuntimeError, match="Scheduling-only"):
        runtime.build_mock_proposal("invalid", "BUY", 1)
    with pytest.raises(ForwardRuntimeError):
        _MultiInstrumentCoordinator(runtime, ("TCS",))
    with pytest.raises(ForwardRuntimeError):
        coordinator.process((replace(quotes[0], instrument_id=999),))
    late = replace(quotes[0], event_id="synthetic-late", sequence=100)
    assert not coordinator.process((late,))[0].accepted
    stale = replace(
        quotes[-1],
        event_id="synthetic-stale",
        sequence=101,
        received_at=(_START + timedelta(hours=1)).isoformat(),
    )
    assert not coordinator.process((stale,))[0].accepted
    evidence(database)


@pytest.mark.parametrize("after", [False, True])
def test_crash_at_scheduler_boundary(session, tmp_path, monkeypatch, after):
    database, calendars, ids, runtime = session
    quotes = observations(ids, gap=False)
    coordinator = _MultiInstrumentCoordinator(runtime, SYMBOLS)
    coordinator.process(quotes[:3])
    original = runtime.schedule_event

    def crash(*args, **kwargs):
        if after:
            original(*args, **kwargs)
        raise RuntimeError("SYNTHETIC_CRASH")

    monkeypatch.setattr(runtime, "schedule_event", crash)
    with pytest.raises(RuntimeError, match="SYNTHETIC_CRASH"):
        coordinator.process(quotes[3:4])
    recovered = _MultiInstrumentCoordinator.open(database, runtime.runtime_id, calendars, SYMBOLS)
    recovered.process(quotes[4:])
    before = evidence(database)
    again = _MultiInstrumentCoordinator.open(database, runtime.runtime_id, calendars, SYMBOLS)
    again.process(quotes)
    assert evidence(database) == before
    assert all(len([c for c in before[0] if c[0] == i and c[1] == "1m"]) == 11 for i in ids)
    other = tmp_path / "uninterrupted-control"
    other.mkdir()
    control_db, _, _, control = globals()["session"].__wrapped__(
        other, monkeypatch, SimpleNamespace(param={})
    )
    _MultiInstrumentCoordinator(control, SYMBOLS).process(quotes)
    assert authoritative_evidence(database) == authoritative_evidence(control_db)


def test_scheduler_replay_and_binding(session):
    database, _, ids, runtime = session
    _MultiInstrumentCoordinator(runtime, SYMBOLS).process(observations(ids))
    candles, events = evidence(database)
    event = events[0]
    assert runtime.schedule_event(event[1], event[2], event[3]) == event[0]
    assert evidence(database) == (candles, events)
    with pytest.raises(ForwardRuntimeError):
        runtime.prepare_evidence(
            ids[1], "1m", _START.isoformat(), event_id=event[0], proposal_id="foreign"
        )
    with closing(get_readonly_connection(database)) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE proposal_binding_state != 'UNBOUND'"
            ).fetchone()[0]
            == 0
        )


def test_uninterrupted_equals_restart(session, tmp_path, monkeypatch):
    database, _calendars, ids, runtime = session
    quotes = observations(ids)
    _MultiInstrumentCoordinator(runtime, SYMBOLS).process(quotes)
    expected = evidence(database)
    other = tmp_path / "other"
    other.mkdir()
    second_db, second_calendars, second_ids, second_runtime = globals()["session"].__wrapped__(
        other, monkeypatch, SimpleNamespace(param={})
    )
    second_quotes = observations(second_ids)
    coordinator = _MultiInstrumentCoordinator(second_runtime, SYMBOLS)
    for quote in second_quotes:
        coordinator.process((quote,))
        coordinator = _MultiInstrumentCoordinator.open(
            second_db, second_runtime.runtime_id, second_calendars, SYMBOLS
        )
    assert evidence(second_db) == expected


def authoritative_evidence(database):
    """Compare authoritative rows; retain all event lifecycle and decision fields.

    Event creation wall time and the recovery marker are operational metadata.
    Restart health records necessarily exist only on the restarted branch.
    """
    result = {}
    with closing(get_readonly_connection(database)) as conn:
        for table in (
            "forward_quotes",
            "forward_runtime_candles",
            "decision_journal_events",
            "decision_journal_audits",
            "decision_journal_outcomes",
            "decision_journal_manifests",
        ):
            result[table] = [
                tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1,2,3")
            ]
        result["events"] = []
        for row in conn.execute("SELECT * FROM forward_scheduler_events ORDER BY event_id"):
            event = dict(row)
            event.pop("created_at")
            if event["result_json"] == '{"recovered":true}':
                event["result_json"] = None
            result["events"].append(event)
        result["health"] = [
            tuple(row)
            for row in conn.execute(
                "SELECT * FROM decision_journal_health WHERE kind != 'RESTART_RECONCILED' ORDER BY event_id"
            )
        ]
        result["cursors"] = json.loads(
            conn.execute("SELECT state_json FROM forward_runtimes").fetchone()[0]
        )["cursor"]
    return result


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("minutes", [2, 12])
def test_scheduling_only_rejects_sma_without_mutation(session, restart, minutes):
    database, calendars, ids, runtime = session
    _MultiInstrumentCoordinator(runtime, SYMBOLS).process(
        observations(ids, gap=False)[: minutes * 3]
    )
    if restart:
        runtime = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    before = authoritative_evidence(database)
    event = max(
        (e for e in before["events"] if e["interval"] == "1m"),
        key=lambda e: e["candle_timestamp"],
    )
    assert event["status"] == "PENDING" and event["proposal_binding_state"] == "UNBOUND"
    with pytest.raises(ForwardRuntimeError, match="Scheduling-only"):
        runtime.build_sma_proposal(
            event["event_id"], fast_window=2, slow_window=3, record_warmup=True
        )
    assert authoritative_evidence(database) == before
    assert before["decision_journal_events"] == []
    evidence(database)


def test_coordinator_rejects_forged_and_non_scheduling_runtimes(session):
    database, calendars, ids, runtime = session

    class ForgedRuntime:
        database_path = database
        max_candidates = runtime.max_candidates
        instrument_ids = ids
        scheduling_only = True

    with pytest.raises(ForwardRuntimeError, match="persisted forward runtime"):
        _MultiInstrumentCoordinator(ForgedRuntime(), SYMBOLS)

    ordinary_runtime = ForwardRuntimeService.create(
        database,
        "ordinary-synthetic-runtime",
        runtime.account_id,
        ids,
        calendars,
        created_at=_START.isoformat(),
    )
    with pytest.raises(ForwardRuntimeError, match="synthetic scheduling mode mismatch"):
        _MultiInstrumentCoordinator(ordinary_runtime, SYMBOLS)


@pytest.mark.parametrize("session", [{"cap": 1}, {"cap": 2}], indirect=True)
def test_capped_coordinator_rejected_before_writes(session):
    database, calendars, _, runtime = session
    with closing(get_readonly_connection(database)) as conn:
        before = "\n".join(conn.iterdump())
    with pytest.raises(ForwardRuntimeError, match="max_candidates"):
        _MultiInstrumentCoordinator(runtime, SYMBOLS)
    with pytest.raises(ForwardRuntimeError, match="max_candidates"):
        _MultiInstrumentCoordinator.open(database, runtime.runtime_id, calendars, SYMBOLS)
    with closing(get_readonly_connection(database)) as conn:
        assert "\n".join(conn.iterdump()) == before


@pytest.mark.parametrize("session", [{"cap": 3}, {"cap": 5}], indirect=True)
def test_changing_peer_ranks_recover_identically(session, tmp_path, monkeypatch):
    database, _, ids, runtime = session
    quotes = tuple(
        replace(
            q,
            price=Decimal(100)
            + (Decimal(10) if q.instrument_id == ids[1] and q.sequence >= 2 else Decimal(0)),
        )
        for q in observations(ids, gap=False)
    )
    _MultiInstrumentCoordinator(runtime, SYMBOLS).process(quotes)
    expected = authoritative_evidence(database)
    other = tmp_path / "changing-rank-restart"
    other.mkdir()

    second_db, calendars, _, second = globals()["session"].__wrapped__(
        other, monkeypatch, SimpleNamespace(param={"cap": runtime.max_candidates})
    )
    coordinator = _MultiInstrumentCoordinator(second, SYMBOLS)
    for quote in quotes:
        assert coordinator.process((quote,))[0].accepted
        coordinator = _MultiInstrumentCoordinator.open(
            second_db, second.runtime_id, calendars, SYMBOLS
        )
    assert authoritative_evidence(second_db) == expected
    coordinator.process(quotes)
    assert authoritative_evidence(second_db) == expected
    evidence(database)
    evidence(second_db)


@pytest.mark.parametrize("session", [{"schema": 17}], indirect=True)
def test_v18_migration_preserves_events_and_rolls_back(session):
    database, _, ids, runtime = session
    _MultiInstrumentCoordinator(runtime, SYMBOLS).process(observations(ids, gap=False)[:6])

    def stored_records(conn):
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_metadata'"
            )
        ]
        return {
            table: [tuple(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables
        }

    with closing(get_connection(database)) as conn:
        original_records = stored_records(conn)
        before = [
            tuple(r)
            for r in conn.execute("SELECT * FROM forward_scheduler_events ORDER BY event_id")
        ]
        conn.execute("""CREATE TRIGGER reject_v18 BEFORE INSERT ON schema_metadata
            WHEN NEW.version = 18 BEGIN SELECT RAISE(ABORT, 'reject v18'); END""")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="reject v18"):
        initialize_database(database)
    with closing(get_connection(database)) as conn:
        assert stored_records(conn) == original_records
        assert conn.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 17
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='protect_forward_event_identity'"
            ).fetchone()
            is None
        )
        assert [
            tuple(r)
            for r in conn.execute("SELECT * FROM forward_scheduler_events ORDER BY event_id")
        ] == before
        conn.execute("DROP TRIGGER reject_v18")
        conn.commit()
    initialize_database(database)
    initialize_database(database)
    with closing(get_readonly_connection(database)) as conn:
        assert stored_records(conn) == original_records
        assert conn.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 18
        assert [
            tuple(r)
            for r in conn.execute("SELECT * FROM forward_scheduler_events ORDER BY event_id")
        ] == before
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='protect_forward_event_identity'"
            ).fetchone()[0]
            == 1
        )
    with closing(get_connection(database)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            conn.execute(
                "UPDATE forward_scheduler_events SET instrument_id=? WHERE event_id=?",
                (ids[1] if before[0][2] != ids[1] else ids[0], before[0][0]),
            )
        conn.rollback()
        assert stored_records(conn) == original_records


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_id", "reassigned"),
        ("runtime_id", "foreign"),
        ("instrument_id", 2),
        ("interval", "15m"),
        ("candle_timestamp", "foreign"),
        ("decision_timestamp", "foreign"),
        ("proposal_id", "foreign"),
        ("created_at", "foreign"),
        ("candle_timestamp", None),
    ],
)
def test_scheduler_identity_immutable_on_fresh_database(session, field, value):
    database, _, ids, runtime = session
    _MultiInstrumentCoordinator(runtime, SYMBOLS).process(observations(ids, gap=False)[:6])
    with closing(get_connection(database)) as conn:
        event = conn.execute(
            "SELECT * FROM forward_scheduler_events ORDER BY instrument_id LIMIT 1"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            conn.execute(
                f"UPDATE forward_scheduler_events SET {field}=? WHERE event_id=?",
                (value, event["event_id"]),
            )
        conn.rollback()
        assert tuple(
            conn.execute(
                "SELECT * FROM forward_scheduler_events WHERE event_id=?", (event["event_id"],)
            ).fetchone()
        ) == tuple(event)
        conn.execute(
            "UPDATE forward_scheduler_events SET status='REJECTED', result_json='{}' WHERE event_id=?",
            (event["event_id"],),
        )
        conn.commit()
        assert (
            conn.execute(
                "SELECT status FROM forward_scheduler_events WHERE event_id=?", (event["event_id"],)
            ).fetchone()[0]
            == "REJECTED"
        )
