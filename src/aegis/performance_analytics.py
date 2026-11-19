"""Read-only performance derived exclusively from verified Phase 8 equity evidence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

from aegis.decision_journal import JournalError
from aegis.paper_equity import (
    IST,
    EquityEvidenceError,
    instant,
    money,
    session_for,
    verify_snapshots,
)


def _trading_days(conn: sqlite3.Connection, session: str, first: Any, last: Any) -> list[Any]:
    """Only administrator-approved, covered, confirmed calendars establish sampling."""
    import json

    from aegis.market_quality import load_approved_calendar_registry

    if (last - first).days > 3700:
        raise EquityEvidenceError("Calendar reporting range exceeds ten years")
    runtime = conn.execute(
        "SELECT config_json FROM forward_runtimes WHERE runtime_id=?", (session,)
    ).fetchone()
    if runtime is None:
        raise EquityEvidenceError("Confirmed reporting calendar unavailable for standalone account")
    ids = json.loads(runtime["config_json"])["instrument_ids"]
    if not ids or len(ids) > 50:
        raise EquityEvidenceError("Unsupported calendar scope")
    registry = load_approved_calendar_registry()
    calendars = []
    for instrument in ids:
        row = conn.execute(
            "SELECT exchange,segment FROM instruments WHERE id=?", (instrument,)
        ).fetchone()
        calendar = registry.resolve(row["exchange"], row["segment"])
        if (
            calendar is None
            or calendar.confidence != "CONFIRMED"
            or calendar.timezone_name != "Asia/Kolkata"
        ):
            raise EquityEvidenceError("Confirmed IST reporting calendar unavailable")
        calendars.append(calendar)
    result = []
    day = first
    while day <= last:
        sessions = [calendar.sessions_for(day) for calendar in calendars]
        if any(s is None for s in sessions):
            raise EquityEvidenceError("Reporting calendar does not cover the required dates")
        if any(s for s in sessions):
            result.append(day)
        day += timedelta(days=1)
    return result


def _between(d: dict[str, Any], start: datetime | None, end: datetime | None) -> bool:
    at = instant(d["timestamp"])
    return (start is None or at >= start) and (end is None or at < end)


def _opening(
    conn: sqlite3.Connection, session: str, records: list[dict[str, Any]], start: datetime | None
) -> dict[str, Any]:
    first = records[0]
    if first["kind"] != "INITIAL" or first["valuation_status"] != "COMPLETE":
        raise EquityEvidenceError("Authenticated inception boundary is missing")
    if start is None or instant(first["timestamp"]) >= start:
        return first
    # A boundary checkpoint contains only strictly earlier financial activity.
    for row in records:
        if (
            instant(row["timestamp"]) == start
            and row["kind"] == "CHECKPOINT"
            and row["valuation_status"] == "COMPLETE"
            and (row["latest_execution_at"] is None or instant(row["latest_execution_at"]) < start)
        ):
            return row
    prior = [
        r
        for r in records
        if r["kind"] == "SESSION_CLOSE"
        and r["valuation_status"] == "COMPLETE"
        and instant(r["timestamp"]) < start
    ]
    if not prior:
        raise EquityEvidenceError("Opening boundary snapshot is missing")
    candidate = prior[-1]
    following = _trading_days(
        conn,
        session,
        instant(candidate["timestamp"]).astimezone(IST).date() + timedelta(days=1),
        start.astimezone(IST).date() - timedelta(days=1),
    )
    if following:
        raise EquityEvidenceError("Opening basis crosses a missing trading-session close")
    if any(
        r["sequence"] > candidate["sequence"] and instant(r["timestamp"]) < start for r in records
    ):
        raise EquityEvidenceError("Activity exists after the proposed opening close")
    return candidate


def _daily(
    conn: sqlite3.Connection,
    session: str,
    records: list[dict[str, Any]],
    start: datetime | None,
    end: datetime | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Daily net changes require actual final-session close checkpoints, never filled days."""
    closes = [
        r for r in records if r["kind"] == "SESSION_CLOSE" and r["valuation_status"] == "COMPLETE"
    ]
    rows = []
    first = records[0]
    previous = None
    limitation = None
    for close in closes:
        local_date = instant(close["timestamp"]).astimezone(IST).date()
        opening = previous
        basis = "PREVIOUS_CONFIRMED_SESSION_CLOSE"
        if (
            opening is None
            and first["kind"] == "INITIAL"
            and first["local_date"] == close["local_date"]
        ):
            opening, basis = first, "INCEPTION"
        if opening is not None:
            try:
                expected = _trading_days(
                    conn,
                    session,
                    instant(opening["timestamp"]).astimezone(IST).date() + timedelta(days=1),
                    local_date,
                )
                if basis != "INCEPTION" and expected != [local_date]:
                    raise EquityEvidenceError("A required daily closing snapshot is missing")
                if _between(close, start, end):
                    change = Decimal(close["equity"]) - Decimal(opening["equity"])
                    rows.append(
                        {
                            "local_date": close["local_date"],
                            "opening_snapshot_id": opening["snapshot_id"],
                            "closing_snapshot_id": close["snapshot_id"],
                            "opening_equity": opening["equity"],
                            "closing_equity": close["equity"],
                            "equity_change": money(change),
                            "net_pnl": money(change),
                            "return_pct": money(change / Decimal(opening["equity"]) * 100),
                            "basis": basis,
                        }
                    )
            except (ValueError, ArithmeticError) as error:
                limitation = str(error)
        elif _between(close, start, end):
            limitation = "Previous daily closing boundary is unavailable"
        previous = close
    if not rows:
        limitation = limitation or "No verified session-close boundary pair in this period"
    return rows, limitation


