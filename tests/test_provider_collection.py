from itertools import pairwise

"""Offline tests for bounded historical collection planning."""

from datetime import date, datetime

import pytest

from aegis.providers.collection import CollectionWindow, plan_collection_windows


def test_daily_collection_is_bounded_and_contiguous():
    windows = plan_collection_windows(
        interval="1d",
        from_date=date(2020, 1, 1),
        to_date=date(2024, 12, 31),
    )
    assert windows[0].from_date == date(2020, 1, 1)
    assert windows[-1].to_date == date(2024, 12, 31)
    assert all((window.to_date - window.from_date).days + 1 <= 366 for window in windows)
    assert all(
        previous.to_date.toordinal() + 1 == following.from_date.toordinal()
        for previous, following in pairwise(windows)
    )


def test_minute_collection_uses_seven_day_windows():
    windows = plan_collection_windows(
        interval="1m",
        from_date=date(2026, 11, 1),
        to_date=date(2026, 11, 18),
    )
    assert [(window.from_date.isoformat(), window.to_date.isoformat()) for window in windows] == [
        ("2026-11-01", "2026-11-07"),
        ("2026-11-08", "2026-11-14"),
        ("2026-11-15", "2026-11-18"),
    ]


def test_single_day_window():
    assert plan_collection_windows(
        interval="1d",
        from_date=date(2026, 11, 18),
        to_date=date(2026, 11, 18),
    ) == (CollectionWindow(date(2026, 11, 18), date(2026, 11, 18)),)


@pytest.mark.parametrize("interval", ["", "5m", "1h", "invalid", None])
def test_unsupported_interval_rejected(interval):
    with pytest.raises(ValueError):
        plan_collection_windows(
            interval=interval,
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 2),
        )


@pytest.mark.parametrize("value", [0, -1, 1001, True, 1.5, "10"])
def test_invalid_window_limit_rejected(value):
    with pytest.raises(ValueError):
        plan_collection_windows(
            interval="1m",
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 2),
            max_windows=value,
        )


def test_reversed_dates_rejected():
    with pytest.raises(ValueError):
        plan_collection_windows(
            interval="1d",
            from_date=date(2026, 2, 1),
            to_date=date(2026, 1, 1),
        )


def test_excessive_collection_rejected():
    with pytest.raises(ValueError):
        plan_collection_windows(
            interval="1m",
            from_date=date(2000, 1, 1),
            to_date=date(2026, 1, 1),
            max_windows=10,
        )


def test_datetime_is_not_accepted_as_date():
    with pytest.raises(ValueError):
        plan_collection_windows(
            interval="1d",
            from_date=datetime(2026, 1, 1),
            to_date=date(2026, 1, 2),
        )


def test_direct_window_constructor_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        CollectionWindow(date(2026, 1, 2), date(2026, 1, 1))
    with pytest.raises(ValueError):
        CollectionWindow(datetime(2026, 1, 1), date(2026, 1, 2))
