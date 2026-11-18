"""Offline-only collection coordinator tests."""

import json
from datetime import date

import pytest

from aegis.providers.universe_manifest import plan_from_universe_manifests
from aegis.providers.universe_runner import (
    summarize_dry_run,
    validate_collection_plan,
)


def _fixture(tmp_path):
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
                    {"symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018"},
                    {"symbol": "TCS", "instrument_key": "NSE_EQ|INE467B01029"},
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
                "members": [{"symbol": "RELIANCE"}, {"symbol": "TCS"}],
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


def test_verified_offline_dry_run(tmp_path):
    master, membership, path = _fixture(tmp_path)
    plan = validate_collection_plan(
        plan_path=path,
        instrument_master_path=master,
        membership_path=membership,
    )
    summary = summarize_dry_run(plan)
    assert summary["instrument_count"] == 2
    assert summary["planned_requests"] == 2
    assert summary["completed_requests"] == 0
    assert summary["network_enabled"] is False
    assert summary["storage_enabled"] is False


def test_modified_plan_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["network_enabled"] = True
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_collection_plan(
            plan_path=path,
            instrument_master_path=master,
            membership_path=membership,
        )


def test_modified_window_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["instruments"][0]["windows"][0]["to_date"] = "2025-01-02"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_collection_plan(
            plan_path=path,
            instrument_master_path=master,
            membership_path=membership,
        )


def test_duplicate_json_keys_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        validate_collection_plan(
            plan_path=path,
            instrument_master_path=master,
            membership_path=membership,
        )


def test_master_change_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)
    document = json.loads(master.read_text(encoding="utf-8"))
    document["members"][0]["instrument_key"] = "NSE_EQ|INE101A01026"
    master.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        validate_collection_plan(
            plan_path=path,
            instrument_master_path=master,
            membership_path=membership,
        )


def _rewrite_plan_with_valid_hash(path, mutate):
    import hashlib

    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)

    canonical = json.dumps(
        {key: value for key, value in document.items() if key != "plan_sha256"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    document["plan_sha256"] = hashlib.sha256(canonical).hexdigest()

    path.write_text(
        json.dumps(document),
        encoding="utf-8",
    )


def _assert_plan_rejected(master, membership, path):
    with pytest.raises((ValueError, TypeError)):
        validate_collection_plan(
            plan_path=path,
            instrument_master_path=master,
            membership_path=membership,
        )


def test_rehashed_window_tampering_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)

    def mutate(document):
        document["instruments"][0]["windows"][0]["to_date"] = "2025-01-02"

    _rewrite_plan_with_valid_hash(path, mutate)
    _assert_plan_rejected(master, membership, path)


def test_rehashed_instrument_key_tampering_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)

    def mutate(document):
        document["instruments"][0]["instrument_key"] = "NSE_EQ|INE101A01026"

    _rewrite_plan_with_valid_hash(path, mutate)
    _assert_plan_rejected(master, membership, path)


def test_rehashed_authorization_tampering_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)

    def mutate(document):
        document["network_enabled"] = True

    _rewrite_plan_with_valid_hash(path, mutate)
    _assert_plan_rejected(master, membership, path)


def test_rehashed_request_budget_tampering_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)

    def mutate(document):
        document["max_total_windows"] = 1

    _rewrite_plan_with_valid_hash(path, mutate)
    _assert_plan_rejected(master, membership, path)


def test_boolean_request_count_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)

    def mutate(document):
        document["planned_requests"] = True

    _rewrite_plan_with_valid_hash(path, mutate)
    _assert_plan_rejected(master, membership, path)


def test_unexpected_plan_field_rejected(tmp_path):
    master, membership, path = _fixture(tmp_path)

    def mutate(document):
        document["authenticated_download_approved"] = True

    _rewrite_plan_with_valid_hash(path, mutate)
    _assert_plan_rejected(master, membership, path)
