"""Append-only evidence from Phase 8 transactions, never a second financial ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aegis.config import Settings
from aegis.decision_journal import JournalError, _read_decision

POLICY = "PHASE8_EQUITY_V1"
IST = ZoneInfo("Asia/Kolkata")
MAX_FILLS = 1000
MAX_SNAPSHOTS = 4000
# Only cross-checking finite-precision average-cost identities uses this tolerance.
# Cash, quotes, fees, source records and snapshot fingerprints remain exact.
BASIS_TOLERANCE = Decimal("1e-38")


class EquityEvidenceError(ValueError):
    """Equity evidence is incomplete, inconsistent or outside the supported bound."""


def canonical(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(document: Any) -> str:
    return hashlib.sha256(canonical(document).encode()).hexdigest()


def money(value: Decimal) -> str:
    if not value.is_finite():
        raise EquityEvidenceError("Non-finite financial evidence")
    return "0" if not value else format(value.normalize(), "f")


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise EquityEvidenceError("Equity timestamps require a timezone")
    return result.astimezone(UTC)


def utc(value: str) -> str:
    return instant(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def micros(value: str) -> int:
    return (instant(value) - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)


def _fetch(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args)]


def session_for(conn: sqlite3.Connection, account_id: int) -> str:
    """A forward session may own an account exclusively; shared ownership fails closed."""
    rows = conn.execute(
        "SELECT runtime_id FROM forward_runtimes WHERE account_id=? LIMIT 2", (account_id,)
    ).fetchall()
    if len(rows) > 1:
        raise EquityEvidenceError("Multiple runtimes share this paper account")
    return str(rows[0][0]) if rows else f"paper-account:{account_id}"


def account_proof(account: dict[str, Any]) -> str:
    return digest(
        {
            k: account[k]
            for k in (
                "id",
                "initial_cash",
                "risk_limits_json",
                "cost_assumptions_json",
                "created_at",
            )
        }
    )


def financial_evidence(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """Bind existing one-to-one fill, cash, realized, snapshot-use and journal records."""
    rows = _fetch(
        conn,
        """SELECT o.*,f.fill_id,f.fees_json,f.execution_costs_json,
        l.entry_id,l.amount,l.entry_type,r.realized_net_pnl,
        s.snapshot_id,s.snapshot_sha256,s.runtime_id,s.event_id,s.proposal_id,s.import_id,
        s.account_id AS use_account_id,s.instrument_id AS use_instrument_id
        FROM paper_orders o JOIN paper_fills f ON f.order_id=o.order_id
        JOIN paper_ledger_entries l ON l.fill_id=f.fill_id
        LEFT JOIN paper_realized_pnl r ON r.order_id=o.order_id
        LEFT JOIN paper_execution_snapshot_uses s ON s.order_id=o.order_id
        WHERE o.account_id=? ORDER BY o.execution_timestamp,l.entry_id LIMIT ?""",
        (account_id, MAX_FILLS + 1),
    )
    if len(rows) > MAX_FILLS:
        raise EquityEvidenceError("Financial evidence exceeds 1000 fills")
    for row in rows:
        if (
            not row["snapshot_id"]
            or row["use_account_id"] != account_id
            or row["use_instrument_id"] != row["instrument_id"]
        ):
            raise EquityEvidenceError("A fill has no authenticated execution snapshot use")
        journals = _fetch(
            conn,
            """SELECT a.audit_id,a.decision_id,a.payload_sha256
            FROM decision_journal_audits a JOIN decision_journal_events d
            ON d.decision_id=a.decision_id WHERE a.order_id=? AND a.fill_id=?
            AND a.stage='EXECUTION' AND a.status='FILLED' AND d.role='ACTIVE' LIMIT 2""",
            (row["order_id"], row["fill_id"]),
        )
        if len(journals) != 1:
            raise EquityEvidenceError("A fill lacks an unambiguous authenticated execution journal")
        record = _read_decision(conn, journals[0]["decision_id"])
        decision = record["decision"]
        expected_run = row["runtime_id"] or f"paper-account:{account_id}"
        if (
            decision["run_id"] != expected_run
            or decision["paper_account_id"] != account_id
            or decision["instrument_id"] != row["instrument_id"]
        ):
            raise EquityEvidenceError("Journal ownership differs from its financial record")
        row["journal"] = journals[0]
        row["strategy_id"] = decision["strategy_id"]
        if row["runtime_id"]:
            authorization = conn.execute(
                """SELECT * FROM forward_execution_authorizations
                WHERE consumed_order_id=? AND status='CONSUMED'""",
                (row["order_id"],),
            ).fetchone()
            if (
                authorization is None
                or authorization["runtime_id"] != row["runtime_id"]
                or authorization["event_id"] != row["event_id"]
                or authorization["snapshot_sha256"] != row["snapshot_sha256"]
            ):
                raise EquityEvidenceError("Consumed execution authorization differs")
    return rows


def _references(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "order_id": r["order_id"],
            "fill_id": r["fill_id"],
            "execution_snapshot_id": r["snapshot_id"],
            "snapshot_sha256": r["snapshot_sha256"],
            "decision_id": r["journal"]["decision_id"],
            "audit_id": r["journal"]["audit_id"],
            "audit_sha256": r["journal"]["payload_sha256"],
        }
        for r in ledger
    ]


def _source(conn: sqlite3.Connection, imported: int, instrument: int) -> dict[str, Any]:
    row = conn.execute(
        """SELECT id,instrument_id,interval,import_type,file_sha256,
        source_use_status,status,completed_at FROM data_imports WHERE id=?""",
        (imported,),
    ).fetchone()
    if (
        row is None
        or row["instrument_id"] != instrument
        or row["interval"] != "1m"
        or row["source_use_status"] != "VERIFIED_PERMITTED"
        or row["status"] != "COMPLETED"
        or not row["file_sha256"]
        or not row["completed_at"]
    ):
        raise EquityEvidenceError("Valuation source is not qualified")
    return dict(row)


def _mark(
    conn: sqlite3.Connection,
    account: dict[str, Any],
    session: str,
    position: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    from aegis.paper_trading import _risk_from_json

    instrument = position["instrument_id"]
    row = conn.execute(
        "SELECT * FROM paper_marks WHERE account_id=? AND instrument_id=?",
        (account["id"], instrument),
    ).fetchone()
    if row is None:
        raise EquityEvidenceError("Missing valuation mark")
    mark = dict(row)
    price = Decimal(mark["mark_price"])
    age = instant(at) - instant(mark["observed_at"])
    if (
        not price.is_finite()
        or price <= 0
        or age < timedelta(0)
        or age
        > timedelta(
            seconds=_risk_from_json(account["risk_limits_json"]).max_market_data_age_seconds
        )
    ):
        raise EquityEvidenceError("Stale, future or invalid valuation mark")
    source = _source(conn, mark["source_import_id"], instrument)
    # Stream completed_at advances as candles arrive; do not retroactively invalidate
    # a formerly eligible observation using that later mutable completion watermark.
    is_stream = source["import_type"] in ("SYNTHETIC_STREAM", "OBSERVED_STREAM")
    if instant(source["completed_at"]) > instant(at):
        raise EquityEvidenceError("Valuation source completion is future-dated")
    if not is_stream and instant(source["completed_at"]) > instant(mark["observed_at"]):
        raise EquityEvidenceError("Source completed after the valuation observation")
    mark["source_completed_at"] = source.pop("completed_at")
    mark["source"] = source
    mark["price"] = money(price)
    quotes = _fetch(
        conn,
        """SELECT q.*,f.import_id FROM forward_quotes q
        JOIN forward_runtime_imports f ON f.runtime_id=q.runtime_id
        AND f.instrument_id=q.instrument_id AND f.interval='1m'
        WHERE q.runtime_id=? AND q.instrument_id=? AND q.accepted=1
        AND f.import_id=? ORDER BY q.sequence DESC LIMIT 200""",
        (session, instrument, mark["source_import_id"]),
    )
    for quote in quotes:
        if (
            instant(quote["quote_timestamp"]) == instant(mark["observed_at"])
            and Decimal(quote["price_decimal"]) == price
            and instant(quote["received_at"]) <= instant(at)
        ):
            runtime = conn.execute(
                "SELECT * FROM forward_runtimes WHERE runtime_id=?", (session,)
            ).fetchone()
            if runtime is None or runtime["account_id"] != account["id"]:
                raise EquityEvidenceError("Valuation runtime ownership differs")
            config = json.loads(runtime["config_json"])
            if (
                hashlib.sha256(runtime["config_json"].encode()).hexdigest()
                != runtime["config_sha256"]
            ):
                raise EquityEvidenceError("Runtime configuration fingerprint differs")
            if config.get("provider") != "DETERMINISTIC_SYNTHETIC":
                raise EquityEvidenceError("No compatible real-data paper valuation policy")
            if source["import_type"] != "SYNTHETIC_STREAM":
                raise EquityEvidenceError("Synthetic and real valuation sources cannot be mixed")
            mark.update(
                provenance="SYNTHETIC",
                reference_kind="QUOTE",
                reference_id=quote["event_id"],
                reference_sha256=digest(quote),
                runtime_config_sha256=runtime["config_sha256"],
            )
            return mark
    # Standalone Phase 8 execution quotes are authenticated by the consumed snapshot.
    orders = _fetch(
        conn,
        """SELECT o.*,s.snapshot_id,s.snapshot_sha256,s.import_id
        FROM paper_orders o JOIN paper_execution_snapshot_uses s ON s.order_id=o.order_id
        WHERE o.account_id=? AND o.instrument_id=? AND o.status='FILLED'
        AND s.runtime_id IS NULL ORDER BY o.execution_timestamp DESC LIMIT 200""",
        (account["id"], instrument),
    )
    for order in orders:
        if (
            session == f"paper-account:{account['id']}"
            and order["import_id"] == mark["source_import_id"]
            and instant(order["execution_timestamp"]) == instant(mark["observed_at"])
            and instant(order["created_at"]) <= instant(at)
            and Decimal(order["reference_price"]) == price
        ):
            mark.update(
                provenance="QUALIFIED_PHASE8_EXECUTION",
                reference_kind="ORDER",
                reference_id=order["order_id"],
                reference_sha256=digest(order),
            )
            return mark
    raise EquityEvidenceError("Valuation mark lacks its accepted quote or execution binding")


def _close_evidence(conn: sqlite3.Connection, session: str, at: str) -> list[dict[str, Any]]:
    """A session-close label requires the approved confirmed calendar's actual close."""
    from aegis.market_quality import load_approved_calendar_registry

    runtime = conn.execute(
        "SELECT config_json FROM forward_runtimes WHERE runtime_id=?", (session,)
    ).fetchone()
    if runtime is None:
        raise EquityEvidenceError("Session-close checkpoint requires a forward calendar scope")
    registry = load_approved_calendar_registry()
    ids = json.loads(runtime["config_json"])["instrument_ids"]
    if not ids or len(ids) > 50:
        raise EquityEvidenceError("Session calendar scope is unavailable")
    result = []
    for instrument_id in ids:
        instrument = conn.execute(
            "SELECT exchange,segment FROM instruments WHERE id=?", (instrument_id,)
        ).fetchone()
        calendar = registry.resolve(instrument["exchange"], instrument["segment"])
        if calendar is None or calendar.confidence != "CONFIRMED":
            raise EquityEvidenceError("Confirmed session-close calendar unavailable")
        local = instant(at).astimezone(ZoneInfo(calendar.timezone_name))
        sessions = calendar.sessions_for(local.date())
        if not sessions or local.time() != max(s.closes for s in sessions):
            raise EquityEvidenceError("Checkpoint is not the confirmed final session close")
        result.append(
            {
                "instrument_id": instrument_id,
                "exchange": calendar.exchange,
                "segment": calendar.segment,
                "date": local.date().isoformat(),
                "close": local.time().isoformat(),
                "source": calendar.source,
                "confidence": calendar.confidence,
            }
        )
    return result


