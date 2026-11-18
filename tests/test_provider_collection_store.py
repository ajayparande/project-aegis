"""Synthetic tests for offline, resumable window storage; no HTTP or SQLite."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis.providers.collection import CollectionWindow
from aegis.providers.collection_store import (
    HistoricalCollectionIntegrityError,
    HistoricalCollectionStore,
    collect_historical_batches,
)
from aegis.providers.contracts import HistoricalCandle, HistoricalCandleBatch

KEY = "NSE_EQ|INE002A01018"
_IST = ZoneInfo("Asia/Kolkata")


def _batch(from_date: date, *, key: str = KEY, interval: str = "1m") -> HistoricalCandleBatch:
    return HistoricalCandleBatch(
        provider_id="upstox",
        provider_instrument_key=key,
        interval=interval,
        fetched_at=datetime(2026, 11, 20, tzinfo=UTC),
        response_sha256="a" * 64,
        candles=(
            HistoricalCandle(
                timestamp=datetime(
                    from_date.year, from_date.month, from_date.day, 9, 15, tzinfo=_IST
                ),
                open=Decimal("100.00"),
                high=Decimal("101.00"),
                low=Decimal("99.00"),
                close=Decimal("100.50"),
                volume=10,
            ),
        ),
    )


def _collect(store: HistoricalCollectionStore, callback):
    return collect_historical_batches(
        store=store,
        fetch=callback,
        provider_id="upstox",
        instrument_key=KEY,
        interval="1m",
        from_date=date(2026, 11, 1),
        to_date=date(2026, 11, 15),
    )


def test_collect_and_resume_without_duplicate_fetches(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    calls = []

    def fetch(*, instrument_key, interval, from_date, to_date):
        calls.append((instrument_key, interval, from_date, to_date))
        return _batch(from_date)

    first = _collect(store, fetch)
    assert (
        first.planned_windows,
        first.fetched_windows,
        first.reused_windows,
        first.candle_count,
    ) == (3, 3, 0, 3)
    second = _collect(store, fetch)
    assert (
        second.planned_windows,
        second.fetched_windows,
        second.reused_windows,
        second.candle_count,
    ) == (3, 0, 3, 3)
    assert len(calls) == 3
    manifests = list((tmp_path / "collector").rglob("*.manifest.json"))
    assert len(manifests) == 3
    each = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert each["source_use_status"] == "UNVERIFIED"
    assert each["calendar_quality_status"] == "UNASSESSED"
    assert each["content_sha256"]
    assert "test-token" not in manifests[0].read_text(encoding="utf-8")


def test_corrupted_candles_fail_closed_before_fetch(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    _collect(store, lambda *, from_date, **kw: _batch(from_date))
    file = next((tmp_path / "collector").rglob("*.candles.json"))
    file.write_bytes(file.read_bytes().replace(b"100.50", b"100.51"))
    with pytest.raises(HistoricalCollectionIntegrityError, match="checksum"):
        _collect(store, lambda **kwargs: pytest.fail("must not fetch after corruption"))


def test_corrupted_manifest_fails_closed(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    _collect(store, lambda *, from_date, **kw: _batch(from_date))
    file = next((tmp_path / "collector").rglob("*.manifest.json"))
    document = json.loads(file.read_text(encoding="utf-8"))
    document["source_use_status"] = "VERIFIED_PERMITTED"
    file.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    with pytest.raises(HistoricalCollectionIntegrityError):
        _collect(store, lambda **kwargs: pytest.fail("must not fetch after corruption"))


def test_incomplete_window_fails_closed_and_is_not_deleted(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    _collect(store, lambda *, from_date, **kw: _batch(from_date))
    manifest = next((tmp_path / "collector").rglob("*.manifest.json"))
    manifest.unlink()
    with pytest.raises(HistoricalCollectionIntegrityError, match="incomplete"):
        _collect(store, lambda **kwargs: pytest.fail("must not fetch"))
    assert list((tmp_path / "collector").rglob("*.candles.json"))


def test_reject_wrong_identity_before_writing(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    with pytest.raises(ValueError, match="identity mismatch"):
        _collect(store, lambda *, from_date, **kw: _batch(from_date, key="WRONG"))
    assert not list(tmp_path.rglob("*.candles.json"))


def test_reject_out_of_window_candle(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    with pytest.raises(ValueError, match="outside requested"):
        _collect(store, lambda **kwargs: _batch(date(2026, 11, 15)))
    assert not list(tmp_path.rglob("*.candles.json"))


def test_reject_future_candle(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    window = CollectionWindow(date(2026, 11, 1), date(2026, 11, 7))
    batch = _batch(date(2026, 11, 1))
    object.__setattr__(batch, "fetched_at", datetime(2026, 10, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="after fetch time"):
        store.save(
            provider_id="upstox", instrument_key=KEY, interval="1m", window=window, batch=batch
        )


def test_identical_window_refuses_overwrite(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    window = CollectionWindow(date(2026, 11, 1), date(2026, 11, 7))
    kwargs = {"provider_id": "upstox", "instrument_key": KEY, "interval": "1m", "window": window}
    store.save(**kwargs, batch=_batch(date(2026, 11, 1)))
    with pytest.raises(HistoricalCollectionIntegrityError, match="already exists"):
        store.save(**kwargs, batch=_batch(date(2026, 11, 1)))


def test_no_network_on_bad_request(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    with pytest.raises(ValueError, match="maximum"):
        collect_historical_batches(
            store=store,
            fetch=lambda **kwargs: pytest.fail("must not fetch"),
            provider_id="upstox",
            instrument_key=KEY,
            interval="1m",
            from_date=date(2026, 1, 1),
            to_date=date(2026, 3, 1),
            max_windows=1,
        )


def test_transient_fetch_failure_preserves_completed_windows(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    first_calls = []

    def flaky(*, from_date, **kwargs):
        first_calls.append(from_date)
        if len(first_calls) == 2:
            raise RuntimeError("synthetic provider interruption")
        return _batch(from_date)

    with pytest.raises(RuntimeError, match="interruption"):
        _collect(store, flaky)
    second_calls = []

    def resumed(*, from_date, **kwargs):
        second_calls.append(from_date)
        return _batch(from_date)

    progress = _collect(store, resumed)
    assert (progress.fetched_windows, progress.reused_windows) == (2, 1)
    assert len(second_calls) == 2


def test_preflight_detects_later_corruption_before_earlier_download(tmp_path: Path):
    store = HistoricalCollectionStore(tmp_path / "collector")
    _collect(store, lambda *, from_date, **kw: _batch(from_date))
    data = sorted((tmp_path / "collector").rglob("*.candles.json"))
    first_manifest = data[0].with_name(data[0].name.replace(".candles.json", ".manifest.json"))
    data[0].unlink()
    first_manifest.unlink()
    data[-1].write_bytes(data[-1].read_bytes() + b"damaged")
    with pytest.raises(HistoricalCollectionIntegrityError):
        _collect(store, lambda **kwargs: pytest.fail("must preflight before fetch"))


def test_atomic_publish_does_not_replace_racing_destination(tmp_path: Path, monkeypatch):
    """Another writer winning the destination-name race must not be overwritten."""
    from aegis.providers import collection_store

    destination = tmp_path / "window.json"

    def racing_link(source, target):
        destination.write_bytes(b"competing-writer")
        raise FileExistsError("synthetic competing writer")

    monkeypatch.setattr(collection_store.os, "link", racing_link)

    with pytest.raises(HistoricalCollectionIntegrityError, match="overwrite"):
        collection_store._atomic_new_file(destination, b"our-data")

    assert destination.read_bytes() == b"competing-writer"
    assert not list(tmp_path.glob(".aegis-*.tmp"))


def test_corrupted_decimal_raises_integrity_error(tmp_path: Path):
    """Malformed OHLC must fail even if the file checksum is internally consistent."""
    import hashlib

    store = HistoricalCollectionStore(tmp_path / "collector")
    window = CollectionWindow(date(2026, 11, 1), date(2026, 11, 7))

    arguments = {
        "provider_id": "upstox",
        "instrument_key": KEY,
        "interval": "1m",
        "window": window,
    }

    store.save(**arguments, batch=_batch(date(2026, 11, 1)))

    candle_file = next((tmp_path / "collector").rglob("*.candles.json"))
    manifest_file = candle_file.with_name(
        candle_file.name.replace(".candles.json", ".manifest.json")
    )

    document = json.loads(candle_file.read_bytes())
    document["candles"][0]["close"] = "not-a-number"

    serialized = (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")

    candle_file.write_bytes(serialized)

    manifest = json.loads(manifest_file.read_bytes())
    manifest["content_bytes"] = len(serialized)
    manifest["content_sha256"] = hashlib.sha256(serialized).hexdigest()

    manifest_file.write_bytes(
        (
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    )

    with pytest.raises(HistoricalCollectionIntegrityError, match="invalid"):
        store.load_verified(**arguments)


def test_m3a4e_deeply_nested_json_is_integrity_error(tmp_path: Path):
    """Untrusted nested JSON must not escape as a raw RecursionError."""
    from aegis.providers.collection_store import _read_json

    path = tmp_path / "nested.json"
    path.write_bytes(b"[" * 1500 + b"0" + b"]" * 1500)

    with pytest.raises(HistoricalCollectionIntegrityError):
        _read_json(path)


def test_m3a4e_nonfinite_manifest_is_integrity_error(tmp_path: Path):
    """NaN in a stored manifest must fail closed with the expected error."""
    store = HistoricalCollectionStore(tmp_path / "collector")
    window = CollectionWindow(date(2026, 11, 1), date(2026, 11, 7))

    arguments = {
        "provider_id": "upstox",
        "instrument_key": KEY,
        "interval": "1m",
        "window": window,
    }

    store.save(**arguments, batch=_batch(date(2026, 11, 1)))

    manifest_file = next((tmp_path / "collector").rglob("*.manifest.json"))
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["candle_count"] = float("nan")

    corrupted = (
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=True,
        )
        + "\n"
    )

    manifest_file.write_text(corrupted, encoding="utf-8")

    with pytest.raises(
        HistoricalCollectionIntegrityError,
        match="invalid JSON values",
    ):
        store.load_verified(**arguments)


def test_m3a4f_symlinked_ancestor_is_rejected(tmp_path: Path, monkeypatch):
    """Reject a root beneath a symlinked parent before any collection."""
    original = Path.is_symlink
    ancestor = tmp_path / "linked-parent"
    root = ancestor / "collector"

    def simulated_symlink(path):
        if path == ancestor:
            return True
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", simulated_symlink)

    with pytest.raises(ValueError, match="symbolic links"):
        HistoricalCollectionStore(root)

    assert not root.exists()


def test_m3a4f_real_ancestor_is_allowed(tmp_path: Path):
    """An ordinary storage root beneath normal directories remains usable."""
    parent = tmp_path / "ordinary-parent"
    parent.mkdir()
    root = parent / "collector"

    store = HistoricalCollectionStore(root)

    assert store.root == root
    assert not root.exists()


def test_m3a4g_reparse_parent_rejected(tmp_path: Path, monkeypatch):
    """Synthetic Windows junction attribute blocks storage construction."""
    import stat
    from types import SimpleNamespace

    parent = tmp_path / "junction-parent"
    root = parent / "collector"
    original = Path.lstat

    def synthetic_lstat(path):
        if path == parent:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", synthetic_lstat)

    with pytest.raises(ValueError, match="reparse points"):
        HistoricalCollectionStore(root)


def test_m3a4g_reparse_substitution_blocks_load(tmp_path: Path, monkeypatch):
    """A reparse point introduced after construction blocks reads."""
    import stat
    from types import SimpleNamespace

    root = tmp_path / "collector"
    store = HistoricalCollectionStore(root)
    original = Path.lstat

    def synthetic_lstat(path):
        if path == root:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", synthetic_lstat)

    with pytest.raises(HistoricalCollectionIntegrityError, match="reparse"):
        store.load_verified(
            provider_id="upstox",
            instrument_key=KEY,
            interval="1m",
            window=CollectionWindow(date(2026, 11, 1), date(2026, 11, 7)),
        )


def test_m3a4g_reparse_substitution_blocks_save(tmp_path: Path, monkeypatch):
    """A reparse point introduced after construction blocks writes."""
    import stat
    from types import SimpleNamespace

    root = tmp_path / "collector"
    store = HistoricalCollectionStore(root)
    original = Path.lstat

    def synthetic_lstat(path):
        if path == root:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", synthetic_lstat)

    with pytest.raises(HistoricalCollectionIntegrityError, match="reparse"):
        store.save(
            provider_id="upstox",
            instrument_key=KEY,
            interval="1m",
            window=CollectionWindow(date(2026, 11, 1), date(2026, 11, 7)),
            batch=_batch(date(2026, 11, 1)),
        )
