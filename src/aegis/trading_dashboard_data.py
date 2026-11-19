"""Bounded, read-only projections of persisted paper records; no execution path."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import time as clock
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aegis.ai_agent.security import redact_nested_secrets, redact_secrets
from aegis.database import get_readonly_connection
from aegis.decision_journal import JournalError, _read_decision
from aegis.market_quality import load_approved_calendar_registry
from aegis.paper_trading import _cash_balance, _costs_from_json, _reconcile, _risk_from_json
from aegis.performance_analytics import read_performance

IST = ZoneInfo("Asia/Kolkata")
PERIODS = ("Today", "This Week", "This Month", "This Year", "Custom Date Range", "All Time")
AUDIT_LIMIT = 1000
UNAVAILABLE = "UNAVAILABLE"


def timestamp(value: str) -> datetime:
    """ISO times are aware; SQLite's documented CURRENT_TIMESTAMP format is UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
            raise ValueError("Timestamp evidence lacks a timezone")
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_microseconds(value: str) -> int:
    """Exact comparison across ISO offsets/fraction precisions, including IST midnight."""
    return (timestamp(value) - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)


@dataclass(frozen=True)
class DateWindow:
    label: str
    start: datetime | None
    end: datetime | None

    def includes(self, value: str) -> bool:
        at = timestamp(value)
        return (self.start is None or at >= self.start) and (self.end is None or at < self.end)


def date_window(
    label: str,
    as_of: datetime,
    start: date | None = None,
    end: date | None = None,
) -> DateWindow:
    if as_of.tzinfo is None or label not in PERIODS:
        raise ValueError("A supported period and aware assessment time are required")
    today = as_of.astimezone(IST).date()
    if label == "All Time":
        return DateWindow(label, None, None)
    if label == "Custom Date Range":
        if type(start) is not date or type(end) is not date or start > end:
            raise ValueError("Custom range requires ordered start and end dates")
    else:
        end = today
        start = {
            "Today": today,
            "This Week": today - timedelta(days=today.weekday()),
            "This Month": today.replace(day=1),
            "This Year": today.replace(month=1, day=1),
        }[label]
    assert start is not None and end is not None
    return DateWindow(
        label,
        datetime.combine(start, time.min, IST).astimezone(UTC),
        datetime.combine(end + timedelta(days=1), time.min, IST).astimezone(UTC),
    )


def safe_text(value: object) -> str:
    """Redact free text, URLs and JWTs; never expose raw source/model payloads."""
    text = redact_secrets(str(value))
    text = re.sub(r"https?://\S+", "[REDACTED_URL]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", text)
    return text[:1000]