def _snapshot(
    conn: sqlite3.Connection,
    account_id: int,
    session: str,
    at: str,
    kind: str,
    event_key: str,
    source_id: str | None,
) -> dict[str, Any]:
    from aegis.paper_trading import _cash_balance, _reconcile

    account = dict(
        conn.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
    )
    if session_for(conn, account_id) != session:
        raise EquityEvidenceError("Equity session does not exclusively own this account")
    previous = conn.execute(
        """SELECT sequence,observed_at,payload_sha256 FROM paper_equity_snapshots
        WHERE account_id=? AND session_id=? ORDER BY sequence DESC LIMIT 1""",
        (account_id, session),
    ).fetchone()
    if instant(at) < instant(account["created_at"]) or (
        previous and instant(at) < instant(previous["observed_at"])
    ):
        raise EquityEvidenceError("Equity timestamps cannot move backwards")
    result: dict[str, Any] = {
        "policy": POLICY,
        "mode": "RESEARCH_ONLY",
        "account_id": account_id,
        "session_id": session,
        "sequence": previous["sequence"] + 1 if previous else 1,
        "previous_sha256": previous["payload_sha256"] if previous else None,
        "kind": kind,
        "event_key": event_key,
        "source_id": source_id,
        "timestamp": utc(at),
        "local_date": instant(at).astimezone(IST).date().isoformat(),
        "account_sha256": account_proof(account),
        "cash": None,
        "positions": [],
        "position_market_value": None,
        "equity": None,
        "cumulative_fees": None,
        "cumulative_realized_net_pnl": None,
        "unrealized_gross_pnl": None,
        "unrealized_net_of_entry_fees": None,
        "gross_trading_pnl": None,
        "net_trading_pnl": None,
        "external_cash_flows": "NOT_SUPPORTED",
        "valuation_status": "INCOMPLETE",
        "reconciliation_status": "FAILED",
        "ledger_ids": [],
        "execution_references": [],
        "ledger_sha256": None,
        "calendar_close": [],
        "latest_execution_at": None,
    }
    if _reconcile(conn, account, return_errors=True):
        result["reason"] = "Phase 8 reconciliation failed"
        return result
    result["reconciliation_status"] = "RECONCILED"
    try:
        ledger = financial_evidence(conn, account_id)
        if any(
            max(instant(r["execution_timestamp"]), instant(r["created_at"])) > instant(at)
            for r in ledger
        ):
            raise EquityEvidenceError("Current ledger contains activity after the snapshot time")
        if any((r["runtime_id"] or f"paper-account:{account_id}") != session for r in ledger):
            raise EquityEvidenceError("Account contains another session's financial activity")
        if kind == "INITIAL" and (ledger or previous):
            raise EquityEvidenceError("Initial equity cannot be backfilled")
        result["ledger_ids"] = [r["entry_id"] for r in ledger]
        result["execution_references"] = _references(ledger)
        result["ledger_sha256"] = digest(ledger)
        result["latest_execution_at"] = max(
            (utc(r["execution_timestamp"]) for r in ledger), default=None
        )
        positions = _fetch(
            conn,
            "SELECT * FROM paper_positions WHERE account_id=? ORDER BY instrument_id LIMIT 101",
            (account_id,),
        )
        if len(positions) > 100:
            raise EquityEvidenceError("Position evidence exceeds 100 instruments")
        cash = _cash_balance(conn, account)
        fees = sum(
            (
                sum((Decimal(v) for v in json.loads(r["fees_json"]).values()), Decimal(0))
                for r in ledger
            ),
            Decimal(0),
        )
        realized = sum(
            (Decimal(r["realized_net_pnl"]) for r in ledger if r["realized_net_pnl"] is not None),
            Decimal(0),
        )
        result.update(
            cash=money(cash),
            cumulative_fees=money(fees),
            cumulative_realized_net_pnl=money(realized),
        )
        value, unrealized, entry_fees = Decimal(0), Decimal(0), Decimal(0)
        provenance = set()
        for position in positions:
            mark = _mark(conn, account, session, position, at)
            position["mark"] = mark
            result["positions"].append(position)
            provenance.add(mark["provenance"])
            value += Decimal(mark["price"]) * position["quantity"]
            unrealized += (
                Decimal(mark["price"]) - Decimal(position["average_entry_price"])
            ) * position["quantity"]
            entry_fees += Decimal(position["remaining_entry_fees"])
        if len(provenance) > 1:
            raise EquityEvidenceError("Incompatible valuation provenance")
        if kind == "SESSION_CLOSE":
            result["calendar_close"] = _close_evidence(conn, session, at)
        equity = cash + value
        net = equity - Decimal(account["initial_cash"])
        if abs(net - (realized + unrealized - entry_fees)) > BASIS_TOLERANCE:
            raise EquityEvidenceError("Equity, realized and unpaid-allocation fee identity differs")
        result.update(
            position_market_value=money(value),
            equity=money(equity),
            unrealized_gross_pnl=money(unrealized),
            unrealized_net_of_entry_fees=money(unrealized - entry_fees),
            net_trading_pnl=money(net),
            gross_trading_pnl=money(net + fees),
            valuation_status="COMPLETE",
            reason=None,
        )
    except (EquityEvidenceError, JournalError) as exc:
        result["reason"] = str(exc)
    return result


