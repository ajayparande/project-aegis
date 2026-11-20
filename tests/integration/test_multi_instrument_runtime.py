"""Bounded restart demonstration consumed through the M4 read model."""

from datetime import timedelta

from aegis.multi_instrument_runtime import _MultiInstrumentCoordinator
from aegis.trading_dashboard_data import date_window, read_dashboard
from tests.test_forward_runtime import _START
from tests.test_multi_instrument_runtime import SYMBOLS, evidence, observations

pytest_plugins = ("tests.test_multi_instrument_runtime",)


def test_bounded_session_dashboard(session):
    database, calendars, ids, runtime = session
    quotes = observations(ids)
    coordinator = _MultiInstrumentCoordinator(runtime, SYMBOLS)
    coordinator.process(quotes[:18])
    coordinator = _MultiInstrumentCoordinator.open(database, runtime.runtime_id, calendars, SYMBOLS)
    coordinator.process(quotes[18:])
    assert coordinator.process((quotes[-1],))[0].duplicate
    report = read_dashboard(
        database,
        runtime.account_id,
        date_window("All Time", as_of=_START + timedelta(minutes=12)),
        as_of=_START + timedelta(minutes=12),
        run_id=runtime.runtime_id,
    )
    assert report["status"] == "AVAILABLE"
    assert {row["symbol"] for row in report["market"]} == set(SYMBOLS)
    assert all(row["data_class"] == "SYNTHETIC" for row in report["market"])
    candles, events = evidence(database)
    from dataclasses import replace

    assert not coordinator.process((replace(quotes[0], sequence=100, event_id="synthetic-late"),))[
        0
    ].accepted
    from collections import Counter

    print(
        {
            "quotes": dict(Counter(q.instrument_id for q in quotes)),
            "candles": dict(Counter((c[0], c[1]) for c in candles)),
            "events": dict(Counter((e[1], e[2]) for e in events)),
        }
    )
