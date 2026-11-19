"""Synthetic accounting lifecycle, integrity, migration and dashboard evidence tests."""

import hashlib
import json
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from aegis import database, paper_equity
from aegis.database import get_connection, get_readonly_connection, initialize_database
from aegis.forward_runtime import ForwardRuntimeService
from aegis.paper_equity import (
    canonical,
    digest,
    record_equity_checkpoint,
    verify_snapshots,
)
from aegis.paper_trading import reconcile_paper_account, record_paper_mark
from aegis.performance_analytics import read_performance
from aegis.trading_dashboard_data import date_window, read_dashboard
from tests.test_database import _create_version_1_database
from tests.test_forward_runtime import _START, _setup
from tests.test_trading_dashboard import session


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "unused.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "unused.log"))
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "calendar.json"))
    monkeypatch.setattr(socket.socket, "connect", Mock(side_effect=AssertionError("No networking")))
    monkeypatch.setattr(
        "requests.Session.request", Mock(side_effect=AssertionError("No broker HTTP"))
    )


def documents(db, runtime):
    with closing(get_readonly_connection(db)) as conn:
        return [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT payload_json FROM paper_equity_snapshots WHERE session_id=? ORDER BY sequence",
                (runtime.runtime_id,),
            )
        ]


def analytics(db, runtime, now, period="All Time", start=None, end=None):
    window = date_window(period, now, start, end)
    with closing(get_readonly_connection(db)) as conn:
        conn.execute("BEGIN")
        return read_performance(
            conn,
            runtime.account_id,
            session=runtime.runtime_id,
            start=window.start,
            end=window.end,
            as_of=now,
        )


def close_session(db, runtime):
    close = _START.replace(hour=9, minute=45)
    identity = record_equity_checkpoint(
        db,
        runtime.account_id,
        session=runtime.runtime_id,
        as_of=close.isoformat(),
        session_close=True,
    )
    assert identity
    return close


def test_initial_equity_is_recorded_in_account_and_runtime_transactions(tmp_path):
    db, _, _, runtime = _setup(tmp_path, initial_cash=Decimal(10000))
    d = documents(db, runtime)
    assert len(d) == 1 and d[0]["kind"] == "INITIAL"
    assert d[0]["cash"] == d[0]["equity"] == "10000"
    assert d[0]["positions"] == [] and d[0]["valuation_status"] == "COMPLETE"
    assert d[0]["cumulative_fees"] == d[0]["cumulative_realized_net_pnl"] == "0"
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_equity_snapshots").fetchone()[0] == 2
        assert len(verify_snapshots(conn, runtime.account_id, runtime.runtime_id)) == 1


def test_buy_sell_fees_and_position_equity_match_independent_expected_totals(tmp_path):
    db, runtime, now = session(tmp_path)
    records = documents(db, runtime)
    fills = [d for d in records if d["kind"] == "FILL"]
    assert len(fills) == 2
    buy, sell = fills
    assert buy["positions"][0]["quantity"] == 1
    assert Decimal(buy["cash"]) + Decimal(buy["position_market_value"]) == Decimal(buy["equity"])
    assert sell["positions"] == [] and sell["valuation_status"] == "COMPLETE"
    # Existing Phase 8 rounds fills to the instrument tick and models fees separately.
    with closing(get_readonly_connection(db)) as conn:
        rows = conn.execute("""SELECT o.side,f.fill_price,f.fees_json FROM paper_fills f
            JOIN paper_orders o ON o.order_id=f.order_id ORDER BY o.execution_timestamp""").fetchall()
    assert [r[0] for r in rows] == ["BUY", "SELL"]
    assert [Decimal(r[1]) for r in rows] == [Decimal("100.20"), Decimal("99.80")]
    fees = sum(
        (sum((Decimal(v) for v in json.loads(r[2]).values()), Decimal(0)) for r in rows), Decimal(0)
    )
    assert fees == Decimal("0.30")
    assert Decimal(sell["cash"]) == Decimal(10000) - Decimal("100.20") + Decimal("99.80") - fees
    assert sell["equity"] == "9999.3" and sell["cumulative_fees"] == "0.3"
    assert sell["cumulative_realized_net_pnl"] == sell["net_trading_pnl"] == "-0.7"
    assert sell["gross_trading_pnl"] == "-0.4"
    report = analytics(db, runtime, now)
    assert report["status"] == "VERIFIED" and report["period_pnl"] == "-0.7"
    assert report["period_return_pct"] == "-0.007"
    assert report["max_drawdown_pct"] is not None
    assert len(report["curve"]) == len(records) and report["sharpe"] is None
    # BUY fee is 0.16: cash 9899.64 plus the 100.40 peak quote = 10000.04.
    # The final cash-only trough is 9999.30, a peak-to-trough drop of 0.74.
    assert max(Decimal(record["equity"]) for record in records) == Decimal("10000.04")
    drawdown = Decimal("0.74") / Decimal("10000.04") * 100
    assert abs(Decimal(report["max_drawdown_pct"]) - drawdown) < Decimal("1e-24")


