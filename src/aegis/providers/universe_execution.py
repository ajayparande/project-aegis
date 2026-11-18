"""Bounded synthetic-only universe collection; no broker or HTTP integration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .collection_store import HistoricalCollectionStore, collect_historical_batches
from .contracts import HistoricalCandle, HistoricalCandleBatch
from .universe_preflight import preflight_universe_collection
from .universe_runner import validate_collection_plan

_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class UniverseExecutionProgress:
    planned_windows: int
    fetched_windows: int
    reused_windows: int
    pending_windows: int
    processed_instruments: int
    total_instruments: int


class _BudgetReached(Exception):
    """Private, expected stop before another synthetic batch."""


def _synthetic_batch(
    *,
    instrument_key: str,
    interval: str,
    from_date: date,
    to_date: date,
) -> HistoricalCandleBatch:
    """Create synthetic data only; never read external market data."""
    if interval != "1d" or from_date > to_date:
        raise ValueError("unsupported synthetic window")

    identity = (
        f"SYNTHETIC_ONLY|{instrument_key}|{interval}|{from_date.isoformat()}|{to_date.isoformat()}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    price = Decimal(100 + int(digest[:4], 16) % 100)
    timestamp = datetime(
        from_date.year,
        from_date.month,
        from_date.day,
        9,
        15,
        tzinfo=_IST,
    )

    return HistoricalCandleBatch(
        provider_id="upstox",
        provider_instrument_key=instrument_key,
        interval=interval,
        candles=(
            HistoricalCandle(
                timestamp=timestamp,
                open=price,
                high=price + Decimal(1),
                low=price - Decimal(1),
                close=price + Decimal("0.50"),
                volume=10,
            ),
        ),
        fetched_at=datetime.now(UTC),
        response_sha256=digest,
    )


def execute_synthetic_universe(
    *,
    plan_path: Path,
    instrument_master_path: Path,
    membership_path: Path,
    storage_root: Path,
    max_new_windows: int = 1000,
) -> UniverseExecutionProgress:
    """Collect synthetic-only batches with global preflight and a strict budget.

    This writes synthetic candles to storage_root. Use isolated test storage,
    never a real historical-data collection directory.
    """
    if type(max_new_windows) is not int or not 0 <= max_new_windows <= 1000:
        raise ValueError("invalid synthetic request budget")

    plan = validate_collection_plan(
        plan_path=plan_path,
        instrument_master_path=instrument_master_path,
        membership_path=membership_path,
    )
    before = preflight_universe_collection(
        plan_path=plan_path,
        instrument_master_path=instrument_master_path,
        membership_path=membership_path,
        storage_root=storage_root,
    )
    if before.planned_windows != plan["planned_requests"]:
        raise ValueError("global preflight mismatch")

    store = HistoricalCollectionStore(storage_root)
    remaining = max_new_windows
    processed = 0

    def bounded_fetch(
        *,
        instrument_key: str,
        interval: str,
        from_date: date,
        to_date: date,
    ) -> HistoricalCandleBatch:
        nonlocal remaining
        if remaining == 0:
            raise _BudgetReached
        batch = _synthetic_batch(
            instrument_key=instrument_key,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )
        remaining -= 1
        return batch

    for instrument in plan["instruments"]:
        try:
            progress = collect_historical_batches(
                store=store,
                fetch=bounded_fetch,
                provider_id="upstox",
                instrument_key=instrument["instrument_key"],
                interval=plan["interval"],
                from_date=date.fromisoformat(plan["from_date"]),
                to_date=date.fromisoformat(plan["to_date"]),
                max_windows=plan["max_windows_per_instrument"],
            )
        except _BudgetReached:
            break

        if progress.planned_windows != instrument["planned_requests"]:
            raise ValueError("instrument window count mismatch")
        processed += 1

    after = preflight_universe_collection(
        plan_path=plan_path,
        instrument_master_path=instrument_master_path,
        membership_path=membership_path,
        storage_root=storage_root,
    )

    newly_stored = after.reused_windows - before.reused_windows
    if not 0 <= newly_stored <= max_new_windows:
        raise ValueError("synthetic collection budget invariant violated")
    if after.planned_windows != before.planned_windows:
        raise ValueError("planned window count changed")

    return UniverseExecutionProgress(
        planned_windows=after.planned_windows,
        fetched_windows=newly_stored,
        reused_windows=before.reused_windows,
        pending_windows=after.missing_windows,
        processed_instruments=processed,
        total_instruments=plan["instrument_count"],
    )
