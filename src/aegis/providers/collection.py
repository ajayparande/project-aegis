"""Broker-neutral, offline historical collection window planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class CollectionWindow:
    from_date: date
    to_date: date

    def __post_init__(self) -> None:
        if type(self.from_date) is not date or type(self.to_date) is not date:
            raise ValueError("collection bounds must be dates")
        if self.from_date > self.to_date:
            raise ValueError("collection start must not exceed end")


def plan_collection_windows(
    *,
    interval: str,
    from_date: date,
    to_date: date,
    max_windows: int = 1000,
) -> tuple[CollectionWindow, ...]:
    """Partition inclusive date ranges into bounded request windows."""
    if interval not in {"1m", "1d"}:
        raise ValueError("unsupported historical interval")

    if type(from_date) is not date or type(to_date) is not date:
        raise ValueError("collection bounds must be dates")

    if from_date > to_date:
        raise ValueError("from_date must not exceed to_date")

    if type(max_windows) is not int or not 1 <= max_windows <= 1000:
        raise ValueError("max_windows must be between 1 and 1000")

    maximum_days = 7 if interval == "1m" else 366
    total_days = (to_date - from_date).days + 1
    required_windows = (total_days + maximum_days - 1) // maximum_days

    if required_windows > max_windows:
        raise ValueError("collection exceeds maximum permitted windows")

    windows: list[CollectionWindow] = []
    current = from_date

    for _ in range(required_windows):
        remaining = (to_date - current).days + 1
        days_in_window = min(maximum_days, remaining)
        end = current + timedelta(days=days_in_window - 1)

        windows.append(CollectionWindow(current, end))

        if end == to_date:
            break

        current = end + timedelta(days=1)

    return tuple(windows)
