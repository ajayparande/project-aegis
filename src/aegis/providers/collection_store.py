"""Offline, fail-closed historical candle batch storage and resume orchestration.

No authentication, HTTP calls, SQLite access, or training is performed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .collection import CollectionWindow, plan_collection_windows
from .contracts import HistoricalCandle, HistoricalCandleBatch

_IST = ZoneInfo("Asia/Kolkata")
_MAX_JSON_BYTES = 16_000_000
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class HistoricalCollectionIntegrityError(RuntimeError):
    """A previously stored window is incomplete, damaged or inconsistent."""


@dataclass(frozen=True, slots=True)
class CollectionProgress:
    planned_windows: int
    fetched_windows: int
    reused_windows: int
    candle_count: int


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _validated_identity(
    provider_id: str, instrument_key: str, interval: str, window: CollectionWindow
) -> None:
    if not isinstance(provider_id, str) or _PROVIDER_ID.fullmatch(provider_id) is None:
        raise ValueError("invalid provider ID")
    if (
        not isinstance(instrument_key, str)
        or not 1 <= len(instrument_key) <= 256
        or not instrument_key.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in instrument_key)
    ):
        raise ValueError("invalid instrument key")
    if not isinstance(interval, str) or interval not in {"1m", "1d"}:
        raise ValueError("unsupported interval")
    if not isinstance(window, CollectionWindow):
        raise TypeError("invalid collection window")
    allowed_days = 7 if interval == "1m" else 366
    if (window.to_date - window.from_date).days + 1 > allowed_days:
        raise ValueError("collection window exceeds interval limit")


def _validate_batch(
    batch: HistoricalCandleBatch,
    provider_id: str,
    instrument_key: str,
    interval: str,
    window: CollectionWindow,
) -> None:
    if not isinstance(batch, HistoricalCandleBatch):
        raise TypeError("provider returned an invalid historical batch")
    if (
        batch.provider_id != provider_id
        or batch.provider_instrument_key != instrument_key
        or batch.interval != interval
    ):
        raise ValueError("provider batch identity mismatch")
    for candle in batch.candles:
        local_date = candle.timestamp.astimezone(_IST).date()
        if not window.from_date <= local_date <= window.to_date:
            raise ValueError("provider candle outside requested IST dates")
        if candle.timestamp > batch.fetched_at:
            raise ValueError("provider candle timestamp after fetch time")


def _batch_document(batch: HistoricalCandleBatch) -> dict[str, Any]:
    return {
        "version": 1,
        "provider_id": batch.provider_id,
        "instrument_key": batch.provider_instrument_key,
        "interval": batch.interval,
        "fetched_at": _utc_text(batch.fetched_at),
        "response_sha256": batch.response_sha256,
        "candles": [
            {
                "timestamp": _utc_text(candle.timestamp),
                "open": str(candle.open),
                "high": str(candle.high),
                "low": str(candle.low),
                "close": str(candle.close),
                "volume": candle.volume,
            }
            for candle in batch.candles
        ],
    }


def _read_json(path: Path) -> tuple[object, bytes]:
    try:
        with path.open("rb") as handle:
            payload = handle.read(_MAX_JSON_BYTES + 1)
        if len(payload) > _MAX_JSON_BYTES:
            raise HistoricalCollectionIntegrityError("stored window exceeds size limit")
        return json.loads(payload), payload
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise HistoricalCollectionIntegrityError(
            "stored window is unreadable or invalid JSON"
        ) from exc


def _parse_batch(document: object) -> HistoricalCandleBatch:
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "version",
            "provider_id",
            "instrument_key",
            "interval",
            "fetched_at",
            "response_sha256",
            "candles",
        }
        or type(document["version"]) is not int
        or document["version"] != 1
    ):
        raise ValueError("invalid candle document schema")
    if not isinstance(document["candles"], list):
        raise TypeError("invalid candle list")
    candles = []
    for row in document["candles"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"timestamp", "open", "high", "low", "close", "volume"}
            or not all(
                isinstance(row[field], str)
                for field in ("timestamp", "open", "high", "low", "close")
            )
        ):
            raise ValueError("invalid stored candle schema")
        candles.append(
            HistoricalCandle(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=row["volume"],
            )
        )
    return HistoricalCandleBatch(
        provider_id=document["provider_id"],
        provider_instrument_key=document["instrument_key"],
        interval=document["interval"],
        fetched_at=datetime.fromisoformat(document["fetched_at"]),
        response_sha256=document["response_sha256"],
        candles=tuple(candles),
    )


def _atomic_new_file(destination: Path, content: bytes) -> None:
    """Publish a complete temporary file without replacing an existing path."""
    if len(content) > _MAX_JSON_BYTES:
        raise ValueError("serialized collection window exceeds size limit")

    descriptor, temporary = tempfile.mkstemp(
        prefix=".aegis-", suffix=".tmp", dir=destination.parent
    )

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise HistoricalCollectionIntegrityError(
                "refusing to overwrite a stored window"
            ) from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


class HistoricalCollectionStore:
    """Locally persisted windows; disk hashes detect accidental damage, not malicious forgery."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("storage root must be a Path")
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise ValueError("storage root must be a real directory")
        self.root = root

    def _paths(
        self, provider_id: str, instrument_key: str, interval: str, window: CollectionWindow
    ) -> tuple[Path, Path]:
        _validated_identity(provider_id, instrument_key, interval, window)
        digest = hashlib.sha256(f"{provider_id}\0{instrument_key}\0{interval}".encode()).hexdigest()
        folder = self.root / provider_id / interval / digest
        stem = f"{window.from_date:%Y%m%d}_{window.to_date:%Y%m%d}"
        return folder / f"{stem}.candles.json", folder / f"{stem}.manifest.json"

    def load_verified(
        self, *, provider_id: str, instrument_key: str, interval: str, window: CollectionWindow
    ) -> HistoricalCandleBatch | None:
        data_path, manifest_path = self._paths(provider_id, instrument_key, interval, window)
        if (
            self.root.is_symlink()
            or any(
                part.is_symlink()
                for part in (
                    data_path.parent.parent.parent,
                    data_path.parent.parent,
                    data_path.parent,
                )
            )
            or data_path.is_symlink()
            or manifest_path.is_symlink()
        ):
            raise HistoricalCollectionIntegrityError("symlink in collection storage path")
        data_exists, manifest_exists = data_path.exists(), manifest_path.exists()
        if not data_exists and not manifest_exists:
            return None
        if not (data_exists and manifest_exists):
            raise HistoricalCollectionIntegrityError(
                "incomplete stored window; manual review required"
            )
        manifest, manifest_bytes = _read_json(manifest_path)
        if not isinstance(manifest, dict) or set(manifest) != {
            "version",
            "provider_id",
            "instrument_key",
            "interval",
            "from_date",
            "to_date",
            "fetched_at",
            "response_sha256",
            "candle_count",
            "content_sha256",
            "content_bytes",
            "source_use_status",
            "calendar_quality_status",
        }:
            raise HistoricalCollectionIntegrityError("stored manifest schema mismatch")
        if manifest_bytes != _canonical_bytes(manifest):
            raise HistoricalCollectionIntegrityError("stored manifest not canonical")
        if type(manifest["version"]) is not int or manifest["version"] != 1:
            raise HistoricalCollectionIntegrityError("unsupported manifest version")
        expected = {
            "version": 1,
            "provider_id": provider_id,
            "instrument_key": instrument_key,
            "interval": interval,
            "from_date": window.from_date.isoformat(),
            "to_date": window.to_date.isoformat(),
            "source_use_status": "UNVERIFIED",
            "calendar_quality_status": "UNASSESSED",
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise HistoricalCollectionIntegrityError(
                "stored manifest identity or eligibility mismatch"
            )
        if (
            type(manifest["candle_count"]) is not int
            or manifest["candle_count"] < 0
            or type(manifest["content_bytes"]) is not int
            or manifest["content_bytes"] < 0
            or not isinstance(manifest["content_sha256"], str)
            or _SHA256.fullmatch(manifest["content_sha256"]) is None
        ):
            raise HistoricalCollectionIntegrityError("stored manifest hash/length invalid")
        document, data_bytes = _read_json(data_path)
        if (
            len(data_bytes) != manifest["content_bytes"]
            or hashlib.sha256(data_bytes).hexdigest() != manifest["content_sha256"]
        ):
            raise HistoricalCollectionIntegrityError("stored candle checksum mismatch")
        try:
            batch = _parse_batch(document)
            _validate_batch(batch, provider_id, instrument_key, interval, window)
            if data_bytes != _canonical_bytes(_batch_document(batch)):
                raise ValueError("stored candles are not canonical")
            if (
                manifest["candle_count"] != len(batch.candles)
                or manifest["fetched_at"] != _utc_text(batch.fetched_at)
                or manifest["response_sha256"] != batch.response_sha256
            ):
                raise ValueError("stored batch and manifest disagree")
        except (ValueError, TypeError, OverflowError, KeyError, InvalidOperation) as exc:
            raise HistoricalCollectionIntegrityError("stored candle content is invalid") from exc
        return batch

    def save(
        self,
        *,
        provider_id: str,
        instrument_key: str,
        interval: str,
        window: CollectionWindow,
        batch: HistoricalCandleBatch,
    ) -> None:
        data_path, manifest_path = self._paths(provider_id, instrument_key, interval, window)
        _validate_batch(batch, provider_id, instrument_key, interval, window)
        if (
            self.load_verified(
                provider_id=provider_id,
                instrument_key=instrument_key,
                interval=interval,
                window=window,
            )
            is not None
        ):
            raise HistoricalCollectionIntegrityError("window already exists; refusing overwrite")
        content = _canonical_bytes(_batch_document(batch))
        manifest = {
            "version": 1,
            "provider_id": provider_id,
            "instrument_key": instrument_key,
            "interval": interval,
            "from_date": window.from_date.isoformat(),
            "to_date": window.to_date.isoformat(),
            "fetched_at": _utc_text(batch.fetched_at),
            "response_sha256": batch.response_sha256,
            "candle_count": len(batch.candles),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_bytes": len(content),
            "source_use_status": "UNVERIFIED",
            "calendar_quality_status": "UNASSESSED",
        }
        data_path.parent.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or any(
            part.is_symlink()
            for part in (data_path.parent.parent.parent, data_path.parent.parent, data_path.parent)
        ):
            raise HistoricalCollectionIntegrityError("symlink in collection storage path")
        _atomic_new_file(data_path, content)
        _atomic_new_file(manifest_path, _canonical_bytes(manifest))


def collect_historical_batches(
    *,
    store: HistoricalCollectionStore,
    fetch: Callable[..., HistoricalCandleBatch],
    provider_id: str,
    instrument_key: str,
    interval: str,
    from_date: date,
    to_date: date,
    max_windows: int = 1000,
) -> CollectionProgress:
    """Fetch missing windows only; callers inject a mock or an explicitly approved fetcher."""
    windows = plan_collection_windows(
        interval=interval, from_date=from_date, to_date=to_date, max_windows=max_windows
    )
    if not isinstance(store, HistoricalCollectionStore) or not callable(fetch):
        raise TypeError("invalid collection store or fetch callback")
    # Verify every previously stored window before invoking any fetch callback.
    # A corrupt later window must not cause an earlier missing one to be fetched.
    existing_counts: list[int | None] = []
    for window in windows:
        existing = store.load_verified(
            provider_id=provider_id,
            instrument_key=instrument_key,
            interval=interval,
            window=window,
        )
        existing_counts.append(None if existing is None else len(existing.candles))

    fetched = reused = candle_count = 0
    for window, prior_count in zip(windows, existing_counts, strict=True):
        if prior_count is not None:
            reused += 1
            candle_count += prior_count
            continue
        batch = fetch(
            instrument_key=instrument_key,
            interval=interval,
            from_date=window.from_date,
            to_date=window.to_date,
        )
        store.save(
            provider_id=provider_id,
            instrument_key=instrument_key,
            interval=interval,
            window=window,
            batch=batch,
        )
        fetched += 1
        candle_count += len(batch.candles)
    return CollectionProgress(len(windows), fetched, reused, candle_count)
