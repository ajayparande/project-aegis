"""Crash recovery regression for the continuous synthetic pilot."""

from contextlib import closing

import pytest

from aegis.continuous_synthetic_pilot import run_batch
from aegis.database import get_connection
from aegis.forward_runtime import ForwardRuntimeService


def test_restart_does_not_silently_skip_pending_decision(tmp_path, monkeypatch):
    original_route = ForwardRuntimeService.route_event
    calls = 0

    def crash_before_route(self, event_id, proposal, snapshot):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("INJECTED_CRASH_BEFORE_ROUTE")
        return original_route(self, event_id, proposal, snapshot)

    with monkeypatch.context() as patch:
        patch.setattr(
            ForwardRuntimeService,
            "route_event",
            crash_before_route,
        )
        with pytest.raises(
            RuntimeError,
            match="INJECTED_CRASH_BEFORE_ROUTE",
        ):
            run_batch(tmp_path, 3)

    database = tmp_path / "pilot.db"

    with closing(get_connection(database)) as connection:
        before = connection.execute(
            """SELECT event_id FROM forward_scheduler_events
               WHERE status = 'PENDING'"""
        ).fetchall()

    assert len(before) == 1
    pending_event_id = before[0]["event_id"]

    # Restart with no monkeypatch. The runner must not silently
    # advance beyond an unresolved persisted decision.
    resumed = run_batch(tmp_path, 1)
    assert resumed["recovered_hold_decisions"] == 1
    assert resumed["reconciliation"] == "RECONCILED"

    with closing(get_connection(database)) as connection:
        event = connection.execute(
            """SELECT status FROM forward_scheduler_events
               WHERE event_id = ?""",
            (pending_event_id,),
        ).fetchone()

        orders = connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]

    assert event is not None
    assert event["status"] == "HOLD"
    assert orders == 0