def _calculate(
    conn: sqlite3.Connection,
    account_id: int,
    session: str,
    *,
    start: datetime | None,
    end: datetime | None,
    as_of: datetime,
) -> dict[str, Any]:
    from aegis.paper_trading import _risk_from_json

    records = verify_snapshots(conn, account_id, session)
    records = [r for r in records if instant(r["timestamp"]) <= as_of]
    if not records:
        raise EquityEvidenceError("No equity snapshots exist at the requested assessment time")
    curve = [
        {
            "snapshot_id": r["snapshot_id"],
            "timestamp": r["timestamp"],
            "sequence": r["sequence"],
            "local_date": r["local_date"],
            "equity": r["equity"],
            "cash": r["cash"],
            "realized_net_pnl": r["cumulative_realized_net_pnl"],
            "unrealized_gross_pnl": r["unrealized_gross_pnl"],
            "cumulative_fees": r["cumulative_fees"],
            "valuation_status": r["valuation_status"],
        }
        for r in records
        if _between(r, start, end)
    ]
    daily, daily_limitation = _daily(conn, session, records, start, end)
    result: dict[str, Any] = {
        "status": "VERIFIED",
        "session_id": session,
        "policy": "NO_EXTERNAL_FLOWS_EVENT_EQUITY_V1",
        "curve": curve,
        "daily": daily,
        "daily_limitation": daily_limitation,
        "period_pnl": None,
        "period_return_pct": None,
        "cumulative_net_return_pct": None,
        "max_drawdown_pct": None,
        "max_drawdown_inr": None,
        "sharpe": None,
        "sharpe_limitation": "Requires at least 20 consecutive complete daily close-to-close returns; 252-session annualization and zero risk-free rate",
        "external_cash_flows": "NOT_SUPPORTED",
        "boundary_reason": None,
        "drawdown_basis": "Observed event snapshots only; unobserved intraday drawdowns are unknown",
    }
    try:
        opening = _opening(conn, session, records, start)
        candidates = [r for r in records if _between(r, start, end)]
        if not candidates:
            raise EquityEvidenceError("No closing equity evidence in the selected period")
        closing = candidates[-1]
        selected = [
            r for r in records if opening["sequence"] <= r["sequence"] <= closing["sequence"]
        ]
        if opening["valuation_status"] != "COMPLETE" or closing["valuation_status"] != "COMPLETE":
            raise EquityEvidenceError("Opening or closing valuation evidence is incomplete")
        if end is not None and end <= as_of:
            # End is exclusive IST midnight; use the last actual confirmed session close.
            start_day = max(
                instant(opening["timestamp"]).astimezone(IST).date(),
                start.astimezone(IST).date()
                if start
                else instant(opening["timestamp"]).astimezone(IST).date(),
            )
            expected = _trading_days(
                conn, session, start_day, end.astimezone(IST).date() - timedelta(days=1)
            )
            observed = {
                instant(r["timestamp"]).astimezone(IST).date()
                for r in selected
                if r["kind"] == "SESSION_CLOSE"
            }
            if not expected or not set(expected) <= observed or closing["kind"] != "SESSION_CLOSE":
                raise EquityEvidenceError(
                    "Closing boundary or a required session-close checkpoint is missing"
                )
        elif closing["positions"]:
            risk = _risk_from_json(
                conn.execute(
                    "SELECT risk_limits_json FROM paper_accounts WHERE id=?", (account_id,)
                ).fetchone()[0]
            )
            if any(
                (as_of - instant(p["mark"]["observed_at"])).total_seconds()
                > risk.max_market_data_age_seconds
                for p in closing["positions"]
            ):
                raise EquityEvidenceError("Current closing valuation is stale")
            for position in closing["positions"]:
                mark = conn.execute(
                    "SELECT mark_price,observed_at,source_import_id FROM paper_marks WHERE account_id=? AND instrument_id=?",
                    (account_id, position["instrument_id"]),
                ).fetchone()
                evidence = position["mark"]
                if (
                    mark is None
                    or Decimal(mark["mark_price"]) != Decimal(evidence["price"])
                    or instant(mark["observed_at"]) != instant(evidence["observed_at"])
                    or mark["source_import_id"] != evidence["source_import_id"]
                ):
                    raise EquityEvidenceError(
                        "Latest position mark has no matching closing equity snapshot"
                    )
        if Decimal(opening["equity"]) <= 0:
            raise EquityEvidenceError("Return requires a positive opening equity")
        change = Decimal(closing["equity"]) - Decimal(opening["equity"])
        initial = Decimal(records[0]["equity"])
        peak = Decimal(opening["equity"])
        maximum, maximum_inr = Decimal(0), Decimal(0)
        drawdown_complete = all(row["valuation_status"] == "COMPLETE" for row in selected)
        if drawdown_complete:
            for row in selected:
                equity = Decimal(row["equity"])
                peak = max(peak, equity)
                maximum = max(maximum, (peak - equity) / peak * 100)
                maximum_inr = max(maximum_inr, peak - equity)
        result.update(
            period_pnl=money(change),
            period_return_pct=money(change / Decimal(opening["equity"]) * 100),
            cumulative_net_return_pct=money((Decimal(closing["equity"]) / initial - 1) * 100),
            max_drawdown_pct=money(maximum) if drawdown_complete else None,
            max_drawdown_inr=money(maximum_inr) if drawdown_complete else None,
            drawdown_basis=result["drawdown_basis"]
            if drawdown_complete
            else "UNAVAILABLE: intermediate valuation evidence is incomplete; endpoint P&L does not infer that path",
            opening_snapshot_id=opening["snapshot_id"],
            closing_snapshot_id=closing["snapshot_id"],
            opening_at=opening["timestamp"],
            closing_at=closing["timestamp"],
            opening_equity=opening["equity"],
            closing_equity=closing["equity"],
            period_fees=money(
                Decimal(closing["cumulative_fees"]) - Decimal(opening["cumulative_fees"])
            ),
            period_realized_net_pnl=money(
                Decimal(closing["cumulative_realized_net_pnl"])
                - Decimal(opening["cumulative_realized_net_pnl"])
            ),
            closing_unrealized_gross_pnl=closing["unrealized_gross_pnl"],
            closing_unrealized_net_of_entry_fees=closing["unrealized_net_of_entry_fees"],
        )
    except (EquityEvidenceError, ArithmeticError) as error:
        result["boundary_reason"] = str(error)
    # Irregular event points are never sampled as daily returns or padded with zeros.
    eligible = [d for d in daily if d["basis"] == "PREVIOUS_CONFIRMED_SESSION_CLOSE"]
    if len(eligible) >= 20 and not daily_limitation:
        expected = _trading_days(
            conn,
            session,
            datetime.fromisoformat(eligible[0]["local_date"]).date(),
            datetime.fromisoformat(eligible[-1]["local_date"]).date(),
        )
        if [d.isoformat() for d in expected] == [d["local_date"] for d in eligible]:
            returns: list[Decimal] = [Decimal(d["return_pct"]) / 100 for d in eligible]
            mean = sum(returns, Decimal(0)) / Decimal(len(returns))
            variance = sum(((v - mean) ** 2 for v in returns), Decimal(0)) / Decimal(
                len(returns) - 1
            )
            if variance > 0:
                result.update(
                    sharpe=money(mean / variance.sqrt() * Decimal(252).sqrt()),
                    sharpe_limitation=None,
                )
    return result


def read_performance(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    session: str | None,
    start: datetime | None,
    end: datetime | None,
    as_of: datetime,
) -> dict[str, Any]:
    """Run in the same read transaction as the dashboard; no initialization or repair."""
    try:
        with localcontext() as context:
            context.prec = 50
            owner = session_for(conn, account_id)
            if session is not None and session != owner:
                raise EquityEvidenceError(
                    "Selected runtime does not own this account's equity series"
                )
            return _calculate(conn, account_id, owner, start=start, end=end, as_of=as_of)
    except (sqlite3.Error, JournalError, ValueError, TypeError, KeyError, ArithmeticError):
        return {
            "status": "UNAVAILABLE",
            "boundary_reason": "Equity evidence is absent, incomplete, inconsistent, unsupported or exceeds reporting bounds",
            "period_pnl": None,
            "period_return_pct": None,
            "curve": [],
            "daily": [],
            "max_drawdown_pct": None,
            "max_drawdown_inr": None,
            "sharpe": None,
        }