def test_open_position_values_use_actual_accepted_quote_and_remain_historical(tmp_path):
    db, runtime, now = session(tmp_path, opened=True)
    rows = documents(db, runtime)
    latest = rows[-1]
    assert latest["positions"][0]["mark"]["reference_kind"] == "QUOTE"
    assert latest["positions"][0]["mark"]["provenance"] == "SYNTHETIC"
    assert latest["valuation_status"] == "COMPLETE"
    assert Decimal(latest["equity"]) == Decimal(latest["cash"]) + Decimal(
        latest["position_market_value"]
    )
    assert Decimal(latest["net_trading_pnl"]) == Decimal(
        latest["cumulative_realized_net_pnl"]
    ) + Decimal(latest["unrealized_net_of_entry_fees"])
    report = analytics(db, runtime, now)
    assert report["period_pnl"] is not None
    stale = analytics(db, runtime, now + timedelta(days=1))
    assert stale["period_pnl"] is None and "stale" in stale["boundary_reason"]
    assert stale["curve"]  # Prior eligible observations remain history, not current marks.


def test_ist_periods_and_actual_session_close_support_daily_net_pnl(tmp_path):
    db, runtime, _ = session(tmp_path)
    close = close_session(db, runtime)
    now = close + timedelta(seconds=1)
    for period in ("Today", "This Week", "This Month", "This Year", "All Time"):
        report = analytics(db, runtime, now, period)
        assert report["period_pnl"] == "-0.7", report
        assert report["daily"][0]["local_date"] == "2026-01-05"
        assert report["daily"][0]["net_pnl"] == "-0.7"
        assert report["daily"][0]["equity_change"] == "-0.7"
        assert report["daily"][0]["basis"] == "INCEPTION"
    past = analytics(
        db,
        runtime,
        close + timedelta(days=1),
        "Custom Date Range",
        date(2026, 1, 5),
        date(2026, 1, 5),
    )
    assert past["period_pnl"] == "-0.7" and past["closing_equity"] == "9999.3"


def test_custom_range_does_not_use_first_event_as_missing_opening_or_infer_close(tmp_path):
    db, runtime, now = session(tmp_path)
    report = analytics(
        db,
        runtime,
        now + timedelta(days=1),
        "Custom Date Range",
        date(2026, 1, 5),
        date(2026, 1, 5),
    )
    assert report["period_pnl"] is None and "Closing boundary" in report["boundary_reason"]
    assert report["daily"] == []
    close_session(db, runtime)
    missing = analytics(
        db,
        runtime,
        now + timedelta(days=2),
        "Custom Date Range",
        date(2026, 1, 6),
        date(2026, 1, 6),
    )
    assert missing["period_pnl"] is None


