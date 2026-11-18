"""Offline-only, fail-closed multi-instrument collection coordinator."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from datetime import date
from pathlib import Path
from typing import Any

from .universe_manifest import plan_from_universe_manifests

_MAX_PLAN_BYTES = 2_000_000


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate plan JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite plan JSON number")


def _safe_file(path: Path) -> None:
    for component in (path, *path.parents):
        metadata = component.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("unsafe plan path")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("plan must be a regular file")


def _load_plan(path: Path) -> dict[str, Any]:
    _safe_file(path)
    with path.open("rb") as handle:
        raw = handle.read(_MAX_PLAN_BYTES + 1)
    if len(raw) > _MAX_PLAN_BYTES:
        raise ValueError("plan exceeds size limit")
    try:
        plan = json.loads(
            raw,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("invalid plan JSON") from exc
    if not isinstance(plan, dict):
        raise TypeError("plan must be a JSON object")
    return plan


def validate_collection_plan(
    *,
    plan_path: Path,
    instrument_master_path: Path,
    membership_path: Path,
) -> dict[str, Any]:
    """Reconstruct the saved plan from manifests; reject any deviation."""
    plan = _load_plan(plan_path)

    if plan.get("schema_version") != 1:
        raise ValueError("unsupported plan version")

    if plan.get("mode") != "RESEARCH_ONLY":
        raise ValueError("research-only mode required")

    for field in (
        "network_enabled",
        "storage_enabled",
        "ml_training_approved",
        "instrument_identity_verified",
        "universe_membership_verified",
        "data_use_permission_verified",
        "manifest_inputs_verified",
    ):
        if plan.get(field) is not False:
            raise ValueError(f"unsafe or missing plan flag: {field}")

    if plan.get("source_use_status") != "UNVERIFIED":
        raise ValueError("unexpected source-use status")

    if plan.get("calendar_quality_status") != "UNASSESSED":
        raise ValueError("unexpected calendar status")

    if plan.get("interval") != "1d":
        raise ValueError("only daily research planning is supported")

    if type(plan.get("instrument_count")) is not int:
        raise ValueError("invalid instrument count")

    if type(plan.get("planned_requests")) is not int:
        raise ValueError("invalid request count")

    if type(plan.get("max_total_windows")) is not int:
        raise ValueError("invalid request budget")

    if type(plan.get("max_windows_per_instrument")) is not int:
        raise ValueError("invalid per-instrument budget")

    if not 1 <= plan["instrument_count"] <= 200:
        raise ValueError("invalid universe size")

    if not 1 <= plan["planned_requests"] <= 1000:
        raise ValueError("invalid request total")

    if not 1 <= plan["max_total_windows"] <= 1000:
        raise ValueError("invalid request budget")

    if not 1 <= plan["max_windows_per_instrument"] <= 20:
        raise ValueError("invalid per-instrument budget")

    fingerprint = plan.get("plan_sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("missing plan fingerprint")

    canonical = json.dumps(
        {key: value for key, value in plan.items() if key != "plan_sha256"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    if hashlib.sha256(canonical).hexdigest() != fingerprint:
        raise ValueError("plan fingerprint mismatch")

    try:
        start = date.fromisoformat(plan["from_date"])
        end = date.fromisoformat(plan["to_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid plan dates") from exc

    if start.isoformat() != plan["from_date"]:
        raise ValueError("noncanonical start date")
    if end.isoformat() != plan["to_date"]:
        raise ValueError("noncanonical end date")

    expected = plan_from_universe_manifests(
        instrument_master_path=instrument_master_path,
        membership_path=membership_path,
        interval="1d",
        from_date=start,
        to_date=end,
        max_total_windows=plan["max_total_windows"],
        max_windows_per_instrument=plan["max_windows_per_instrument"],
    )

    if plan != expected:
        raise ValueError("plan differs from reconstructed universe")

    return plan


def summarize_dry_run(plan: dict[str, Any]) -> dict[str, Any]:
    """Return bounded progress information; never perform collection."""
    entries = plan["instruments"]
    requests = sum(item["planned_requests"] for item in entries)

    if requests != plan["planned_requests"]:
        raise ValueError("request total mismatch")

    return {
        "mode": "RESEARCH_ONLY",
        "execution_mode": "OFFLINE_DRY_RUN",
        "network_enabled": False,
        "storage_enabled": False,
        "instrument_count": len(entries),
        "planned_requests": requests,
        "completed_requests": 0,
        "pending_requests": requests,
        "plan_sha256": plan["plan_sha256"],
        "source_use_status": "UNVERIFIED",
        "ml_training_approved": False,
        "instruments": [
            {
                "symbol": item["symbol"],
                "planned_requests": item["planned_requests"],
                "completed_requests": 0,
            }
            for item in entries
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aegis offline-only universe collection dry-run")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--master", required=True, type=Path)
    parser.add_argument("--membership", required=True, type=Path)
    args = parser.parse_args()

    plan = validate_collection_plan(
        plan_path=args.plan,
        instrument_master_path=args.master,
        membership_path=args.membership,
    )
    summary = summarize_dry_run(plan)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