def capture(
    conn: sqlite3.Connection,
    account_id: int,
    at: str,
    kind: str,
    event_key: str,
    *,
    session: str | None = None,
    source_id: str | None = None,
) -> str | None:
    """Evidence sidecar in caller's transaction; failure never blocks protective exits.

    A savepoint prevents partial evidence. Missing/incomplete evidence invalidates
    analytics through coverage checks, while Phase 8 retains financial authority.
    No migrations, journal repair or execution are performed here.
    """
    if not conn.in_transaction:
        raise EquityEvidenceError("Equity evidence requires an existing accounting transaction")
    conn.execute("SAVEPOINT equity_evidence")
    try:
        Settings.from_env()
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='paper_equity_snapshots'"
        ).fetchone():
            return None  # Older supported schemas remain viewable without implicit migrations.
        session = session or session_for(conn, account_id)
        identity = digest([POLICY, account_id, session, event_key])
        existing = conn.execute(
            "SELECT snapshot_id FROM paper_equity_snapshots WHERE snapshot_id=?", (identity,)
        ).fetchone()
        if existing:
            return str(existing[0])
        with localcontext() as context:
            context.prec = 50
            document = _snapshot(conn, account_id, session, at, kind, event_key, source_id)
        encoded = canonical(document)
        conn.execute(
            """INSERT INTO paper_equity_snapshots
            (snapshot_id,account_id,session_id,sequence,event_key,kind,observed_at,observed_us,
             local_date,valuation_status,reconciliation_status,previous_sha256,payload_json,payload_sha256)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                identity,
                account_id,
                session,
                document["sequence"],
                event_key,
                kind,
                document["timestamp"],
                micros(at),
                document["local_date"],
                document["valuation_status"],
                document["reconciliation_status"],
                document["previous_sha256"],
                encoded,
                digest(document),
            ),
        )
        return identity
    except (sqlite3.Error, ValueError, TypeError, KeyError, ArithmeticError, JournalError):
        conn.execute("ROLLBACK TO equity_evidence")
        return None
    finally:
        conn.execute("RELEASE equity_evidence")


def record_equity_checkpoint(
    database_path: Path, account_id: int, *, session: str, as_of: str, session_close: bool = False
) -> str | None:
    """Explicit trusted accounting checkpoint; never called by dashboard reads.

    It captures current reconciled state only, with no historical reconstruction.
    A closing checkpoint additionally requires the approved confirmed session close.
    """
    from aegis.paper_trading import _transaction

    Settings.from_env()
    kind = "SESSION_CLOSE" if session_close else "CHECKPOINT"
    with _transaction(database_path) as conn:
        return capture(conn, account_id, as_of, kind, f"{kind}:{utc(as_of)}", session=session)


def verify_snapshots(
    conn: sqlite3.Connection, account_id: int, session: str
) -> list[dict[str, Any]]:
    """Authenticate evidence in the caller's read transaction; never repair or replay orders."""
    with localcontext() as context:
        context.prec = 50
        return _verify_snapshots(conn, account_id, session)


def _verify_snapshots(
    conn: sqlite3.Connection, account_id: int, session: str
) -> list[dict[str, Any]]:
    from aegis.paper_trading import _reconcile

    account_row = conn.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
    if account_row is None or session_for(conn, account_id) != session:
        raise EquityEvidenceError("Session does not exclusively own the selected account")
    account = dict(account_row)
    if _reconcile(conn, account, return_errors=True):
        raise EquityEvidenceError("Phase 8 reconciliation failed")
    rows = _fetch(
        conn,
        """SELECT * FROM paper_equity_snapshots WHERE account_id=? AND session_id=?
        ORDER BY sequence LIMIT ?""",
        (account_id, session, MAX_SNAPSHOTS + 1),
    )
    if not rows or len(rows) > MAX_SNAPSHOTS:
        raise EquityEvidenceError("No equity evidence or snapshot reporting limit exceeded")
    ledger = financial_evidence(conn, account_id)
    if any((r["runtime_id"] or f"paper-account:{account_id}") != session for r in ledger):
        raise EquityEvidenceError("Mixed financial session ownership")
    by_id = {r["entry_id"]: r for r in ledger}
    previous = None
    previous_ids: set[str] = set()
    seen_fills: set[str] = set()
    result = []
    for sequence, row in enumerate(rows, 1):
        d = json.loads(row["payload_json"])
        if (
            row["payload_json"] != canonical(d)
            or digest(d) != row["payload_sha256"]
            or row["snapshot_id"] != digest([POLICY, account_id, session, d["event_key"]])
            or d["policy"] != POLICY
            or d["mode"] != "RESEARCH_ONLY"
            or d["account_id"] != account_id
            or d["session_id"] != session
            or d["sequence"] != sequence
            or row["sequence"] != sequence
            or d["account_sha256"] != account_proof(account)
            or d["previous_sha256"] != (previous["sha256"] if previous else None)
            or row["previous_sha256"] != d["previous_sha256"]
            or micros(d["timestamp"]) != row["observed_us"]
            or d["local_date"] != instant(d["timestamp"]).astimezone(IST).date().isoformat()
            or any(
                row[k] != d[k]
                for k in (
                    "kind",
                    "event_key",
                    "local_date",
                    "valuation_status",
                    "reconciliation_status",
                )
            )
            or row["observed_at"] != d["timestamp"]
            or (previous and instant(d["timestamp"]) < instant(previous["timestamp"]))
        ):
            raise EquityEvidenceError(
                "Equity identity, fingerprint, continuity or timestamp differs"
            )
        d["snapshot_id"], d["sha256"] = row["snapshot_id"], row["payload_sha256"]
        if d["kind"] == "FILL":
            if d["source_id"] in seen_fills or d["event_key"] != f"fill:{d['source_id']}":
                raise EquityEvidenceError("Duplicate or contradictory fill snapshot identity")
            seen_fills.add(d["source_id"])
        if d["valuation_status"] != "COMPLETE" and any(
            d[k] is not None
            for k in (
                "equity",
                "position_market_value",
                "unrealized_gross_pnl",
                "unrealized_net_of_entry_fees",
                "gross_trading_pnl",
                "net_trading_pnl",
            )
        ):
            raise EquityEvidenceError("Incomplete evidence claims a complete financial valuation")
        if d["valuation_status"] == "COMPLETE":
            refs = [by_id[i] for i in d["ledger_ids"]]
            ids = set(d["ledger_ids"])
            if (
                len(ids) != len(refs)
                or {r["order_id"] for r in refs} != seen_fills
                or not previous_ids <= ids
                or digest(refs) != d["ledger_sha256"]
                or _references(refs) != d["execution_references"]
                or d["reconciliation_status"] != "RECONCILED"
                or d["external_cash_flows"] != "NOT_SUPPORTED"
                or any(instant(r["execution_timestamp"]) > instant(d["timestamp"]) for r in refs)
                or d["latest_execution_at"]
                != max((utc(r["execution_timestamp"]) for r in refs), default=None)
            ):
                raise EquityEvidenceError(
                    "Referenced financial evidence differs or is future-dated"
                )
            # Reconciliation invariants, not a reconstructed authority or cost-basis engine.
            quantities: dict[int, int] = defaultdict(int)
            invested: dict[int, Decimal] = defaultdict(Decimal)
            realized: dict[int, Decimal] = defaultdict(Decimal)
            fees = Decimal(0)
            for ref in refs:
                instrument = ref["instrument_id"]
                quantities[instrument] += ref["quantity"] * (1 if ref["side"] == "BUY" else -1)
                invested[instrument] -= Decimal(ref["amount"])
                realized[instrument] += Decimal(ref["realized_net_pnl"] or "0")
                fees += sum((Decimal(v) for v in json.loads(ref["fees_json"]).values()), Decimal(0))
            expected_cash = Decimal(account["initial_cash"]) + sum(
                (Decimal(r["amount"]) for r in refs), Decimal(0)
            )
            positions = {p["instrument_id"]: p for p in d["positions"]}
            if len(positions) != len(d["positions"]) or set(positions) != {
                i for i, qty in quantities.items() if qty != 0
            }:
                raise EquityEvidenceError(
                    "Snapshot position quantities contradict referenced fills"
                )
            value, unrealized, remaining_fees = Decimal(0), Decimal(0), Decimal(0)
            provenance = set()
            for instrument, p in positions.items():
                m = p["mark"]
                # Verify historical mark against its durable quote, not the mutable latest paper_marks row.
                mark = _verify_mark(conn, account, session, p, d["timestamp"])
                if (
                    m != mark
                    or p["quantity"] != quantities[instrument]
                    or p["account_id"] != account_id
                ):
                    raise EquityEvidenceError("Persisted position/mark evidence differs")
                cost = Decimal(p["average_entry_price"]) * p["quantity"]
                entry_fees = Decimal(p["remaining_entry_fees"])
                if (
                    abs(cost + entry_fees - invested[instrument] - realized[instrument])
                    > BASIS_TOLERANCE
                    or cost <= 0
                    or entry_fees < 0
                ):
                    raise EquityEvidenceError(
                        "Snapshot cost basis contradicts Phase 8 cash/realized evidence"
                    )
                value += Decimal(m["price"]) * p["quantity"]
                unrealized += (Decimal(m["price"]) - Decimal(p["average_entry_price"])) * p[
                    "quantity"
                ]
                remaining_fees += entry_fees
                provenance.add(m["provenance"])
            expected_realized = sum(realized.values(), Decimal(0))
            net = expected_cash + value - Decimal(account["initial_cash"])
            expected = {
                "cash": expected_cash,
                "equity": expected_cash + value,
                "position_market_value": value,
                "cumulative_fees": fees,
                "cumulative_realized_net_pnl": expected_realized,
                "unrealized_gross_pnl": unrealized,
                "unrealized_net_of_entry_fees": unrealized - remaining_fees,
                "net_trading_pnl": net,
                "gross_trading_pnl": net + fees,
            }
            if (
                any(d[k] != money(v) for k, v in expected.items())
                or len(provenance) > 1
                or abs(net - (expected_realized + unrealized - remaining_fees)) > BASIS_TOLERANCE
            ):
                raise EquityEvidenceError("Snapshot financial invariants differ")
            if d["kind"] == "INITIAL" and (refs or previous):
                raise EquityEvidenceError("Initial snapshot is not authentic inception")
            if d["kind"] == "INITIAL":
                origin = (
                    account["created_at"]
                    if session == f"paper-account:{account_id}"
                    else conn.execute(
                        "SELECT created_at FROM forward_runtimes WHERE runtime_id=?", (session,)
                    ).fetchone()[0]
                )
                if utc(origin) != d["timestamp"]:
                    raise EquityEvidenceError(
                        "Initial snapshot time differs from authenticated account/session creation"
                    )
            if d["kind"] == "FILL" and not any(r["order_id"] == d["source_id"] for r in refs):
                raise EquityEvidenceError("Snapshot source fill is orphaned")
            if d["kind"] == "SESSION_CLOSE" and d["calendar_close"] != _close_evidence(
                conn, session, d["timestamp"]
            ):
                raise EquityEvidenceError("Session-close calendar evidence differs")
            previous_ids = ids
        result.append(d)
        previous = d
    # Deleted/interrupted fill snapshots cannot be concealed by a later mark/checkpoint.
    fills = {d["source_id"] for d in result if d["kind"] == "FILL"}
    if fills != {r["order_id"] for r in ledger}:
        raise EquityEvidenceError("Missing or orphaned committed-fill equity evidence")
    return result


def _verify_mark(
    conn: sqlite3.Connection,
    account: dict[str, Any],
    session: str,
    position: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    """Read-only historical verification independent of mutable latest-mark projections."""
    from aegis.paper_trading import _risk_from_json

    m = position["mark"]
    age = instant(at) - instant(m["observed_at"])
    if (
        age < timedelta(0)
        or age
        > timedelta(
            seconds=_risk_from_json(account["risk_limits_json"]).max_market_data_age_seconds
        )
        or Decimal(m["price"]) <= 0
        or not Decimal(m["price"]).is_finite()
        or m["instrument_id"] != position["instrument_id"]
        or m["account_id"] != account["id"]
        or Decimal(m["mark_price"]) != Decimal(m["price"])
    ):
        raise EquityEvidenceError("Snapshot mark is stale, invalid or future-dated")
    source = _source(conn, m["source_import_id"], position["instrument_id"])
    completion = source.pop("completed_at")
    if (
        instant(completion) < instant(m["source_completed_at"])
        or instant(m["source_completed_at"]) > instant(at)
        or (
            source["import_type"] not in ("SYNTHETIC_STREAM", "OBSERVED_STREAM")
            and instant(m["source_completed_at"]) > instant(m["observed_at"])
        )
    ):
        raise EquityEvidenceError(
            "Valuation source completion evidence is inconsistent or future-dated"
        )
    if source != m["source"]:
        raise EquityEvidenceError("Valuation import evidence differs")
    if m["reference_kind"] == "QUOTE":
        rows = _fetch(
            conn,
            """SELECT q.*,f.import_id FROM forward_quotes q
            JOIN forward_runtime_imports f ON f.runtime_id=q.runtime_id AND f.instrument_id=q.instrument_id
            AND f.interval='1m' WHERE q.runtime_id=? AND q.event_id=? AND q.instrument_id=?""",
            (session, m["reference_id"], position["instrument_id"]),
        )
        runtime = conn.execute(
            "SELECT config_json,config_sha256 FROM forward_runtimes WHERE runtime_id=?", (session,)
        ).fetchone()
        if (
            len(rows) != 1
            or digest(rows[0]) != m["reference_sha256"]
            or runtime is None
            or runtime["config_sha256"] != m["runtime_config_sha256"]
            or hashlib.sha256(runtime["config_json"].encode()).hexdigest()
            != runtime["config_sha256"]
            or json.loads(runtime["config_json"]).get("provider") != "DETERMINISTIC_SYNTHETIC"
            or source["import_type"] != "SYNTHETIC_STREAM"
            or m["provenance"] != "SYNTHETIC"
            or rows[0]["accepted"] != 1
            or rows[0]["import_id"] != m["source_import_id"]
            or Decimal(rows[0]["price_decimal"]) != Decimal(m["price"])
            or instant(rows[0]["quote_timestamp"]) != instant(m["observed_at"])
            or instant(rows[0]["received_at"]) > instant(at)
        ):
            raise EquityEvidenceError("Accepted valuation quote evidence differs")
    elif m["reference_kind"] == "ORDER":
        rows = _fetch(
            conn,
            """SELECT o.*,s.snapshot_id,s.snapshot_sha256,s.import_id
            FROM paper_orders o JOIN paper_execution_snapshot_uses s ON s.order_id=o.order_id
            WHERE o.account_id=? AND o.order_id=? AND s.runtime_id IS NULL AND o.status='FILLED'""",
            (account["id"], m["reference_id"]),
        )
        if (
            session != f"paper-account:{account['id']}"
            or len(rows) != 1
            or digest(rows[0]) != m["reference_sha256"]
            or rows[0]["import_id"] != m["source_import_id"]
            or Decimal(rows[0]["reference_price"]) != Decimal(m["price"])
            or instant(rows[0]["execution_timestamp"]) != instant(m["observed_at"])
            or instant(rows[0]["created_at"]) > instant(at)
            or m["provenance"] != "QUALIFIED_PHASE8_EXECUTION"
        ):
            raise EquityEvidenceError("Consumed Phase 8 valuation evidence differs")
    else:
        raise EquityEvidenceError("Unsupported valuation reference")
    return m
