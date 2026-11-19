"""Read-only terminal tests over existing authoritative synthetic execution fixtures."""

import hashlib
import json
import socket
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from aegis.backtesting import default_cost_assumptions
from aegis.database import get_connection, get_readonly_connection, initialize_database
from aegis.paper_trading import create_paper_account, get_paper_account_report
from aegis.synthetic_sma_session import SMASessionConfig, run_existing_sma_runtime
from aegis.trading_dashboard import PAGES
from aegis.trading_dashboard_data import (
    csv_bytes,
    date_window,
    list_accounts,
    read_dashboard,
    timestamp,
)
from tests.test_forward_runtime import _provider, _setup


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(tmp_path / "unused.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "unused.log"))
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(tmp_path / "runtime_calendar.json"))
    monkeypatch.delenv("AEGIS_DASHBOARD_DATABASE_PATH", raising=False)
    original_connect = socket.socket.connect

    def local_pipe_only(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        raise AssertionError("No external networking")

    monkeypatch.setattr(socket.socket, "connect", local_pipe_only)
    monkeypatch.setattr(
        "requests.Session.request", Mock(side_effect=AssertionError("No broker HTTP"))
    )


def session(root: Path, *, opened=False):
    root.mkdir(exist_ok=True)
    db, _, instruments, runtime = _setup(
        root, initial_cash=Decimal(10000), costs=default_cost_assumptions()
    )
    prices = (
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
    )
    quotes = tuple(
        replace(q, price=Decimal(p), quantity=20)
        for q, p in zip(_provider(instruments, count=len(prices)), prices)
    )
    selected = quotes[:8] if opened else quotes
    run_existing_sma_runtime(runtime, selected, SMASessionConfig())
    now = timestamp(selected[-1].timestamp) + timedelta(seconds=1)
    return db, runtime, now


def snapshot(db, runtime, now, **kwargs):
    return read_dashboard(
        db,
        runtime.account_id,
        date_window("All Time", now),
        as_of=now,
        run_id=runtime.runtime_id,
        **kwargs,
    )


def test_readonly_connections_and_dashboard_do_not_modify_database(tmp_path, monkeypatch):
    db, runtime, now = session(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with closing(get_readonly_connection(db)) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE paper_accounts SET initial_cash='1'")
    monkeypatch.setattr(
        "aegis.trading_dashboard_data.get_readonly_connection", get_readonly_connection
    )
    result = snapshot(db, runtime, now)
    assert result["status"] == "AVAILABLE", result
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "period,start",
    [
        ("Today", date(2026, 11, 19)),
        ("This Week", date(2026, 11, 16)),
        ("This Month", date(2026, 11, 1)),
        ("This Year", date(2026, 1, 1)),
    ],
)
def test_ist_date_boundaries(period, start):
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)
    window = date_window(period, now)
    assert window.start.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date() == start
    assert window.includes("2026-11-18T18:30:00Z")
    if period == "Today":
        assert not window.includes("2026-11-18T18:29:59.999999Z")
        assert not window.includes("2026-11-19T18:30:00Z")


@pytest.mark.parametrize(
    "start,end", [(None, None), (date(2026, 2, 1), None), (date(2026, 2, 2), date(2026, 2, 1))]
)
def test_custom_date_validation(start, end):
    with pytest.raises(ValueError):
        date_window("Custom Date Range", datetime.now(UTC), start, end)


def test_period_accounting_uses_equity_basis_not_fill_turnover(tmp_path):
    db, runtime, now = session(tmp_path)
    data = snapshot(db, runtime, now)
    core = get_paper_account_report(db, runtime.account_id)
    finance = data["finance"]
    assert finance["reconciliation"] == "RECONCILED"
    assert Decimal(finance["available_cash"]) == Decimal(core["available_cash"])
    assert Decimal(finance["period_pnl"]) == Decimal(core["available_cash"]) - 10000
    assert finance["completed_trades"] == 1
    assert len(data["trades"]) == 2
    strategies = {d["strategy"] for d in data["decisions"] if d["verified_order_id"]}
    assert strategies and "UNAVAILABLE" not in strategies
    assert {r["bucket"] for r in finance["realized_groups"]["strategy"]} == strategies
    past = date_window("Custom Date Range", now, date(2026, 1, 4), date(2026, 1, 4))
    filtered = read_dashboard(db, runtime.account_id, past, as_of=now, run_id=runtime.runtime_id)
    assert filtered["trades"] == []
    assert filtered["finance"]["period_pnl"] is None
    assert filtered["finance"]["period_return_pct"] is None
    assert filtered["finance"]["period_fees"] == "0"


