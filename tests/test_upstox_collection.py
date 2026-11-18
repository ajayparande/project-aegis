"""Offline integration tests for the explicitly gated Upstox collection bridge."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from aegis.providers.collection_store import HistoricalCollectionStore
from aegis.providers.contracts import HistoricalCandle, HistoricalCandleBatch
from aegis.providers.upstox_collection import (
    UpstoxCollectionRequest,
    collect_upstox_history,
)

KEY = "NSE_EQ|INE002A01018"


def _request(**changes):
    args = {
        "instrument_key": KEY,
        "interval": "1m",
        "from_date": date(2026, 11, 1),
        "to_date": date(2026, 11, 15),
    }
    args.update(changes)
    return UpstoxCollectionRequest(**args)


def _batch(from_date):
    return HistoricalCandleBatch(
        provider_id="upstox",
        provider_instrument_key=KEY,
        interval="1m",
        candles=(
            HistoricalCandle(
                timestamp=datetime(
                    from_date.year,
                    from_date.month,
                    from_date.day,
                    9,
                    15,
                    tzinfo=ZoneInfo("Asia/Kolkata"),
                ),
                open=Decimal(100),
                high=Decimal(101),
                low=Decimal(99),
                close=Decimal(100),
                volume=10,
            ),
        ),
        fetched_at=datetime(2026, 11, 20, tzinfo=UTC),
        response_sha256="a" * 64,
    )


def test_plan_is_offline_and_bounded():
    assert len(_request().windows()) == 3
    assert _request(interval="1d").windows()[0].from_date == date(2026, 11, 1)


@pytest.mark.parametrize(
    "key", ["", "NSE_EQ|", "BSE_EQ|INE002A01018", "NSE_EQ|../bad", "NSE_EQ|INE002A01018\n"]
)
def test_invalid_identity_rejected(key):
    with pytest.raises(ValueError):
        _request(instrument_key=key)


def test_max_request_budget_prevents_network():
    with pytest.raises(ValueError):
        _request(max_windows=2)


@pytest.mark.parametrize(
    "network_authorized,data_use_reviewed", [(False, False), (True, False), (False, True)]
)
def test_disabled_by_default_before_fetch(
    tmp_path: Path, network_authorized: bool, data_use_reviewed: bool
):
    client = Mock()
    with pytest.raises(PermissionError):
        collect_upstox_history(
            store=HistoricalCollectionStore(tmp_path / "store"),
            client=client,
            request=_request(),
            network_authorized=network_authorized,
            data_use_reviewed=data_use_reviewed,
        )
    client.fetch.assert_not_called()


def test_explicitly_gated_fake_client_collect_and_resume(tmp_path: Path):
    client = Mock()
    client.fetch.side_effect = lambda *, from_date, **kwargs: _batch(from_date)
    store = HistoricalCollectionStore(tmp_path / "store")
    args = {
        "store": store,
        "client": client,
        "request": _request(),
        "network_authorized": True,
        "data_use_reviewed": True,
    }
    result = collect_upstox_history(**args)
    assert (result.fetched_windows, result.reused_windows) == (3, 0)
    result = collect_upstox_history(**args)
    assert (result.fetched_windows, result.reused_windows) == (0, 3)
    assert client.fetch.call_count == 3


def test_bad_client_rejected_when_enabled(tmp_path: Path):
    with pytest.raises(TypeError, match="client"):
        collect_upstox_history(
            store=HistoricalCollectionStore(tmp_path / "store"),
            client=object(),
            request=_request(),
            network_authorized=True,
            data_use_reviewed=True,
        )
