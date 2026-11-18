"""Offline regression tests for dated universe ingestion."""

import json
from datetime import date
from pathlib import Path

import pytest

from aegis.providers.universe_manifest import (
    UniverseManifestError,
    plan_from_universe_manifests,
)


def _documents(tmp_path: Path):
    master = tmp_path / "master.json"
    universe = tmp_path / "universe.json"
    master.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "instrument_master",
                "observed_on": "2026-11-18",
                "source_reference": "synthetic-master",
                "members": [
                    {"symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018"},
                    {"symbol": "TCS", "instrument_key": "NSE_EQ|INE467B01029"},
                ],
            }
        ),
        encoding="utf-8",
    )
    universe.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "universe_membership",
                "observed_on": "2026-11-18",
                "source_reference": "synthetic-membership",
                "members": [{"symbol": "TCS"}, {"symbol": "RELIANCE"}],
            }
        ),
        encoding="utf-8",
    )
    return master, universe


def _plan(tmp_path, **changes):
    master, universe = _documents(tmp_path)
    args = {
        "instrument_master_path": master,
        "membership_path": universe,
        "interval": "1d",
        "from_date": date(2025, 1, 1),
        "to_date": date(2025, 12, 31),
    }
    args.update(changes)
    return plan_from_universe_manifests(**args)


def test_joined_plan_remains_unverified(tmp_path):
    plan = _plan(tmp_path)
    assert plan["instrument_count"] == 2
    assert plan["planned_requests"] == 2
    assert plan["network_enabled"] is False
    assert plan["storage_enabled"] is False
    assert plan["ml_training_approved"] is False
    assert plan["manifest_inputs_verified"] is False
    assert plan["universe_membership_verified"] is False
    assert plan["instrument_identity_verified"] is False
    assert plan["source_use_status"] == "UNVERIFIED"
    assert len(plan["plan_sha256"]) == 64


def test_missing_membership_symbol_fails(tmp_path):
    master, universe = _documents(tmp_path)
    document = json.loads(universe.read_text(encoding="utf-8"))
    document["members"] = [{"symbol": "UNKNOWN"}]
    universe.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(UniverseManifestError, match="unresolved"):
        plan_from_universe_manifests(
            instrument_master_path=master,
            membership_path=universe,
            interval="1d",
            from_date=date(2025, 1, 1),
            to_date=date(2025, 12, 31),
        )


def test_duplicate_master_symbols_fail(tmp_path):
    master, universe = _documents(tmp_path)
    document = json.loads(master.read_text(encoding="utf-8"))
    document["members"][1]["symbol"] = "RELIANCE"
    master.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(UniverseManifestError, match="duplicate"):
        plan_from_universe_manifests(
            instrument_master_path=master,
            membership_path=universe,
            interval="1d",
            from_date=date(2025, 1, 1),
            to_date=date(2025, 12, 31),
        )


def test_invalid_json_fails(tmp_path):
    master, universe = _documents(tmp_path)
    master.write_text('{"kind":"a","kind":"b"}', encoding="utf-8")
    with pytest.raises(UniverseManifestError):
        plan_from_universe_manifests(
            instrument_master_path=master,
            membership_path=universe,
            interval="1d",
            from_date=date(2025, 1, 1),
            to_date=date(2025, 12, 31),
        )


def test_missing_file_fails(tmp_path):
    master, universe = _documents(tmp_path)
    with pytest.raises(UniverseManifestError):
        plan_from_universe_manifests(
            instrument_master_path=master.with_name("missing.json"),
            membership_path=universe,
            interval="1d",
            from_date=date(2025, 1, 1),
            to_date=date(2025, 12, 31),
        )


def test_request_budget_enforced(tmp_path):
    with pytest.raises(ValueError, match="total request budget"):
        _plan(
            tmp_path,
            interval="1m",
            from_date=date(2026, 11, 1),
            to_date=date(2026, 11, 18),
            max_total_windows=5,
        )


def test_unverified_membership_not_historical_proof(tmp_path):
    plan = _plan(tmp_path)
    assert plan["universe_observed_on"] == "2026-11-18"
    assert plan["universe_membership_verified"] is False
    assert plan["instrument_master_observed_on"] == "2026-11-18"


def test_deep_json_fails_closed(tmp_path):
    master, universe = _documents(tmp_path)
    master.write_bytes(b"[" * 1500 + b"0" + b"]" * 1500)
    with pytest.raises(UniverseManifestError):
        plan_from_universe_manifests(
            instrument_master_path=master,
            membership_path=universe,
            interval="1d",
            from_date=date(2025, 1, 1),
            to_date=date(2025, 12, 31),
        )