def test_missing_and_empty_database_never_initialized(tmp_path):
    db = tmp_path / "missing.db"
    assert list_accounts(db) == []
    now = datetime.now(UTC)
    assert read_dashboard(db, 1, date_window("All Time", now), as_of=now)["status"] == "UNAVAILABLE"
    assert not db.exists()
    initialize_database(db)
    assert list_accounts(db) == []
    assert read_dashboard(db, 1, date_window("All Time", now), as_of=now)["status"] == "UNAVAILABLE"


def test_synthetic_and_real_provenance_are_distinct(tmp_path):
    db, runtime, now = session(tmp_path)
    assert snapshot(db, runtime, now)["market"][0]["data_class"] == "SYNTHETIC"
    with closing(get_connection(db)) as conn:
        row = conn.execute("SELECT config_json FROM forward_runtimes").fetchone()
        config = json.loads(row[0])
        config["provider"] = "UPSTOX_V3_OBSERVE_ONLY"
        text = json.dumps(config, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE forward_runtimes SET config_json=?,config_sha256=?",
            (text, hashlib.sha256(text.encode()).hexdigest()),
        )
        conn.commit()
    data = snapshot(db, runtime, now)
    assert data["market"][0]["data_class"] == "REAL_OBSERVATION"
    assert data["market"][0]["provider"] == "UPSTOX_V3_OBSERVE_ONLY"


def test_stale_quotes_are_not_current_prices(tmp_path):
    db, runtime, now = session(tmp_path)
    data = snapshot(db, runtime, now + timedelta(days=1))
    assert data["market"][0]["freshness"] == "STALE"
    assert data["market"][0]["quote_timestamp"]


def test_positions_are_current_snapshot_and_stale_marks_are_unavailable(tmp_path):
    db, runtime, now = session(tmp_path, opened=True)
    data = snapshot(db, runtime, now)
    assert data["finance"]["reconciliation"] == "RECONCILED"
    position = data["finance"]["positions"][0]
    assert position["quantity"] == 1
    assert position["valuation_status"] == "ELIGIBLE_PERSISTED_QUOTE"
    assert Decimal(position["market_value"]) == Decimal(position["mark_price"])
    future_period = date_window("Custom Date Range", now, date(2026, 1, 6), date(2026, 1, 6))
    future = read_dashboard(
        db, runtime.account_id, future_period, as_of=now, run_id=runtime.runtime_id
    )
    assert future["trades"] == []
    assert len(future["finance"]["positions"]) == 1
    stale = snapshot(db, runtime, now + timedelta(days=1))
    assert stale["finance"]["equity"] is None
    assert stale["finance"]["positions"][0]["valuation_status"] == "STALE"
    assert stale["finance"]["positions"][0]["unrealized_gross_pnl"] is None


def test_unreconciled_positions_are_not_presented_as_authoritative(tmp_path):
    db, runtime, now = session(tmp_path, opened=True)
    with closing(get_connection(db)) as conn:
        conn.execute("UPDATE paper_positions SET quantity=99")
        conn.commit()
    data = snapshot(db, runtime, now)
    assert data["finance"]["reconciliation"] == "FAILED"
    assert "positions" not in data["finance"]
    assert "equity" not in data["finance"]


def test_multiple_accounts_are_isolated(tmp_path):
    db, runtime, now = session(tmp_path)
    with closing(get_readonly_connection(db)) as conn:
        existing = dict(conn.execute("SELECT * FROM paper_accounts").fetchone())
    from aegis.paper_trading import _costs_from_json, _risk_from_json

    other = create_paper_account(
        db,
        name="Separate synthetic account",
        initial_cash=Decimal(5000),
        risk_limits=_risk_from_json(existing["risk_limits_json"]),
        costs=_costs_from_json(existing["cost_assumptions_json"]),
        created_at=now.isoformat(),
    )
    data = read_dashboard(db, other, date_window("All Time", now), as_of=now)
    assert data["finance"]["initial_capital"] == "5000"
    assert data["trades"] == [] and data["market"] == []
    forbidden = read_dashboard(
        db, other, date_window("All Time", now), as_of=now, run_id=runtime.runtime_id
    )
    assert forbidden["status"] == "UNAVAILABLE"


def test_journal_ordering_pagination_and_execution_links(tmp_path):
    db, runtime, now = session(tmp_path)
    data = snapshot(db, runtime, now, limit=200)
    journal = data["journal"]
    assert [(timestamp(r["timestamp"]), r["kind"], r["record_id"]) for r in journal] == sorted(
        (timestamp(r["timestamp"]), r["kind"], r["record_id"]) for r in journal
    )
    page1 = snapshot(db, runtime, now, limit=10)["journal"]
    page2 = snapshot(db, runtime, now, limit=10, offset=10)["journal"]
    assert page1 + page2 == journal[:20]
    assert {"QUOTE", "CANDLE", "SCHEDULER", "DECISION", "ORDER", "FILL"} <= {
        r["kind"] for r in journal
    }
    assert any(d["state"] == "NO_DECISION" for d in data["decisions"])
    assert any(d.get("verified_order_id") for d in data["decisions"])
    assert all(d["integrity"] == "VERIFIED" for d in data["decisions"])


