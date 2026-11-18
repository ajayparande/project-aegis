"""Synthetic cross-instrument restart and corruption tests."""

import json
from datetime import date

import pytest

from aegis.providers.universe_manifest import plan_from_universe_manifests
from aegis.providers.universe_simulation import (
    SyntheticCollectionState,
    simulate_collection,
)


def _plan(tmp_path):
    master = tmp_path / "master.json"
    membership = tmp_path / "membership.json"

    members = [
        {"symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018"},
        {"symbol": "TCS", "instrument_key": "NSE_EQ|INE467B01029"},
    ]

    master.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "instrument_master",
                "observed_on": "2026-11-18",
                "source_reference": "synthetic-master",
                "members": members,
            }
        ),
        encoding="utf-8",
    )

    membership.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "universe_membership",
                "observed_on": "2026-11-18",
                "source_reference": "synthetic-membership",
                "members": [{"symbol": member["symbol"]} for member in members],
            }
        ),
        encoding="utf-8",
    )

    return plan_from_universe_manifests(
        instrument_master_path=master,
        membership_path=membership,
        interval="1d",
        from_date=date(2024, 1, 1),
        to_date=date(2025, 12, 31),
    )


def test_interruption_and_cross_instrument_restart(tmp_path):
    plan = _plan(tmp_path)
    state = SyntheticCollectionState()
    total = plan["planned_requests"]

    first = simulate_collection(plan=plan, state=state, max_new_windows=3)
    assert first.simulated == 3
    assert first.reused == 0
    assert first.pending == total - 3
    assert first.interrupted is True

    second = simulate_collection(plan=plan, state=state, max_new_windows=1000)
    assert second.reused == 3
    assert second.simulated == total - 3
    assert second.pending == 0
    assert second.interrupted is False

    third = simulate_collection(plan=plan, state=state, max_new_windows=1000)
    assert third.reused == total
    assert third.simulated == 0


def test_corrupted_later_instrument_blocks_all_progress(tmp_path):
    plan = _plan(tmp_path)
    state = SyntheticCollectionState()
    last = plan["instruments"][-1]
    window = last["windows"][-1]
    identity = (
        last["instrument_key"],
        window["from_date"],
        window["to_date"],
    )
    state.corrupted.add(identity)

    with pytest.raises(ValueError, match="corrupted"):
        simulate_collection(plan=plan, state=state, max_new_windows=1000)

    assert not state.completed


def test_zero_budget_does_not_advance(tmp_path):
    plan = _plan(tmp_path)
    state = SyntheticCollectionState()
    result = simulate_collection(plan=plan, state=state, max_new_windows=0)
    assert result.simulated == 0
    assert result.pending == plan["planned_requests"]
    assert not state.completed


def test_boolean_budget_rejected(tmp_path):
    with pytest.raises(ValueError, match="budget"):
        simulate_collection(
            plan=_plan(tmp_path),
            state=SyntheticCollectionState(),
            max_new_windows=True,
        )


def test_unknown_completed_window_rejected(tmp_path):
    state = SyntheticCollectionState(
        completed={("NSE_EQ|INE002A01018", "2000-01-01", "2000-01-02")}
    )
    with pytest.raises(ValueError, match="unknown"):
        simulate_collection(
            plan=_plan(tmp_path),
            state=state,
            max_new_windows=1,
        )
