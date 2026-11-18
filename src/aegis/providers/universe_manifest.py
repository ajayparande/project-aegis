"""Strict offline instrument-master and universe-membership ingestion.

Neither file establishes provider authenticity, historical membership
accuracy, source-use permission, or ML eligibility.
"""

from __future__ import annotations

import json
import re
import stat
from datetime import date
from pathlib import Path
from typing import Any

from .universe_collection import (
    UniverseMember,
    plan_universe_collection,
)

_MAX_BYTES = 2_000_000
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._&-]{0,24}$")


class UniverseManifestError(ValueError):
    """A supplied universe or instrument-master document is invalid."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UniverseManifestError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise UniverseManifestError("nonfinite JSON number")


def _load(path: Path) -> dict[str, Any]:
    if not isinstance(path, Path):
        raise TypeError("manifest path must be a Path")

    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or (
            getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise UniverseManifestError("reparse point in manifest path")

    if not path.is_file():
        raise UniverseManifestError("manifest must be an existing regular file")

    try:
        with path.open("rb") as handle:
            payload = handle.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise UniverseManifestError("manifest exceeds size limit")
        document = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise UniverseManifestError("manifest is invalid or unreadable") from exc

    if not isinstance(document, dict):
        raise UniverseManifestError("manifest must be a JSON object")
    return document


def _header(document: dict[str, Any], kind: str) -> date:
    if set(document) != {"schema_version", "kind", "observed_on", "source_reference", "members"}:
        raise UniverseManifestError("unexpected manifest fields")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise UniverseManifestError("unsupported manifest version")
    if document["kind"] != kind:
        raise UniverseManifestError("incorrect manifest kind")

    reference = document["source_reference"]
    if (
        not isinstance(reference, str)
        or not 1 <= len(reference.strip()) <= 256
        or any(ord(char) < 32 or ord(char) == 127 for char in reference)
    ):
        raise UniverseManifestError("invalid source reference")

    observed = document["observed_on"]
    if not isinstance(observed, str):
        raise UniverseManifestError("invalid observation date")
    try:
        parsed = date.fromisoformat(observed)
    except ValueError as exc:
        raise UniverseManifestError("invalid observation date") from exc
    if parsed.isoformat() != observed:
        raise UniverseManifestError("noncanonical observation date")
    return parsed


def _rows(document: dict[str, Any], fields: set[str]) -> list[dict[str, str]]:
    rows = document["members"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise UniverseManifestError("manifest must contain 1 to 200 members")
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise UniverseManifestError("invalid member fields")
        if not all(isinstance(value, str) for value in row.values()):
            raise UniverseManifestError("member values must be strings")
        if _SYMBOL.fullmatch(row["symbol"]) is None:
            raise UniverseManifestError("invalid trading symbol")
    return rows


def plan_from_universe_manifests(
    *,
    instrument_master_path: Path,
    membership_path: Path,
    interval: str,
    from_date: date,
    to_date: date,
    max_total_windows: int = 1000,
    max_windows_per_instrument: int = 20,
) -> dict[str, object]:
    """Join bounded offline snapshots and produce an unverified collection plan."""
    master = _load(instrument_master_path)
    membership = _load(membership_path)

    master_observed = _header(master, "instrument_master")
    membership_observed = _header(membership, "universe_membership")

    master_rows = _rows(master, {"symbol", "instrument_key"})
    membership_rows = _rows(membership, {"symbol"})

    try:
        members = tuple(UniverseMember(row["symbol"], row["instrument_key"]) for row in master_rows)
    except ValueError as exc:
        raise UniverseManifestError("invalid instrument identity") from exc

    by_symbol: dict[str, UniverseMember] = {}
    seen_keys: set[str] = set()
    for member in members:
        if member.symbol in by_symbol or member.instrument_key in seen_keys:
            raise UniverseManifestError("duplicate master identity")
        by_symbol[member.symbol] = member
        seen_keys.add(member.instrument_key)

    selected: list[UniverseMember] = []
    used_symbols: set[str] = set()
    for row in membership_rows:
        symbol = row["symbol"]
        if symbol in used_symbols:
            raise UniverseManifestError("duplicate universe membership")
        if symbol not in by_symbol:
            raise UniverseManifestError("unresolved universe symbol")
        selected.append(by_symbol[symbol])
        used_symbols.add(symbol)

    plan = plan_universe_collection(
        members=tuple(selected),
        interval=interval,
        from_date=from_date,
        to_date=to_date,
        universe_observed_on=membership_observed,
        universe_source_reference=membership["source_reference"],
        max_total_windows=max_total_windows,
        max_windows_per_instrument=max_windows_per_instrument,
    )

    # Extra audit metadata does not promote any eligibility status.
    plan["instrument_master_observed_on"] = master_observed.isoformat()
    plan["instrument_master_source_reference"] = master["source_reference"]
    plan["manifest_inputs_verified"] = False

    # Recompute the fingerprint after enriching the plan.
    import hashlib

    original = plan.pop("plan_sha256")
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    plan["plan_sha256"] = hashlib.sha256(canonical).hexdigest()
    assert original != ""  # Original planner already validated its own output.
    return plan
