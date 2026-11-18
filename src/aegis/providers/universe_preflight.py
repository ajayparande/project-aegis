"""Read-only global preflight for a validated research collection plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .collection import CollectionWindow
from .collection_store import HistoricalCollectionStore
from .universe_runner import validate_collection_plan


@dataclass(frozen=True, slots=True)
class UniversePreflight:
    instrument_count: int
    planned_windows: int
    reused_windows: int
    missing_windows: int
    stored_candles: int
    network_enabled: bool = False
    storage_enabled: bool = False


def preflight_universe_collection(
    *,
    plan_path: Path,
    instrument_master_path: Path,
    membership_path: Path,
    storage_root: Path,
) -> UniversePreflight:
    """Verify all existing windows before any separate execution stage.

    This function never creates files, fetches data, or grants permission.
    Any corrupt or incomplete stored window raises immediately.
    """
    plan = validate_collection_plan(
        plan_path=plan_path,
        instrument_master_path=instrument_master_path,
        membership_path=membership_path,
    )

    store = HistoricalCollectionStore(storage_root)

    reused = 0
    missing = 0
    candles = 0
    seen: set[tuple[str, str, str, str]] = set()

    for instrument in plan["instruments"]:
        key = instrument["instrument_key"]
        windows = instrument["windows"]

        if len(windows) != instrument["planned_requests"]:
            raise ValueError("instrument request count mismatch")

        for item in windows:
            start = date.fromisoformat(item["from_date"])
            end = date.fromisoformat(item["to_date"])

            if start.isoformat() != item["from_date"] or end.isoformat() != item["to_date"]:
                raise ValueError("noncanonical collection window")

            identity = (
                key,
                plan["interval"],
                item["from_date"],
                item["to_date"],
            )
            if identity in seen:
                raise ValueError("duplicate collection window")
            seen.add(identity)

            window = CollectionWindow(
                from_date=start,
                to_date=end,
            )

            existing = store.load_verified(
                provider_id="upstox",
                instrument_key=key,
                interval=plan["interval"],
                window=window,
            )

            if existing is None:
                missing += 1
            else:
                reused += 1
                candles += len(existing.candles)

    planned = plan["planned_requests"]
    if len(seen) != planned or reused + missing != planned:
        raise ValueError("global collection count mismatch")

    return UniversePreflight(
        instrument_count=plan["instrument_count"],
        planned_windows=planned,
        reused_windows=reused,
        missing_windows=missing,
        stored_candles=candles,
    )
