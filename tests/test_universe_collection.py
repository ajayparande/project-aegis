"""Offline tests for deterministic multi-stock collection planning."""

from datetime import date

import pytest

from aegis.providers.universe_collection import (
    UniverseMember,
    plan_universe_collection,
)

RELIANCE = UniverseMember("RELIANCE", "NSE_EQ|INE002A01018")
TCS = UniverseMember("TCS", "NSE_EQ|INE467B01029")


def make_plan(**changes):
    values = {
        "members": (RELIANCE, TCS),
        "interval": "1d",
        "from_date": date(2025, 1, 1),
        "to_date": date(2025, 12, 31),
        "universe_observed_on": date(2026, 11, 18),
        "universe_source_reference": "offline-unverified-test-snapshot",
        "max_total_windows": 20,
    }
    values.update(changes)
    return plan_universe_collection(**values)


def test_multistock_daily_plan():
    plan = make_plan()
    assert plan["instrument_count"] == 2
    assert plan["planned_requests"] == 2
    assert plan["network_enabled"] is False
    assert plan["storage_enabled"] is False
    assert plan["ml_training_approved"] is False
    assert plan["data_use_permission_verified"] is False
    assert plan["universe_membership_verified"] is False
    assert plan["source_use_status"] == "UNVERIFIED"
    assert len(plan["plan_sha256"]) == 64


def test_plan_is_independent_of_member_order():
    assert make_plan()["plan_sha256"] == make_plan(members=(TCS, RELIANCE))["plan_sha256"]


def test_minute_windows_respect_global_budget():
    with pytest.raises(ValueError, match="total request budget"):
        make_plan(
            interval="1m",
            from_date=date(2026, 11, 1),
            to_date=date(2026, 11, 18),
            max_total_windows=5,
        )


def test_minute_windows_count():
    plan = make_plan(
        interval="1m",
        from_date=date(2026, 11, 1),
        to_date=date(2026, 11, 18),
    )
    assert plan["planned_requests"] == 6


def test_repeated_instrument_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        make_plan(members=(RELIANCE, RELIANCE))


def test_repeated_symbol_is_rejected():
    other = UniverseMember("RELIANCE", "NSE_EQ|INE467B01029")
    with pytest.raises(ValueError, match="duplicate"):
        make_plan(members=(RELIANCE, other))


@pytest.mark.parametrize("members", [(), [], (RELIANCE,) * 201])
def test_invalid_universe_size_or_type(members):
    with pytest.raises(ValueError):
        make_plan(members=members)


@pytest.mark.parametrize("limit", [0, -1, True, 1001])
def test_invalid_total_budget(limit):
    with pytest.raises(ValueError):
        make_plan(max_total_windows=limit)


def test_invalid_instrument_key():
    with pytest.raises(ValueError):
        UniverseMember("BAD", "INVALID")


def test_invalid_source_reference():
    with pytest.raises(ValueError):
        make_plan(universe_source_reference="")


def test_invalid_observation_date():
    with pytest.raises(TypeError):
        make_plan(universe_observed_on="2026-11-18")


def test_rejects_excessive_per_instrument_windows():
    with pytest.raises(ValueError):
        make_plan(
            interval="1m",
            from_date=date(2025, 1, 1),
            to_date=date(2025, 12, 31),
        )


def test_bounded_fifty_instrument_plan():
    # Synthetic keys test scaling; they do not assert these instruments exist.
    members = tuple(
        UniverseMember(
            symbol=f"TEST{i:03d}",
            instrument_key=f"NSE_EQ|IN{i:010d}",
        )
        for i in range(50)
    )
    plan = make_plan(
        members=members,
        max_total_windows=50,
    )
    assert plan["instrument_count"] == 50
    assert plan["planned_requests"] == 50
    assert plan["instrument_identity_verified"] is False
