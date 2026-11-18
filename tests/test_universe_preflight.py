"""Synthetic tests for the global read-only collection preflight."""

import json
from datetime import date

import pytest

from aegis.providers.universe_manifest import plan_from_universe_manifests
from aegis.providers.universe_preflight import preflight_universe_collection


def _inputs(tmp_path):
    master = tmp_path / "master.json"
    membership = tmp_path / "membership.json"
    plan_path = tmp_path / "plan.json"

    master.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "instrument_master",
                "observed_on": "2026-11-18",
                "source_reference": "synthetic-master",
                "members": [
                    {
                        "symbol": "RELIANCE",
                        "instrument_key": "NSE_EQ|INE002A01018",
                    },
                    {
                        "symbol": "TCS",
                        "instrument_key": "NSE_EQ|INE467B01029",
                    },
                ],
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
                "members": [
                    {"symbol": "RELIANCE"},
                    {"symbol": "TCS"},
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = plan_from_universe_manifests(
        instrument_master_path=master,
        membership_path=membership,
        interval="1d",
        from_date=date(2025, 1, 1),
        to_date=date(2025, 12, 31),
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return master, membership, plan_path


def _run(tmp_path):
    master, membership, plan = _inputs(tmp_path)
    return preflight_universe_collection(
        plan_path=plan,
        instrument_master_path=master,
        membership_path=membership,
        storage_root=tmp_path / "unused_store",
    )


def test_missing_windows_reported_without_writes(tmp_path):
    result = _run(tmp_path)
    assert result.instrument_count == 2
    assert result.planned_windows == 2
    assert result.missing_windows == 2
    assert result.reused_windows == 0
    assert result.stored_candles == 0
    assert result.network_enabled is False
    assert result.storage_enabled is False
    assert not (tmp_path / "unused_store").exists()


def test_tampered_plan_fails_before_storage(tmp_path):
    master, membership, plan = _inputs(tmp_path)
    document = json.loads(plan.read_text(encoding="utf-8"))
    document["instruments"][0]["windows"][0]["to_date"] = "2025-01-02"
    plan.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError):
        preflight_universe_collection(
            plan_path=plan,
            instrument_master_path=master,
            membership_path=membership,
            storage_root=tmp_path / "unused_store",
        )


def test_invalid_storage_root_rejected(tmp_path):
    master, membership, plan = _inputs(tmp_path)
    invalid_root = tmp_path / "file_instead_of_directory"
    invalid_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError):
        preflight_universe_collection(
            plan_path=plan,
            instrument_master_path=master,
            membership_path=membership,
            storage_root=invalid_root,
        )
