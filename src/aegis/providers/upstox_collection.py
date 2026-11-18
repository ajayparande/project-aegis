"""Explicitly gated Upstox historical collection orchestration.

No network happens on import, construction, or planning. An authenticated
fetch only occurs after the caller expressly enables it. No SQLite access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .collection import CollectionWindow, plan_collection_windows
from .collection_store import (
    CollectionProgress,
    HistoricalCollectionStore,
    collect_historical_batches,
)
from .contracts import HistoricalCandleBatch

_KEY = re.compile(r"^NSE_EQ[|]IN[A-Z0-9]{10}$")


class _HistoricalFetcher(Protocol):
    def fetch(
        self, *, instrument_key: str, interval: str, from_date: date, to_date: date
    ) -> HistoricalCandleBatch: ...


@dataclass(frozen=True, slots=True)
class UpstoxCollectionRequest:
    instrument_key: str
    interval: str
    from_date: date
    to_date: date
    max_windows: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_key, str) or _KEY.fullmatch(self.instrument_key) is None:
            raise ValueError("only canonical NSE equity ISIN instrument keys are supported")
        # Delegate all interval, calendar-day and window-limit validation.
        plan_collection_windows(
            interval=self.interval,
            from_date=self.from_date,
            to_date=self.to_date,
            max_windows=self.max_windows,
        )

    def windows(self) -> tuple[CollectionWindow, ...]:
        """Offline request plan, with no access to provider credentials."""
        return plan_collection_windows(
            interval=self.interval,
            from_date=self.from_date,
            to_date=self.to_date,
            max_windows=self.max_windows,
        )


def collect_upstox_history(
    *,
    store: HistoricalCollectionStore,
    client: _HistoricalFetcher,
    request: UpstoxCollectionRequest,
    network_authorized: bool = False,
    data_use_reviewed: bool = False,
) -> CollectionProgress:
    """Gate read-only fetches, then reuse the existing verified-resume collector.

    The caller is responsible for reviewing applicable data permissions. The
    persisted manifests stay UNVERIFIED even if this explicit gate is enabled.
    """
    if not isinstance(request, UpstoxCollectionRequest):
        raise TypeError("invalid Upstox collection request")
    if not isinstance(store, HistoricalCollectionStore):
        raise TypeError("invalid historical collection store")
    windows = request.windows()
    if network_authorized is not True or data_use_reviewed is not True:
        raise PermissionError("authenticated collection is disabled pending explicit approval")
    fetch = getattr(client, "fetch", None)
    if not callable(fetch):
        raise TypeError("historical fetch client must provide fetch")
    # The complete preflight and per-window validation remain in the store.
    return collect_historical_batches(
        store=store,
        fetch=fetch,
        provider_id="upstox",
        instrument_key=request.instrument_key,
        interval=request.interval,
        from_date=windows[0].from_date,
        to_date=windows[-1].to_date,
        max_windows=request.max_windows,
    )
