"""Offline multi-instrument historical collection planning.

Planning does not verify historical constituents, data-use permission,
calendar quality, or ML training eligibility. No network or storage.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date

from .upstox_collection import UpstoxCollectionRequest

_KEY = re.compile(r"^NSE_EQ[|]IN[A-Z0-9]{10}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._&-]{0,24}$")


@dataclass(frozen=True, slots=True)
class UniverseMember:
    symbol: str
    instrument_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or _SYMBOL.fullmatch(self.symbol) is None:
            raise ValueError("invalid universe trading symbol")
        if not isinstance(self.instrument_key, str) or _KEY.fullmatch(self.instrument_key) is None:
            raise ValueError("invalid NSE equity instrument key")


def plan_universe_collection(
    *,
    members: tuple[UniverseMember, ...],
    interval: str,
    from_date: date,
    to_date: date,
    universe_observed_on: date,
    universe_source_reference: str,
    max_total_windows: int = 1000,
    max_windows_per_instrument: int = 20,
) -> dict[str, object]:
    """Return a deterministic, strictly bounded offline collection plan."""
    if not isinstance(members, tuple) or not 1 <= len(members) <= 200:
        raise ValueError("universe must contain 1 to 200 members")
    if not all(isinstance(member, UniverseMember) for member in members):
        raise TypeError("universe members must be UniverseMember instances")
    if type(universe_observed_on) is not date:
        raise TypeError("universe observation date must be a date")
    if (
        not isinstance(universe_source_reference, str)
        or not 1 <= len(universe_source_reference.strip()) <= 256
        or any(ord(char) < 32 or ord(char) == 127 for char in universe_source_reference)
    ):
        raise ValueError("invalid universe source reference")
    if type(max_total_windows) is not int or not 1 <= max_total_windows <= 1000:
        raise ValueError("maximum total requests must be between 1 and 1000")
    if type(max_windows_per_instrument) is not int or not 1 <= max_windows_per_instrument <= 20:
        raise ValueError("per-instrument request limit must be between 1 and 20")

    keys = [member.instrument_key for member in members]
    symbols = [member.symbol for member in members]
    if len(set(keys)) != len(keys) or len(set(symbols)) != len(symbols):
        raise ValueError("duplicate universe instrument or trading symbol")

    entries: list[dict[str, object]] = []
    total = 0

    # Sort independently of caller ordering for reproducible plans.
    for member in sorted(members, key=lambda item: item.instrument_key):
        request = UpstoxCollectionRequest(
            instrument_key=member.instrument_key,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            max_windows=max_windows_per_instrument,
        )
        windows = request.windows()
        total += len(windows)
        if total > max_total_windows:
            raise ValueError("universe collection exceeds total request budget")

        entries.append(
            {
                "symbol": member.symbol,
                "instrument_key": member.instrument_key,
                "planned_requests": len(windows),
                "windows": [
                    {
                        "from_date": window.from_date.isoformat(),
                        "to_date": window.to_date.isoformat(),
                    }
                    for window in windows
                ],
            }
        )

    plan: dict[str, object] = {
        "schema_version": 1,
        "mode": "RESEARCH_ONLY",
        "provider_id": "upstox",
        "network_enabled": False,
        "storage_enabled": False,
        "interval": interval,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "universe_observed_on": universe_observed_on.isoformat(),
        "universe_source_reference": universe_source_reference,
        "universe_membership_verified": False,
        "instrument_identity_verified": False,
        "data_use_permission_verified": False,
        "source_use_status": "UNVERIFIED",
        "calendar_quality_status": "UNASSESSED",
        "ml_training_approved": False,
        "instrument_count": len(entries),
        "planned_requests": total,
        "max_total_windows": max_total_windows,
        "max_windows_per_instrument": max_windows_per_instrument,
        "instruments": entries,
    }

    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )

    plan["plan_sha256"] = hashlib.sha256(canonical).hexdigest()
    return plan
