from datetime import UTC, datetime

import pytest

from aegis.providers.upstox import UpstoxHistoricalAdapter

NOW = datetime(2026, 11, 18, tzinfo=UTC)
KEY = "NSE_EQ|INE002A01018"


def payload(rows):
    return {"status": "success", "data": {"candles": rows}}


def parse(rows):
    return UpstoxHistoricalAdapter().parse_historical_response(
        payload(rows), instrument_key=KEY, interval="1m", fetched_at=NOW
    )


def test_valid_and_sorted_and_zero_volume():
    rows = [
        ["2026-11-17T09:16:00+05:30", 101.0, 102.0, 100.0, 101.5, 10, 0],
        ["2026-11-17T09:15:00+05:30", 100.0, 101.0, 99.0, 100.5, 0, 0],
    ]
    batch = parse(rows)
    assert batch.provider_id == "upstox"
    assert batch.candles[0].volume == 0
    assert batch.candles[0].timestamp.minute == 15
    assert len(batch.response_sha256) == 64


def test_duplicate_timestamps_rejected():
    row = ["2026-11-17T09:15:00+05:30", 100, 101, 99, 100, 10]
    with pytest.raises(ValueError, match="unique"):
        parse([row, row])


@pytest.mark.parametrize(
    "row",
    [
        ["2026-11-17T09:15:00", 100, 101, 99, 100, 10],
        ["2026-11-17T09:15:00+05:30", float("nan"), 101, 99, 100, 10],
        ["2026-11-17T09:15:00+05:30", 100, 101, 99, 100, True],
        ["2026-11-17T09:15:00+05:30", 100, 101, 99, 100, -1],
        ["2026-11-17T09:15:00+05:30", 100, 99, 99, 100, 10],
        ["2026-11-17T09:15:00+05:30", 100, 101, 99],
    ],
)
def test_bad_rows_fail_closed(row):
    with pytest.raises(ValueError):
        parse([row])


def test_failure_response_rejected():
    with pytest.raises(ValueError):
        UpstoxHistoricalAdapter().parse_historical_response(
            {"status": "error", "data": {"candles": []}},
            instrument_key=KEY,
            interval="1m",
            fetched_at=NOW,
        )


def test_empty_response_is_valid_but_not_complete():
    assert parse([]).candles == ()