def test_trade_filters_apply_before_pagination(tmp_path):
    db, runtime, now = session(tmp_path)
    buy = snapshot(
        db, runtime, now, limit=1, trade_action="BUY", trade_search="SYN", trade_status="FILLED"
    )
    assert len(buy["trades"]) == 1 and buy["trades"][0]["side"] == "BUY"
    assert snapshot(db, runtime, now, trade_search="' OR 1=1 --")["trades"] == []


def test_csv_drops_payloads_redacts_secrets_and_neutralizes_formulas():
    result = csv_bytes(
        [
            {
                "symbol": '=HYPERLINK("evil")',
                "reason": "access_token=private-value https://feed.upstox.com/?code=secret-url",
                "payload_json": '{"private":"never-render-this"}',
                "api_secret": "plain-secret",
                "amount": "-12.50",
            }
        ]
    ).decode("utf-8-sig")
    assert "private-value" not in result and "secret-url" not in result
    assert "plain-secret" not in result and "never-render-this" not in result
    assert "payload_json" not in result
    assert "'=HYPERLINK" in result and "-12.50" in result


def test_sql_period_filter_preserves_microseconds_at_ist_midnight(tmp_path):
    db, runtime, _now = session(tmp_path)
    boundaries = ("2026-11-18T18:29:59.999999Z", "2026-11-18T18:30:00.000000Z")
    with closing(get_connection(db)) as conn:
        for at in boundaries:
            payload = "{}"
            conn.execute(
                "INSERT INTO decision_journal_health VALUES (?,?,?,?,?,?)",
                (
                    hashlib.sha256(at.encode()).hexdigest(),
                    runtime.runtime_id,
                    "BOUNDARY_TEST",
                    payload,
                    hashlib.sha256(payload.encode()).hexdigest(),
                    at,
                ),
            )
        conn.commit()
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)
    data = read_dashboard(
        db, runtime.account_id, date_window("Today", now), as_of=now, run_id=runtime.runtime_id
    )
    assert [row["created_at"] for row in data["health"]] == [boundaries[1]]


def test_journal_verification_failure_is_reported_without_repair(tmp_path, monkeypatch):
    from aegis.decision_journal import JournalError

    db, runtime, now = session(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "aegis.trading_dashboard_data._read_decision",
        Mock(side_effect=JournalError("Corrupt record")),
    )
    data = snapshot(db, runtime, now)
    assert data["journal_reconciliation"]["status"] == "FAILED"
    assert all(row["integrity"] == "FAILED" for row in data["decisions"])
    assert not any(row.get("verified_order_id") for row in data["decisions"])
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_dashboard_never_calls_mutating_or_broker_apis(tmp_path, monkeypatch):
    db, runtime, now = session(tmp_path)
    targets = (
        "aegis.database.initialize_database",
        "aegis.database.get_connection",
        "aegis.paper_trading.submit_paper_order",
        "aegis.paper_trading.reconcile_paper_account",
        "aegis.forward_runtime.ForwardRuntimeService.open",
        "requests.Session.request",
    )
    guards = []
    for target in targets:
        guard = Mock(side_effect=AssertionError("Dashboard called a mutating or network API"))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
    assert snapshot(db, runtime, now)["status"] == "AVAILABLE"
    for guard in guards:
        guard.assert_not_called()


def test_streamlit_renders_all_pages_and_refresh_without_writes(tmp_path, monkeypatch):
    db, _runtime, _now = session(tmp_path)
    monkeypatch.setenv("AEGIS_DASHBOARD_DATABASE_PATH", str(db))
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src/aegis/trading_dashboard.py")).run(
        timeout=15
    )
    assert not app.exception
    assert [tab.label for tab in app.tabs] == list(PAGES)
    assert next(s for s in app.selectbox if s.label == "Refresh interval").value == "15 seconds"
    assert any("RESEARCH_ONLY" in caption.value for caption in app.caption)
    next(s for s in app.selectbox if s.label == "Refresh interval").set_value("Manual").run()
    assert not app.exception
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_streamlit_empty_state_is_truthful(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    initialize_database(db)
    monkeypatch.setenv("AEGIS_DASHBOARD_DATABASE_PATH", str(db))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src/aegis/trading_dashboard.py")).run(
        timeout=10
    )
    assert not app.exception
    assert any("No persisted paper accounts" in i.value for i in app.info)
