"""Bounded, persistent, research-only synthetic pilot.

P10-P5B foundation: durable quote processing and restart verification.
No broker connections or live orders.
"""

from __future__ import annotations

import argparse
import json
import os
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
    Quote,
)
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_quality import load_calendar_registry
from aegis.paper_trading import (
    RiskLimits,
    create_paper_account,
    reconcile_paper_account,
)

START = datetime(2026, 1, 5, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
RUNTIME_ID = "p10p5-synthetic-runtime"


def _recover_pending_holds(
    database: Path,
    runtime: ForwardRuntimeService,
) -> int:
    """Recover only eligible pending synthetic HOLD decisions.

    Never replay financial orders or substitute a newer quote.
    """
    with closing(get_connection(database)) as connection:
        pending = connection.execute(
            "SELECT event_id, instrument_id, interval, decision_timestamp, "
            "proposal_binding_state FROM forward_scheduler_events "
            "WHERE runtime_id = ? AND status = 'PENDING' "
            "ORDER BY decision_timestamp, event_id",
            (RUNTIME_ID,),
        ).fetchall()

    recovered = 0
    for event in pending:
        if event["proposal_binding_state"] != "UNBOUND":
            raise RuntimeError(
                "Pending event has an existing proposal binding; automatic recovery is prohibited."
            )

        instrument_id = int(event["instrument_id"])

        with closing(get_connection(database)) as connection:
            latest = connection.execute(
                "SELECT * FROM forward_quotes "
                "WHERE runtime_id = ? AND instrument_id = ? "
                "AND accepted = 1 ORDER BY sequence DESC LIMIT 1",
                (RUNTIME_ID, instrument_id),
            ).fetchone()

        if latest is None:
            raise RuntimeError("Pending event has no accepted quote evidence.")

        if latest["sequence"] is None or latest["quote_timestamp"] is None:
            raise RuntimeError("Pending event has incomplete quote evidence.")

        quote = Quote(
            instrument_id=instrument_id,
            timestamp=str(latest["quote_timestamp"]),
            sequence=int(latest["sequence"]),
            price=Decimal(str(latest["price_decimal"])),
            quantity=int(latest["quantity"]),
            event_id=str(latest["event_id"]),
            received_at=str(latest["received_at"]),
        )

        proposal = runtime.build_mock_proposal(str(event["event_id"]), "HOLD", 0)

        snapshot = runtime.execution_snapshot(
            instrument_id,
            quote,
            interval=str(event["interval"]),
        )

        outcome = runtime.route_event(str(event["event_id"]), proposal, snapshot)

        if outcome.status != "HOLD":
            raise RuntimeError(
                f"Pending HOLD recovery was rejected: {outcome.status} {outcome.reason}"
            )

        recovered += 1

    return recovered


def run_batch(root: Path, count: int = 5) -> dict[str, object]:
    if type(count) is not int or not 1 <= count <= 20:
        raise ValueError("count must be between 1 and 20")

    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    database = root / "pilot.db"
    calendar_path = root / "synthetic_calendar.json"

    from aegis.synthetic_paper_pilot import _create_test_calendar

    previous_calendar = os.environ.get("AEGIS_CALENDAR_CONFIG_PATH")
    try:
        if not calendar_path.exists():
            _create_test_calendar(calendar_path)
        os.environ["AEGIS_CALENDAR_CONFIG_PATH"] = str(calendar_path)
        calendars = load_calendar_registry(calendar_path)

        if not database.exists():
            initialize_database(database)
            instrument = register_instrument(
                database,
                InstrumentInput(
                    "NSE",
                    "SYNTH",
                    "Persistent synthetic pilot",
                    "EQUITY",
                    segment="NSE",
                    tick_size=Decimal("0.05"),
                ),
            )
            account_id = create_paper_account(
                database,
                name="p10p5-synthetic",
                initial_cash=Decimal(10000),
                risk_limits=RiskLimits(
                    max_order_value=Decimal(2000),
                    max_position_quantity=10,
                    max_instrument_exposure=Decimal(3000),
                    max_portfolio_exposure=Decimal(8000),
                    max_daily_loss=Decimal(200),
                    max_market_data_age_seconds=60,
                ),
                costs=zero_cost_assumptions(),
                created_at=START.isoformat(),
            )
            runtime = ForwardRuntimeService.create(
                database,
                RUNTIME_ID,
                account_id,
                (instrument.id,),
                calendars,
                max_candidates=5,
                max_quote_delay_seconds=60,
                degraded_after_seconds=10,
                stale_after_seconds=180,
                max_execution_price_deviation=Decimal("0.05"),
                created_at=START.isoformat(),
            )
        else:
            runtime = ForwardRuntimeService.open(database, RUNTIME_ID, calendars)

        instrument_id = runtime.instrument_ids[0]

        # Recover eligible HOLD events before advancing the quote cursor.
        recovered_holds = _recover_pending_holds(database, runtime)

        # Fail closed if any pending event remains unresolved.
        with closing(get_connection(database)) as connection:
            pending = connection.execute(
                "SELECT event_id, interval FROM forward_scheduler_events "
                "WHERE runtime_id = ? AND status = 'PENDING' "
                "ORDER BY decision_timestamp, event_id LIMIT 1",
                (RUNTIME_ID,),
            ).fetchone()

        if pending is not None:
            raise RuntimeError(
                "Unresolved scheduler event blocks quote continuation: "
                f"{pending['event_id']} ({pending['interval']})."
            )

        with closing(get_connection(database)) as connection:
            cursor = connection.execute(
                """SELECT sequence FROM forward_quotes
                   WHERE runtime_id = ? AND instrument_id = ?
                     AND accepted = 1
                   ORDER BY sequence DESC LIMIT 1""",
                (RUNTIME_ID, instrument_id),
            ).fetchone()

        completed = int(cursor["sequence"]) if cursor else 0
        if completed + count > 20:
            raise ValueError("Pilot exceeds the 20-quote synthetic session limit")

        provider = DeterministicSyntheticQuoteProvider(
            (instrument_id,),
            start_at=START,
            base_prices={instrument_id: Decimal(100)},
            price_steps={instrument_id: Decimal("0.05")},
        )

        quotes = list(provider.quotes(completed + count))[completed:]
        accepted = 0
        scheduled = 0
        holds = 0

        for quote in quotes:
            result = runtime.process_quote(quote)
            if not result.accepted or result.duplicate:
                raise RuntimeError(f"Quote processing failed: {result.reason}")
            accepted += 1

            for event_id in result.scheduled_event_ids:
                scheduled += 1
                proposal = runtime.build_mock_proposal(event_id, "HOLD", 0)
                snapshot = runtime.execution_snapshot(
                    instrument_id,
                    quote,
                    interval=proposal.timeframe,
                )
                outcome = runtime.route_event(event_id, proposal, snapshot)
                if outcome.status != "HOLD":
                    raise RuntimeError(f"Expected HOLD: {outcome.status} {outcome.reason}")
                holds += 1

        reconciliation = reconcile_paper_account(database, runtime.account_id)
        if reconciliation["status"] != "RECONCILED":
            raise RuntimeError("Paper reconciliation failed")

        return {
            "mode": "RESEARCH_ONLY",
            "broker": "NONE",
            "live_orders": False,
            "quotes_processed": accepted,
            "completed_quote_sequence": completed + accepted,
            "scheduler_events": scheduled,
            "hold_decisions": holds,
            "recovered_hold_decisions": recovered_holds,
            "reconciliation": reconciliation["status"],
            "database": str(database),
        }
    finally:
        if previous_calendar is None:
            os.environ.pop("AEGIS_CALENDAR_CONFIG_PATH", None)
        else:
            os.environ["AEGIS_CALENDAR_CONFIG_PATH"] = previous_calendar


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run_batch(args.root, args.count), indent=2))


if __name__ == "__main__":
    main()
