"""Offline conversion of NSE constituents and Upstox instrument master."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import stat
from datetime import date
from pathlib import Path
from typing import Any

from .universe_collection import UniverseMember

_MAX_SOURCE_BYTES = 150_000_000
_MAX_CSV_BYTES = 1_000_000
_MAX_EXPANDED_BYTES = 150_000_000


def _check_path(path: Path, *, directory: bool = False) -> None:
    if not isinstance(path, Path):
        raise TypeError("source path must be a Path")
    for component in (path, *path.parents):
        metadata = component.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("symlink or reparse point in path")
    metadata = path.stat()
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("output must be a directory")
    elif not stat.S_ISREG(metadata.st_mode):
        raise ValueError("source must be a regular file")


def _read(path: Path, limit: int) -> bytes:
    _check_path(path)
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("source file exceeds size limit")
    return payload


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate instrument-master JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite instrument-master JSON number")


def _load_instruments(path: Path) -> tuple[list[Any], bytes]:
    raw = _read(path, _MAX_SOURCE_BYTES)
    if path.name.lower().endswith(".json.gz"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
                decoded = handle.read(_MAX_EXPANDED_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise ValueError("invalid compressed instrument master") from exc
        if len(decoded) > _MAX_EXPANDED_BYTES:
            raise ValueError("expanded instrument master exceeds size limit")
    elif path.name.lower().endswith(".json"):
        decoded = raw
    else:
        raise ValueError("instrument master must be .json or .json.gz")

    try:
        records = json.loads(
            decoded,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("invalid instrument-master JSON") from exc

    if not isinstance(records, list):
        raise TypeError("Upstox instrument master must be an array")
    return records, raw


def convert_offline_sources(
    *,
    nse_csv: Path,
    upstox_gzip: Path,
    nse_observed_on: date,
    upstox_observed_on: date,
) -> tuple[dict[str, object], dict[str, object]]:
    """Convert local sources; retain original unverified eligibility."""
    if type(nse_observed_on) is not date or type(upstox_observed_on) is not date:
        raise TypeError("observation dates must be dates")

    nse_bytes = _read(nse_csv, _MAX_CSV_BYTES)
    try:
        reader = csv.DictReader(
            io.StringIO(nse_bytes.decode("utf-8-sig")),
            strict=True,
        )
        if reader.fieldnames is None or "Symbol" not in reader.fieldnames:
            raise ValueError("NSE CSV must include the Symbol column")
        symbols = []
        for row in reader:
            if None in row or not isinstance(row["Symbol"], str):
                raise ValueError("malformed NSE constituent row")
            symbols.append(row["Symbol"].strip())
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("invalid NSE constituent CSV") from exc

    if not 1 <= len(symbols) <= 200 or len(set(symbols)) != len(symbols):
        raise ValueError("invalid or duplicate NSE constituent symbols")

    for symbol in symbols:
        UniverseMember(symbol, "NSE_EQ|INE002A01018")

    instruments, raw_master = _load_instruments(upstox_gzip)
    selected = set(symbols)
    matched: dict[str, UniverseMember] = {}

    for instrument in instruments:
        if not isinstance(instrument, dict):
            continue
        if instrument.get("segment") != "NSE_EQ" or instrument.get("instrument_type") != "EQ":
            continue
        raw_symbol: object = instrument.get("trading_symbol")
        if not isinstance(raw_symbol, str) or raw_symbol not in selected:
            continue
        key = instrument.get("instrument_key")
        if not isinstance(key, str):
            raise TypeError("missing instrument key for selected symbol")
        member = UniverseMember(raw_symbol, key)
        if raw_symbol in matched:
            raise ValueError("ambiguous instrument-master symbol")
        matched[raw_symbol] = member

    if set(matched) != selected:
        raise ValueError("unresolved NSE symbols: " + ",".join(sorted(selected - set(matched))))
    if len({member.instrument_key for member in matched.values()}) != len(matched):
        raise ValueError("duplicate selected instrument keys")

    master = {
        "schema_version": 1,
        "kind": "instrument_master",
        "observed_on": upstox_observed_on.isoformat(),
        "source_reference": (
            (
                "upstox-nse-json-gz:sha256:"
                if upstox_gzip.name.lower().endswith(".json.gz")
                else "upstox-nse-json:sha256:"
            )
            + hashlib.sha256(raw_master).hexdigest()
        ),
        "members": [
            {"symbol": member.symbol, "instrument_key": member.instrument_key}
            for member in sorted(matched.values(), key=lambda item: item.symbol)
        ],
    }
    membership = {
        "schema_version": 1,
        "kind": "universe_membership",
        "observed_on": nse_observed_on.isoformat(),
        "source_reference": ("nse-nifty50-csv:sha256:" + hashlib.sha256(nse_bytes).hexdigest()),
        "members": [{"symbol": symbol} for symbol in sorted(selected)],
    }
    return master, membership


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline NSE/Upstox universe converter")
    parser.add_argument("--nse-csv", required=True, type=Path)
    parser.add_argument("--upstox-gzip", required=True, type=Path)
    parser.add_argument("--nse-observed-on", required=True, type=date.fromisoformat)
    parser.add_argument("--upstox-observed-on", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    master, membership = convert_offline_sources(
        nse_csv=args.nse_csv,
        upstox_gzip=args.upstox_gzip,
        nse_observed_on=args.nse_observed_on,
        upstox_observed_on=args.upstox_observed_on,
    )

    output = args.output_dir
    _check_path(output, directory=True)
    paths = (
        output / "upstox_master_selected.json",
        output / "nse_universe_membership.json",
    )
    if any(path.exists() or path.is_symlink() for path in paths):
        parser.error("destination manifest already exists")

    # Validate both outputs before writing either.
    documents = (master, membership)
    payloads = [
        json.dumps(doc, sort_keys=True, indent=2, allow_nan=False) + "\n" for doc in documents
    ]

    created: list[Path] = []
    try:
        for path, payload in zip(paths, payloads, strict=True):
            with path.open("x", encoding="utf-8") as handle:
                created.append(path)
                handle.write(payload)
                handle.flush()
    except OSError:
        for created_path in created:
            created_path.unlink(missing_ok=True)
        raise

    members = master["members"]
    if not isinstance(members, list):
        raise TypeError("invalid master members collection")
    print(f"OFFLINE_UNIVERSE_MANIFESTS_CREATED: {len(members)} stocks")
    print("SOURCE_USE_STATUS: UNVERIFIED")
    print("HISTORICAL_MEMBERSHIP: NOT_CONFIRMED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
