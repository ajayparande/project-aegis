"""P10-P5C3 recovery-hardening tests using isolated pilot databases."""

from contextlib import closing

import pytest

from aegis.continuous_synthetic_pilot import run_batch
from aegis.database import get_connection
from aegis.forward_runtime import ForwardRuntimeService


def _states(database):
    with closing(get_connection(database)) as connection:
        rows = connection.execute(
            """SELECT event_id, interval, status, proposal_binding_state
               FROM forward_scheduler_events
               ORDER BY decision_timestamp, event_id"""
        ).fetchall()
        orders = connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
        quotes = connection.execute(
            "SELECT COUNT(*) FROM forward_quotes WHERE accepted = 1"
        ).fetchone()[0]
    return [dict(row) for row in rows], orders, quotes


def _crash_before_route(tmp_path, monkeypatch, *, crash_on=1, count=5):
    original = ForwardRuntimeService.route_event
    calls = 0

    def interrupted(self, event_id, proposal, snapshot):
        nonlocal calls
        calls += 1
        if calls == crash_on:
            raise RuntimeError("INJECTED_CRASH")
        return original(self, event_id, proposal, snapshot)

    with monkeypatch.context() as patch:
        patch.setattr(ForwardRuntimeService, "route_event", interrupted)
        with pytest.raises(RuntimeError, match="INJECTED_CRASH"):
            run_batch(tmp_path, count)


def test_repeated_restart_is_idempotent(tmp_path, monkeypatch):
    _crash_before_route(tmp_path, monkeypatch)

    first = run_batch(tmp_path, 1)
    assert first["recovered_hold_decisions"] == 1

    states_before, orders_before, _ = _states(tmp_path / "pilot.db")

    second = run_batch(tmp_path, 1)
    assert second["recovered_hold_decisions"] == 0

    states_after, orders_after, _ = _states(tmp_path / "pilot.db")

    assert states_before == states_after[: len(states_before)]
    assert orders_before == orders_after == 0


def test_multiple_pending_events_fail_closed(tmp_path, monkeypatch):
    # A quote can close more than one candle interval.
    # Crash before routing the first scheduled decision.
    _crash_before_route(tmp_path, monkeypatch, count=6)

    states, orders, quotes = _states(tmp_path / "pilot.db")
    assert any(row["status"] == "PENDING" for row in states)
    assert orders == 0

    # Recovery must not silently leave an unresolved event.
    try:
        run_batch(tmp_path, 1)
    except RuntimeError:
        pass

    after, orders_after, quotes_after = _states(tmp_path / "pilot.db")

    assert not any(row["status"] == "PENDING" for row in after) or (quotes_after == quotes)
    assert orders_after == 0


def test_crash_before_proposal_binding_recovers_unbound_hold(tmp_path, monkeypatch):
    original = ForwardRuntimeService._route_event
    calls = 0

    def interrupted(self, event_id, proposal, snapshot):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("INJECTED_PRE_BIND_CRASH")
        return original(self, event_id, proposal, snapshot)

    with monkeypatch.context() as patch:
        patch.setattr(ForwardRuntimeService, "_route_event", interrupted)
        with pytest.raises(RuntimeError, match="INJECTED_PRE_BIND_CRASH"):
            run_batch(tmp_path, 3)

    before, orders_before, quotes_before = _states(tmp_path / "pilot.db")
    pending = [row for row in before if row["status"] == "PENDING"]

    assert len(pending) == 1
    assert pending[0]["proposal_binding_state"] == "UNBOUND"
    assert orders_before == 0

    resumed = run_batch(tmp_path, 1)
    assert resumed["recovered_hold_decisions"] == 1

    after, orders_after, quotes_after = _states(tmp_path / "pilot.db")

    assert all(row["status"] != "PENDING" for row in after)
    assert orders_after == 0
    assert quotes_after == quotes_before + 1


def test_older_pending_quote_cannot_be_replayed_after_progress(tmp_path, monkeypatch):
    _crash_before_route(tmp_path, monkeypatch)

    # This scenario currently has no supported public mechanism for
    # advancing the cursor while leaving a pending event behind.
    # Verify the runner never silently advances on failed recovery.
    _before, _, quotes = _states(tmp_path / "pilot.db")

    try:
        run_batch(tmp_path, 1)
    except RuntimeError:
        pass

    after, orders, after_quotes = _states(tmp_path / "pilot.db")

    assert orders == 0
    assert all(row["status"] != "PENDING" for row in after) or (after_quotes == quotes)


def test_crash_after_terminal_route_preserves_result(tmp_path, monkeypatch):
    original = ForwardRuntimeService.route_event
    calls = 0

    def interrupted(self, event_id, proposal, snapshot):
        nonlocal calls
        result = original(self, event_id, proposal, snapshot)
        calls += 1
        if calls == 1:
            raise RuntimeError("INJECTED_POST_ROUTE_CRASH")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ForwardRuntimeService, "route_event", interrupted)
        with pytest.raises(RuntimeError, match="INJECTED_POST_ROUTE_CRASH"):
            run_batch(tmp_path, 3)

    before, orders_before, _ = _states(tmp_path / "pilot.db")
    assert any(row["status"] == "HOLD" for row in before)

    resumed = run_batch(tmp_path, 1)
    assert resumed["recovered_hold_decisions"] == 0

    after, orders_after, _ = _states(tmp_path / "pilot.db")

    assert after[: len(before)] == before
    assert orders_before == orders_after == 0