@pytest.mark.parametrize(
    "problem", ["missing", "stale", "future", "unqualified", "mixed_source", "future_source"]
)
def test_incomplete_valuation_is_persisted_honestly_and_excluded(tmp_path, problem):
    db, runtime, now = session(tmp_path, opened=True)
    with closing(get_connection(db)) as conn:
        if problem == "missing":
            conn.execute("DELETE FROM paper_marks")
        elif problem == "future":
            conn.execute(
                "UPDATE paper_marks SET observed_at=?", ((now + timedelta(minutes=5)).isoformat(),)
            )
        elif problem == "unqualified":
            conn.execute(
                "UPDATE data_imports SET source_use_status='UNVERIFIED' WHERE interval='1m'"
            )
        elif problem == "mixed_source":
            conn.execute(
                "UPDATE data_imports SET import_type='OBSERVED_STREAM' WHERE interval='1m'"
            )
        elif problem == "future_source":
            conn.execute(
                "UPDATE data_imports SET completed_at=? WHERE interval='1m'",
                ((now + timedelta(days=1)).isoformat(),),
            )
        conn.commit()
    at = now + timedelta(minutes=5) if problem == "stale" else now
    record_equity_checkpoint(
        db, runtime.account_id, session=runtime.runtime_id, as_of=at.isoformat()
    )
    last = documents(db, runtime)[-1]
    assert last["valuation_status"] == "INCOMPLETE"
    assert last["equity"] is None and last["reason"]
    report = analytics(db, runtime, at)
    assert report["period_pnl"] is None and report["max_drawdown_pct"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("equity", "123456"),
        ("ledger_ids", ["orphan"]),
        ("external_cash_flows", "KNOWN_DEPOSIT"),
        ("timestamp", "2025-01-01T00:00:00.000000Z"),
    ],
)
def test_rehashed_contradictory_or_orphaned_snapshots_fail_closed(tmp_path, field, value):
    db, runtime, now = session(tmp_path)
    with closing(get_connection(db)) as conn:
        conn.execute("DROP TRIGGER equity_no_update")
        row = conn.execute(
            "SELECT * FROM paper_equity_snapshots WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
            (runtime.runtime_id,),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload[field] = value
        conn.execute(
            "UPDATE paper_equity_snapshots SET payload_json=?,payload_sha256=?,observed_at=?,observed_us=? WHERE snapshot_id=?",
            (
                canonical(payload),
                digest(payload),
                payload["timestamp"],
                paper_equity.micros(payload["timestamp"]),
                row["snapshot_id"],
            ),
        )
        conn.commit()
    report = analytics(db, runtime, now)
    assert report["status"] == "UNAVAILABLE"
    assert report["curve"] == [] and report["period_pnl"] is None
    assert reconcile_paper_account(db, runtime.account_id)["status"] == "RECONCILED"


def test_append_only_triggers_and_fingerprint_tampering(tmp_path):
    db, runtime, now = session(tmp_path)
    with closing(get_connection(db)) as conn:
        for sql in (
            "DELETE FROM paper_equity_snapshots",
            "UPDATE paper_equity_snapshots SET payload_sha256='" + "f" * 64 + "'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only|immutable"):
                conn.execute(sql)
        row = dict(conn.execute("SELECT * FROM paper_equity_snapshots LIMIT 1").fetchone())
        for identity in (row["snapshot_id"], "a" * 64):
            replaced = {**row, "snapshot_id": identity}
            with pytest.raises(sqlite3.IntegrityError, match="identity already exists"):
                conn.execute(
                    "INSERT OR REPLACE INTO paper_equity_snapshots ("
                    + ",".join(replaced)
                    + ") VALUES ("
                    + ",".join("?" for _ in replaced)
                    + ")",
                    tuple(replaced.values()),
                )
        conn.execute("DROP TRIGGER equity_no_update")
        conn.execute(
            "UPDATE paper_equity_snapshots SET payload_sha256=? WHERE session_id=? AND sequence=1",
            ("f" * 64, runtime.runtime_id),
        )
        conn.commit()
    assert analytics(db, runtime, now)["status"] == "UNAVAILABLE"


def test_reconciliation_failure_does_not_become_external_cash_flow(tmp_path):
    db, runtime, now = session(tmp_path)
    with closing(get_connection(db)) as conn:
        conn.execute(
            "UPDATE paper_ledger_entries SET amount='1000' WHERE entry_type='SELL_CASH_CREDIT'"
        )
        conn.commit()
    record_equity_checkpoint(
        db, runtime.account_id, session=runtime.runtime_id, as_of=now.isoformat()
    )
    last = documents(db, runtime)[-1]
    assert last["reconciliation_status"] == "FAILED" and last["equity"] is None
    assert analytics(db, runtime, now)["status"] == "UNAVAILABLE"


def test_restart_and_duplicate_checkpoint_do_not_duplicate_financial_evidence(tmp_path):
    db, runtime, now = session(tmp_path)
    first = record_equity_checkpoint(
        db, runtime.account_id, session=runtime.runtime_id, as_of=now.isoformat()
    )
    before = documents(db, runtime)
    restarted = ForwardRuntimeService.open(db, runtime.runtime_id, runtime.calendars)
    second = record_equity_checkpoint(
        db, runtime.account_id, session=restarted.runtime_id, as_of=now.isoformat()
    )
    assert first == second and documents(db, runtime) == before
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
    assert analytics(db, runtime, now)["period_pnl"] == "-0.7"


def test_crash_before_commit_rolls_back_equity_and_mark_together(tmp_path):
    db, runtime, now = session(tmp_path, opened=True)
    before = documents(db, runtime)
    with closing(get_connection(db)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        mark = conn.execute("SELECT * FROM paper_marks").fetchone()
        record_paper_mark(
            conn,
            runtime.account_id,
            mark["instrument_id"],
            Decimal(mark["mark_price"]),
            mark["observed_at"],
            mark["source_import_id"],
            as_of=now.isoformat(),
            quote_event_id="rollback-example",
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM paper_equity_snapshots WHERE session_id=?",
                (runtime.runtime_id,),
            ).fetchone()[0]
            == len(before) + 1
        )
        conn.rollback()
    assert documents(db, runtime) == before


def test_interrupted_snapshot_recording_leaves_no_partial_evidence_or_blocks_fills(
    tmp_path, monkeypatch
):
    original = paper_equity._snapshot

    def interrupt(conn, *args):
        original(conn, *args)
        raise ValueError("interrupted evidence projection")

    monkeypatch.setattr(paper_equity, "_snapshot", interrupt)
    db, runtime, now = session(tmp_path)
    assert documents(db, runtime) == []
    assert reconcile_paper_account(db, runtime.account_id)["status"] == "RECONCILED"
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
    assert analytics(db, runtime, now)["status"] == "UNAVAILABLE"


def test_missing_committed_fill_snapshot_cannot_be_hidden_by_later_checkpoint(
    tmp_path, monkeypatch
):
    original = paper_equity.capture

    def omit_fill(conn, account, at, kind, key, **kwargs):
        return None if kind == "FILL" else original(conn, account, at, kind, key, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(paper_equity, "capture", omit_fill)
        db, runtime, now = session(tmp_path)
    record_equity_checkpoint(
        db, runtime.account_id, session=runtime.runtime_id, as_of=now.isoformat()
    )
    assert analytics(db, runtime, now)["status"] == "UNAVAILABLE"


def test_dashboard_analytics_readonly_and_authenticated_strategy_attribution(tmp_path, monkeypatch):
    db, runtime, now = session(tmp_path)
    close_session(db, runtime)
    now = _START.replace(hour=12)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monkeypatch.setattr(
        paper_equity, "capture", Mock(side_effect=AssertionError("Read attempted capture"))
    )
    data = read_dashboard(
        db, runtime.account_id, date_window("All Time", now), as_of=now, run_id=runtime.runtime_id
    )
    assert data["status"] == "AVAILABLE"
    assert data["analytics"]["status"] == "VERIFIED"
    assert data["analytics"]["daily"][0]["net_pnl"] == "-0.7"
    assert data["finance"]["realized_groups"]["strategy"][0]["bucket"] != "UNAVAILABLE"
    assert data["finance"]["realized_groups"]["symbol"][0]["bucket"] == "SYNTH"
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    paper_equity.capture.assert_not_called()


def test_independent_sessions_and_ambiguous_shared_account_fail_closed(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    db1, runtime1, now1 = session(one)
    db2, runtime2, now2 = session(two)
    assert analytics(db1, runtime1, now1)["period_pnl"] == "-0.7"
    assert analytics(db2, runtime2, now2)["period_pnl"] == "-0.7"
    with closing(get_readonly_connection(db1)) as conn:
        foreign = read_performance(
            conn, runtime1.account_id, session=runtime2.runtime_id, start=None, end=None, as_of=now1
        )
    assert foreign["status"] == "UNAVAILABLE"
    ForwardRuntimeService.create(
        db1,
        "other-runtime-same-account",
        runtime1.account_id,
        runtime1.instrument_ids,
        runtime1.calendars,
        created_at=now1.isoformat(),
    )
    assert analytics(db1, runtime1, now1)["status"] == "UNAVAILABLE"


def test_session_close_requires_actual_approved_calendar_boundary(tmp_path):
    db, runtime, now = session(tmp_path)
    record_equity_checkpoint(
        db,
        runtime.account_id,
        session=runtime.runtime_id,
        as_of=now.isoformat(),
        session_close=True,
    )
    assert documents(db, runtime)[-1]["valuation_status"] == "INCOMPLETE"
    assert "confirmed final session close" in documents(db, runtime)[-1]["reason"]


@pytest.mark.parametrize("version", range(1, 17))
def test_equity_migration_preserves_every_previously_supported_schema(tmp_path, version):
    db = tmp_path / "migration.db"
    _create_version_1_database(db)
    with closing(get_connection(db)) as conn:
        for current in range(1, version):
            for statement in database.MIGRATIONS[current]:
                database._execute_statements(conn, statement)
            conn.execute("INSERT INTO schema_metadata(version) VALUES (?)", (current + 1,))
        source = tuple(conn.execute("SELECT * FROM sources").fetchone())
        conn.commit()
    initialize_database(db)
    initialize_database(db)
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 17
        assert tuple(conn.execute("SELECT * FROM sources").fetchone()) == source
        assert conn.execute("SELECT COUNT(*) FROM paper_equity_snapshots").fetchone()[0] == 0


def test_failed_migration_rolls_back_new_table_and_version(tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    with monkeypatch.context() as patch:
        patch.setattr(database, "SCHEMA_VERSION", 16)
        initialize_database(db)
    with closing(get_connection(db)) as conn:
        conn.execute("""CREATE TRIGGER fail_v17 BEFORE INSERT ON schema_metadata WHEN NEW.version=17
            BEGIN SELECT RAISE(ABORT,'injected migration failure'); END""")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected migration failure"):
        initialize_database(db)
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 16
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='paper_equity_snapshots'"
        ).fetchone()


def test_legacy_sessions_are_not_backfilled_by_migration_or_dashboard(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(database, "SCHEMA_VERSION", 16)
        db, runtime, now = session(tmp_path)
    initialize_database(db)
    assert documents(db, runtime) == []
    assert analytics(db, runtime, now)["status"] == "UNAVAILABLE"
    record_equity_checkpoint(
        db, runtime.account_id, session=runtime.runtime_id, as_of=now.isoformat()
    )
    assert documents(db, runtime)[0]["kind"] == "CHECKPOINT"
    assert analytics(db, runtime, now)["status"] == "UNAVAILABLE"


def test_midnight_checkpoint_preserves_ist_date_and_is_not_a_fake_daily_return(tmp_path):
    db, runtime, _now = session(tmp_path)
    midnight = datetime(2026, 1, 5, 18, 30, tzinfo=UTC)
    record_equity_checkpoint(
        db, runtime.account_id, session=runtime.runtime_id, as_of=midnight.isoformat()
    )
    assert documents(db, runtime)[-1]["local_date"] == "2026-01-06"
    report = analytics(db, runtime, midnight + timedelta(seconds=1), "Today")
    assert report["period_pnl"] == "0"
    assert report["daily"] == []


def test_irregular_event_snapshots_are_not_used_for_sharpe(tmp_path):
    db, runtime, now = session(tmp_path)
    for offset in range(1, 25):
        record_equity_checkpoint(
            db,
            runtime.account_id,
            session=runtime.runtime_id,
            as_of=(now + timedelta(seconds=offset)).isoformat(),
        )
    report = analytics(db, runtime, now + timedelta(seconds=30))
    assert len(report["curve"]) > 20 and report["daily"] == [] and report["sharpe"] is None


def test_fill_and_equity_snapshot_roll_back_if_transaction_does_not_commit(tmp_path, monkeypatch):
    original = paper_equity.capture

    def crash_after_recording(conn, account, at, kind, key, **kwargs):
        result = original(conn, account, at, kind, key, **kwargs)
        if kind == "FILL":
            assert result is not None
            raise RuntimeError("CRASH_BEFORE_ACCOUNTING_COMMIT")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(paper_equity, "capture", crash_after_recording)
        with pytest.raises(RuntimeError, match="CRASH_BEFORE_ACCOUNTING_COMMIT"):
            session(tmp_path)
    with closing(get_readonly_connection(tmp_path / "runtime.db")) as conn:
        for table in ("paper_fills", "paper_orders", "paper_ledger_entries", "paper_positions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM paper_equity_snapshots WHERE kind='FILL'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("boundary", ["financial_commit", "control_transaction"])
@pytest.mark.parametrize("resume", ["restart", "continuation"])
def test_equity_evidence_survives_existing_f1_recovery(tmp_path, monkeypatch, boundary, resume):
    from tests.test_forward_runtime import (
        test_committed_buy_recovery_preserves_finances_and_restores_stop as exercise,
    )

    exercise(tmp_path, monkeypatch, boundary, resume)
    with closing(get_readonly_connection(tmp_path / "runtime.db")) as conn:
        runtime = conn.execute("SELECT account_id,runtime_id FROM forward_runtimes").fetchone()
        snapshots = verify_snapshots(conn, runtime["account_id"], runtime["runtime_id"])
        assert len([d for d in snapshots if d["kind"] == "FILL"]) == 2
        assert snapshots[-1]["positions"] == []


def test_repeating_average_cost_preserves_phase8_decimal_semantics(tmp_path):
    from aegis.paper_trading import PaperOrderRequest, submit_paper_order
    from tests.test_paper_trading import _buy, _import_candle, _snapshot, setup

    fixture = setup.__wrapped__(tmp_path)
    db, _, instrument, _imports, calendars, account = fixture
    assert _buy(fixture, quantity=2).status == "FILLED"
    at = "2026-01-05T09:19:00+05:30"
    imported, _ = _import_candle(db, tmp_path, 18, 80)
    snapshot = _snapshot(db, instrument, imported, at, at, calendars, "80")
    request = PaperOrderRequest(
        instrument, "BUY", 1, "2026-01-05T09:15:00+05:30", "2026-01-05T09:15:30+05:30"
    )
    assert (
        submit_paper_order(db, account, "repeating-average", request, snapshot).status == "FILLED"
    )
    with closing(get_readonly_connection(db)) as conn:
        records = verify_snapshots(conn, account, f"paper-account:{account}")
        assert all(d["valuation_status"] == "COMPLETE" for d in records)
        report = read_performance(
            conn, account, session=None, start=None, end=None, as_of=paper_equity.instant(at)
        )
    assert report["period_pnl"] == "-40"
    assert records[-1]["equity"] == "960"


def test_same_instrument_in_separate_account_never_combines_equity(tmp_path):
    from aegis.paper_trading import _costs_from_json, _risk_from_json, create_paper_account

    db, runtime, now = session(tmp_path)
    with closing(get_readonly_connection(db)) as conn:
        account = conn.execute(
            "SELECT * FROM paper_accounts WHERE id=?", (runtime.account_id,)
        ).fetchone()
    other = create_paper_account(
        db,
        name="independent second portfolio",
        initial_cash=Decimal(5000),
        risk_limits=_risk_from_json(account["risk_limits_json"]),
        costs=_costs_from_json(account["cost_assumptions_json"]),
        created_at=now.isoformat(),
    )
    second = ForwardRuntimeService.create(
        db,
        "separate-account-runtime",
        other,
        runtime.instrument_ids,
        runtime.calendars,
        created_at=now.isoformat(),
    )
    first, separate = analytics(db, runtime, now), analytics(db, second, now)
    assert first["closing_equity"] == "9999.3" and first["period_pnl"] == "-0.7"
    assert separate["closing_equity"] == "5000" and separate["period_pnl"] == "0"
    assert first["session_id"] != separate["session_id"]


def test_valid_endpoints_survive_intermediate_valuation_gap_but_drawdown_does_not(tmp_path):
    db, runtime, now = session(tmp_path, opened=True)
    with closing(get_connection(db)) as conn:
        mark = dict(conn.execute("SELECT * FROM paper_marks").fetchone())
        conn.execute("DELETE FROM paper_marks")
        conn.commit()
    record_equity_checkpoint(
        db, runtime.account_id, session=runtime.runtime_id, as_of=now.isoformat()
    )
    assert documents(db, runtime)[-1]["equity"] is None
    with closing(get_connection(db)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        record_paper_mark(
            conn,
            runtime.account_id,
            mark["instrument_id"],
            Decimal(mark["mark_price"]),
            mark["observed_at"],
            mark["source_import_id"],
            as_of=(now + timedelta(seconds=1)).isoformat(),
        )
        conn.commit()
    report = analytics(db, runtime, now + timedelta(seconds=1))
    assert report["status"] == "VERIFIED" and report["period_pnl"] is not None
    assert report["max_drawdown_pct"] is None
    assert any(r["equity"] is None for r in report["curve"])


@pytest.mark.parametrize("failure", ["F2", "F3"])
def test_equity_evidence_survives_existing_f2_f3_recovery(tmp_path, monkeypatch, failure):
    if failure == "F2":
        from tests.test_forward_runtime import (
            test_restart_recovers_missing_scheduler_event_exactly_once as exercise,
        )
    else:
        from tests.test_decision_journal import (
            test_journal_reconciliation_preserves_f1_committed_buy_recovery as exercise,
        )
    exercise(tmp_path, monkeypatch)
    with closing(get_readonly_connection(tmp_path / "runtime.db")) as conn:
        runtime = conn.execute("SELECT account_id,runtime_id FROM forward_runtimes").fetchone()
        snapshots = verify_snapshots(conn, runtime["account_id"], runtime["runtime_id"])
        assert all(d["valuation_status"] == "COMPLETE" for d in snapshots)
        assert (
            len([d for d in snapshots if d["kind"] == "FILL"])
            == conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
        )
