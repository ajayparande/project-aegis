"""Disk-backed synthetic multi-instrument collection integration tests."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis.providers.collection_store import (
    HistoricalCollectionIntegrityError,
    HistoricalCollectionStore,
    collect_historical_batches,
)
from aegis.providers.contracts import HistoricalCandle, HistoricalCandleBatch
from aegis.providers.universe_manifest import plan_from_universe_manifests
from aegis.providers.universe_preflight import preflight_universe_collection
from aegis.providers.universe_runner import validate_collection_plan

_MEMBERS = (
    ("RELIANCE", "NSE_EQ|INE002A01018"),
    ("TCS", "NSE_EQ|INE467B01029"),
)
_IST = ZoneInfo("Asia/Kolkata")


def _setup(tmp_path: Path):
    master = tmp_path / "master.json"
    membership = tmp_path / "membership.json"
    plan_path = tmp_path / "plan.json"
    storage = tmp_path / "collection"

    master.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "instrument_master",
                "observed_on": "2026-11-18",
                "source_reference": "synthetic-master",
                "members": [{"symbol": symbol, "instrument_key": key} for symbol, key in _MEMBERS],
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
                "members": [{"symbol": symbol} for symbol, _ in _MEMBERS],
            }
        ),
        encoding="utf-8",
    )
    plan = plan_from_universe_manifests(
        instrument_master_path=master,
        membership_path=membership,
        interval="1d",
        from_date=date(2024, 1, 1),
        to_date=date(2025, 12, 31),
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return master, membership, plan_path, storage


def _batch(*, instrument_key, interval, from_date, to_date):
    assert interval == "1d"
    assert from_date <= to_date
    return HistoricalCandleBatch(
        provider_id="upstox",
        provider_instrument_key=instrument_key,
        interval=interval,
        candles=(
            HistoricalCandle(
                timestamp=datetime(
                    from_date.year,
                    from_date.month,
                    from_date.day,
                    9,
                    15,
                    tzinfo=_IST,
                ),
                open=Decimal("100.00"),
                high=Decimal("101.00"),
                low=Decimal("99.00"),
                close=Decimal("100.50"),
                volume=10,
            ),
        ),
        fetched_at=datetime(2026, 11, 20, tzinfo=UTC),
        response_sha256="a" * 64,
    )


def _run(inputs, fetch):
    master, membership, plan_path, storage = inputs

    # Global preflight must complete before the first fetch callback.
    before = preflight_universe_collection(
        plan_path=plan_path,
        instrument_master_path=master,
        membership_path=membership,
        storage_root=storage,
    )
    plan = validate_collection_plan(
        plan_path=plan_path,
        instrument_master_path=master,
        membership_path=membership,
    )
    store = HistoricalCollectionStore(storage)

    fetched = reused = 0

    for instrument in plan["instruments"]:
        progress = collect_historical_batches(
            store=store,
            fetch=fetch,
            provider_id="upstox",
            instrument_key=instrument["instrument_key"],
            interval=plan["interval"],
            from_date=date.fromisoformat(plan["from_date"]),
            to_date=date.fromisoformat(plan["to_date"]),
            max_windows=plan["max_windows_per_instrument"],
        )
        assert progress.planned_windows == instrument["planned_requests"]
        fetched += progress.fetched_windows
        reused += progress.reused_windows

    assert fetched + reused == plan["planned_requests"]
    return before, fetched, reused


def test_disk_persistence_and_process_style_restart(tmp_path):
    inputs = _setup(tmp_path)
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        return _batch(**kwargs)

    before, fetched, reused = _run(inputs, fetch)
    assert before.missing_windows == 4
    assert (fetched, reused) == (4, 0)
    assert len(calls) == 4

    data_files = list(inputs[3].rglob("*.candles.json"))
    manifests = list(inputs[3].rglob("*.manifest.json"))
    assert len(data_files) == len(manifests) == 4

    # Construct fresh store objects, as a new process would.
    before, fetched, reused = _run(
        inputs,
        lambda **kwargs: pytest.fail("restart must reuse disk data"),
    )
    assert before.reused_windows == 4
    assert before.missing_windows == 0
    assert (fetched, reused) == (0, 4)
    assert len(calls) == 4


def test_interrupted_collection_resumes_without_duplicates(tmp_path):
    inputs = _setup(tmp_path)
    calls = []

    def interrupted_fetch(**kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            raise RuntimeError("synthetic interruption")
        return _batch(**kwargs)

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        _run(inputs, interrupted_fetch)

    assert len(list(inputs[3].rglob("*.manifest.json"))) == 2

    resumed_calls = []

    def resumed_fetch(**kwargs):
        resumed_calls.append(kwargs)
        return _batch(**kwargs)

    before, fetched, reused = _run(inputs, resumed_fetch)
    assert before.reused_windows == 2
    assert before.missing_windows == 2
    assert (fetched, reused) == (2, 2)
    assert len(resumed_calls) == 2
    assert len(list(inputs[3].rglob("*.manifest.json"))) == 4


def test_later_instrument_corruption_blocks_earlier_fetch(tmp_path):
    inputs = _setup(tmp_path)
    _run(inputs, lambda **kwargs: _batch(**kwargs))

    master, membership, plan_path, storage = inputs
    plan = validate_collection_plan(
        plan_path=plan_path,
        instrument_master_path=master,
        membership_path=membership,
    )
    store = HistoricalCollectionStore(storage)

    first = plan["instruments"][0]
    last = plan["instruments"][-1]

    def paths_for(instrument, window_index):
        from aegis.providers.collection import CollectionWindow

        window_data = instrument["windows"][window_index]
        window = CollectionWindow(
            date.fromisoformat(window_data["from_date"]),
            date.fromisoformat(window_data["to_date"]),
        )
        return store._paths("upstox", instrument["instrument_key"], "1d", window)

    first_data, first_manifest = paths_for(first, 0)
    first_data.unlink()
    first_manifest.unlink()

    last_data, _ = paths_for(last, -1)
    last_data.write_bytes(last_data.read_bytes() + b"corrupt")

    with pytest.raises(HistoricalCollectionIntegrityError):
        _run(
            inputs,
            lambda **kwargs: pytest.fail("global corruption must block all fetching"),
        )

    assert not first_data.exists()
    assert not first_manifest.exists()


def test_incomplete_manifest_pair_blocks_collection(tmp_path):
    inputs = _setup(tmp_path)
    _run(inputs, lambda **kwargs: _batch(**kwargs))

    manifest = next(inputs[3].rglob("*.manifest.json"))
    manifest.unlink()

    with pytest.raises(
        HistoricalCollectionIntegrityError,
        match="incomplete",
    ):
        _run(
            inputs,
            lambda **kwargs: pytest.fail("incomplete storage must block fetching"),
        )


def test_research_manifests_remain_unverified(tmp_path):
    inputs = _setup(tmp_path)
    _run(inputs, lambda **kwargs: _batch(**kwargs))

    for path in inputs[3].rglob("*.manifest.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["source_use_status"] == "UNVERIFIED"
        assert document["calendar_quality_status"] == "UNASSESSED"


def test_synthetic_coordinator_budget_and_restart(tmp_path):
    from aegis.providers.universe_execution import execute_synthetic_universe

    master, membership, plan_path, storage = _setup(tmp_path)
    args = {
        "plan_path": plan_path,
        "instrument_master_path": master,
        "membership_path": membership,
        "storage_root": storage,
    }

    zero = execute_synthetic_universe(**args, max_new_windows=0)
    assert (zero.fetched_windows, zero.reused_windows) == (0, 0)
    assert zero.pending_windows == 4
    assert not list(storage.rglob("*.candles.json"))

    first = execute_synthetic_universe(**args, max_new_windows=1)
    assert (first.fetched_windows, first.reused_windows) == (1, 0)
    assert first.pending_windows == 3

    second = execute_synthetic_universe(**args, max_new_windows=2)
    assert (second.fetched_windows, second.reused_windows) == (2, 1)
    assert second.pending_windows == 1

    third = execute_synthetic_universe(**args, max_new_windows=250)
    assert (third.fetched_windows, third.reused_windows) == (1, 3)
    assert third.pending_windows == 0

    fourth = execute_synthetic_universe(**args, max_new_windows=250)
    assert (fourth.fetched_windows, fourth.reused_windows) == (0, 4)
    assert fourth.pending_windows == 0
    assert len(list(storage.rglob("*.manifest.json"))) == 4


def test_synthetic_coordinator_rejects_invalid_budgets(tmp_path):
    from aegis.providers.universe_execution import execute_synthetic_universe

    master, membership, plan_path, storage = _setup(tmp_path)
    for budget in (True, -1, 1001, 1.5):
        with pytest.raises(ValueError, match="budget"):
            execute_synthetic_universe(
                plan_path=plan_path,
                instrument_master_path=master,
                membership_path=membership,
                storage_root=storage,
                max_new_windows=budget,
            )
    assert not storage.exists()


def test_synthetic_coordinator_later_corruption_blocks_all(tmp_path):
    from aegis.providers.collection import CollectionWindow
    from aegis.providers.universe_execution import execute_synthetic_universe

    master, membership, plan_path, storage = _setup(tmp_path)
    args = {
        "plan_path": plan_path,
        "instrument_master_path": master,
        "membership_path": membership,
        "storage_root": storage,
    }
    initial = execute_synthetic_universe(**args, max_new_windows=250)
    assert initial.pending_windows == 0

    plan = validate_collection_plan(
        plan_path=plan_path,
        instrument_master_path=master,
        membership_path=membership,
    )
    store = HistoricalCollectionStore(storage)

    def paths_for(instrument, window_index):
        item = instrument["windows"][window_index]
        window = CollectionWindow(
            date.fromisoformat(item["from_date"]),
            date.fromisoformat(item["to_date"]),
        )
        return store._paths("upstox", instrument["instrument_key"], "1d", window)

    first_data, first_manifest = paths_for(plan["instruments"][0], 0)
    last_data, _ = paths_for(plan["instruments"][-1], -1)
    first_data.unlink()
    first_manifest.unlink()
    last_data.write_bytes(last_data.read_bytes() + b"corrupted")

    with pytest.raises(HistoricalCollectionIntegrityError):
        execute_synthetic_universe(**args, max_new_windows=250)

    assert not first_data.exists()
    assert not first_manifest.exists()


def test_synthetic_coordinator_no_injected_fetcher(tmp_path):
    from aegis.providers.universe_execution import execute_synthetic_universe

    master, membership, plan_path, storage = _setup(tmp_path)
    with pytest.raises(TypeError, match="synthetic_fetch"):
        execute_synthetic_universe(
            plan_path=plan_path,
            instrument_master_path=master,
            membership_path=membership,
            storage_root=storage,
            synthetic_fetch=lambda **kwargs: pytest.fail("must not run"),
        )
    assert not storage.exists()
