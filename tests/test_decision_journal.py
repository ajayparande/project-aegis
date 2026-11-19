"""Synthetic journal persistence, horizon, failure and Phase 8 integration regressions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aegis import database as database_module
from aegis import decision_journal as journal
from aegis.database import get_connection, get_readonly_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "journal.db"
    initialize_database(db)
    instrument = register_instrument(
        db,
        InstrumentInput(
            "NSE",
            "JOURNAL",
            "Synthetic journal equity",
            "EQUITY",
            segment="NSE",
            tick_size=Decimal("0.05"),
        ),
    )
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": "2026-01-05",
                        "coverage_end": "2026-01-09",
                        "source": "Generated synthetic test calendar",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "15:30"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(calendar_path))
    baseline = tmp_path / "baseline.csv"
    baseline.write_text(
        "timestamp,open,high,low,close,volume\n2026-01-05T03:45:00Z,100,100,100,100,1000\n"
    )
    imported = import_ohlcv_csv(db, baseline, "NSE", "JOURNAL", "1m")
    declare_import_source_use_status(
        db,
        imported.import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic",
        rationale="synthetic pre-decision evidence",
    )
    with closing(get_connection(db)) as conn:
        conn.execute(
            "UPDATE data_imports SET completed_at='2026-01-05T03:46:00Z' WHERE id=?",
            (imported.import_id,),
        )
        conn.execute(
            "UPDATE import_source_use_history SET declared_at='2026-01-05T03:45:00Z' WHERE import_id=?",
            (imported.import_id,),
        )
        conn.commit()
    return db, instrument.id, calendar_path


def _event(registered_id: int, **changes) -> journal.DecisionEvent:
    return journal.DecisionEvent(
        **{
            "run_id": "synthetic-run",
            "event_id": "candle-1",
            "instrument_id": registered_id,
            "strategy_id": "sma",
            "strategy_version": "1.0.0",
            "state": "BUY",
            "decision_at": "2026-01-05T03:46:00Z",
            "candle_completed_at": "2026-01-05T03:46:00Z",
            "quantity": 1,
            "reference_price": Decimal("100.00"),
            "evidence_references": ("synthetic:candle:1",),
            **changes,
        }
    )


@pytest.mark.parametrize("state", ["BUY", "SELL", "HOLD", "BLOCKED", "NO_DECISION"])
def test_decision_states_are_distinct_and_immutable(environment, state):
    db, instrument_id, _ = environment
    event = _event(instrument_id, state=state, quantity=1 if state in {"BUY", "SELL"} else None)
    identity = journal.append_decision(db, event)
    assert journal.get_decision(db, identity)["decision"]["state"] == state
    with pytest.raises(FrozenInstanceError):
        event.state = "HOLD"
    detached = journal.get_decision(db, identity)
    detached["decision"]["state"] = "changed"
    assert journal.get_decision(db, identity)["decision"]["state"] == state


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": ""},
        {"event_id": "contains spaces"},
        {"instrument_id": True},
        {"decision_at": "2026-01-05T09:16:00"},
        {"candle_completed_at": "2026-01-05T03:47:00Z"},
        {"information_cutoff": "2026-01-05T03:47:00Z"},
        {"reference_price": float("nan")},
        {"reference_price": Decimal("Infinity")},
        {"reference_price": Decimal("1E+999999999")},
        {"quantity": True},
        {"quantity": -1},
        {"confidence": Decimal("1.01")},
        {"state": "NO_DECISION", "quantity": None, "confidence": Decimal("0.5")},
        {"stage_durations_ns": (("model_inference", -1),)},
        {"stage_durations_ns": (("model_inference", True),)},
        {"role": "EXECUTOR"},
        {"model_id": "lightgbm", "model_version": None},
        {"reason_codes": ("arbitrary secret prose",)},
    ],
)
def test_invalid_identity_time_and_numeric_contracts(environment, changes):
    with pytest.raises((journal.JournalError, ValueError)):
        _event(environment[1], **changes)


def test_identical_delivery_restart_and_conflicting_payloads(environment):
    db, instrument_id, _ = environment
    event = _event(instrument_id)
    first = journal.append_decision(db, event)
    retried = replace(
        event,
        created_at=datetime.now(UTC).isoformat(),
        stage_durations_ns=(("candle_validation", 100),),
    )
    assert journal.append_decision(db, retried) == first
    assert len(journal.list_decisions(db, event.run_id)) == 1
    with pytest.raises(journal.JournalConflictError):
        journal.append_decision(db, replace(event, quantity=2))
    journal.append_validation(db, first, "BLOCKED", ("RISK_LIMIT",))
    assert journal.append_validation(db, first, "BLOCKED", ("RISK_LIMIT",))
    with pytest.raises(journal.JournalConflictError):
        journal.append_validation(db, first, "ALLOWED")
    assert journal.get_decision(db, first)["decision"]["quantity"] == 1


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "REPLACE"])
def test_database_rejects_event_mutation_even_through_raw_sql(environment, operation):
    db, instrument_id, _ = environment
    identity = journal.append_decision(db, _event(instrument_id))
    with closing(get_connection(db)) as conn, pytest.raises(sqlite3.IntegrityError):
        if operation == "UPDATE":
            conn.execute(
                "UPDATE decision_journal_events SET state='SELL' WHERE decision_id=?", (identity,)
            )
        elif operation == "DELETE":
            conn.execute("DELETE FROM decision_journal_events WHERE decision_id=?", (identity,))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO decision_journal_events SELECT * FROM decision_journal_events"
            )


def test_event_and_required_audit_roll_back_together(environment):
    db, instrument_id, _ = environment
    with closing(get_connection(db)) as conn:
        conn.execute("""CREATE TRIGGER reject_journal_audit BEFORE INSERT ON decision_journal_audits
                     BEGIN SELECT RAISE(ABORT, 'injected journal write failure'); END""")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        journal.append_decision(db, _event(instrument_id))
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM decision_journal_events").fetchone()[0] == 0


def test_manifest_redaction_size_bounds_and_nested_immutability(environment):
    db, _, _ = environment
    payload = {
        "scope": "synthetic",
        "strategy_id": "sma",
        "data_manifest": {
            "api_key": "do-not-store-this",
            "prompt": "private model prose",
            "note": "password=sensitive-value",
            "imports": [1],
        },
    }
    journal.record_manifest(db, "manifest-1", payload)
    payload["data_manifest"]["imports"].append(2)
    with closing(get_readonly_connection(db)) as conn:
        raw = conn.execute("SELECT payload_json FROM decision_journal_manifests").fetchone()[0]
    assert "do-not-store-this" not in raw and "private model prose" not in raw
    assert "sensitive-value" not in raw and "[REDACTED]" in raw
    assert json.loads(raw)["data_manifest"]["imports"] == [1]
    with pytest.raises(journal.JournalError):
        journal.record_manifest(
            db, "oversized", {"data_manifest": {str(i): "x" * 1000 for i in range(20)}}
        )


def _future_candles(
    db: Path,
    tmp_path: Path,
    count: int = 60,
    *,
    skip: int | None = None,
    start: datetime | None = None,
    symbol: str = "JOURNAL",
):
    start = start or datetime(2026, 1, 5, 3, 46, tzinfo=UTC)
    lines = ["timestamp,open,high,low,close,volume"]
    for index in range(count):
        if index == skip:
            continue
        timestamp = (start + timedelta(minutes=index)).isoformat()
        lines.append(f"{timestamp},100,102,99,101,1000")
    path = tmp_path / "future.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    imported = import_ohlcv_csv(db, path, "NSE", symbol, "1m")
    declare_import_source_use_status(
        db,
        imported.import_id,
        "VERIFIED_PERMITTED",
        declared_by="synthetic fixture",
        rationale="Generated test prices only.",
    )
    return imported.import_id


@pytest.mark.parametrize("horizon", [5, 15, 60])
def test_outcomes_use_qualified_future_data_and_never_rewrite_decisions(
    environment, tmp_path, horizon
):
    db, instrument_id, _ = environment
    decision = _event(instrument_id, state="HOLD", quantity=None)
    identity = journal.append_decision(db, decision)
    original = journal.get_decision(db, identity)["decision"]
    _future_candles(db, tmp_path)
    outcome = journal.evaluate_outcome(
        db,
        identity,
        horizon_minutes=horizon,
        evaluated_at=datetime.now(UTC).isoformat(),
        hypothetical_cost=Decimal("0.002"),
    )
    with closing(get_readonly_connection(db)) as conn:
        row = conn.execute(
            "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
        ).fetchone()
    result = json.loads(row[0])
    assert result["coverage_status"] == "COMPLETE"
    assert Decimal(result["observed_return"]) == Decimal("0.01")
    assert Decimal(result["hypothetical_net_after_cost"]) == Decimal("0.008")
    assert result["actual_paper_pnl_ref"] is None
    assert journal.get_decision(db, identity)["decision"] == original
    assert (
        journal.evaluate_outcome(
            db,
            identity,
            horizon_minutes=horizon,
            evaluated_at=datetime.now(UTC).isoformat(),
            hypothetical_cost=Decimal("0.002"),
        )
        == outcome
    )


def test_unelapsed_and_incomplete_horizons_do_not_fabricate_outcomes(environment, tmp_path):
    db, instrument_id, _ = environment
    identity = journal.append_decision(db, _event(instrument_id))
    with pytest.raises(journal.JournalError, match="not elapsed"):
        journal.evaluate_outcome(
            db, identity, horizon_minutes=5, evaluated_at="2026-01-05T03:48:00Z"
        )
    _future_candles(db, tmp_path, count=5, skip=2)
    outcome = journal.evaluate_outcome(
        db, identity, horizon_minutes=5, evaluated_at=datetime.now(UTC).isoformat()
    )
    with closing(get_readonly_connection(db)) as conn:
        result = json.loads(
            conn.execute(
                "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
            ).fetchone()[0]
        )
    assert result["coverage_status"] == "INCOMPLETE"
    assert result["observed_return"] is None and result["hypothetical_net_after_cost"] is None


def test_migration_16_is_additive_idempotent_and_transactional(tmp_path, monkeypatch):
    db = tmp_path / "old-v15.db"
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 15)
    initialize_database(db)
    with closing(get_connection(db)) as conn:
        conn.execute("INSERT INTO sources(name,source_type) VALUES('preserved','synthetic')")
        conn.execute("""CREATE TRIGGER fail_v16 BEFORE INSERT ON schema_metadata WHEN NEW.version=16
                     BEGIN SELECT RAISE(ABORT,'injected migration failure'); END""")
        conn.commit()
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 16)
    with pytest.raises(sqlite3.IntegrityError):
        initialize_database(db)
    with closing(get_connection(db)) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 15
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'decision_journal_%'"
        ).fetchall()
        assert conn.execute("SELECT name FROM sources").fetchone()[0] == "preserved"
        conn.execute("DROP TRIGGER fail_v16")
        conn.commit()
    initialize_database(db)
    initialize_database(db)
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 16
        assert (
            conn.execute("SELECT COUNT(*) FROM schema_metadata WHERE version=16").fetchone()[0] == 1
        )


def _runtime(tmp_path: Path):
    # Reuse the reviewed synthetic Phase 8 fixture rather than inventing execution evidence.
    from test_forward_runtime import _setup

    return _setup(tmp_path)


def _runtime_buy(tmp_path: Path, *, action: str = "BUY"):
    from test_forward_runtime import _provider

    db, calendars, instruments, runtime = _runtime(tmp_path)
    quotes = list(_provider(instruments, count=4))
    runtime.process_quote(quotes[0])
    event = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event, action, 0 if action == "HOLD" else 1)
    snapshot = runtime.execution_snapshot(instruments[0], quotes[1])
    return db, calendars, instruments, runtime, quotes, event, proposal, snapshot


@pytest.mark.parametrize("action", ["BUY", "HOLD"])
def test_runtime_order_and_hold_journal_linkage_restart_and_replay(tmp_path, action):
    from aegis.forward_runtime import ForwardRuntimeService

    db, calendars, _, runtime, _, event, proposal, snapshot = _runtime_buy(tmp_path, action=action)
    result = runtime.route_event(event, proposal, snapshot)
    assert result.status == ("FILLED" if action == "BUY" else "HOLD"), result.reason
    records = journal.list_decisions(db, runtime.runtime_id)
    assert len(records) == 1
    original = records[0]["decision"]
    durations = original["processing_stage_durations_ns"]
    assert durations["candle_validation"] >= 0 and durations["signal_generation"] >= 0
    assert durations["feature_preparation"] is None and durations["model_inference"] is None
    executions = [a for a in records[0]["audits"] if a["stage"] == "EXECUTION"]
    if action == "BUY":
        assert executions[0]["order_id"] == result.order_id and executions[0]["fill_id"]
    else:
        assert result.order_id is None and executions == []
        assert any(a["status"] == "HOLD" for a in records[0]["audits"])
    restored = ForwardRuntimeService.open(db, runtime.runtime_id, calendars)
    assert restored.route_event(event, proposal, snapshot).duplicate is True
    assert journal.list_decisions(db, runtime.runtime_id)[0]["decision"] == original
    assert journal.reconcile_journal(db, runtime.runtime_id)["status"] == "RECONCILED"


@pytest.mark.parametrize(
    ("action", "reject", "crash_before_route"),
    [("HOLD", False, False), ("HOLD", True, False), ("HOLD", False, True), ("BUY", True, False)],
)
def test_runtime_audit_gap_stays_unresolved_until_authenticated_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    reject: bool,
    crash_before_route: bool,
) -> None:
    """A real scheduler result cannot be replaced with the captured requested action."""
    from aegis.forward_runtime import ForwardRuntimeService

    db, calendars, _, runtime, _, event, proposal, snapshot = _runtime_buy(tmp_path, action=action)
    if reject:
        snapshot = replace(snapshot, reference_price=snapshot.reference_price + Decimal(1))

    def crash(*args, **kwargs):
        raise RuntimeError("F3_FINAL_AUDIT_CRASH")

    with monkeypatch.context() as patch:
        if crash_before_route:
            patch.setattr(ForwardRuntimeService, "_route_event", crash)
        else:
            patch.setattr(journal, "finish_runtime_decision", crash)
        with pytest.raises(RuntimeError, match="F3_FINAL_AUDIT_CRASH"):
            runtime.route_event(event, proposal, snapshot)

    record = journal.list_decisions(db, runtime.runtime_id)[0]
    identity, original = record["decision_id"], record["decision"]
    assert original["original_signal"] == action
    assert [a["stage"] for a in record["audits"]] == ["PERSISTED"]
    expected = "REJECTED" if reject else "HOLD"
    with closing(get_readonly_connection(db)) as connection:
        scheduler = connection.execute(
            "SELECT status, proposal_binding_state FROM forward_scheduler_events WHERE event_id=?",
            (event,),
        ).fetchone()
        assert scheduler["status"] == ("PENDING" if crash_before_route else expected)
        assert scheduler["proposal_binding_state"] == ("UNBOUND" if crash_before_route else "BOUND")
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0

    def forbid_write(*args, **kwargs):
        raise AssertionError("reconciliation attempted to write")

    with monkeypatch.context() as patch:
        patch.setattr(journal, "get_connection", forbid_write)
        report = journal.reconcile_journal(db, runtime.runtime_id)
        assert report == journal.reconcile_journal(db, runtime.runtime_id)
    assert report["status"] == "UNRESOLVED" and not report["audit_complete"]
    assert report["complete"]  # All rows inspected; terminal audit evidence is incomplete.
    assert report["counts"] == {"PENDING": 1}
    assert report["requested_counts"] == {action: 1}
    assert report["unresolved_decision_ids"] == [identity]
    if not crash_before_route:
        assert report["authoritative_counts"] == {expected: 1}

    for _ in range(2):
        runtime = ForwardRuntimeService.open(db, runtime.runtime_id, calendars)
        assert journal.reconcile_journal(db, runtime.runtime_id) == report
        assert journal.get_decision(db, identity) == record
    for _ in range(3):
        assert runtime.route_event(event, proposal, snapshot).status == expected
    final = journal.get_decision(db, identity)
    assert final["decision"] == original
    assert len([a for a in final["audits"] if a["stage"] == "VALIDATION"]) == 1
    assert not any(a["stage"] == "EXECUTION" for a in final["audits"])
    report = journal.reconcile_journal(db, runtime.runtime_id)
    assert report["status"] == "RECONCILED" and report["audit_complete"]
    assert report["counts"] == {"BLOCKED" if reject else "HOLD": 1}
    assert report["authoritative_counts"] == {expected: 1}
    with closing(get_readonly_connection(db)) as connection:
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_runtime_finalization_rejects_false_hold_and_ignores_observation_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _, _, runtime, _, event, proposal, snapshot = _runtime_buy(tmp_path, action="HOLD")
    snapshot = replace(snapshot, reference_price=Decimal(101))
    with monkeypatch.context() as patch:
        patch.setattr(journal, "finish_runtime_decision", lambda *a, **k: None)
        assert runtime.route_event(event, proposal, snapshot).status == "REJECTED"
    identity = journal.list_decisions(db, runtime.runtime_id)[0]["decision_id"]
    with pytest.raises(journal.JournalConflictError, match="scheduler outcome"):
        journal.finish_runtime_decision(db, identity, "HOLD")
    # Public validation labels are observations, not an authenticated final result.
    journal.append_validation(db, identity, "HOLD")
    assert journal.reconcile_journal(db, runtime.runtime_id)["counts"] == {"PENDING": 1}
    assert runtime.route_event(event, proposal, snapshot).status == "REJECTED"
    report = journal.reconcile_journal(db, runtime.runtime_id)
    assert report["status"] == "RECONCILED" and report["counts"] == {"BLOCKED": 1}


def test_journal_reconciliation_preserves_f1_committed_buy_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aegis.forward_paper_trading import ForwardPaperTradingEngine
    from aegis.forward_runtime import ForwardRuntimeService

    db, calendars, _, runtime, _, event, proposal, snapshot = _runtime_buy(tmp_path)
    original = ForwardPaperTradingEngine._route_runtime_proposal

    def after_commit(self, *args, **kwargs):
        assert original(self, *args, **kwargs).status == "FILLED"
        raise RuntimeError("F3_AFTER_FINANCIAL_COMMIT")

    with monkeypatch.context() as patch:
        patch.setattr(ForwardPaperTradingEngine, "_route_runtime_proposal", after_commit)
        with pytest.raises(RuntimeError, match="F3_AFTER_FINANCIAL_COMMIT"):
            runtime.route_event(event, proposal, snapshot)
    before = journal.reconcile_journal(db, runtime.runtime_id)
    assert before["status"] == "UNRESOLVED" and before["counts"] == {"PENDING": 1}
    with closing(get_readonly_connection(db)) as connection:
        financial = {
            table: [
                tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in ("paper_orders", "paper_fills", "paper_positions", "paper_ledger_entries")
        }
    for _ in range(2):
        runtime = ForwardRuntimeService.open(db, runtime.runtime_id, calendars)
        report = journal.reconcile_journal(db, runtime.runtime_id)
        assert report["status"] == "RECONCILED" and report["counts"] == {"FILLED": 1}
        with closing(get_readonly_connection(db)) as connection:
            for table, rows in financial.items():
                assert [
                    tuple(row)
                    for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
                ] == rows


@pytest.mark.parametrize("contradictory_status", ["HOLD", "REJECTED"])
def test_reconciliation_rejects_scheduler_and_terminal_audit_disagreement(
    tmp_path: Path,
    contradictory_status: str,
) -> None:
    db, _, _, runtime, _, event, proposal, snapshot = _runtime_buy(tmp_path, action="HOLD")
    assert runtime.route_event(event, proposal, snapshot).status == "HOLD"
    original = journal.list_decisions(db, runtime.runtime_id)[0]
    # Corrupt only the disposable scheduler outcome, preserving protected proposal
    # bindings and the authentic append-only HOLD audit.
    with closing(get_connection(db)) as connection:
        connection.execute(
            "UPDATE forward_scheduler_events SET status=?, result_json=? WHERE event_id=?",
            (
                contradictory_status,
                json.dumps({"status": "REJECTED", "reason": "Synthetic mismatch"}),
                event,
            ),
        )
        connection.commit()
    report = journal.reconcile_journal(db, runtime.runtime_id)
    assert report["status"] == "UNRESOLVED" and not report["audit_complete"]
    assert report["counts"] == {"PENDING": 1}
    assert report["unresolved_decision_ids"] == [original["decision_id"]]
    assert journal.get_decision(db, original["decision_id"]) == original


def test_risk_rejection_and_missing_snapshot_are_journaled_as_blocked(tmp_path):
    db, _, _, runtime, _, event, proposal, _ = _runtime_buy(tmp_path)
    result = runtime.route_event(event, proposal, None)
    assert result.status == "REJECTED"
    record = journal.list_decisions(db, runtime.runtime_id)[0]
    assert record["decision"]["original_signal"] == "BUY"
    assert any(a["status"] == "BLOCKED" for a in record["audits"])


def test_feed_outage_is_no_decision_not_hold(tmp_path):
    from test_forward_runtime import _provider

    db, _, instruments, runtime = _runtime(tmp_path)
    runtime.process_quote(next(_provider(instruments, count=1)))
    runtime.monitor_feed_health("2026-01-05T03:50:00Z")
    records = journal.list_decisions(db, runtime.runtime_id)
    assert len(records) == 1 and records[0]["decision"]["state"] == "NO_DECISION"
    assert records[0]["decision"]["confidence"] is None


@pytest.mark.parametrize("stage", ["intent", "result"])
def test_journal_failure_blocks_or_rolls_back_new_buy(tmp_path, stage):
    db, _, _, runtime, _, event, proposal, snapshot = _runtime_buy(tmp_path)
    table = "decision_journal_events" if stage == "intent" else "decision_journal_audits"
    condition = "" if stage == "intent" else "WHEN NEW.stage='EXECUTION'"
    with closing(get_connection(db)) as conn:
        conn.execute(
            f"CREATE TRIGGER fail_journal BEFORE INSERT ON {table} {condition} BEGIN SELECT RAISE(ABORT,'injected journal failure'); END"
        )
        conn.commit()
    result = runtime.route_event(event, proposal, snapshot)
    assert result.status == "REJECTED" and "journal" in result.reason.lower()
    with closing(get_readonly_connection(db)) as conn:
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM forward_execution_authorizations WHERE status='CONSUMED'"
            ).fetchone()[0]
            == 0
        )


def test_supplemental_journal_failure_does_not_suppress_protective_exit(tmp_path):
    db, _, _, runtime, quotes, event, proposal, snapshot = _runtime_buy(tmp_path)
    proposal = replace(proposal, target_price=Decimal("100.10"))
    assert runtime.route_event(event, proposal, snapshot).status == "FILLED"
    with closing(get_connection(db)) as conn:
        conn.execute("""CREATE TRIGGER fail_exit_journal BEFORE INSERT ON decision_journal_events
                     WHEN NEW.state='SELL' BEGIN SELECT RAISE(ABORT,'injected analytics failure'); END""")
        conn.commit()
    result = runtime.process_quote(quotes[2]).exit_result
    assert result is not None and result.status == "FILLED", result
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0


def test_restart_repairs_exit_journal_from_consumed_authorization_without_refilling(tmp_path):
    from aegis.forward_runtime import ForwardRuntimeService

    db, calendars, _, runtime, quotes, event, proposal, snapshot = _runtime_buy(tmp_path)
    proposal = replace(proposal, target_price=Decimal("100.10"))
    assert runtime.route_event(event, proposal, snapshot).status == "FILLED"
    with closing(get_connection(db)) as conn:
        conn.execute("""CREATE TRIGGER fail_exit BEFORE INSERT ON decision_journal_events
                     WHEN NEW.state='SELL' BEGIN SELECT RAISE(ABORT,'synthetic fault'); END""")
        conn.commit()
    assert runtime.process_quote(quotes[2]).exit_result.status == "FILLED"
    assert len(journal.list_decisions(db, runtime.runtime_id)) == 1
    gaps = journal.reconcile_journal(db, runtime.runtime_id)
    assert gaps["status"] == "UNRESOLVED" and len(gaps["unresolved_order_ids"]) == 1
    # A continuing analytics failure must not turn a healthy financial account into a hard halt.
    restored = ForwardRuntimeService.open(db, runtime.runtime_id, calendars)
    assert restored._journal_recovery_error
    with closing(get_connection(db)) as conn:
        assert conn.execute("SELECT halted FROM paper_accounts").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_exit")
        conn.commit()
    restored = ForwardRuntimeService.open(db, runtime.runtime_id, calendars)
    assert not restored._journal_recovery_error
    records = journal.list_decisions(db, runtime.runtime_id)
    sell = next(r for r in records if r["decision"]["state"] == "SELL")
    assert sell["decision"]["quality_status"] == "UNKNOWN"
    assert any(a["stage"] == "RECOVERY" for a in sell["audits"])
    execution = next(a for a in sell["audits"] if a["stage"] == "EXECUTION")
    assert execution["fill_id"] and execution["duration_ns"] is None
    assert journal.recover_order_links(db, runtime.runtime_id) == ()
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM paper_realized_pnl").fetchone()[0] == 1


def test_runtime_risk_rejection_links_rejected_order_without_fill(tmp_path):
    db, _, _, runtime, _, event, proposal, snapshot = _runtime_buy(tmp_path)
    with closing(get_connection(db)) as conn:
        row = conn.execute("SELECT risk_limits_json FROM paper_accounts").fetchone()
        limits = json.loads(row[0])
        limits["max_order_value"] = "50"
        conn.execute("UPDATE paper_accounts SET risk_limits_json=?", (json.dumps(limits),))
        conn.commit()
    result = runtime.route_event(event, proposal, snapshot)
    assert result.status == "REJECTED" and "Maximum order value" in result.reason
    record = journal.list_decisions(db, runtime.runtime_id)[0]
    assert record["decision"]["original_signal"] == "BUY"
    assert any(a["stage"] == "VALIDATION" and a["status"] == "BLOCKED" for a in record["audits"])
    execution = next(a for a in record["audits"] if a["stage"] == "EXECUTION")
    assert execution["order_id"] == result.order_id and execution["fill_id"] is None


def test_stale_quote_and_bad_proposal_have_health_and_no_decision_evidence(tmp_path):
    db, _, _, runtime, quotes, event, proposal, snapshot = _runtime_buy(tmp_path)
    # Wrong instrument cannot inherit the event's execution authority.
    wrong = replace(proposal, instrument_id=proposal.instrument_id + 99)
    assert runtime.route_event(event, wrong, snapshot).status == "REJECTED"
    records = journal.list_decisions(db, runtime.runtime_id)
    assert records[0]["decision"]["state"] == "NO_DECISION"
    stale = replace(quotes[2], event_id="stale-quote", received_at="2026-01-05T04:00:00Z")
    assert not runtime.process_quote(stale).accepted
    with closing(get_readonly_connection(db)) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM decision_journal_health WHERE kind='QUOTE_REJECTED'"
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_journal_labels_and_shadow_predictions_cannot_authorize_runtime_execution(tmp_path):
    db, _, instruments, runtime, _, event, proposal, _snapshot = _runtime_buy(tmp_path)
    shadow = _event(
        instruments[0],
        run_id=runtime.runtime_id,
        event_id=event,
        role="SHADOW",
        model_id="lightgbm-research",
        model_version="untrained-fixture",
        feature_set_version="test-v1",
        calendar_confidence="CONFIRMED",
        quality_status="PASS",
    )
    identity = journal.append_decision(db, shadow)
    journal.append_validation(db, identity, "ALLOWED")
    assert runtime.route_event(event, proposal, None).status == "REJECTED"
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert journal.get_decision(db, identity)["decision"]["role"] == "SHADOW"


@pytest.mark.parametrize(
    "table",
    [
        "decision_journal_audits",
        "decision_journal_health",
        "decision_journal_manifests",
        "decision_journal_outcomes",
    ],
)
def test_all_journal_tables_enforce_append_only_sql(environment, tmp_path, table):
    db, instrument_id, _ = environment
    identity = journal.append_decision(db, _event(instrument_id))
    journal.record_health(db, "synthetic-run", "health-1", "STARTED", "2026-01-05T03:46:00Z")
    journal.record_manifest(db, "synthetic-run", {"scope": "synthetic"})
    _future_candles(db, tmp_path, 5)
    journal.evaluate_outcome(
        db, identity, horizon_minutes=5, evaluated_at=datetime.now(UTC).isoformat()
    )
    with closing(get_connection(db)) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
        for sql in (
            f"DELETE FROM {table}",
            f"UPDATE {table} SET payload_json='{{}}'",
            f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


@pytest.mark.parametrize("status", ["UNVERIFIED", "PROHIBITED"])
def test_unqualified_future_data_never_produces_outcome_metrics(environment, tmp_path, status):
    db, instrument_id, _ = environment
    identity = journal.append_decision(db, _event(instrument_id))
    imported = _future_candles(db, tmp_path, 5)
    declare_import_source_use_status(
        db, imported, status, declared_by="synthetic", rationale="test revocation"
    )
    outcome = journal.evaluate_outcome(
        db, identity, horizon_minutes=5, evaluated_at=datetime.now(UTC).isoformat()
    )
    with closing(get_readonly_connection(db)) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
            ).fetchone()[0]
        )
    assert payload["coverage_status"] == "INCOMPLETE" and payload["observed_return"] is None


def test_backfill_requires_new_outcome_version_and_never_changes_original_decision(
    environment, tmp_path
):
    db, instrument_id, _ = environment
    event = _event(instrument_id)
    identity = journal.append_decision(db, event)
    original = journal.get_decision(db, identity)["decision"]
    first = journal.evaluate_outcome(
        db, identity, horizon_minutes=5, evaluated_at=datetime.now(UTC).isoformat()
    )
    _future_candles(db, tmp_path, 5)
    with pytest.raises(journal.JournalConflictError):
        journal.evaluate_outcome(
            db, identity, horizon_minutes=5, evaluated_at=datetime.now(UTC).isoformat()
        )
    second = journal.evaluate_outcome(
        db,
        identity,
        horizon_minutes=5,
        evaluated_at=datetime.now(UTC).isoformat(),
        evaluation_version="v2",
        supersedes_outcome_id=first,
    )
    assert first != second
    assert journal.get_decision(db, identity)["decision"] == original
    with closing(get_readonly_connection(db)) as conn:
        assert (
            conn.execute(
                "SELECT coverage_status FROM decision_journal_outcomes WHERE outcome_id=?", (first,)
            ).fetchone()[0]
            == "INCOMPLETE"
        )
        assert (
            conn.execute(
                "SELECT coverage_status FROM decision_journal_outcomes WHERE outcome_id=?",
                (second,),
            ).fetchone()[0]
            == "COMPLETE"
        )
        assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_outcome_cannot_see_import_arriving_after_evaluation_cutoff(environment, tmp_path):
    db, instrument_id, _ = environment
    identity = journal.append_decision(db, _event(instrument_id))
    _future_candles(db, tmp_path, 5)
    outcome = journal.evaluate_outcome(
        db, identity, horizon_minutes=5, evaluated_at="2026-01-05T04:00:00Z"
    )
    with closing(get_readonly_connection(db)) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
            ).fetchone()[0]
        )
    assert payload["coverage_status"] == "INCOMPLETE" and payload["evidence"] == []


def test_cross_session_holiday_horizons_are_explicit_and_censored(environment):
    db, instrument_id, path = environment
    configured = json.loads(path.read_text())
    configured["calendars"][0]["holidays"] = [{"date": "2026-01-06", "reason": "Synthetic holiday"}]
    path.write_text(json.dumps(configured), encoding="utf-8")
    event = _event(
        instrument_id,
        decision_at="2026-01-05T09:59:00Z",
        candle_completed_at="2026-01-05T03:46:00Z",
        information_cutoff="2026-01-05T09:59:00Z",
    )
    identity = journal.append_decision(db, event)
    for kind in ("TRADING_MINUTES", "WALL_MINUTES"):
        journal.evaluate_outcome(
            db,
            identity,
            horizon_minutes=5,
            horizon_kind=kind,
            evaluated_at=datetime.now(UTC).isoformat(),
        )
    with closing(get_readonly_connection(db)) as conn:
        rows = conn.execute(
            "SELECT horizon_kind, as_of_end, coverage_status FROM decision_journal_outcomes"
        ).fetchall()
    by_kind = {r["horizon_kind"]: r for r in rows}
    assert by_kind["TRADING_MINUTES"]["as_of_end"] == "2026-01-07T03:49:00.000000Z"
    assert by_kind["WALL_MINUTES"]["as_of_end"] == "2026-01-05T10:04:00.000000Z"
    assert all(r["coverage_status"] == "INCOMPLETE" for r in rows)


def test_read_only_journal_queries_never_request_a_write_connection(environment, monkeypatch):
    db, instrument_id, _ = environment
    identity = journal.append_decision(db, _event(instrument_id))

    def forbidden(*args, **kwargs):
        raise AssertionError("read-only view attempted a write connection")

    monkeypatch.setattr(journal, "get_connection", forbidden)
    assert journal.get_decision(db, identity)
    assert journal.list_decisions(db, "synthetic-run")
    assert journal.reconcile_journal(db, "synthetic-run")["unresolved_decision_ids"] == [identity]


def test_wall_horizon_crossing_market_close_is_censored_even_with_last_bar(environment, tmp_path):
    db, instrument_id, _ = environment
    start = datetime(2026, 1, 5, 9, 59, tzinfo=UTC)
    identity = journal.append_decision(
        db,
        _event(
            instrument_id,
            decision_at=start.isoformat(),
            candle_completed_at="2026-01-05T03:46:00Z",
            information_cutoff=start.isoformat(),
        ),
    )
    _future_candles(db, tmp_path, 1, start=start)
    outcome = journal.evaluate_outcome(
        db,
        identity,
        horizon_minutes=5,
        horizon_kind="WALL_MINUTES",
        evaluated_at=datetime.now(UTC).isoformat(),
    )
    with closing(get_readonly_connection(db)) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
            ).fetchone()[0]
        )
    assert payload["coverage_status"] == "INCOMPLETE"
    assert payload["censored_reason"] == "NON_TRADING_OR_PARTIAL_HORIZON"
    assert payload["observed_return"] is None


def test_proposal_secrets_are_not_retained_and_actual_pnl_is_only_a_ledger_reference(tmp_path):
    db, _, _, runtime, quotes, event, proposal, snapshot = _runtime_buy(tmp_path)
    proposal = replace(
        proposal, target_price=Decimal("100.10"), rationale="api_key=synthetic-private-value"
    )
    assert runtime.route_event(event, proposal, snapshot).status == "FILLED"
    exit_result = runtime.process_quote(quotes[2]).exit_result
    assert exit_result.status == "FILLED"
    sell = next(
        r
        for r in journal.list_decisions(db, runtime.runtime_id)
        if r["decision"]["state"] == "SELL"
    )
    assert sell["decision"]["reference_price"] is None
    execution = next(a for a in sell["audits"] if a["stage"] == "EXECUTION")
    assert Decimal(execution["execution_reference_price"]) == Decimal(quotes[2].price)
    _future_candles(db, tmp_path, 5, start=datetime(2026, 1, 5, 3, 47, tzinfo=UTC), symbol="SYNTH")
    outcome = journal.evaluate_outcome(
        db, sell["decision_id"], horizon_minutes=5, evaluated_at=datetime.now(UTC).isoformat()
    )
    with closing(get_readonly_connection(db)) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
            ).fetchone()[0]
        )
        raw = "".join(
            row[0] for row in conn.execute("SELECT payload_json FROM decision_journal_events")
        )
    assert payload["actual_paper_pnl_ref"] == exit_result.order_id
    assert "realized_net_pnl" not in payload
    assert "synthetic-private-value" not in raw and "api_key=" not in raw


def test_database_constraints_reject_missing_required_payload_fields(environment):
    db, instrument_id, _ = environment
    identity = journal.append_decision(db, _event(instrument_id))
    with closing(get_connection(db)) as conn:
        row = dict(
            conn.execute(
                "SELECT * FROM decision_journal_events WHERE decision_id=?", (identity,)
            ).fetchone()
        )
        row.update(decision_id="a" * 64, logical_key="a" * 64, payload_json="{}")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO decision_journal_events ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                tuple(row.values()),
            )


@pytest.mark.parametrize("stage", ["intent", "result"])
def test_non_runtime_exit_gap_can_be_recovered_without_new_financial_effects(tmp_path, stage):
    from test_paper_trading import _buy, _sell, setup

    fixture = setup.__wrapped__(tmp_path)
    db, _, _, _, _, account_id = fixture
    assert _buy(fixture).status == "FILLED"
    table = "decision_journal_events" if stage == "intent" else "decision_journal_audits"
    condition = "NEW.state='SELL'" if stage == "intent" else "NEW.stage='EXECUTION'"
    with closing(get_connection(db)) as conn:
        conn.execute(
            f"CREATE TRIGGER fail_exit BEFORE INSERT ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'synthetic analytics failure'); END"
        )
        conn.commit()
    result = _sell(fixture, quantity=2)
    assert result.status == "FILLED"
    run_id = f"paper-account:{account_id}"
    assert journal.reconcile_journal(db, run_id)["unresolved_order_ids"] == [result.order_id]
    with closing(get_connection(db)) as conn:
        conn.execute("DROP TRIGGER fail_exit")
        conn.commit()
    assert len(journal.recover_paper_order_links(db, account_id)) == 1
    assert journal.recover_paper_order_links(db, account_id) == ()
    assert journal.reconcile_journal(db, run_id)["status"] == "RECONCILED"
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "event_id",
        "strategy_id",
        "strategy_version",
        "model_id",
        "model_version",
        "feature_set_version",
        "proposal_id",
        "evidence_references",
        "configuration_fingerprints",
        "stage_durations_ns",
    ],
)
@pytest.mark.parametrize(
    "credential",
    [
        "api_key:synthetic-private-credential",
        "api_key=synthetic-private-credential",
        "API_KEY : synthetic-private-credential",
        "access_token:synthetic-private-credential",
        "client_secret=synthetic-private-credential",
        "password:synthetic-private-credential",
        "namespace/api-key:synthetic-private-credential",
        "prefix.refresh_token:synthetic-private-credential",
        "authorization bearer synthetic-private-credential",
        "bearer:synthetic-private-credential",
        "secret synthetic-private-credential",
        "apikey%3Asynthetic-private-credential",
    ],
)
def test_credential_assignments_rejected_for_every_identifier(field, credential):
    changes = {"model_id": "research-model", "model_version": "v1", field: credential}
    if field == "evidence_references":
        changes[field] = (credential,)
    if field == "configuration_fingerprints":
        changes[field] = ((credential, "a" * 64),)
    if field == "stage_durations_ns":
        changes[field] = ((credential, 1),)
    with pytest.raises(journal.JournalError) as error:
        _event(1, **changes)
    assert "synthetic-private-credential" not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        "NSE:SYMBOL",
        "sma-v1.2.3",
        "model@v2",
        "550e8400-e29b-41d4-a716-446655440000",
        "secret-strategy-v1",
        "bearer-model",
    ],
)
def test_legitimate_identifier_names_remain_valid(value):
    assert _event(1, strategy_id=value).strategy_id == value


@pytest.mark.parametrize(
    "api",
    ["manifest", "health-run", "health-event", "validation", "outcome-version", "outcome-parent"],
)
def test_public_identifier_inputs_fail_without_persistence_or_secret_errors(environment, api):
    db, instrument, _ = environment
    identity = journal.append_decision(db, _event(instrument))
    secret = "namespace/client_secret:synthetic-private-credential"
    calls = {
        "manifest": lambda: journal.record_manifest(db, secret, {"scope": "synthetic"}),
        "health-run": lambda: journal.record_health(
            db, secret, "health", "STARTED", "2026-01-05T03:46:00Z"
        ),
        "health-event": lambda: journal.record_health(
            db, "run", secret, "STARTED", "2026-01-05T03:46:00Z"
        ),
        "validation": lambda: journal.append_validation(db, secret, "ALLOWED"),
        "outcome-version": lambda: journal.evaluate_outcome(
            db,
            identity,
            horizon_minutes=5,
            evaluated_at=datetime.now(UTC).isoformat(),
            evaluation_version=secret,
        ),
        "outcome-parent": lambda: journal.evaluate_outcome(
            db,
            identity,
            horizon_minutes=5,
            evaluated_at=datetime.now(UTC).isoformat(),
            supersedes_outcome_id=secret,
        ),
    }
    with pytest.raises(journal.JournalError) as error:
        calls[api]()
    assert "synthetic-private-credential" not in str(error.value)
    with closing(get_readonly_connection(db)) as conn:
        for table in (
            "decision_journal_manifests",
            "decision_journal_health",
            "decision_journal_outcomes",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("horizon", [5, 15, 60])
def test_late_baseline_is_unavailable_and_cannot_be_promoted_on_retry(
    environment, tmp_path, horizon
):
    db, instrument, _ = environment
    with closing(get_connection(db)) as conn:
        conn.execute("UPDATE data_imports SET completed_at='2026-01-05T03:48:00Z'")
        conn.commit()
    event = _event(instrument)
    identity = journal.append_decision(db, event)
    original = journal.get_decision(db, identity)["decision"]
    assert original["reference_price"] is None and original["candle_completed_at"] is None
    _future_candles(db, tmp_path)
    outcome = journal.evaluate_outcome(
        db, identity, horizon_minutes=horizon, evaluated_at=datetime.now(UTC).isoformat()
    )
    with closing(get_readonly_connection(db)) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
            ).fetchone()[0]
        )
    assert payload["coverage_status"] == "UNAVAILABLE"
    assert payload["censored_reason"] == "MISSING_QUALIFIED_DECISION_BASELINE"
    assert payload["observed_return"] is None and payload["hypothetical_net_after_cost"] is None
    assert journal.append_decision(db, event) == identity
    assert journal.get_decision(db, identity)["decision"] == original


def test_direct_buy_keeps_future_execution_price_outside_original_decision(tmp_path):
    from test_paper_trading import _buy, setup

    fixture = setup.__wrapped__(tmp_path)
    db, _, _, _, _, account = fixture
    result = _buy(fixture)
    assert result.status == "FILLED"
    record = journal.list_decisions(db, f"paper-account:{account}")[0]
    original = record["decision"]
    assert original["information_cutoff"] == "2026-01-05T03:45:00.000000Z"
    assert original["reference_price"] is None and original["candle_completed_at"] is None
    assert original["source_import_id"] is None
    execution = next(a for a in record["audits"] if a["stage"] == "EXECUTION")
    assert execution["execution_timestamp"] == "2026-01-05T03:48:00Z"
    assert execution["execution_reference_price"] == "100"
    assert _buy(fixture).duplicate
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1


def test_legacy_contaminated_record_cannot_be_used_as_baseline(environment, tmp_path):
    db, instrument, _ = environment
    identity = journal.append_decision(db, _event(instrument))
    legacy = _event(instrument, event_id="legacy", reference_price=Decimal(999))
    with closing(get_connection(db)) as conn:
        row = dict(
            conn.execute(
                "SELECT * FROM decision_journal_events WHERE decision_id=?", (identity,)
            ).fetchone()
        )
        payload = json.loads(row["payload_json"])
        payload.pop("baseline_evidence")
        payload.pop("input_payload_sha256")
        payload["reference_price"] = "999"
        payload["event_id"] = "legacy"
        row.update(
            decision_id=legacy.decision_id,
            logical_key=legacy.decision_id,
            event_id="legacy",
            payload_json=journal._json(payload),
        )
        row["payload_sha256"] = journal._digest(row["payload_json"])
        conn.execute(
            f"INSERT INTO decision_journal_events ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
            tuple(row.values()),
        )
        conn.commit()
    _future_candles(db, tmp_path)
    outcome = journal.evaluate_outcome(
        db, legacy.decision_id, horizon_minutes=5, evaluated_at=datetime.now(UTC).isoformat()
    )
    with closing(get_readonly_connection(db)) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM decision_journal_outcomes WHERE outcome_id=?", (outcome,)
            ).fetchone()[0]
        )
    assert payload["coverage_status"] == "UNAVAILABLE" and payload["observed_return"] is None
    assert journal.get_decision(db, legacy.decision_id)["decision"]["reference_price"] == "999"
    assert journal.append_decision(db, legacy) == legacy.decision_id
    assert journal.get_decision(db, legacy.decision_id)["decision"].get("baseline_evidence") is None
