"""Offline-only historical collection planning CLI.

No credentials, HTTP requests, storage writes or SQLite access.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date

from .upstox_collection import UpstoxCollectionRequest

_MAX_CLI_WINDOWS = 20


def _iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc

    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan historical NSE data collection offline.")
    parser.add_argument(
        "--instrument-key",
        required=True,
        help="Canonical NSE equity key, e.g. NSE_EQ|INE002A01018",
    )
    parser.add_argument(
        "--interval",
        required=True,
        choices=("1m", "1d"),
    )
    parser.add_argument(
        "--from-date",
        required=True,
        type=_iso_date,
    )
    parser.add_argument(
        "--to-date",
        required=True,
        type=_iso_date,
    )
    parser.add_argument(
        "--max-windows",
        type=int,
        default=20,
    )

    args = parser.parse_args(argv)

    if not 1 <= args.max_windows <= _MAX_CLI_WINDOWS:
        parser.error("max-windows must be between 1 and 20")

    try:
        request = UpstoxCollectionRequest(
            instrument_key=args.instrument_key,
            interval=args.interval,
            from_date=args.from_date,
            to_date=args.to_date,
            max_windows=args.max_windows,
        )
        windows = request.windows()
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    document = {
        "schema_version": 1,
        "mode": "RESEARCH_ONLY",
        "network_enabled": False,
        "storage_enabled": False,
        "provider_id": "upstox",
        "instrument_key": request.instrument_key,
        "interval": request.interval,
        "from_date": request.from_date.isoformat(),
        "to_date": request.to_date.isoformat(),
        "max_windows": request.max_windows,
        "planned_requests": len(windows),
        "source_use_status": "UNVERIFIED",
        "calendar_quality_status": "UNASSESSED",
        "windows": [
            {
                "from_date": window.from_date.isoformat(),
                "to_date": window.to_date.isoformat(),
            }
            for window in windows
        ],
    }

    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