def sanitized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: safe_text(value) if isinstance(value, str) else value
            for key, value in row.items()
            if not key.endswith("_json") and "payload" not in key.lower()
        }
        for row in redact_nested_secrets(rows)
    ]


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Export already-projected fields, with secret and spreadsheet-formula protection."""
    rows = sanitized(rows)
    handle = io.StringIO(newline="")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        clean = {}
        for key, value in row.items():
            if (
                isinstance(value, str)
                and value.lstrip().startswith(("=", "+", "-", "@"))
                and not re.fullmatch(r"-?\d+(\.\d+)?", value)
            ):
                value = "'" + value
            clean[key] = value
        writer.writerow(clean)
    return handle.getvalue().encode("utf-8-sig")


def _rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, args).fetchall()]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def list_accounts(database: Path) -> list[dict[str, Any]]:
    if not database.is_file():
        return []
    with closing(get_readonly_connection(database)) as conn:
        if "paper_accounts" not in _tables(conn):
            return []
        return sanitized(
            _rows(conn, "SELECT id,name,created_at FROM paper_accounts ORDER BY id LIMIT 101")
        )


def list_sessions(database: Path, account_id: int) -> list[dict[str, Any]]:
    with closing(get_readonly_connection(database)) as conn:
        if "forward_runtimes" not in _tables(conn):
            return []
        return sanitized(
            _rows(
                conn,
                "SELECT runtime_id,status,created_at FROM forward_runtimes WHERE account_id=? ORDER BY runtime_id LIMIT 101",
                (account_id,),
            )
        )


def _period_sql(column: str, window: DateWindow) -> tuple[str, tuple[Any, ...]]:
    sql, args = "", []
    if window.start is not None:
        sql += f" AND aegis_time_us({column}) >= ?"
        args.append(utc_microseconds(window.start.isoformat()))
    if window.end is not None:
        sql += f" AND aegis_time_us({column}) < ?"
        args.append(utc_microseconds(window.end.isoformat()))
    return sql, tuple(args)


def _object(value: object) -> dict[str, Any]:
    document = json.loads(str(value))
    if not isinstance(document, dict):
        raise TypeError("Persisted object is malformed")
    return document


def _execution_strategy(conn: sqlite3.Connection, execution: dict[str, Any]) -> str:
    """Resolve strategy from the authenticated decision bound to an execution."""
    if "decision_journal_events" not in _tables(conn) or not execution["event_id"]:
        return UNAVAILABLE
    matches = conn.execute(
        """SELECT decision_id FROM decision_journal_events
        WHERE run_id=? AND event_id=? AND role='ACTIVE'
        AND json_extract(payload_json,'$.proposal_id')=? LIMIT 2""",
        (execution["runtime_id"], execution["event_id"], execution["proposal_id"]),
    ).fetchall()
    if len(matches) == 1:
        try:
            return str(_read_decision(conn, matches[0][0])["decision"]["strategy_id"])
        except (JournalError, ValueError, TypeError, KeyError):
            pass
    return UNAVAILABLE


def _runtime(conn: sqlite3.Connection, account_id: int, run_id: str | None) -> dict[str, Any]:
    if run_id is None or "forward_runtimes" not in _tables(conn):
        return {}
    rows = _rows(
        conn,
        "SELECT * FROM forward_runtimes WHERE account_id=? AND runtime_id=?",
        (account_id, run_id),
    )
    if not rows:
        raise ValueError("Runtime does not belong to the selected account")
    runtime = rows[0]
    for field in ("config", "state"):
        if (
            hashlib.sha256(runtime[field + "_json"].encode()).hexdigest()
            != runtime[field + "_sha256"]
        ):
            raise ValueError("Persisted runtime fingerprint differs")
    runtime["config"] = _object(runtime["config_json"])
    return runtime


def _market(
    conn: sqlite3.Connection, runtime: dict[str, Any], as_of: datetime
) -> list[dict[str, Any]]:
    if not runtime:
        return []
    config, run = runtime["config"], runtime["runtime_id"]
    results = []
    for instrument_id in config.get("instrument_ids", [])[:50]:
        items = _rows(
            conn,
            """SELECT q.*,i.exchange,i.trading_symbol FROM forward_quotes q
            JOIN instruments i ON i.id=q.instrument_id
            WHERE q.runtime_id=? AND q.instrument_id=? AND q.accepted=1
              AND aegis_time_us(q.quote_timestamp)<=aegis_time_us(?)
            ORDER BY q.sequence DESC LIMIT 1""",
            (run, instrument_id, as_of.isoformat()),
        )
        if not items:
            continue
        quote = items[0]
        age = (as_of - timestamp(quote["quote_timestamp"])).total_seconds()
        limit = int(config.get("max_quote_delay_seconds", 30))
        provenance = _rows(
            conn,
            """SELECT d.source_use_status,d.import_type FROM forward_runtime_imports f
            JOIN data_imports d ON d.id=f.import_id WHERE f.runtime_id=? AND f.instrument_id=? LIMIT 1""",
            (run, instrument_id),
        )
        candles = _rows(
            conn,
            """SELECT candle_timestamp,close_decimal,completed_at FROM forward_runtime_candles
            WHERE runtime_id=? AND instrument_id=? AND interval='1m' AND aegis_time_us(completed_at)<=aegis_time_us(?)
            ORDER BY completed_at DESC LIMIT 1""",
            (run, instrument_id, as_of.isoformat()),
        )
        decision = (
            _rows(
                conn,
                """SELECT state,decision_at,decision_id FROM decision_journal_events
            WHERE run_id=? AND instrument_id=? AND aegis_time_us(decision_at)<=aegis_time_us(?)
            ORDER BY decision_at DESC,decision_id DESC LIMIT 1""",
                (run, instrument_id, as_of.isoformat()),
            )
            if "decision_journal_events" in _tables(conn)
            else []
        )
        results.append(
            {
                "exchange": quote["exchange"],
                "symbol": quote["trading_symbol"],
                "instrument_id": instrument_id,
                "latest_persisted_price": quote["price_decimal"],
                "quote_timestamp": quote["quote_timestamp"],
                "received_at": quote["received_at"],
                "provider": config.get("provider", UNAVAILABLE),
                "data_class": {
                    "DETERMINISTIC_SYNTHETIC": "SYNTHETIC",
                    "UPSTOX_V3_OBSERVE_ONLY": "REAL_OBSERVATION",
                }.get(config.get("provider"), "UNKNOWN_PROVENANCE"),
                "source_use": provenance[0]["source_use_status"] if provenance else UNAVAILABLE,
                "freshness": "FRESH" if 0 <= age <= limit else "STALE",
                "age_seconds": int(age),
                "persisted_runtime_status": runtime["status"],
                "completed_candle": candles[0]["candle_timestamp"] if candles else UNAVAILABLE,
                "candle_close": candles[0]["close_decimal"] if candles else None,
                "latest_decision": decision[0]["state"] if decision else "NO_RECORD",
            }
        )
    return results


def _positions(
    conn: sqlite3.Connection, account: dict[str, Any], as_of: datetime
) -> list[dict[str, Any]]:
    rows = _rows(
        conn,
        """SELECT p.*,i.trading_symbol,i.exchange,m.mark_price,m.observed_at,m.source_import_id,
        d.source_use_status FROM paper_positions p JOIN instruments i ON i.id=p.instrument_id
        LEFT JOIN paper_marks m ON m.account_id=p.account_id AND m.instrument_id=p.instrument_id
        LEFT JOIN data_imports d ON d.id=m.source_import_id WHERE p.account_id=? LIMIT 101""",
        (account["id"],),
    )
    if len(rows) > 100:
        raise ValueError("Position reporting limit exceeded")
    max_age = int(_object(account["risk_limits_json"])["max_market_data_age_seconds"])
    for row in rows:
        row.update(
            valuation_status=UNAVAILABLE,
            market_value=None,
            unrealized_gross_pnl=None,
            unrealized_return_pct=None,
            eligible_mark_price=None,
        )
        if row["observed_at"] is not None:
            age = (as_of - timestamp(row["observed_at"])).total_seconds()
            row["valuation_status"] = "STALE" if age > max_age else UNAVAILABLE
            candidates = conn.execute(
                """SELECT q.price_decimal FROM forward_quotes q
                JOIN forward_runtimes r ON r.runtime_id=q.runtime_id
                JOIN forward_runtime_imports f ON f.runtime_id=q.runtime_id AND f.instrument_id=q.instrument_id
                WHERE r.account_id=? AND q.instrument_id=? AND q.accepted=1
                  AND aegis_time_us(q.quote_timestamp)=aegis_time_us(?) AND f.import_id=? LIMIT 4""",
                (account["id"], row["instrument_id"], row["observed_at"], row["source_import_id"]),
            ).fetchall()
            linked = any(Decimal(q[0]) == Decimal(row["mark_price"]) for q in candidates)
            if 0 <= age <= max_age and linked and row["source_use_status"] == "VERIFIED_PERMITTED":
                price, entry = Decimal(row["mark_price"]), Decimal(row["average_entry_price"])
                row.update(
                    valuation_status="ELIGIBLE_PERSISTED_QUOTE",
                    eligible_mark_price=row["mark_price"],
                    market_value=str(price * row["quantity"]),
                    unrealized_gross_pnl=str((price - entry) * row["quantity"]),
                    unrealized_return_pct=str((price / entry - 1) * 100),
                )
        control = (
            _rows(
                conn,
                """SELECT t.stop_loss,t.target_price,t.runtime_id FROM forward_trade_controls t
            JOIN forward_runtimes r ON r.runtime_id=t.runtime_id
            WHERE r.account_id=? AND t.instrument_id=? AND t.active=1 LIMIT 2""",
                (account["id"], row["instrument_id"]),
            )
            if "forward_trade_controls" in _tables(conn)
            else []
        )
        row.update(
            stop_loss=control[0]["stop_loss"] if len(control) == 1 else None,
            target_price=control[0]["target_price"] if len(control) == 1 else None,
            runtime_id=control[0]["runtime_id"] if len(control) == 1 else UNAVAILABLE,
        )
        row["position_age_seconds"] = None
    return rows


def _finance(
    conn: sqlite3.Connection, account: dict[str, Any], window: DateWindow, as_of: datetime
) -> dict[str, Any]:
    _risk_from_json(account["risk_limits_json"])
    _costs_from_json(account["cost_assumptions_json"])
    orders = _rows(
        conn,
        """SELECT o.order_id,o.instrument_id,o.side,o.quantity,o.status,o.execution_timestamp,
        f.fees_json,r.realized_net_pnl,i.trading_symbol,s.runtime_id,s.event_id,s.proposal_id
        FROM paper_orders o LEFT JOIN paper_fills f ON f.order_id=o.order_id
        LEFT JOIN paper_realized_pnl r ON r.order_id=o.order_id
        LEFT JOIN instruments i ON i.id=o.instrument_id
        LEFT JOIN paper_execution_snapshot_uses s ON s.order_id=o.order_id
        LEFT JOIN paper_ledger_entries l ON l.order_id=o.order_id
        WHERE o.account_id=? ORDER BY aegis_time_us(o.execution_timestamp),l.entry_id,o.order_id LIMIT ?""",
        (account["id"], AUDIT_LIMIT + 1),
    )
    if len(orders) > AUDIT_LIMIT:
        return {
            "reconciliation": UNAVAILABLE,
            "limitation": "Account audit exceeds 1000 orders; use a scoped offline report",
        }
    errors = _reconcile(conn, account, return_errors=True)
    if errors:
        return {
            "reconciliation": "FAILED",
            "limitation": "Persisted financial records do not reconcile",
        }
    if timestamp(account["created_at"]) > as_of or any(
        timestamp(o["execution_timestamp"]) > as_of for o in orders
    ):
        return {
            "reconciliation": "RECONCILED",
            "limitation": "As-of predates the current account projection",
        }
    cash = _cash_balance(conn, account)
    positions = _positions(conn, account, as_of)
    valuation_available = all(p["market_value"] is not None for p in positions)
    market_value = (
        sum((Decimal(p["market_value"]) for p in positions), Decimal(0))
        if valuation_available
        else None
    )
    equity = cash + market_value if market_value is not None else None
    realized = sum(
        (Decimal(o["realized_net_pnl"]) for o in orders if o["realized_net_pnl"] is not None),
        Decimal(0),
    )
    chosen = [o for o in orders if window.includes(o["execution_timestamp"])]
    fees = sum(
        (
            sum((Decimal(v) for v in _object(o["fees_json"]).values()), Decimal(0))
            for o in chosen
            if o["fees_json"]
        ),
        Decimal(0),
    )
    period_realized = sum(
        (Decimal(o["realized_net_pnl"]) for o in chosen if o["realized_net_pnl"] is not None),
        Decimal(0),
    )
    # Completed cycles aggregate existing realized P&L records, never fill turnover.
    quantities: dict[int, int] = defaultdict(int)
    cycle_opened: dict[int, str] = {}
    cycle_pnl: dict[int, Decimal] = defaultdict(Decimal)
    closed = []
    for order in orders:
        if order["status"] != "FILLED":
            continue
        instrument = order["instrument_id"]
        if order["side"] == "BUY" and quantities[instrument] == 0:
            cycle_opened[instrument] = order["execution_timestamp"]
        quantities[instrument] += order["quantity"] * (1 if order["side"] == "BUY" else -1)
        if order["realized_net_pnl"] is not None:
            cycle_pnl[instrument] += Decimal(order["realized_net_pnl"])
        if order["side"] == "SELL" and quantities[instrument] == 0:
            if window.includes(order["execution_timestamp"]):
                closed.append(cycle_pnl[instrument])
            cycle_pnl[instrument] = Decimal(0)
            cycle_opened.pop(instrument, None)
    for position in positions:
        opened = cycle_opened.get(position["instrument_id"])
        position["position_age_seconds"] = (
            int((as_of - timestamp(opened)).total_seconds()) if opened else None
        )
    wins, losses = [p for p in closed if p > 0], [p for p in closed if p < 0]
    groups: dict[str, dict[str, Decimal]] = {
        k: defaultdict(Decimal) for k in ("day", "week", "month", "year", "symbol", "strategy")
    }
    for order in chosen:
        if order["realized_net_pnl"] is None:
            continue
        at = timestamp(order["execution_timestamp"]).astimezone(IST).date()
        keys = {
            "day": at.isoformat(),
            "week": (at - timedelta(days=at.weekday())).isoformat(),
            "month": at.strftime("%Y-%m"),
            "year": str(at.year),
            "symbol": order["trading_symbol"],
            "strategy": _execution_strategy(conn, order),
        }
        for grouping, key in keys.items():
            groups[grouping][str(key)] += Decimal(order["realized_net_pnl"])
    initial = Decimal(account["initial_cash"])
    # Only a persisted inception basis plus current eligible equity supports net return.
    # Daily risk-basis snapshots are not assumed to be midnight or closing equity.
    inception_in_period = window.includes(account["created_at"])
    end_includes_now = window.end is None or as_of < window.end
    period_pnl = (
        equity - initial
        if equity is not None and inception_in_period and end_includes_now
        else None
    )
    return {
        "reconciliation": "RECONCILED",
        "initial_capital": str(initial),
        "available_cash": str(cash),
        "equity": str(equity) if equity is not None else None,
        "realized_pnl": str(realized),
        "unrealized_gross_pnl": str(
            sum((Decimal(p["unrealized_gross_pnl"]) for p in positions), Decimal(0))
        )
        if valuation_available
        else None,
        "net_pnl": str(equity - initial) if equity is not None else None,
        "period_pnl": str(period_pnl) if period_pnl is not None else None,
        "period_return_pct": str(period_pnl / initial * 100) if period_pnl is not None else None,
        "period_basis": "Persisted account inception to current eligible snapshot"
        if period_pnl is not None
        else "UNAVAILABLE: no reliable opening/closing equity boundary pair",
        "period_realized_pnl": str(period_realized),
        "period_fees": str(fees),
        "positions": positions,
        "completed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": str(Decimal(len(wins)) / len(closed) * 100) if closed else None,
        "profit_factor": str(sum(wins) / -sum(losses)) if losses else None,
        "average_win": str(sum(wins) / len(wins)) if wins else None,
        "average_loss": str(sum(losses) / len(losses)) if losses else None,
        "realized_groups": {
            k: [{"bucket": b, "realized_pnl": str(v)} for b, v in sorted(values.items())]
            for k, values in groups.items()
        },
        "max_drawdown": None,
        "sharpe": None,
        "equity_history": _rows(
            conn,
            "SELECT local_date,starting_equity FROM paper_daily_state WHERE account_id=? ORDER BY local_date LIMIT 1001",
            (account["id"],),
        ),
    }


def _decisions(
    conn: sqlite3.Connection, run: str, window: DateWindow, limit: int, offset: int
) -> list[dict[str, Any]]:
    if "decision_journal_events" not in _tables(conn):
        return []
    where, args = _period_sql("decision_at", window)
    ids = conn.execute(
        "SELECT decision_id FROM decision_journal_events WHERE run_id=?"
        + where
        + " ORDER BY decision_at DESC,decision_id DESC LIMIT ? OFFSET ?",
        (run, *args, limit, offset),
    ).fetchall()
    rows = []
    for item in ids:
        try:
            record = _read_decision(conn, item[0])
        except (JournalError, ValueError, TypeError, KeyError):
            rows.append({"decision_id": item[0], "integrity": "FAILED", "outcome": "UNVERIFIED"})
            continue
        d = record["decision"]
        executed = conn.execute(
            """SELECT o.order_id,o.status FROM decision_journal_audits a
            JOIN paper_orders o ON o.order_id=a.order_id
            JOIN paper_execution_snapshot_uses s ON s.order_id=o.order_id
            JOIN forward_execution_authorizations x ON x.consumed_order_id=o.order_id
            WHERE a.decision_id=? AND a.stage='EXECUTION' AND x.status='CONSUMED'
            AND x.runtime_id=? AND x.event_id=? AND s.event_id=x.event_id LIMIT 1""",
            (item[0], run, d["event_id"]),
        ).fetchone()
        rows.append(
            {
                "decision_id": item[0],
                "event_id": d["event_id"],
                "instrument_id": d["instrument_id"],
                "strategy": d["strategy_id"],
                "role": d.get("role"),
                "model_id": d.get("model_id"),
                "model_version": d.get("model_version"),
                "origin_evidence": "MODEL_RECORDED"
                if d.get("model_id")
                else "NO_AI_MODEL_RECORDED",
                "state": d["state"],
                "timestamp": d["decision_at"],
                "confidence": d.get("confidence"),
                "reason_codes": ", ".join(d.get("reason_codes", [])),
                "evidence_references": ", ".join(d.get("evidence_references", [])),
                "calendar": d.get("calendar_confidence"),
                "source_use": d.get("source_use_status"),
                "quality": d.get("quality_status"),
                "python_validation": d.get("risk_status"),
                "proposal_id": d.get("proposal_id"),
                "integrity": "VERIFIED",
                "verified_order_id": executed[0] if executed else None,
                "outcome": executed[1] if executed else "NO_AUTHENTICATED_EXECUTION_LINK",
            }
        )
    return rows


def _journal(
    conn: sqlite3.Connection, account: int, run: str, window: DateWindow, limit: int, offset: int
) -> list[dict[str, Any]]:
    # Every UNION arm retains its own source kind and stable ID. Time order is not causal proof.
    arms = [
        (
            "paper_equity_snapshots",
            "SELECT observed_at timestamp,'EQUITY' kind,snapshot_id record_id,valuation_status status,event_key reference FROM paper_equity_snapshots WHERE account_id=? AND session_id=?",
            (account, run),
        ),
        (
            "paper_orders",
            "SELECT o.execution_timestamp timestamp,'ORDER' kind,o.order_id record_id,o.status status,o.order_id reference FROM paper_orders o WHERE o.account_id=?",
            (account,),
        ),
        (
            "paper_fills",
            "SELECT f.execution_timestamp timestamp,'FILL' kind,f.fill_id record_id,'FILLED' status,f.order_id reference FROM paper_fills f JOIN paper_orders o ON o.order_id=f.order_id WHERE o.account_id=?",
            (account,),
        ),
        (
            "paper_order_events",
            "SELECT e.event_timestamp timestamp,'ORDER_EVENT' kind,e.event_id record_id,e.event_type status,e.order_id reference FROM paper_order_events e JOIN paper_orders o ON o.order_id=e.order_id WHERE o.account_id=?",
            (account,),
        ),
        (
            "forward_quotes",
            "SELECT received_at timestamp,'QUOTE' kind,event_id record_id,CASE accepted WHEN 1 THEN 'ACCEPTED' ELSE 'REJECTED' END status,quote_timestamp reference FROM forward_quotes WHERE runtime_id=?",
            (run,),
        ),
        (
            "forward_runtime_candles",
            "SELECT completed_at timestamp,'CANDLE' kind,CAST(instrument_id AS TEXT)||':'||interval||':'||candle_timestamp record_id,'COMPLETED' status,candle_timestamp reference FROM forward_runtime_candles WHERE runtime_id=?",
            (run,),
        ),
        (
            "forward_scheduler_events",
            "SELECT decision_timestamp timestamp,'SCHEDULER' kind,event_id record_id,status,proposal_id reference FROM forward_scheduler_events WHERE runtime_id=?",
            (run,),
        ),
        (
            "decision_journal_events",
            "SELECT decision_at timestamp,'DECISION' kind,decision_id record_id,state status,event_id reference FROM decision_journal_events WHERE run_id=?",
            (run,),
        ),
        (
            "decision_journal_audits",
            "SELECT a.created_at timestamp,'AUDIT' kind,a.audit_id record_id,a.stage||':'||a.status status,a.decision_id reference FROM decision_journal_audits a JOIN decision_journal_events e ON e.decision_id=a.decision_id WHERE e.run_id=?",
            (run,),
        ),
        (
            "decision_journal_health",
            "SELECT created_at timestamp,'HEALTH' kind,event_id record_id,kind status,NULL reference FROM decision_journal_health WHERE run_id=?",
            (run,),
        ),
        (
            "forward_execution_authorizations",
            "SELECT authorized_at timestamp,CASE authorization_kind WHEN 'STOP_TARGET_EXIT' THEN 'PROTECTIVE_EXIT' ELSE 'AUTHORIZATION' END kind,event_id record_id,authorization_kind||':'||status status,consumed_order_id reference FROM forward_execution_authorizations WHERE runtime_id=?",
            (run,),
        ),
    ]
    selected = [(sql, args) for table, sql, args in arms if table in _tables(conn)]
    union = " UNION ALL ".join(sql for sql, _ in selected)
    args = tuple(value for _, values in selected for value in values)
    where, date_args = _period_sql("timestamp", window)
    return _rows(
        conn,
        "SELECT * FROM ("
        + union
        + ") WHERE 1=1"
        + where
        + " ORDER BY aegis_time_us(timestamp),kind,record_id LIMIT ? OFFSET ?",
        (*args, *date_args, limit, offset),
    )


def read_dashboard(
    database: Path,
    account_id: int,
    window: DateWindow,
    *,
    as_of: datetime,
    run_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    trade_search: str = "",
    trade_action: str | None = None,
    trade_status: str | None = None,
) -> dict[str, Any]:
    """One read transaction, bounded pages and account audit; never recover or repair."""
    if as_of.tzinfo is None or not 1 <= limit <= 200 or not 0 <= offset <= 10000:
        raise ValueError("Invalid reporting time or pagination bound")
    if (
        len(trade_search) > 64
        or trade_action not in (None, "BUY", "SELL")
        or trade_status not in (None, "FILLED", "REJECTED")
    ):
        raise ValueError("Invalid trade filter")
    output: dict[str, Any] = {
        "status": "UNAVAILABLE",
        "as_of": as_of.isoformat(),
        "period": window.label,
    }
    if not database.is_file():
        return {**output, "message": "Database is unavailable; it will not be initialized"}
    try:
        with closing(get_readonly_connection(database)) as conn, localcontext() as context:
            context.prec = 50
            steps = [0]
            deadline = clock.monotonic() + 2

            def budget() -> int:
                steps[0] += 1
                return int(steps[0] > 250 or clock.monotonic() > deadline)

            conn.set_progress_handler(budget, 10000)
            conn.create_function("aegis_time_us", 1, utc_microseconds, deterministic=True)
            conn.execute("BEGIN")
            tables = _tables(conn)
            required = {
                "paper_accounts",
                "paper_orders",
                "paper_positions",
                "paper_marks",
                "paper_fills",
                "paper_ledger_entries",
                "paper_realized_pnl",
                "instruments",
            }
            if not required <= tables:
                return {
                    **output,
                    "message": "Paper schema is unavailable; no migration was attempted",
                }
            accounts = _rows(conn, "SELECT * FROM paper_accounts WHERE id=?", (account_id,))
            if not accounts:
                return {**output, "message": "No selected paper account exists"}
            account = accounts[0]
            runtime = _runtime(conn, account_id, run_id)
            run = run_id or f"paper-account:{account_id}"
            where, args = _period_sql("o.execution_timestamp", window)
            if trade_search:
                where += " AND instr(upper(i.trading_symbol),upper(?))>0"
                args += (trade_search,)
            for column, value in (("side", trade_action), ("status", trade_status)):
                if value:
                    where += f" AND o.{column}=?"
                    args += (value,)
            trades = _rows(
                conn,
                """SELECT o.order_id,o.execution_timestamp,o.side,o.quantity,o.reference_price,
                o.fill_price,o.status,o.reason,i.trading_symbol,f.fill_id,f.fees_json,s.runtime_id,s.proposal_id,s.event_id
                FROM paper_orders o LEFT JOIN instruments i ON i.id=o.instrument_id
                LEFT JOIN paper_fills f ON f.order_id=o.order_id
                LEFT JOIN paper_execution_snapshot_uses s ON s.order_id=o.order_id
                WHERE o.account_id=?"""
                + where
                + " ORDER BY o.execution_timestamp DESC,o.order_id LIMIT ? OFFSET ?",
                (account_id, *args, limit, offset),
            )
            for trade in trades:
                trade["modeled_fees"] = (
                    str(
                        sum(
                            (Decimal(v) for v in _object(trade.pop("fees_json")).values()),
                            Decimal(0),
                        )
                    )
                    if trade.get("fees_json")
                    else None
                )
                trade["strategy"] = _execution_strategy(conn, trade)
            proposals = []
            if "forward_scheduler_events" in tables:
                where, args = _period_sql("e.decision_timestamp", window)
                events = _rows(
                    conn,
                    """SELECT e.event_id,e.proposal_id,e.proposal_json,e.proposal_binding_state,e.status,e.decision_timestamp
                    FROM forward_scheduler_events e JOIN forward_runtimes r ON r.runtime_id=e.runtime_id
                    WHERE r.account_id=? AND e.runtime_id=?"""
                    + where
                    + " ORDER BY e.decision_timestamp DESC,e.event_id LIMIT ? OFFSET ?",
                    (account_id, run, *args, limit, offset),
                )
                for event in events:
                    proposal = _object(event.pop("proposal_json")) if event["proposal_json"] else {}
                    proposals.append(
                        {
                            **event,
                            **{
                                key: proposal.get(key)
                                for key in (
                                    "instrument_id",
                                    "action",
                                    "rationale",
                                    "confidence",
                                    "strategy_id",
                                )
                            },
                            "record_type": "PROPOSAL_NOT_EXECUTION_PROOF",
                        }
                    )
            health = []
            if "decision_journal_health" in tables:
                where, args = _period_sql("created_at", window)
                health = _rows(
                    conn,
                    "SELECT event_id,kind,created_at FROM decision_journal_health WHERE run_id=?"
                    + where
                    + " ORDER BY created_at DESC,event_id LIMIT ? OFFSET ?",
                    (run, *args, limit, offset),
                )
            output.update(
                status="AVAILABLE",
                account={
                    key: account.get(key)
                    for key in ("id", "name", "created_at", "halted", "halt_class", "halt_reason")
                },
                risk_limits=redact_nested_secrets(_object(account["risk_limits_json"])),
                finance=_finance(conn, account, window, as_of),
                market=_market(conn, runtime, as_of),
                trades=trades,
                decisions=_decisions(conn, run, window, limit, offset),
                proposals=proposals,
                health=health,
                journal=_journal(conn, account_id, run, window, limit, offset),
                runtime={
                    key: runtime.get(key)
                    for key in ("runtime_id", "status", "updated_at", "last_event_at")
                },
                scope="Financial snapshots/activity cover the selected paper account. Runtime selection scopes market, decisions, runtime events and equity analytics. Verified equity requires exclusive account/session ownership.",
                available_tables=sorted(tables),
                source_imports=_rows(
                    conn,
                    """SELECT f.runtime_id,f.instrument_id,f.interval,f.import_id,
                    d.import_type,d.source_use_status,d.status AS import_status
                    FROM forward_runtime_imports f JOIN data_imports d ON d.id=f.import_id
                    WHERE f.runtime_id=? ORDER BY f.instrument_id,f.interval LIMIT 151""",
                    (run,),
                )
                if "forward_runtime_imports" in tables
                else [],
            )
            output["analytics"] = read_performance(
                conn, account_id, session=run_id, start=window.start, end=window.end, as_of=as_of
            )
            analytics = output["analytics"]
            output["finance"]["period_pnl"] = analytics["period_pnl"]
            output["finance"]["period_return_pct"] = analytics["period_return_pct"]
            output["finance"]["period_basis"] = (
                f"Verified equity snapshots {analytics['opening_at']} to {analytics['closing_at']}"
                if analytics.get("period_pnl") is not None
                else "UNAVAILABLE: " + str(analytics.get("boundary_reason"))
            )
        try:
            calendar = load_approved_calendar_registry().resolve("NSE", "NSE")
            output["calendar_confidence"] = calendar.confidence if calendar else UNAVAILABLE
        except (ValueError, OSError):
            output["calendar_confidence"] = UNAVAILABLE
        decisions = output["decisions"]
        output["journal_reconciliation"] = {
            "status": "FAILED"
            if any(d["integrity"] == "FAILED" for d in decisions)
            else "VERIFIED_DISPLAYED_PAGE"
            if decisions
            else UNAVAILABLE,
            "scope": "Displayed page only; no full-run audit or recovery is invoked",
        }
        return _sanitize_report(output)
    except (sqlite3.Error, ValueError, TypeError, KeyError, OSError, ArithmeticError):
        return {
            "status": UNAVAILABLE,
            "message": "Evidence is incomplete, inconsistent, unsupported or exceeds the bounded query budget",
        }


def _sanitize_report(value: Any) -> Any:
    value = redact_nested_secrets(value)
    if isinstance(value, dict):
        return {key: _sanitize_report(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_report(item) for item in value]
    return safe_text(value) if isinstance(value, str) else value
