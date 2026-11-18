"""Isolated, offline synthetic paper-trading demonstration.

No broker integration, external data, credentials, or live orders.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from aegis.backtesting import zero_cost_assumptions
from aegis.database import get_connection, initialize_database
from aegis.forward_runtime import (
    DeterministicSyntheticQuoteProvider,
    ForwardRuntimeService,
)
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_quality import load_calendar_registry
from aegis.paper_trading import (
    RiskLimits,
    create_paper_account,
    get_paper_account_report,
    reconcile_paper_account,
)

_START = datetime(2026, 1, 5, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def _create_test_calendar(path: Path) -> None:
    document = {
        "schema_version": 1,
        "staleness_policy": {
            "potential_after_missing": 1,
            "stale_after_missing": 3,
        },
        "calendars": [
            {
                "exchange": "NSE",
                "segment": "NSE",
                "timezone": "Asia/Kolkata",
                "confidence": "CONFIRMED",
                "coverage_start": "2026-01-05",
                "coverage_end": "2026-01-05",
                "source": "SYNTHETIC_TEST_ONLY",
                "weekdays": [0, 1, 2, 3, 4],
                "sessions": [{"open": "09:15", "close": "09:45"}],
                "holidays": [],
                "special_sessions": [],
            }
        ],
    }
    path.write_text(
        json.dumps(document, sort_keys=True),
        encoding="utf-8",
    )


def run_pilot() -> dict[str, object]:
    """Execute an isolated BUY/target-SELL cycle and verify its ledger."""
    with tempfile.TemporaryDirectory(prefix="aegis-synthetic-pilot-") as temp:
        root = Path(temp)
        database = root / "pilot.db"
        calendar_path = root / "synthetic_calendar.json"

        _create_test_calendar(calendar_path)

        # Calendar loading is configuration-sensitive. Restore caller state.
        previous_calendar = os.environ.get("AEGIS_CALENDAR_CONFIG_PATH")
        os.environ["AEGIS_CALENDAR_CONFIG_PATH"] = str(calendar_path)

        try:
            calendars = load_calendar_registry(calendar_path)
            initialize_database(database)

            instrument = register_instrument(
                database,
                InstrumentInput(
                    "NSE",
                    "SYNTH",
                    "Isolated synthetic paper pilot",
                    "EQUITY",
                    segment="NSE",
                    tick_size=Decimal("0.05"),
                ),
            )

            limits = RiskLimits(
                max_order_value=Decimal(2000),
                max_position_quantity=10,
                max_instrument_exposure=Decimal(3000),
                max_portfolio_exposure=Decimal(8000),
                max_daily_loss=Decimal(200),
                max_market_data_age_seconds=60,
            )

            account_id = create_paper_account(
                database,
                name="synthetic-pilot",
                initial_cash=Decimal(10000),
                risk_limits=limits,
                costs=zero_cost_assumptions(),
                created_at=_START.isoformat(),
            )

            runtime = ForwardRuntimeService.create(
                database,
                "synthetic-pilot-runtime",
                account_id,
                (instrument.id,),
                calendars,
                max_candidates=5,
                max_quote_delay_seconds=60,
                degraded_after_seconds=10,
                stale_after_seconds=180,
                max_execution_price_deviation=Decimal("0.05"),
                created_at=_START.isoformat(),
            )

            provider = DeterministicSyntheticQuoteProvider(
                (instrument.id,),
                start_at=_START,
                base_prices={instrument.id: Decimal(100)},
                price_steps={instrument.id: Decimal("0.05")},
            )
            quotes = list(provider.quotes(5))

            first = runtime.process_quote(quotes[0])
            second = runtime.process_quote(quotes[1])

            if first.scheduled_event_ids:
                raise RuntimeError("Unexpected event on first quote")
            if len(second.scheduled_event_ids) != 1:
                raise RuntimeError("Expected exactly one completed-candle event")

            event_id = second.scheduled_event_ids[0]
            proposal = runtime.build_mock_proposal(
                event_id,
                "BUY",
                1,
                stop_loss=Decimal(99),
                target_price=Decimal("100.25"),
            )
            snapshot = runtime.execution_snapshot(instrument.id, quotes[1])
            buy = runtime.route_event(event_id, proposal, snapshot)

            if buy.status != "FILLED":
                raise RuntimeError(f"Synthetic BUY failed: {buy.reason}")

            duplicate = runtime.route_event(event_id, proposal, snapshot)
            if duplicate.status != "FILLED" or not duplicate.duplicate:
                raise RuntimeError("Duplicate BUY was not handled idempotently")

            exit_results = []
            for quote in quotes[2:]:
                result = runtime.process_quote(quote)
                if result.exit_result is not None:
                    exit_results.append(result.exit_result)

            if len(exit_results) != 1 or exit_results[0].status != "FILLED":
                raise RuntimeError("Expected exactly one filled target exit")

            reconciliation = reconcile_paper_account(database, account_id)
            if reconciliation["status"] != "RECONCILED":
                raise RuntimeError("Paper account reconciliation failed")

            with closing(get_connection(database)) as connection:
                rows = connection.execute(
                    """
                    SELECT side, status, COUNT(*) AS total
                    FROM paper_orders
                    WHERE account_id = ?
                    GROUP BY side, status
                    ORDER BY side
                    """,
                    (account_id,),
                ).fetchall()

            order_counts = {f"{row['side']}_{row['status']}": row["total"] for row in rows}
            if order_counts != {"BUY_FILLED": 1, "SELL_FILLED": 1}:
                raise RuntimeError(f"Unexpected paper orders: {order_counts}")

            restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
            valuation = restored.portfolio_valuation()
            if valuation["simulated"] is not True:
                raise RuntimeError("Non-simulated valuation detected")
            if Decimal(str(valuation["market_value"])) != 0:
                raise RuntimeError("Expected a fully closed position")

            report = get_paper_account_report(database, account_id)
            if report["positions"]:
                raise RuntimeError("Unexpected remaining open positions")

            # A second restart must not create another order.
            ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
            with closing(get_connection(database)) as connection:
                order_total = connection.execute(
                    "SELECT COUNT(*) FROM paper_orders WHERE account_id = ?",
                    (account_id,),
                ).fetchone()[0]
            if order_total != 2:
                raise RuntimeError("Restart changed the order count")

            return {
                "mode": "RESEARCH_ONLY",
                "data_source": "DETERMINISTIC_SYNTHETIC",
                "broker": "NONE",
                "live_orders": False,
                "initial_cash_inr": "10000",
                "orders": order_counts,
                "reconciliation": reconciliation["status"],
                "restart_verified": True,
                "cash_inr": str(valuation["cash"]),
                "market_value_inr": str(valuation["market_value"]),
                "equity_inr": str(valuation["equity"]),
                "account_report": {
                    "available_cash": report.get("available_cash"),
                    "realized_net_pnl": report.get("realized_net_pnl"),
                    "unrealized_gross_pnl": report.get("unrealized_gross_pnl"),
                },
            }
        finally:
            if previous_calendar is None:
                os.environ.pop("AEGIS_CALENDAR_CONFIG_PATH", None)
            else:
                os.environ["AEGIS_CALENDAR_CONFIG_PATH"] = previous_calendar


def main() -> None:
    result = run_pilot()
    print("\nPROJECT AEGIS - SYNTHETIC PAPER PILOT")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("\nP10P3_SYNTHETIC_PAPER_PILOT_PASSED")


if __name__ == "__main__":
    main()
