"""Read-only Aegis Paper Terminal. Launch: python -m aegis.trading_dashboard."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st

from aegis import trading_dashboard_ui as ui
from aegis.config import Settings
from aegis.trading_dashboard_data import (
    IST,
    PERIODS,
    UNAVAILABLE,
    csv_bytes,
    date_window,
    list_accounts,
    list_sessions,
    read_dashboard,
)

PAGES = (
    "Overview",
    "Live Market",
    "Open Positions",
    "Trade History",
    "Performance Analytics",
    "Strategy & AI Decisions",
    "Risk & System Health",
    "Execution Journal",
)
REFRESH = {"Manual": None, "5 seconds": 5, "15 seconds": 15, "30 seconds": 30, "60 seconds": 60}


def overview(data: dict[str, Any]) -> None:
    finance, analytics = data["finance"], data["analytics"]
    ui.kpis(
        [
            {
                "label": "Portfolio equity",
                "value": finance.get("equity"),
                "accent": True,
                "note": "Initial capital " + ui.formatted(finance.get("initial_capital")),
            },
            {
                "label": "Available paper cash",
                "value": finance.get("available_cash"),
                "note": "Reconciled cash balance",
            },
            {
                "label": "Net P&L",
                "value": finance.get("net_pnl"),
                "directional": True,
                "note": "Since account inception · simulated",
            },
            {
                "label": "Selected-period return",
                "value": finance.get("period_return_pct"),
                "kind": "percent",
                "directional": True,
                "note": "Period net P&L " + ui.formatted(finance.get("period_pnl")),
            },
            {
                "label": "Realized P&L",
                "value": finance.get("realized_pnl"),
                "directional": True,
                "note": "Lifetime net realized · fees included",
            },
            {
                "label": "Unrealized P&L",
                "value": finance.get("unrealized_gross_pnl"),
                "directional": True,
                "note": "Eligible marks · gross of exit costs",
            },
            {
                "label": "Modeled fees",
                "value": finance.get("period_fees"),
                "note": "Selected period · already accounted for",
            },
            {
                "label": "Open positions",
                "value": len(finance["positions"]) if "positions" in finance else None,
                "kind": "count",
                "note": f"{finance.get('completed_trades', UNAVAILABLE)} completed trades in period",
            },
        ]
    )
    left, right = st.columns([2, 1], gap="medium")
    with left, st.container(border=True):
        ui.section("Portfolio equity", "VERIFIED OBSERVATIONS · INR")
        ui.plot(
            ui.equity_chart(analytics), "UNAVAILABLE · No verified equity snapshots in this period."
        )
        st.caption(
            "Observed event points; gaps are never filled. "
            + str(analytics.get("closing_at") or "")
        )
    with right, st.container(border=True):
        ui.section("Daily net P&L", "CONFIRMED SESSION CLOSES")
        ui.plot(
            ui.pnl_chart(
                analytics.get("daily", []), bucket="local_date", value="net_pnl", height=260
            ),
            analytics.get("daily_limitation")
            or "UNAVAILABLE · Closing equity boundaries are missing.",
        )
        st.caption("Actual closing checkpoints · IST reporting dates")
    with st.container(border=True):
        ui.section("Recent simulated transactions", "SELECTED PERIOD · DISPLAYED PAGE")
        ui.styled_table(
            data["trades"][:5], "No simulated transactions in this period.", max_height=210
        )
    with st.expander("Accounting basis & realized attribution"):
        st.caption(finance.get("period_basis", UNAVAILABLE))
        st.caption(
            "Equity is cash plus eligible marked value before hypothetical liquidation costs. Positions are current snapshots."
        )
        st.write("Reconciliation", finance.get("reconciliation", UNAVAILABLE))
        st.write(
            "Winning / losing completed trades",
            f"{finance.get('wins', UNAVAILABLE)} / {finance.get('losses', UNAVAILABLE)}",
        )
        st.write("Win rate", ui.formatted(finance.get("win_rate_pct"), "percent"))
        first, second = st.columns(2)
        with first:
            ui.section("Daily realized trade P&L")
            ui.plot(
                ui.pnl_chart(finance.get("realized_groups", {}).get("day", [])),
                "No realized P&L events in this period.",
            )
        with second:
            ui.section("Stock-wise realized trade P&L")
            ui.plot(
                ui.pnl_chart(finance.get("realized_groups", {}).get("symbol", [])),
                "No realized P&L events in this period.",
            )


def performance(data: dict[str, Any]) -> None:
    finance, analytics = data["finance"], data["analytics"]
    ui.kpis(
        [
            {
                "label": "Period net P&L",
                "value": analytics.get("period_pnl"),
                "directional": True,
                "note": "Verified opening to closing equity",
            },
            {
                "label": "Period return",
                "value": analytics.get("period_return_pct"),
                "kind": "percent",
                "directional": True,
                "note": "Same period & session as the charts",
            },
            {
                "label": "Maximum observed drawdown",
                "value": analytics.get("max_drawdown_pct"),
                "kind": "percent",
                "note": "Magnitude · sampled equity observations",
            },
            {
                "label": "Cumulative net return",
                "value": analytics.get("cumulative_net_return_pct"),
                "kind": "percent",
                "directional": True,
                "note": "Authenticated inception to closing equity",
            },
        ]
    )
    if analytics.get("boundary_reason"):
        st.warning("Period metrics UNAVAILABLE · " + analytics["boundary_reason"])
    first, second = st.columns(2, gap="medium")
    with first, st.container(border=True):
        ui.section("Portfolio equity", "VERIFIED · INR")
        ui.plot(
            ui.equity_chart(analytics), "UNAVAILABLE · No verified equity points in this period."
        )
    with second, st.container(border=True):
        ui.section("Observed drawdown", "NEGATIVE % BELOW OBSERVED PEAK")
        ui.plot(
            ui.drawdown_chart(analytics),
            "UNAVAILABLE · A complete verified drawdown path is required.",
        )
    first, second = st.columns(2, gap="medium")
    with first, st.container(border=True):
        ui.section("Daily net P&L", "INR · IST SESSION DATES")
        ui.plot(
            ui.pnl_chart(analytics.get("daily", []), bucket="local_date", value="net_pnl"),
            analytics.get("daily_limitation")
            or "UNAVAILABLE · Daily closing boundaries are missing.",
        )
    with second, st.container(border=True):
        ui.section("Realized attribution", "FILLED TRADES · NET REALIZED")
        grouping = st.selectbox(
            "Realized P&L grouping", ["day", "week", "month", "year", "symbol", "strategy"], index=4
        )
        ui.plot(
            ui.pnl_chart(finance.get("realized_groups", {}).get(grouping, []), height=175),
            "No realized P&L events in this period.",
        )
    ui.kpis(
        [
            {
                "label": "Completed trades",
                "value": finance.get("completed_trades"),
                "kind": "count",
                "note": "Completed reconciled position cycles",
            },
            {
                "label": "Win rate",
                "value": finance.get("win_rate_pct"),
                "kind": "percent",
                "note": f"{finance.get('wins', UNAVAILABLE)} winners · {finance.get('losses', UNAVAILABLE)} losers",
            },
            {
                "label": "Profit factor",
                "value": finance.get("profit_factor"),
                "kind": "ratio",
                "note": "Net completed winners / absolute losers",
            },
            {
                "label": "Modeled fees",
                "value": finance.get("period_fees"),
                "note": "Period activity · never subtracted twice",
            },
        ]
    )
    st.caption(analytics.get("drawdown_basis", "Equity evidence UNAVAILABLE"))
    with st.expander("Accounting components, Sharpe & daily boundaries"):
        st.write("Average winning trade", ui.formatted(finance.get("average_win")))
        st.write("Average losing trade", ui.formatted(finance.get("average_loss")))
        st.write("Period realized P&L", ui.formatted(finance.get("period_realized_pnl")))
        st.write(
            "Closing gross unrealized P&L",
            ui.formatted(analytics.get("closing_unrealized_gross_pnl")),
        )
        st.write(
            "Closing unrealized after paid entry fees",
            ui.formatted(analytics.get("closing_unrealized_net_of_entry_fees")),
        )
        st.write("Sharpe (qualified daily series)", ui.formatted(analytics.get("sharpe"), "ratio"))
        if analytics.get("sharpe_limitation"):
            st.caption(analytics["sharpe_limitation"])
        st.caption(finance.get("period_basis", UNAVAILABLE))
        ui.styled_table(
            analytics.get("daily", []), "No verified daily boundary pairs in this period."
        )


def risk_health(data: dict[str, Any]) -> None:
    ui.section("Risk & system health", "PERSISTED FACTS · NO RECOVERY OR REPAIR")
    ui.health_strip(data)
    first, second = st.columns([1, 2], gap="medium")
    with first, st.container(border=True):
        ui.section("Account controls")
        st.write("Mode", "RESEARCH_ONLY")
        st.write("Live broker orders", "DISABLED")
        st.write("Account halted", bool(data["account"]["halted"]))
        st.write("Halt class", data["account"]["halt_class"])
        st.write("Halt reason", data["account"]["halt_reason"] or "No persisted halt reason")
        st.write("Journal integrity", data["journal_reconciliation"]["status"])
        st.write("Equity evidence", data["analytics"]["status"])
        st.caption(
            "Journal checks cover displayed records. Persisted RUNNING does not prove a connected feed."
        )
    with second, st.container(border=True):
        ui.section("Source-use declarations", "NOT A DETERMINATION OF LEGAL RIGHTS")
        ui.styled_table(data["source_imports"])
        ui.section("Stale quote alerts")
        ui.styled_table(
            [row for row in data["market"] if row["freshness"] == "STALE"],
            "No stale observations on this page. Missing quotes do not prove freshness.",
            max_height=170,
        )
    with st.expander("Persisted risk limits & runtime state", expanded=True):
        first, second = st.columns(2)
        with first:
            st.json(data["risk_limits"])
        with second:
            st.json(data["runtime"])
    with st.expander("Scheduler, blocked decisions & health events"):
        ui.styled_table(
            [
                {
                    key: row.get(key)
                    for key in (
                        "event_id",
                        "decision_timestamp",
                        "status",
                        "proposal_id",
                        "proposal_binding_state",
                    )
                }
                for row in data["proposals"]
            ]
        )
        ui.styled_table(
            [
                r
                for r in data["decisions"]
                if r.get("state") == "BLOCKED"
                or r.get("python_validation") in ("REJECTED", "FAILED", "BLOCKED")
            ]
        )
        ui.styled_table(data["health"])
    st.caption(
        "Calendar/source qualification may remain blocked. Existing permitted synthetic records remain viewable. Historical M2 portfolios are not merged."
    )


def render_pages(data: dict[str, Any]) -> None:
    tabs = st.tabs(PAGES)
    with tabs[0]:
        overview(data)
    with tabs[1]:
        ui.section("Market observations", "PERSISTED · NO BROKER CONNECTION")
        st.caption(
            "Synthetic and real observations retain their provenance. Historical quotes are not current live prices; snapshots alone are not live ticks."
        )
        ui.styled_table(data["market"], "No accepted quotes are persisted for this runtime.")
        with st.expander("Runtime & feed history", expanded=True):
            st.json(data["runtime"])
            st.caption("A persisted RUNNING runtime is not evidence of a currently connected feed.")
            ui.styled_table(data["health"][:10])
    with tabs[2]:
        ui.section("Open paper positions", "CURRENT RECONCILED SNAPSHOT")
        st.caption(
            "Independent of activity dates. Eligible, timestamped marks are required for valuation. Unrealized P&L is gross; paid fees remain in account equity."
        )
        ui.styled_table(
            data["finance"].get("positions", []),
            "No reconciled open positions, or reconciliation is unavailable.",
        )
    with tabs[3]:
        ui.section("Trade history", "SIMULATED FILLS · SELECTED PERIOD")
        st.caption(
            "Filter by symbol, action and status in the sidebar. Click a column header to sort. Additional evidence columns scroll within the table."
        )
        ui.styled_table(
            data["trades"], "No simulated transactions match these filters.", max_height=540
        )
        st.download_button(
            "Download sanitized trade page (CSV)",
            csv_bytes(data["trades"]),
            file_name="aegis-paper-trades.csv",
            mime="text/csv",
        )
    with tabs[4]:
        performance(data)
    with tabs[5]:
        ui.section("Strategy & AI decisions", "AUTHENTICATED DECISION EVIDENCE")
        st.caption(
            "Proposals are not executions. Order links require consumed authorization and execution evidence. NO_DECISION remains explicit; AI origin is shown only where recorded."
        )
        ui.styled_table(data["decisions"], max_height=470)
        with st.expander("Scheduler proposals · not proof of execution"):
            ui.styled_table(data["proposals"])
    with tabs[6]:
        risk_health(data)
    with tabs[7]:
        ui.section("Execution journal", "PERSISTED CHRONOLOGY · STABLE RECORD IDS")
        st.caption(
            "Chronological proximity is not causal proof. Recovery, reconciliation and halt events appear only where persisted."
        )
        ui.styled_table(data["journal"], max_height=540)
        st.download_button(
            "Download sanitized journal page (CSV)",
            csv_bytes(data["journal"]),
            file_name="aegis-paper-journal.csv",
            mime="text/csv",
        )


def render_app() -> None:
    st.set_page_config(page_title="Aegis | Paper Terminal", page_icon="◈", layout="wide")
    st.html(ui.STYLES)
    empty_slot = st.empty()
    try:
        Settings.from_env()
    except ValueError:
        st.error("Only RESEARCH_ONLY configuration is supported.")
        return
    with st.sidebar, st.container(key="aegis-workspace", gap="xsmall"):
        st.html(
            '<div class="aegis-brand"><div class="aegis-monogram">A</div><div><h1>AEGIS</h1><p>RESEARCH WORKSPACE</p></div></div>'
        )
        with st.expander("Database · read only"):
            st.caption("RESEARCH_ONLY · Read-only evidence")
            path_text = st.text_input(
                "Existing SQLite database (read only)",
                value=os.environ.get("AEGIS_DASHBOARD_DATABASE_PATH", ""),
            )
        if not path_text:
            with empty_slot.container():
                ui.header()
                st.info(
                    "Select an existing Aegis paper database. The dashboard never initializes or migrates it."
                )
            return
        database = Path(path_text).expanduser()
        try:
            accounts = list_accounts(database)
        except (sqlite3.Error, OSError, ValueError):
            st.error("Database is unavailable or unsupported. No initialization was attempted.")
            return
        if not accounts:
            with empty_slot.container():
                ui.header()
                st.info(
                    "No persisted paper accounts are available. No accounts, trades or prices were created."
                )
            return
        account_id = st.selectbox(
            "Paper account / portfolio",
            [a["id"] for a in accounts],
            format_func=lambda value: next(
                f"{a['id']} · {a['name']}" for a in accounts if a["id"] == value
            ),
        )
        try:
            sessions = list_sessions(database, account_id)
        except (sqlite3.Error, OSError):
            sessions = []
        run = st.selectbox(
            "Paper session",
            [None] + [r["runtime_id"] for r in sessions],
            index=1 if sessions else 0,
            format_func=lambda value: value or "Standalone account journal",
        )
        period = st.selectbox("Date range · Asia/Kolkata", PERIODS, index=5)
        today = datetime.now(IST).date()
        start = end = None
        if period == "Custom Date Range":
            start = st.date_input("Start date", value=today)
            end = st.date_input("End date", value=today)
        try:
            date_window(period, datetime.now(UTC), start, end)
        except ValueError as error:
            st.error(str(error))
            return
        refresh_column, button_column = st.columns(
            [3, 2], gap="xsmall", vertical_alignment="bottom"
        )
        with refresh_column:
            refresh = st.selectbox("Refresh interval", list(REFRESH), index=2)
        with button_column:
            if st.button("Refresh", width="stretch", help="Refresh now"):
                st.rerun()
        with st.expander("Trade filters & record page"):
            search = st.text_input("Symbol search", max_chars=64)
            action = st.selectbox("Action", ["All", "BUY", "SELL"])
            status = st.selectbox("Execution status", ["All", "FILLED", "REJECTED"])
            page = st.number_input("Record page", min_value=1, max_value=101, value=1)
            st.caption(
                "100 records per page. Filters apply before pagination; positions remain current snapshots."
            )
        st.html('<div class="aegis-section"><h2>System status</h2><span>EVIDENCE ONLY</span></div>')
        status_slot = st.empty()

    @st.fragment(run_every=REFRESH[refresh])
    def body() -> None:
        now = datetime.now(UTC)
        current_window = date_window(period, now, start, end)
        data = read_dashboard(
            database,
            account_id,
            current_window,
            as_of=now,
            run_id=run,
            limit=100,
            offset=(int(page) - 1) * 100,
            trade_search=search,
            trade_action=None if action == "All" else action,
            trade_status=None if status == "All" else status,
        )
        ui.header(
            data if data["status"] == "AVAILABLE" else None,
            period=period,
            refresh=refresh,
            session=run,
        )
        if data["status"] != "AVAILABLE":
            st.warning(data.get("message", "Evidence is unavailable."))
            return
        with status_slot.container():
            ui.health_strip(data)
        finance = data["finance"]
        if finance.get("reconciliation") != "RECONCILED" or finance.get("limitation"):
            st.warning(
                finance.get("limitation", "Financial values unavailable until records reconcile.")
            )
        render_pages(data)
        with st.expander("Scope & evidence limits"):
            st.caption(data["scope"])
            st.caption(
                f"View assessed {ui.local_time(now.isoformat())}. Last evidence refers to persisted observations, not the refresh clock."
            )

    with st.container(key="aegis-terminal", gap="xsmall"):
        body()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local read-only Aegis paper terminal")
    parser.add_argument(
        "--database", type=Path, help="Explicit existing database; never initialized"
    )
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.database:
        os.environ["AEGIS_DASHBOARD_DATABASE_PATH"] = str(args.database.absolute())
    from streamlit.web import cli

    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__)),
        "--server.address=127.0.0.1",
        f"--server.port={args.port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
        "--client.toolbarMode=minimal",
        *ui.theme_arguments(),
    ]
    return int(cli.main() or 0)


if __name__ == "__main__":
    from streamlit.runtime.scriptrunner import get_script_run_ctx

    if get_script_run_ctx(suppress_warning=True) is not None:
        render_app()
    else:
        raise SystemExit(main())
