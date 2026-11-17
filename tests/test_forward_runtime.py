"""Focused acceptance tests for the deterministic synthetic forward runtime."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from streamlit.testing.v1 import AppTest

import aegis.database as database_module
import aegis.forward_paper_trading as forward_paper_trading_module
import aegis.forward_runtime as forward_runtime_module
import aegis.paper_trading as paper_trading_module
from aegis.backtesting import zero_cost_assumptions
from aegis.database import get_connection, initialize_database
from aegis.forward_paper_trading import (
    DecisionAction,
    DecisionProposal,
    ForwardPaperTradingEngine,
    ProposalValidationError,
    _proposal_canonical_json,
    _proposal_sha256,
)
from aegis.forward_runtime import (
    DeterministicSyntheticQuoteProvider,
    ForwardRuntimeError,
    ForwardRuntimeService,
    Quote,
    list_forward_runtime_summaries,
)
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_quality import load_calendar_registry
from aegis.paper_trading import (
    PaperOrderRequest,
    PaperTradingError,
    RiskLimits,
    create_paper_account,
    get_paper_account_report,
    reconcile_paper_account,
    submit_paper_order,
)

_TZ = ZoneInfo("Asia/Kolkata")
_START = datetime(2026, 1, 5, 9, 15, tzinfo=_TZ)
_LIMITS = RiskLimits(
    max_order_value=Decimal(10000),
    max_position_quantity=10,
    max_instrument_exposure=Decimal(10000),
    max_portfolio_exposure=Decimal(20000),
    max_daily_loss=Decimal(5000),
    max_market_data_age_seconds=60,
)


@pytest.fixture(autouse=True)
def isolate_calendar_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGIS_CALENDAR_CONFIG_PATH", raising=False)


def _calendar(
    path: Path,
    *,
    coverage_end: str = "2026-01-05",
    holidays: list[dict[str, str]] | None = None,
    special_sessions: list[dict[str, object]] | None = None,
    weekdays: list[int] | None = None,
):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {
                    "potential_after_missing": 1,
                    "stale_after_missing": 3,
                },
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": "2026-01-05",
                        "coverage_end": coverage_end,
                        "source": "SYNTHETIC_TEST_ONLY",
                        "weekdays": [0, 1, 2, 3, 4] if weekdays is None else weekdays,
                        "sessions": [{"open": "09:15", "close": "09:45"}],
                        "holidays": [] if holidays is None else holidays,
                        "special_sessions": ([] if special_sessions is None else special_sessions),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    os.environ["AEGIS_CALENDAR_CONFIG_PATH"] = str(path)
    return load_calendar_registry(path)


def _setup(
    tmp_path: Path,
    symbols: tuple[str, ...] = ("SYNTH",),
    *,
    max_candidates: int = 5,
    calendars=None,
) -> tuple[Path, object, tuple[int, ...], ForwardRuntimeService]:
    database = tmp_path / "runtime.db"
    initialize_database(database)
    calendars = calendars or _calendar(tmp_path / "runtime_calendar.json")
    instruments = tuple(
        register_instrument(
            database,
            InstrumentInput(
                "NSE",
                symbol,
                f"{symbol} synthetic fixture",
                "EQUITY",
                segment="NSE",
                tick_size=Decimal("0.05"),
            ),
        )
        for symbol in symbols
    )
    account_id = create_paper_account(
        database,
        name=f"runtime-{tmp_path.name}",
        initial_cash=Decimal(100000),
        risk_limits=_LIMITS,
        costs=zero_cost_assumptions(),
        created_at=_START.isoformat(),
    )
    runtime = ForwardRuntimeService.create(
        database,
        f"runtime-{tmp_path.name}",
        account_id,
        tuple(item.id for item in instruments),
        calendars,
        max_candidates=max_candidates,
        max_quote_delay_seconds=60,
        degraded_after_seconds=10,
        stale_after_seconds=180,
        max_execution_price_deviation=Decimal("0.05"),
        created_at=_START.isoformat(),
    )
    return database, calendars, tuple(item.id for item in instruments), runtime


def _set_feed_record(
    database: Path,
    runtime: ForwardRuntimeService,
    instrument_id: int,
    *,
    status: str | None,
) -> None:
    with closing(get_connection(database)) as connection:
        row = connection.execute(
            "SELECT state_json FROM forward_runtimes WHERE runtime_id = ?",
            (runtime.runtime_id,),
        ).fetchone()
        state = json.loads(row["state_json"])
        if status is None:
            state["feed"].pop(str(instrument_id), None)
        else:
            state["feed"][str(instrument_id)]["status"] = status
        state_json = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        connection.execute(
            """UPDATE forward_runtimes SET state_json = ?, state_sha256 = ?
               WHERE runtime_id = ?""",
            (state_json, hashlib.sha256(state_json.encode()).hexdigest(), runtime.runtime_id),
        )
        connection.commit()


def _add_historical_import(
    database: Path,
    instrument_id: int,
    timestamp: str,
    *,
    source_use_status: str = "VERIFIED_PERMITTED",
) -> int:
    with closing(get_connection(database)) as connection:
        instrument = connection.execute(
            "SELECT exchange, trading_symbol FROM instruments WHERE id = ?", (instrument_id,)
        ).fetchone()
        cursor = connection.execute(
            """INSERT INTO data_imports
               (import_type, status, started_at, completed_at, records_received,
                records_inserted, metadata_json, instrument_id, original_filename,
                file_sha256, requested_exchange, requested_trading_symbol, interval,
                total_rows, accepted_rows, source_use_status)
               VALUES ('SYNTHETIC_STREAM', 'COMPLETED', ?, ?, 1, 1, '{}', ?, ?, ?,
                       ?, ?, '1m', 1, 1, ?)""",
            (
                timestamp,
                timestamp,
                instrument_id,
                "external.synthetic",
                hashlib.sha256(f"external:{instrument_id}:{timestamp}".encode()).hexdigest(),
                instrument["exchange"],
                instrument["trading_symbol"],
                source_use_status,
            ),
        )
        import_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO import_source_use_history
               (import_id, previous_status, new_status, declared_by, rationale)
               VALUES (?, NULL, ?, 'test', 'isolated provenance test')""",
            (import_id, source_use_status),
        )
        connection.execute(
            """INSERT INTO historical_candles
               (instrument_id, import_id, interval, candle_timestamp, open_decimal,
                high_decimal, low_decimal, close_decimal, volume)
               VALUES (?, ?, '1m', ?, '101', '101', '101', '101', 1)""",
            (instrument_id, import_id, timestamp),
        )
        connection.commit()
        return import_id


def _provider(instrument_ids: tuple[int, ...], *, count: int, step: str = "0.05"):
    return DeterministicSyntheticQuoteProvider(
        instrument_ids,
        start_at=_START,
        base_prices={
            instrument_id: Decimal(100 + index * 100)
            for index, instrument_id in enumerate(instrument_ids)
        },
        price_steps={instrument_id: Decimal(step) for instrument_id in instrument_ids},
    ).quotes(count)


def _assert_no_paper_execution(database: Path, account_id: int) -> None:
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM paper_fills f JOIN paper_orders o
               ON o.order_id = f.order_id WHERE o.account_id = ?""",
                (account_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_ledger_entries WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
            == 0
        )


def _buy_runtime_position(
    runtime: ForwardRuntimeService,
    instrument_id: int,
    quotes: list[Quote],
    *,
    stop_loss: Decimal | None,
    target_price: Decimal | None,
) -> None:
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(
        event_id, "BUY", 1, stop_loss=stop_loss, target_price=target_price
    )
    result = runtime.route_event(
        event_id, proposal, runtime.execution_snapshot(instrument_id, quotes[1])
    )
    assert result.status == "FILLED", result.reason


def _quote(
    instrument_id: int,
    timestamp: str,
    sequence: int,
    price: Decimal,
    event_id: str,
    received_at: str | None = None,
    quantity: int = 1,
) -> Quote:
    return Quote(
        instrument_id,
        timestamp,
        sequence,
        price,
        quantity,
        event_id,
        received_at or timestamp,
    )


def _event_write_from_row(row: sqlite3.Row) -> database_module._SchedulerEventWrite:
    return database_module._SchedulerEventWrite(
        event_id=str(row["event_id"]),
        runtime_id=str(row["runtime_id"]),
        instrument_id=int(row["instrument_id"]),
        interval=str(row["interval"]),
        candle_timestamp=str(row["candle_timestamp"]),
        decision_timestamp=str(row["decision_timestamp"]),
        status=str(row["status"]),
        proposal_id=str(row["proposal_id"]),
        proposal_sha256=(
            str(row["proposal_sha256"]) if row["proposal_sha256"] is not None else None
        ),
        proposal_json=str(row["proposal_json"]) if row["proposal_json"] is not None else None,
        proposal_binding_state=str(row["proposal_binding_state"]),
        result_json=str(row["result_json"]) if row["result_json"] is not None else None,
        created_at=str(row["created_at"]),
    )


def _insert_scheduler_event(
    connection: sqlite3.Connection,
    event: database_module._SchedulerEventWrite,
) -> sqlite3.Cursor:
    return connection.execute(
        """INSERT INTO forward_scheduler_events
           (event_id, runtime_id, instrument_id, interval, candle_timestamp,
            decision_timestamp, status, proposal_id, proposal_sha256, proposal_json,
            proposal_binding_state, result_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        event.values(),
    )


def test_synthetic_provider_keeps_instrument_streams_independent(tmp_path: Path) -> None:
    _, _, instruments, runtime = _setup(tmp_path, ("ALPHA", "BETA"))

    results = runtime.consume_provider(
        DeterministicSyntheticQuoteProvider(
            instruments,
            start_at=_START,
            base_prices={instruments[0]: Decimal(100), instruments[1]: Decimal(200)},
            price_steps={instruments[0]: Decimal("0.05"), instruments[1]: Decimal("0.10")},
        ),
        3,
    )

    assert len(results) == 6
    with closing(get_connection(runtime.database_path)) as connection:
        rows = connection.execute(
            """SELECT instrument_id, COUNT(*) AS count, MIN(sequence) AS first_sequence,
                      MAX(sequence) AS last_sequence
               FROM forward_quotes WHERE runtime_id = ? AND accepted = 1
               GROUP BY instrument_id ORDER BY instrument_id""",
            (runtime.runtime_id,),
        ).fetchall()
    assert [(row["count"], row["first_sequence"], row["last_sequence"]) for row in rows] == [
        (3, 1, 3),
        (3, 1, 3),
    ]


def test_invalid_duplicate_delayed_and_out_of_order_quotes_are_safe(tmp_path: Path) -> None:
    _, _, (instrument_id,), runtime = _setup(tmp_path)
    first = _quote(instrument_id, "2026-01-05T09:15:00+05:30", 1, Decimal(100), "first")
    assert runtime.process_quote(first).accepted
    assert runtime.monitor_feed_health(first.timestamp)[0].status == "HEALTHY"
    duplicate = runtime.process_quote(first)
    assert duplicate.duplicate
    assert not runtime.process_quote(
        _quote(instrument_id, "2026-01-05T09:16:00+05:30", 2, Decimal(0), "invalid")
    ).accepted
    assert not runtime.process_quote(
        _quote(instrument_id, "2026-01-05T09:14:00+05:30", 3, Decimal(100), "out-of-order")
    ).accepted
    assert not runtime.process_quote(
        _quote(
            instrument_id,
            "2026-01-05T09:15:00+05:30",
            4,
            Decimal(100),
            "delayed",
            "2026-01-05T09:17:00+05:30",
        )
    ).accepted
    health = runtime.monitor_feed_health("2026-01-05T09:17:00+05:30")
    assert health[0].status == "DEGRADED"
    assert health[0].rejected_events == 3
    stale = runtime.monitor_feed_health("2026-01-05T09:19:00+05:30")
    assert stale[0].status == "STALE"
    assert (
        list_forward_runtime_summaries(runtime.database_path)[0]["feed_health"][str(instrument_id)]
        == "STALE"
    )
    assert runtime.process_quote(
        _quote(instrument_id, "2026-01-05T09:16:00+05:30", 2, Decimal(100), "recovery")
    ).accepted
    assert runtime.monitor_feed_health("2026-01-05T09:16:00+05:30")[0].status == "HEALTHY"


def test_quotes_must_be_inside_the_configured_exchange_session(tmp_path: Path) -> None:
    folder = tmp_path / "calendar-cases"
    folder.mkdir()
    calendar = _calendar(
        folder / "runtime_calendar.json",
        coverage_end="2026-01-09",
        weekdays=[0, 1, 2, 4],
        holidays=[{"date": "2026-01-06", "reason": "Synthetic test holiday"}],
        special_sessions=[
            {
                "date": "2026-01-07",
                "reason": "Synthetic test special session",
                "sessions": [{"open": "10:00", "close": "10:30"}],
            }
        ],
    )

    def run_case(name: str, timestamp: str):
        case_dir = tmp_path / name
        case_dir.mkdir()
        _, _, (case_instrument,), case_runtime = _setup(case_dir, calendars=calendar)
        quote = _quote(case_instrument, timestamp, 1, Decimal(100), name)
        return case_runtime.process_quote(quote)

    accepted = run_case("regular-open", "2026-01-05T03:45:00Z")
    pre_open = run_case("pre-open", "2026-01-05T09:14:00+05:30")
    holiday = run_case("holiday", "2026-01-06T09:15:00+05:30")
    non_trading = run_case("weekday-closed", "2026-01-08T09:15:00+05:30")
    special_open = run_case("special-open", "2026-01-07T10:00:00+05:30")
    special_regular_time = run_case("special-regular", "2026-01-07T09:15:00+05:30")
    post_close = run_case("post-close", "2026-01-05T09:45:00+05:30")
    uncovered = run_case("uncovered-date", "2026-01-12T09:15:00+05:30")

    assert accepted.accepted
    assert not pre_open.accepted and "outside" in (pre_open.reason or "").lower()
    assert not holiday.accepted and "outside" in (holiday.reason or "").lower()
    assert not non_trading.accepted and "outside" in (non_trading.reason or "").lower()
    assert special_open.accepted
    assert not special_regular_time.accepted
    assert not post_close.accepted
    assert not uncovered.accepted and "cover" in (uncovered.reason or "").lower()

    boundary_folder = tmp_path / "boundary"
    boundary_folder.mkdir()
    boundary_database, _, (boundary_instrument,), boundary_runtime = _setup(boundary_folder)
    assert boundary_runtime.process_quote(
        _quote(
            boundary_instrument,
            "2026-01-05T09:44:00+05:30",
            1,
            Decimal(100),
            "last-session-minute",
        )
    ).accepted
    assert not boundary_runtime.process_quote(
        _quote(
            boundary_instrument,
            "2026-01-05T09:45:00+05:30",
            2,
            Decimal(100),
            "exclusive-close",
        )
    ).accepted
    with closing(get_connection(boundary_database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_runtime_candles WHERE runtime_id = ?",
                (boundary_runtime.runtime_id,),
            ).fetchone()[0]
            == 0
        )


def test_streaming_aggregates_only_completed_1m_5m_and_15m_candles(
    tmp_path: Path,
) -> None:
    _, _, instruments, runtime = _setup(tmp_path)
    runtime.consume_provider(
        DeterministicSyntheticQuoteProvider(
            instruments,
            start_at=_START,
            base_prices={instruments[0]: Decimal(100)},
        ),
        17,
    )
    with closing(get_connection(runtime.database_path)) as connection:
        counts = {
            row["interval"]: row["count"]
            for row in connection.execute(
                """SELECT interval, COUNT(*) AS count FROM forward_runtime_candles
                   WHERE runtime_id = ? GROUP BY interval""",
                (runtime.runtime_id,),
            )
        }
        candle = connection.execute(
            """SELECT * FROM forward_runtime_candles WHERE runtime_id = ?
               AND instrument_id = ? AND interval = '5m'
               ORDER BY candle_timestamp LIMIT 1""",
            (runtime.runtime_id, instruments[0]),
        ).fetchone()
        in_progress = connection.execute(
            """            SELECT COUNT(*) FROM historical_candles WHERE instrument_id = ?
                   AND interval = '1m' AND candle_timestamp = '2026-01-05T04:01:00Z'""",
            (instruments[0],),
        ).fetchone()[0]

    assert counts == {"1m": 16, "5m": 3, "15m": 1}
    assert candle["open_decimal"] == "100.05"
    assert candle["high_decimal"] == "100.25"
    assert candle["low_decimal"] == "100.05"
    assert candle["close_decimal"] == "100.25"
    assert candle["volume"] == 5
    assert in_progress == 0
    with pytest.raises(ForwardRuntimeError, match="future"):
        runtime.prepare_evidence(instruments[0], "1m", "2026-01-05T09:15:00+05:30")
    evidence = runtime.prepare_evidence(instruments[0], "1m", "2026-01-05T09:32:00+05:30")
    assert evidence.latest_candle_timestamp == "2026-01-05T04:00:00Z"
    assert evidence.runtime_id == runtime.runtime_id
    assert evidence.source_import_id is not None
    assert (
        evidence.evidence_id
        == runtime.prepare_evidence(instruments[0], "1m", "2026-01-05T09:32:00+05:30").evidence_id
    )


def test_one_minute_candle_uses_quote_open_high_low_close_and_volume(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = (
        _quote(
            instrument_id,
            "2026-01-05T09:15:00+05:30",
            1,
            Decimal(100),
            "bar-1",
            quantity=2,
        ),
        _quote(
            instrument_id,
            "2026-01-05T09:15:30+05:30",
            2,
            Decimal("100.20"),
            "bar-2",
            quantity=3,
        ),
        _quote(instrument_id, "2026-01-05T09:16:00+05:30", 3, Decimal("100.10"), "bar-3"),
    )
    runtime.process_quote(quotes[0])
    runtime.process_quote(quotes[1])
    runtime.process_quote(quotes[2])
    with closing(get_connection(database)) as connection:
        candle = connection.execute(
            """SELECT open_decimal, high_decimal, low_decimal, close_decimal, volume
               FROM forward_runtime_candles WHERE runtime_id = ? AND instrument_id = ?
                 AND interval = '1m'""",
            (runtime.runtime_id, instrument_id),
        ).fetchone()
    assert tuple(candle) == ("100", "100.2", "100", "100.2", 5)


def test_in_progress_quote_cannot_be_used_as_decision_evidence(tmp_path: Path) -> None:
    _, _, (instrument_id,), runtime = _setup(tmp_path)
    quote = _quote(instrument_id, "2026-01-05T09:15:30+05:30", 1, Decimal(100), "in-progress")
    assert runtime.process_quote(quote).accepted
    with pytest.raises(ForwardRuntimeError, match="No completed runtime candle"):
        runtime.prepare_evidence(instrument_id, "1m", quote.timestamp)


def test_candidate_cap_and_scheduler_event_ids_are_bounded_and_stable(
    tmp_path: Path,
) -> None:
    database, _, instruments, runtime = _setup(
        tmp_path, ("ALPHA", "BETA", "GAMMA"), max_candidates=2
    )
    runtime.consume_provider(
        DeterministicSyntheticQuoteProvider(
            instruments,
            start_at=_START,
            base_prices={item: Decimal(100) for item in instruments},
            price_steps={
                instruments[0]: Decimal("0.05"),
                instruments[1]: Decimal("0.10"),
                instruments[2]: Decimal("0.15"),
            },
        ),
        3,
    )
    candidates = runtime.select_candidates()
    assert len(candidates) <= 2
    assert candidates[0] == instruments[2]
    with closing(get_connection(database)) as connection:
        candle = connection.execute(
            """SELECT candle_timestamp FROM forward_runtime_candles
               WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m'
               ORDER BY candle_timestamp DESC LIMIT 1""",
            (runtime.runtime_id, instruments[2]),
        ).fetchone()
    assert candle is not None
    event_id = runtime.schedule_event(instruments[2], "1m", candle["candle_timestamp"])
    assert event_id
    assert runtime.schedule_event(instruments[2], "1m", candle["candle_timestamp"]) == event_id
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            == 1
        )


def test_phase8_route_requires_snapshot_and_preserves_execution_controls(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    for quote in quotes:
        result = runtime.process_quote(quote)
    event_id = result.scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(
        event_id, "BUY", 1, stop_loss=Decimal(99), target_price=Decimal(110)
    )
    no_snapshot = runtime.route_event(event_id, proposal, None)
    assert no_snapshot.status == "REJECTED"
    assert "snapshot" in (no_snapshot.reason or "").lower()

    with closing(get_connection(database)) as connection:
        event = connection.execute(
            """SELECT event_id, proposal_id FROM forward_scheduler_events
               WHERE runtime_id = ? ORDER BY created_at DESC LIMIT 1""",
            (runtime.runtime_id,),
        ).fetchone()
    assert event is not None
    assert event_id == event["event_id"]
    assert proposal.proposal_id == event["proposal_id"]
    with closing(get_connection(database)) as connection:
        rejected = connection.execute(
            "SELECT status, quantity FROM paper_orders WHERE account_id = ?",
            (runtime.account_id,),
        ).fetchall()
    assert rejected == []


def test_execution_snapshot_must_match_an_independently_accepted_quote(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])
    forged = replace(snapshot, reference_price=snapshot.reference_price + Decimal("0.05"))

    result = runtime.route_event(event_id, proposal, forged)

    assert result.status == "REJECTED"
    assert "persisted quote" in (result.reason or "").lower()
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ?", (runtime.account_id,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "health", ["STARTING", "DEGRADED", "STALE", "DISCONNECTED", "UNKNOWN", None]
)
def test_buy_requires_valid_healthy_instrument_feed(tmp_path: Path, health: str | None) -> None:
    case_dir = tmp_path / (health or "missing")
    case_dir.mkdir()
    database, _, (instrument_id,), runtime = _setup(case_dir)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])
    _set_feed_record(database, runtime, instrument_id, status=health)

    result = runtime.route_event(event_id, proposal, snapshot)

    assert result.status == "REJECTED"
    assert "feed" in (result.reason or "").lower()
    with closing(get_connection(database)) as connection:
        event = connection.execute(
            "SELECT status, result_json FROM forward_scheduler_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        assert event["status"] == "REJECTED"
        assert json.loads(event["result_json"])["reason"] == result.reason
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ?", (runtime.account_id,)
            ).fetchone()[0]
            == 0
        )


def test_expired_buy_quote_and_stale_peer_instrument_are_isolated(tmp_path: Path) -> None:
    database, _, instruments, runtime = _setup(tmp_path, ("ALPHA", "BETA"))
    provider = DeterministicSyntheticQuoteProvider(
        instruments,
        start_at=_START,
        base_prices={instruments[0]: Decimal(100), instruments[1]: Decimal(200)},
    )
    quotes = list(provider.quotes(2))
    for quote in quotes:
        runtime.process_quote(quote)
    with closing(get_connection(database)) as connection:
        events = connection.execute(
            """SELECT event_id, instrument_id FROM forward_scheduler_events
               WHERE runtime_id = ? AND status = 'PENDING'""",
            (runtime.runtime_id,),
        ).fetchall()
    event_by_instrument = {int(row["instrument_id"]): str(row["event_id"]) for row in events}
    _set_feed_record(database, runtime, instruments[0], status="STALE")
    healthy_event = event_by_instrument[instruments[1]]
    healthy_quote = quotes[-1]
    healthy_proposal = runtime.build_mock_proposal(healthy_event, "BUY", 1)
    healthy_snapshot = runtime.execution_snapshot(instruments[1], healthy_quote)
    healthy_result = runtime.route_event(healthy_event, healthy_proposal, healthy_snapshot)
    assert healthy_result.status == "FILLED"

    next_quotes = list(provider.quotes(3))[4:6]
    accepted_quotes = [runtime.process_quote(quote) for quote in next_quotes]
    expired_event = accepted_quotes[-1].scheduled_event_ids[0]
    expired_proposal = runtime.build_mock_proposal(expired_event, "BUY", 1)
    expired_snapshot = runtime.execution_snapshot(instruments[1], quotes[1])
    expired_result = runtime.route_event(expired_event, expired_proposal, expired_snapshot)
    assert expired_result.status == "REJECTED"
    assert "latest accepted quote" in (expired_result.reason or "").lower()


def test_delayed_but_accepted_quote_is_rejected_for_buy_entry(tmp_path: Path) -> None:
    _, _, (instrument_id,), runtime = _setup(tmp_path)
    first = _quote(instrument_id, "2026-01-05T09:15:00+05:30", 1, Decimal(100), "fresh-1")
    second = _quote(
        instrument_id,
        "2026-01-05T09:16:00+05:30",
        2,
        Decimal("100.05"),
        "fresh-2",
        "2026-01-05T09:16:20+05:30",
    )
    assert runtime.process_quote(first).accepted
    event_id = runtime.process_quote(second).scheduled_event_ids[0]
    result = runtime.route_event(
        event_id,
        runtime.build_mock_proposal(event_id, "BUY", 1),
        runtime.execution_snapshot(instrument_id, second),
    )
    assert result.status == "REJECTED"
    assert "expired or degraded" in (result.reason or "").lower()


def test_healthy_feed_recovery_allows_a_new_buy(tmp_path: Path) -> None:
    _, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=3))
    runtime.process_quote(quotes[0])
    first_event = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    assert runtime.monitor_feed_health("2026-01-05T09:20:00+05:30")[0].status == "STALE"
    rejected = runtime.route_event(
        first_event,
        runtime.build_mock_proposal(first_event, "BUY", 1),
        runtime.execution_snapshot(instrument_id, quotes[1]),
    )
    assert rejected.status == "REJECTED"

    recovered_quote = runtime.process_quote(quotes[2])
    assert runtime.monitor_feed_health(quotes[2].timestamp)[0].status == "HEALTHY"
    recovered_event = recovered_quote.scheduled_event_ids[0]
    recovered = runtime.route_event(
        recovered_event,
        runtime.build_mock_proposal(recovered_event, "BUY", 1),
        runtime.execution_snapshot(instrument_id, quotes[2]),
    )
    assert recovered.status == "FILLED"


def test_evidence_stays_bound_to_the_exact_runtime_import(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    for quote in _provider((instrument_id,), count=4):
        runtime.process_quote(quote)
    with closing(get_connection(database)) as connection:
        runtime_import_id = int(
            connection.execute(
                """SELECT import_id FROM forward_runtime_imports
                   WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m'""",
                (runtime.runtime_id, instrument_id),
            ).fetchone()["import_id"]
        )
    external_import = _add_historical_import(database, instrument_id, "2026-01-05T03:50:00Z")
    evidence = runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:25:00+05:30")
    assert evidence.source_import_id == runtime_import_id
    assert evidence.source_import_id != external_import
    assert evidence.latest_candle_timestamp == "2026-01-05T03:47:00Z"
    assert evidence.runtime_id == runtime.runtime_id
    assert evidence.evidence_id

    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE forward_runtime_imports SET import_id = ?
               WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m'""",
            (external_import, runtime.runtime_id, instrument_id),
        )
        connection.commit()
    with pytest.raises(ForwardRuntimeError, match="different runtime"):
        runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:25:00+05:30")


@pytest.mark.parametrize("permission", ["UNVERIFIED", "PROHIBITED"])
def test_runtime_evidence_rejects_unverified_or_prohibited_source(
    tmp_path: Path, permission: str
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    for quote in _provider((instrument_id,), count=2):
        runtime.process_quote(quote)
    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE data_imports SET source_use_status = ?
               WHERE id = (SELECT import_id FROM forward_runtime_imports
                           WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m')""",
            (permission, runtime.runtime_id, instrument_id),
        )
        connection.commit()
    with pytest.raises(ForwardRuntimeError, match="provenance"):
        runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:18:00+05:30")


def test_runtime_evidence_rejects_wrong_provider_origin(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=3))
    for quote in quotes:
        runtime.process_quote(quote)
    with closing(get_connection(database)) as connection:
        import_id = connection.execute(
            """SELECT import_id FROM forward_runtime_imports
               WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m'""",
            (runtime.runtime_id, instrument_id),
        ).fetchone()["import_id"]
        connection.execute(
            "UPDATE data_imports SET metadata_json = ? WHERE id = ?",
            (json.dumps({"runtime_id": runtime.runtime_id, "provider": "OTHER"}), import_id),
        )
        connection.commit()

    with pytest.raises(ForwardRuntimeError, match="provenance"):
        runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:17:00+05:30")


def test_runtime_evidence_rejects_future_import_completion(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    for quote in _provider((instrument_id,), count=2):
        runtime.process_quote(quote)
    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE data_imports SET completed_at = ?
               WHERE id = (SELECT import_id FROM forward_runtime_imports
                           WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m')""",
            ("2026-01-05T09:18:00+05:30", runtime.runtime_id, instrument_id),
        )
        connection.commit()

    with pytest.raises(ForwardRuntimeError, match="future completion timestamp"):
        runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:17:00+05:30")


def test_scheduler_routing_is_idempotent_and_stop_or_target_exit_occurs_once(
    tmp_path: Path,
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    for quote in quotes:
        result = runtime.process_quote(quote)
    event_id = result.scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(
        event_id, "BUY", 1, stop_loss=Decimal(99), target_price=Decimal("100.25")
    )
    snapshot = runtime.execution_snapshot(instrument_id, quotes[-1])
    with closing(get_connection(database)) as connection:
        binding_state = connection.execute(
            """SELECT proposal_binding_state FROM forward_scheduler_events
               WHERE event_id = ?""",
            (event_id,),
        ).fetchone()["proposal_binding_state"]
    assert binding_state == "UNBOUND"
    filled = runtime.route_event(event_id, proposal, snapshot)
    assert filled.status == "FILLED"
    with closing(get_connection(database)) as connection:
        binding_state = connection.execute(
            """SELECT proposal_binding_state FROM forward_scheduler_events
               WHERE event_id = ?""",
            (event_id,),
        ).fetchone()["proposal_binding_state"]
    assert binding_state == "BOUND"
    duplicate = runtime.route_event(event_id, proposal, snapshot)
    assert duplicate.status == "FILLED"
    assert duplicate.duplicate
    substituted = runtime.route_event(
        event_id,
        replace(proposal, entry_price=proposal.entry_price + Decimal("0.01")),
        snapshot,
    )
    assert substituted.status == "REJECTED"
    assert "content" in (substituted.reason or "").lower()
    valuation = runtime.portfolio_valuation()
    assert valuation["simulated"] is True
    assert Decimal(str(valuation["cash"])) + Decimal(str(valuation["market_value"])) == Decimal(
        str(valuation["equity"])
    )

    next_quotes = list(_provider((instrument_id,), count=5))[2:]
    results = [runtime.process_quote(quote) for quote in next_quotes]
    exit_results = [item.exit_result for item in results if item.exit_result is not None]
    assert len(exit_results) == 1
    assert exit_results[0].status == "FILLED"
    with closing(get_connection(database)) as connection:
        exit_events = connection.execute(
            """SELECT proposal_binding_state, proposal_sha256, proposal_json
               FROM forward_scheduler_events WHERE proposal_id LIKE 'stop-target-%'"""
        ).fetchall()
        orders = connection.execute(
            "SELECT side, status FROM paper_orders WHERE account_id = ? ORDER BY created_at, side",
            (runtime.account_id,),
        ).fetchall()
    assert len(exit_events) == 1
    assert exit_events[0]["proposal_binding_state"] == "BOUND"
    assert exit_events[0]["proposal_sha256"] is not None
    assert exit_events[0]["proposal_json"] is not None
    assert len(orders) == 2
    assert {row["side"] for row in orders} == {"BUY", "SELL"}
    assert all(row["status"] == "FILLED" for row in orders)
    assert runtime.portfolio_valuation()["market_value"] == "0"


def test_direct_scheduler_event_inserts_require_authorization(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    with closing(get_connection(database)) as connection:
        row = connection.execute(
            "SELECT * FROM forward_scheduler_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        legitimate = _event_write_from_row(row)
        assert legitimate.proposal_binding_state == "UNBOUND"
        for state in ("UNBOUND", "BOUND"):
            forged_id = f"forged-{state.lower()}"
            proposal_id = f"synthetic-{forged_id[:40]}"
            proposal_json = (
                json.dumps({"proposal_id": proposal_id}, sort_keys=True, separators=(",", ":"))
                if state == "BOUND"
                else None
            )
            forged = replace(
                legitimate,
                event_id=forged_id,
                proposal_id=proposal_id,
                proposal_sha256=(
                    hashlib.sha256(proposal_json.encode()).hexdigest()
                    if proposal_json is not None
                    else None
                ),
                proposal_json=proposal_json,
                proposal_binding_state=state,
            )
            with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
                _insert_scheduler_event(connection, forged)
            connection.rollback()
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM forward_scheduler_events WHERE event_id = ?",
                    (forged_id,),
                ).fetchone()[0]
                == 0
            )


def test_event_write_authorization_is_exact_and_one_use(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    proposal_json = _proposal_canonical_json(proposal)
    proposal_hash = hashlib.sha256(proposal_json.encode("utf-8")).hexdigest()

    with closing(get_connection(database)) as connection:
        row = connection.execute(
            "SELECT * FROM forward_scheduler_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        base = _event_write_from_row(row)
        event_a_id = "authorized-event-a"
        event_a = replace(
            base,
            event_id=event_a_id,
            proposal_id=f"synthetic-{event_a_id[:40]}",
        )
        event_b_id = "authorized-event-b"
        event_b = replace(
            base,
            event_id=event_b_id,
            proposal_id=f"synthetic-{event_b_id[:40]}",
        )
        wrong_fields = replace(event_a, instrument_id=event_a.instrument_id + 1)

        stop_id = "authorized-stop-event"
        stop_proposal_id = f"stop-target-{stop_id}"
        stop_json = json.dumps(
            {"proposal_id": stop_proposal_id}, sort_keys=True, separators=(",", ":")
        )
        stop_write = replace(
            base,
            event_id=stop_id,
            proposal_id=stop_proposal_id,
            proposal_sha256=hashlib.sha256(stop_json.encode("utf-8")).hexdigest(),
            proposal_json=stop_json,
            proposal_binding_state="BOUND",
        )

        with database_module._authorize_scheduler_event_write(
            connection, "CREATE_SCHEDULER_EVENT", event_a
        ):
            with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
                _insert_scheduler_event(connection, event_b)
            with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
                _insert_scheduler_event(connection, wrong_fields)
            with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
                _insert_scheduler_event(connection, stop_write)
            _insert_scheduler_event(connection, event_a)
            with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
                _insert_scheduler_event(connection, event_a)
        connection.commit()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE event_id = ?",
                (event_a_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE event_id = ?",
                (event_b_id,),
            ).fetchone()[0]
            == 0
        )

        with database_module._authorize_scheduler_event_write(
            connection, "CREATE_STOP_TARGET_EXIT", stop_write
        ):
            with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
                _insert_scheduler_event(connection, event_b)
            _insert_scheduler_event(connection, stop_write)

        binding_write = replace(
            base,
            proposal_sha256=proposal_hash,
            proposal_json=proposal_json,
            proposal_binding_state="BOUND",
        )
        with (
            database_module._authorize_scheduler_event_write(
                connection, "BIND_PROPOSAL", binding_write
            ),
            pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"),
        ):
            _insert_scheduler_event(connection, binding_write)

        insert_event_id = "insert-auth-cannot-bind"
        insert_write = replace(
            base,
            event_id=insert_event_id,
            proposal_id=f"synthetic-{insert_event_id[:40]}",
        )
        with (
            database_module._authorize_scheduler_event_write(
                connection, "CREATE_SCHEDULER_EVENT", insert_write
            ),
            pytest.raises(sqlite3.IntegrityError, match="proposal binding"),
        ):
            connection.execute(
                """UPDATE forward_scheduler_events
                   SET proposal_sha256 = ?, proposal_json = ?,
                       proposal_binding_state = 'BOUND'
                   WHERE event_id = ?""",
                (proposal_hash, proposal_json, event_id),
            )

        bad_hash_write = replace(binding_write, proposal_sha256="0" * 64)
        with (
            pytest.raises(ValueError, match="invalid proposal fields"),
            database_module._authorize_scheduler_event_write(
                connection, "BIND_PROPOSAL", bad_hash_write
            ),
        ):
            pytest.fail("Invalid proposal hash unexpectedly received authorization.")

        noncanonical_json = json.dumps(json.loads(proposal_json), indent=2)
        noncanonical_write = replace(
            binding_write,
            proposal_json=noncanonical_json,
            proposal_sha256=hashlib.sha256(noncanonical_json.encode("utf-8")).hexdigest(),
        )
        with (
            pytest.raises(ValueError, match="invalid proposal fields"),
            database_module._authorize_scheduler_event_write(
                connection, "BIND_PROPOSAL", noncanonical_write
            ),
        ):
            pytest.fail("Noncanonical proposal JSON unexpectedly received authorization.")


def test_event_write_authorization_clears_after_errors_and_rollback(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]

    with closing(get_connection(database)) as connection:
        row = connection.execute(
            "SELECT * FROM forward_scheduler_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        base = _event_write_from_row(row)
        expected_id = "exception-authorized-event"
        expected = replace(
            base,
            event_id=expected_id,
            proposal_id=f"synthetic-{expected_id[:40]}",
        )
        other_id = "exception-other-event"
        other = replace(base, event_id=other_id, proposal_id=f"synthetic-{other_id[:40]}")

        with (
            pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"),
            database_module._authorize_scheduler_event_write(
                connection, "CREATE_SCHEDULER_EVENT", expected
            ),
        ):
            _insert_scheduler_event(connection, other)
        assert connection._event_write_capability is None
        with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
            _insert_scheduler_event(connection, expected)
        connection.rollback()

        rolled_back_id = "rolled-back-authorized-event"
        rolled_back = replace(
            base,
            event_id=rolled_back_id,
            proposal_id=f"synthetic-{rolled_back_id[:40]}",
        )
        connection.execute("BEGIN IMMEDIATE")
        with database_module._authorize_scheduler_event_write(
            connection, "CREATE_SCHEDULER_EVENT", rolled_back
        ):
            _insert_scheduler_event(connection, rolled_back)
        assert connection._event_write_capability is None
        connection.rollback()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE event_id = ?",
                (rolled_back_id,),
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(sqlite3.IntegrityError, match="scheduler event insert"):
            _insert_scheduler_event(connection, rolled_back)
        connection.rollback()


def test_proposal_binding_requires_route_authorization_and_is_immutable(
    tmp_path: Path,
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])

    with closing(get_connection(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="proposal binding"):
            connection.execute(
                """UPDATE forward_scheduler_events
                   SET proposal_sha256 = ?, proposal_json = ?,
                       proposal_binding_state = 'BOUND'
                   WHERE event_id = ?""",
                ("0" * 64, '{"substituted":true}', event_id),
            )
        connection.rollback()
        event = connection.execute(
            """SELECT proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
    assert tuple(event) == (None, None, "UNBOUND")

    result = runtime.route_event(event_id, proposal, snapshot)
    assert result.status == "FILLED"
    with closing(get_connection(database)) as connection:
        bound_event = connection.execute(
            """SELECT proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="proposal binding"):
            connection.execute(
                """UPDATE forward_scheduler_events SET proposal_binding_state = 'UNBOUND'
                   WHERE event_id = ?""",
                (event_id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="proposal binding"):
            connection.execute(
                """UPDATE forward_scheduler_events SET proposal_sha256 = ?
                   WHERE event_id = ?""",
                ("f" * 64, event_id),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="proposal binding"):
            connection.execute(
                """UPDATE forward_scheduler_events SET proposal_json = ?
                   WHERE event_id = ?""",
                ('{"substituted":true}', event_id),
            )
        connection.rollback()
        unchanged = connection.execute(
            """SELECT proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
    assert tuple(unchanged) == tuple(bound_event)
    duplicate = runtime.route_event(event_id, proposal, snapshot)
    assert duplicate.status == "FILLED"
    assert duplicate.duplicate


def test_v11_unbound_pending_event_fails_closed_after_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    latest_schema_version = database_module.SCHEMA_VERSION
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 11)
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    legacy_event_details: list[tuple[int, str, str]] = []

    def capture_legacy_event(
        scheduled_instrument_id: int, interval: str, candle_timestamp: str
    ) -> str:
        legacy_event_details.append((scheduled_instrument_id, interval, candle_timestamp))
        return runtime._event_id(scheduled_instrument_id, interval, candle_timestamp)

    monkeypatch.setattr(runtime, "schedule_event", capture_legacy_event)
    runtime.process_quote(quotes[0])
    scheduled = runtime.process_quote(quotes[1]).scheduled_event_ids
    assert len(scheduled) == 1
    event_id = scheduled[0]
    scheduled_instrument_id, interval, candle_timestamp = legacy_event_details[0]
    decision_time = datetime.fromisoformat(candle_timestamp) + timedelta(minutes=1)
    with closing(get_connection(database)) as connection:
        connection.execute(
            """INSERT INTO forward_scheduler_events
               (event_id, runtime_id, instrument_id, interval, candle_timestamp,
                decision_timestamp, status, proposal_id, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, NULL, ?)""",
            (
                event_id,
                runtime.runtime_id,
                scheduled_instrument_id,
                interval,
                candle_timestamp,
                decision_time.isoformat().replace("+00:00", "Z"),
                f"synthetic-{event_id[:40]}",
                decision_time.isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.commit()
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])

    monkeypatch.setattr(database_module, "SCHEMA_VERSION", latest_schema_version)
    initialize_database(database)

    with closing(get_connection(database)) as connection:
        for binding_state in ("UNBOUND", "BOUND"):
            with pytest.raises(sqlite3.IntegrityError, match="proposal binding"):
                connection.execute(
                    """UPDATE forward_scheduler_events SET proposal_binding_state = ?
                       WHERE event_id = ?""",
                    (binding_state, event_id),
                )
            connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="proposal binding"):
            connection.execute(
                """UPDATE forward_scheduler_events
                   SET proposal_sha256 = ?, proposal_json = ? WHERE event_id = ?""",
                ("f" * 64, '{"substituted":true}', event_id),
            )
        connection.rollback()
        pending_event = connection.execute(
            """SELECT status, proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        assert tuple(pending_event) == ("PENDING", None, None, "LEGACY_UNVERIFIABLE")

    result = runtime.route_event(event_id, proposal, snapshot)

    assert result.status == "REJECTED"
    assert "legacy" in (result.reason or "").lower()
    with closing(get_connection(database)) as connection:
        event = connection.execute(
            """SELECT status, proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        assert tuple(event) == ("REJECTED", None, None, "LEGACY_UNVERIFIABLE")
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

    duplicate = runtime.route_event(event_id, proposal, snapshot)
    assert duplicate.status == "REJECTED"
    assert duplicate.duplicate


def _digest_fixture() -> DecisionProposal:
    return DecisionProposal(
        proposal_id="digest-proposal",
        instrument_id=1,
        action=DecisionAction.BUY,
        quantity=1,
        entry_price=Decimal("100.05"),
        stop_loss=Decimal("99.05"),
        target_price=Decimal("101.05"),
        confidence=Decimal("0.8"),
        rationale="digest fixture",
        timeframe="1m",
        decision_timestamp="2026-01-05T09:16:00.000000+05:30",
        submitted_at="2026-01-05T09:16:00.100000+05:30",
        source="SYNTHETIC_TEST",
        evidence_key="evidence-a",
        expires_at="2026-01-05T09:16:00.900000+05:30",
    )


def test_proposal_digest_preserves_subsecond_timestamps_and_expiry() -> None:
    proposal = _digest_fixture()

    one_microsecond_later = replace(proposal, decision_timestamp="2026-01-05T09:16:00.000001+05:30")
    expiry_in_same_second = replace(proposal, expires_at="2026-01-05T09:16:00.400000+05:30")

    assert _proposal_sha256(proposal) != _proposal_sha256(one_microsecond_later)
    assert _proposal_sha256(proposal) != _proposal_sha256(expiry_in_same_second)
    assert _proposal_sha256(proposal) == _proposal_sha256(
        replace(proposal, expires_at="2026-01-05T03:46:00.900000Z")
    )
    assert _proposal_sha256(proposal) == _proposal_sha256(
        replace(proposal, entry_price=Decimal("100.050"))
    )
    assert _proposal_sha256(replace(proposal, evidence_key=None)) != _proposal_sha256(
        replace(proposal, evidence_key="None")
    )
    assert '"canonicalization_version":"aegis.decision-proposal/v1"' in _proposal_canonical_json(
        proposal
    )


@pytest.mark.parametrize(
    "change",
    [
        {"proposal_id": "different-proposal"},
        {"instrument_id": 2},
        {"action": DecisionAction.SELL, "stop_loss": Decimal(101), "target_price": Decimal(99)},
        {"quantity": 2},
        {"entry_price": Decimal("100.06")},
        {"stop_loss": Decimal("99.06")},
        {"target_price": Decimal("101.06")},
        {"confidence": Decimal("0.81")},
        {"timeframe": "5m"},
        {"decision_timestamp": "2026-01-05T09:16:00.000001+05:30"},
        {"submitted_at": "2026-01-05T09:16:00.100001+05:30"},
        {"expires_at": "2026-01-05T09:16:00.900001+05:30"},
        {"evidence_key": "evidence-b"},
        {"source": "OTHER_SOURCE"},
        {"rationale": "changed rationale"},
    ],
)
def test_proposal_digest_binds_each_validation_field(change: dict[str, object]) -> None:
    proposal = _digest_fixture()

    assert _proposal_sha256(proposal) != _proposal_sha256(replace(proposal, **change))


@pytest.mark.parametrize("restart", [False, True])
def test_subsecond_expiry_substitution_is_rejected_after_event_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restart: bool
) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = replace(
        runtime.build_mock_proposal(event_id, "BUY", 1),
        expires_at="2026-01-05T09:16:00.400000+05:30",
    )
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])

    def interrupt_evidence(*args, **kwargs):
        raise RuntimeError("interruption after digest persistence")

    monkeypatch.setattr(runtime, "prepare_evidence", interrupt_evidence)
    with pytest.raises(RuntimeError, match="digest persistence"):
        runtime.route_event(event_id, proposal, snapshot)
    monkeypatch.undo()

    if restart:
        runtime = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    substituted = replace(proposal, expires_at="2026-01-05T09:16:00.900000+05:30")

    result = runtime.route_event(event_id, substituted, snapshot)

    assert result.status == "REJECTED"
    assert "content" in (result.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)


def test_buy_deviation_limit_fails_closed_before_order_creation(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = replace(
        runtime.build_mock_proposal(event_id, "BUY", 1),
        entry_price=Decimal(5000),
    )

    result = runtime.route_event(
        event_id, proposal, runtime.execution_snapshot(instrument_id, quotes[1])
    )

    assert result.status == "REJECTED"
    assert "deviation limit" in (result.reason or "").lower()
    with closing(get_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_expired_proposal_is_rejected_before_order_creation(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = replace(
        runtime.build_mock_proposal(event_id, "BUY", 1),
        expires_at="2026-01-05T09:16:30+05:30",
    )
    evidence = runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:16:00+05:30")
    engine = ForwardPaperTradingEngine(
        database,
        runtime.account_id,
        calendars=runtime.calendars,
        max_execution_price_deviation=Decimal("0.05"),
    )

    validation = engine.validate_decision_proposal(
        proposal,
        evidence,
        evaluation_time="2026-01-05T09:17:00+05:30",
    )

    assert not validation.valid
    assert "expired" in (validation.reason or "").lower()
    with closing(get_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0


def test_forward_route_rejects_timeframe_mismatch_without_execution(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = replace(runtime.build_mock_proposal(event_id, "BUY", 1), timeframe="5m")

    result = runtime.route_event(
        event_id, proposal, runtime.execution_snapshot(instrument_id, quotes[1])
    )

    assert result.status == "REJECTED"
    assert "timeframe" in (result.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)


def test_forward_route_requires_explicit_expiry_without_execution(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = replace(runtime.build_mock_proposal(event_id, "BUY", 1), expires_at=None)

    result = runtime.route_event(
        event_id, proposal, runtime.execution_snapshot(instrument_id, quotes[1])
    )

    assert result.status == "REJECTED"
    assert "explicit" in (result.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)


def test_proposal_expiry_uses_timezone_equivalence_and_exact_boundary(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    evidence = runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:16:00+05:30")
    proposal = replace(
        runtime.build_mock_proposal(event_id, "BUY", 1),
        expires_at="2026-01-05T09:17:00+05:30",
    )
    engine = ForwardPaperTradingEngine(
        database,
        runtime.account_id,
        calendars=runtime.calendars,
        max_execution_price_deviation=Decimal("0.05"),
    )

    before_boundary = engine.validate_decision_proposal(
        proposal, evidence, evaluation_time="2026-01-05T03:46:59Z"
    )
    at_boundary = engine.validate_decision_proposal(
        proposal, evidence, evaluation_time="2026-01-05T03:47:00Z"
    )
    after_boundary = engine.validate_decision_proposal(
        proposal, evidence, evaluation_time="2026-01-05T09:17:01+05:30"
    )

    assert before_boundary.valid
    assert not at_boundary.valid
    assert not after_boundary.valid
    assert "expired" in (at_boundary.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)


@pytest.mark.parametrize(
    "expiry",
    ["2026-01-05T09:17:00", "not-a-timestamp"],
)
def test_proposal_rejects_naive_or_malformed_expiry(tmp_path: Path, expiry: str) -> None:
    _, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    quote = quotes[1]
    event_id = runtime.process_quote(quote).scheduled_event_ids[0]
    with pytest.raises(ProposalValidationError, match="expires_at"):
        replace(runtime.build_mock_proposal(event_id, "BUY", 1), expires_at=expiry)


def test_public_router_rejects_future_runtime_evidence_without_execution(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    evidence = runtime.prepare_evidence(instrument_id, "1m", "2026-01-05T09:16:00+05:30")
    future_evidence = replace(
        evidence,
        as_of="2026-01-05T09:17:00+05:30",
        runtime_id=None,
        evidence_id=None,
    )
    engine = ForwardPaperTradingEngine(
        database,
        runtime.account_id,
        calendars=runtime.calendars,
        max_execution_price_deviation=Decimal("0.05"),
    )

    result = engine.route_proposal(
        proposal,
        evidence=future_evidence,
        execution_snapshot=runtime.execution_snapshot(instrument_id, quotes[1]),
        evaluation_time="2026-01-05T09:16:00+05:30",
    )

    assert result.status == "REJECTED"
    assert "future-dated" in (result.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)


def test_public_router_rejects_evidence_labeled_for_another_runtime(
    tmp_path: Path,
) -> None:
    database, calendars, instruments, runtime = _setup(tmp_path)
    other_instrument = register_instrument(
        database,
        InstrumentInput(
            "NSE",
            "OTHER",
            "Other synthetic fixture",
            "EQUITY",
            segment="NSE",
            tick_size=Decimal("0.05"),
        ),
    )
    other = ForwardRuntimeService.create(
        database,
        f"{runtime.runtime_id}-other",
        runtime.account_id,
        (other_instrument.id,),
        calendars,
        max_quote_delay_seconds=60,
        degraded_after_seconds=10,
        stale_after_seconds=180,
        max_execution_price_deviation=Decimal("0.05"),
        created_at=_START.isoformat(),
    )
    quotes = list(_provider(instruments, count=2))
    for quote in quotes:
        runtime.process_quote(quote)
    with closing(get_connection(database)) as connection:
        event_id = connection.execute(
            """SELECT event_id FROM forward_scheduler_events
               WHERE runtime_id = ? ORDER BY decision_timestamp LIMIT 1""",
            (runtime.runtime_id,),
        ).fetchone()["event_id"]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    evidence = runtime.prepare_evidence(
        instruments[0],
        "1m",
        proposal.decision_timestamp,
        event_id=event_id,
        proposal_id=proposal.proposal_id,
    )
    mislabeled = replace(evidence, runtime_id=other.runtime_id)
    engine = ForwardPaperTradingEngine(
        database,
        runtime.account_id,
        calendars=calendars,
        max_execution_price_deviation=Decimal("0.05"),
    )

    result = engine.route_proposal(
        proposal,
        evidence=mislabeled,
        execution_snapshot=runtime.execution_snapshot(instruments[0], quotes[1]),
        evaluation_time=proposal.decision_timestamp,
    )

    assert result.status == "REJECTED"
    assert "runtime evidence" in (result.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)


def test_proposal_substitution_and_direct_phase8_runtime_submission_fail_closed(
    tmp_path: Path,
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])
    substituted = replace(proposal, proposal_id="substituted-proposal")

    rejected = runtime.route_event(event_id, substituted, snapshot)
    assert rejected.status == "REJECTED"
    assert "identity" in (rejected.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)

    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        1,
        proposal.decision_timestamp,
        proposal.submitted_at,
    )
    with pytest.raises(PaperTradingError, match="Forward execution"):
        submit_paper_order(
            database,
            runtime.account_id,
            "direct-forward-binding",
            request,
            snapshot,
            forward_event_id=event_id,
        )
    with pytest.raises(PaperTradingError, match="generic paper-order path"):
        submit_paper_order(
            database,
            runtime.account_id,
            "direct-forward-unbound",
            request,
            snapshot,
        )
    _assert_no_paper_execution(database, runtime.account_id)


def test_direct_runtime_paths_cannot_authorize_forged_or_disconnected_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])

    def interrupt_evidence(*args, **kwargs):
        raise RuntimeError("leave a bound pending event")

    with monkeypatch.context() as interruption:
        interruption.setattr(runtime, "prepare_evidence", interrupt_evidence)
        with pytest.raises(RuntimeError, match="bound pending event"):
            runtime.route_event(event_id, proposal, snapshot)
    _set_feed_record(database, runtime, instrument_id, status="DISCONNECTED")

    forged_snapshot = replace(
        snapshot,
        available_quantity=snapshot.available_quantity + 10,
    )
    evidence = runtime.prepare_evidence(
        instrument_id,
        "1m",
        proposal.decision_timestamp,
        event_id=event_id,
        proposal_id=proposal.proposal_id,
    )
    engine = ForwardPaperTradingEngine(
        database,
        runtime.account_id,
        calendars=runtime.calendars,
        max_execution_price_deviation=Decimal("0.05"),
    )
    result = engine._route_runtime_proposal(
        proposal,
        evidence,
        execution_snapshot=forged_snapshot,
        runtime_id=runtime.runtime_id,
        event_id=event_id,
        evaluation_time=snapshot.as_of,
    )
    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        1,
        proposal.decision_timestamp,
        proposal.submitted_at,
    )

    assert result.status == "REJECTED"
    assert "feed" in (result.reason or "").lower() or "quote" in (result.reason or "").lower()
    with pytest.raises(PaperTradingError, match="feed"):
        submit_paper_order(
            database,
            runtime.account_id,
            "direct-disconnected-forward",
            request,
            forged_snapshot,
            forward_event_id=event_id,
        )
    assert not hasattr(paper_trading_module, "_FORWARD_RUNTIME_AUTHORITY")
    _assert_no_paper_execution(database, runtime.account_id)
    report = get_paper_account_report(database, runtime.account_id)
    assert report["available_cash"] == "100000"
    assert report["positions"] == []
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_execution_authorizations WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
            == 0
        )


def test_forward_order_and_authorization_rollback_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])
    request = PaperOrderRequest(
        instrument_id,
        "BUY",
        1,
        proposal.decision_timestamp,
        proposal.submitted_at,
    )

    def fail_order_event(*args, **kwargs):
        raise RuntimeError("injected Phase 8 transaction failure")

    with monkeypatch.context() as transaction_failure:
        transaction_failure.setattr(paper_trading_module, "_insert_order_event", fail_order_event)
        with pytest.raises(RuntimeError, match="Phase 8 transaction failure"):
            runtime.route_event(event_id, proposal, snapshot)

    with closing(get_connection(database)) as connection:
        authorization = connection.execute(
            """SELECT status, consumed_order_id FROM forward_execution_authorizations
               WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        assert tuple(authorization) == ("AUTHORIZED", None)
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM paper_execution_snapshot_uses").fetchone()[0]
            == 0
        )

    _set_feed_record(database, runtime, instrument_id, status="DISCONNECTED")
    with pytest.raises(PaperTradingError, match="healthy"):
        submit_paper_order(
            database,
            runtime.account_id,
            proposal.proposal_id,
            request,
            snapshot,
            forward_event_id=event_id,
        )
    _set_feed_record(database, runtime, instrument_id, status="HEALTHY")

    wrong_account = create_paper_account(
        database,
        name="wrong-forward-account",
        initial_cash=Decimal(100000),
        risk_limits=_LIMITS,
        costs=zero_cost_assumptions(),
        created_at=_START.isoformat(),
    )
    with pytest.raises(PaperTradingError, match="pending event"):
        submit_paper_order(
            database,
            wrong_account,
            proposal.proposal_id,
            request,
            snapshot,
            forward_event_id=event_id,
        )
    with pytest.raises(PaperTradingError, match="canonical proposal"):
        submit_paper_order(
            database,
            runtime.account_id,
            "substituted-proposal",
            request,
            snapshot,
            forward_event_id=event_id,
        )
    with pytest.raises(PaperTradingError, match="pending event"):
        submit_paper_order(
            database,
            runtime.account_id,
            proposal.proposal_id,
            request,
            snapshot,
            forward_event_id="another-event",
        )
    with pytest.raises(PaperTradingError, match="pending event"):
        submit_paper_order(
            database,
            runtime.account_id,
            proposal.proposal_id,
            replace(request, instrument_id=instrument_id + 1),
            snapshot,
            forward_event_id=event_id,
        )
    with pytest.raises(PaperTradingError, match="snapshot"):
        submit_paper_order(
            database,
            runtime.account_id,
            proposal.proposal_id,
            request,
            replace(snapshot, available_quantity=snapshot.available_quantity + 1),
            forward_event_id=event_id,
        )
    with pytest.raises(PaperTradingError, match="snapshot"):
        submit_paper_order(
            database,
            runtime.account_id,
            proposal.proposal_id,
            request,
            replace(
                snapshot,
                quality=replace(
                    snapshot.quality,
                    reasons=snapshot.quality.reasons + ("tampered quality content",),
                ),
            ),
            forward_event_id=event_id,
        )

    with closing(get_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM paper_execution_snapshot_uses").fetchone()[0]
            == 0
        )

    result = runtime.route_event(event_id, proposal, snapshot)

    assert result.status == "FILLED"
    with closing(get_connection(database)) as connection:
        authorization = connection.execute(
            """SELECT status, consumed_order_id FROM forward_execution_authorizations
               WHERE event_id = ?""",
            (event_id,),
        ).fetchone()
        assert authorization["status"] == "CONSUMED"
        assert authorization["consumed_order_id"] == result.order_id
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1


def test_forward_order_rechecks_calendar_inside_order_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])
    calendar_path = calendars.configuration_path
    assert calendar_path is not None
    original_submit = forward_paper_trading_module.submit_paper_order

    def replace_calendar_then_submit(*args, **kwargs):
        configuration = json.loads(calendar_path.read_text(encoding="utf-8"))
        configuration["calendars"][0]["confidence"] = "CONFIGURED"
        calendar_path.write_text(json.dumps(configuration), encoding="utf-8")
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(
        forward_paper_trading_module,
        "submit_paper_order",
        replace_calendar_then_submit,
    )

    result = runtime.route_event(event_id, proposal, snapshot)

    assert result.status == "REJECTED"
    assert "current approved-calendar" in (result.reason or "").lower()
    with closing(get_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_ledger_entries").fetchone()[0] == 0
        order = connection.execute(
            "SELECT status, reason FROM paper_orders WHERE account_id = ?",
            (runtime.account_id,),
        ).fetchone()
    assert order is not None
    assert order["status"] == "REJECTED"
    assert "approved-calendar" in order["reason"].lower()


def test_concurrent_forward_event_submission_creates_one_order(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: runtime.route_event(event_id, proposal, snapshot),
                range(2),
            )
        )

    assert all(result.status == "FILLED" for result in results), results
    assert len({result.order_id for result in results}) == 1
    with closing(get_connection(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1


def test_proposal_payload_cannot_be_substituted_after_event_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    snapshot = runtime.execution_snapshot(instrument_id, quotes[1])

    def interrupt_evidence(*args, **kwargs):
        raise RuntimeError("injected interruption after event binding")

    monkeypatch.setattr(runtime, "prepare_evidence", interrupt_evidence)
    with pytest.raises(RuntimeError, match="after event binding"):
        runtime.route_event(event_id, proposal, snapshot)
    monkeypatch.undo()

    substituted = replace(proposal, entry_price=proposal.entry_price + Decimal("0.01"))
    result = runtime.route_event(event_id, substituted, snapshot)

    assert result.status == "REJECTED"
    assert "content" in (result.reason or "").lower()
    _assert_no_paper_execution(database, runtime.account_id)


def test_restart_recovers_missing_scheduler_event_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=3))
    runtime.process_quote(quotes[0])

    def fail_event_creation(*args, **kwargs):
        raise RuntimeError("injected crash after candle commit")

    monkeypatch.setattr(runtime, "schedule_event", fail_event_creation)
    with pytest.raises(RuntimeError, match="injected crash"):
        runtime.process_quote(quotes[1])
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM forward_runtime_candles
               WHERE runtime_id = ? AND instrument_id = ? AND interval = '1m'""",
                (runtime.runtime_id, instrument_id),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE runtime_id = ?",
                (runtime.runtime_id,),
            ).fetchone()[0]
            == 0
        )

    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)

    with closing(get_connection(database)) as connection:
        events = connection.execute(
            """SELECT event_id, proposal_id, status FROM forward_scheduler_events
               WHERE runtime_id = ?""",
            (runtime.runtime_id,),
        ).fetchall()
    assert len(events) == 1
    assert events[0]["proposal_id"] == f"synthetic-{events[0]['event_id'][:40]}"
    assert events[0]["status"] == "PENDING"
    assert restored.recover_scheduler_events() == ()


def test_partial_direct_sell_keeps_remaining_position_protected(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = [replace(quote, quantity=2) for quote in _provider((instrument_id,), count=3)]
    runtime.process_quote(quotes[0])
    buy_event = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    buy = runtime.build_mock_proposal(
        buy_event, "BUY", 2, stop_loss=Decimal(99), target_price=Decimal(110)
    )
    assert (
        runtime.route_event(
            buy_event, buy, runtime.execution_snapshot(instrument_id, quotes[1])
        ).status
        == "FILLED"
    )

    sell_event = runtime.process_quote(quotes[2]).scheduled_event_ids[0]
    sell = runtime.build_mock_proposal(sell_event, "SELL", 1)
    result = runtime.route_event(
        sell_event, sell, runtime.execution_snapshot(instrument_id, quotes[2])
    )

    assert result.status == "FILLED"
    with closing(get_connection(database)) as connection:
        position = connection.execute(
            "SELECT quantity FROM paper_positions WHERE account_id = ? AND instrument_id = ?",
            (runtime.account_id, instrument_id),
        ).fetchone()
        control = connection.execute(
            """SELECT active, stop_loss, target_price FROM forward_trade_controls
               WHERE runtime_id = ? AND instrument_id = ?""",
            (runtime.runtime_id, instrument_id),
        ).fetchone()
    assert position["quantity"] == 1
    assert control["active"] == 1
    assert control["stop_loss"] == "99"
    assert control["target_price"] == "110"


def test_stop_loss_exit_triggers_at_most_once(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = [
        _quote(instrument_id, "2026-01-05T09:15:00+05:30", 1, Decimal("100.05"), "stop-q1"),
        _quote(instrument_id, "2026-01-05T09:16:00+05:30", 2, Decimal("100.10"), "stop-q2"),
        _quote(instrument_id, "2026-01-05T09:17:00+05:30", 3, Decimal("100.00"), "stop-q3"),
        _quote(instrument_id, "2026-01-05T09:18:00+05:30", 4, Decimal("99.95"), "stop-q4"),
    ]
    runtime.process_quote(quotes[0])
    entry_event = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    entry = runtime.build_mock_proposal(
        entry_event, "BUY", 1, stop_loss=Decimal("100.00"), target_price=Decimal(110)
    )
    assert (
        runtime.route_event(
            entry_event, entry, runtime.execution_snapshot(instrument_id, quotes[1])
        ).status
        == "FILLED"
    )

    exit_results = [
        result.exit_result
        for result in (runtime.process_quote(quotes[2]), runtime.process_quote(quotes[3]))
        if result.exit_result is not None
    ]
    assert len(exit_results) == 1
    assert exit_results[0].status == "FILLED"
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE account_id = ? AND side = 'SELL'",
                (runtime.account_id,),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    ("stop_loss", "target_price", "trigger_price"),
    [
        (Decimal("100.00"), Decimal(110), Decimal("100.00")),
        (Decimal(90), Decimal("100.15"), Decimal("100.15")),
    ],
)
def test_rejected_stop_or_target_exit_recovers_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_loss: Decimal,
    target_price: Decimal,
    trigger_price: Decimal,
) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    quotes = [
        _quote(instrument_id, "2026-01-05T09:15:00+05:30", 1, Decimal("100.05"), "retry-q1"),
        _quote(instrument_id, "2026-01-05T09:16:00+05:30", 2, Decimal("100.10"), "retry-q2"),
        _quote(instrument_id, "2026-01-05T09:17:00+05:30", 3, trigger_price, "retry-q3"),
        _quote(instrument_id, "2026-01-05T09:18:00+05:30", 4, trigger_price, "retry-q4"),
        _quote(instrument_id, "2026-01-05T09:19:00+05:30", 5, trigger_price, "retry-q5"),
    ]
    _buy_runtime_position(
        runtime,
        instrument_id,
        quotes,
        stop_loss=stop_loss,
        target_price=target_price,
    )
    with closing(get_connection(database)) as connection:
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ? WHERE id = ?",
            (
                json.dumps(replace(_LIMITS, max_order_value=Decimal(1)).to_dict()),
                runtime.account_id,
            ),
        )
        connection.commit()

    original_submit = forward_runtime_module.submit_paper_order

    def interrupt_after_rejection(*args, **kwargs):
        result = original_submit(*args, **kwargs)
        assert result.status == "REJECTED"
        raise RuntimeError("simulated interruption after persisted rejection")

    monkeypatch.setattr(forward_runtime_module, "submit_paper_order", interrupt_after_rejection)
    with pytest.raises(RuntimeError, match="persisted rejection"):
        runtime.process_quote(quotes[2])
    monkeypatch.setattr(forward_runtime_module, "submit_paper_order", original_submit)

    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    with closing(get_connection(database)) as connection:
        control = connection.execute(
            """SELECT active, exit_event_id FROM forward_trade_controls
               WHERE runtime_id = ? AND instrument_id = ?""",
            (runtime.runtime_id, instrument_id),
        ).fetchone()
        connection.execute(
            "UPDATE paper_accounts SET risk_limits_json = ? WHERE id = ?",
            (json.dumps(_LIMITS.to_dict()), runtime.account_id),
        )
        connection.commit()
    assert control["active"] == 1
    assert control["exit_event_id"] is None

    results = [restored.process_quote(quote) for quote in quotes[3:]]
    exits = [result.exit_result for result in results if result.exit_result is not None]
    assert len(exits) == 1
    assert exits[0].status == "FILLED"
    with closing(get_connection(database)) as connection:
        orders = connection.execute(
            """SELECT status FROM paper_orders
               WHERE account_id = ? AND side = 'SELL'""",
            (runtime.account_id,),
        ).fetchall()
    assert sorted(row["status"] for row in orders) == ["FILLED", "REJECTED"]
    assert get_paper_account_report(database, runtime.account_id)["positions"] == []


def test_risk_halt_allows_only_a_valid_direct_long_reduction(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=4))
    _buy_runtime_position(runtime, instrument_id, quotes, stop_loss=None, target_price=None)
    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE paper_accounts SET halted = 1, halt_reason = 'Daily loss limit.',
               halt_class = 'RISK' WHERE id = ?""",
            (runtime.account_id,),
        )
        connection.commit()

    oversized_event = runtime.process_quote(quotes[2]).scheduled_event_ids[0]
    oversized = runtime.build_mock_proposal(oversized_event, "SELL", 2)
    rejected = runtime.route_event(
        oversized_event,
        oversized,
        runtime.execution_snapshot(instrument_id, quotes[2]),
    )
    assert rejected.status == "REJECTED"
    assert "position" in (rejected.reason or "").lower()

    valid_event = runtime.process_quote(quotes[3]).scheduled_event_ids[0]
    valid = runtime.build_mock_proposal(valid_event, "SELL", 1)
    filled = runtime.route_event(
        valid_event, valid, runtime.execution_snapshot(instrument_id, quotes[3])
    )
    assert filled.status == "FILLED"
    with closing(get_connection(database)) as connection:
        account = connection.execute(
            "SELECT halted, halt_reason FROM paper_accounts WHERE id = ?",
            (runtime.account_id,),
        ).fetchone()
    assert account["halted"] == 1
    assert account["halt_reason"] == "Daily loss limit."


def test_reconciliation_halt_prevents_automatic_exit_submission(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = [
        _quote(instrument_id, "2026-01-05T09:15:00+05:30", 1, Decimal("100.05"), "halt-q1"),
        _quote(instrument_id, "2026-01-05T09:16:00+05:30", 2, Decimal("100.10"), "halt-q2"),
        _quote(instrument_id, "2026-01-05T09:17:00+05:30", 3, Decimal("100.00"), "halt-q3"),
    ]
    _buy_runtime_position(
        runtime,
        instrument_id,
        quotes,
        stop_loss=Decimal("100.00"),
        target_price=Decimal(110),
    )
    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE paper_positions SET average_entry_price = '999'
               WHERE account_id = ? AND instrument_id = ?""",
            (runtime.account_id, instrument_id),
        )
        connection.commit()
    reconciliation = reconcile_paper_account(database, runtime.account_id)
    assert reconciliation["status"] == "FAILED"

    result = runtime.process_quote(quotes[2])
    assert result.exit_result is None
    with closing(get_connection(database)) as connection:
        sell_count = connection.execute(
            "SELECT COUNT(*) FROM paper_orders WHERE account_id = ? AND side = 'SELL'",
            (runtime.account_id,),
        ).fetchone()[0]
        account = connection.execute(
            "SELECT halted, halt_reason FROM paper_accounts WHERE id = ?",
            (runtime.account_id,),
        ).fetchone()
    assert sell_count == 0
    assert account["halted"] == 1
    assert "reconciliation" in account["halt_reason"].lower()


def test_quote_marks_change_without_mutating_ledger_and_survive_restart(tmp_path: Path) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    quotes = [
        _quote(instrument_id, "2026-01-05T09:15:00+05:30", 1, Decimal("100.05"), "mark-q1"),
        _quote(instrument_id, "2026-01-05T09:16:00+05:30", 2, Decimal("100.10"), "mark-q2"),
        _quote(instrument_id, "2026-01-05T09:17:00+05:30", 3, Decimal("99.90"), "mark-q3"),
        _quote(instrument_id, "2026-01-05T09:18:00+05:30", 4, Decimal("100.15"), "mark-q4"),
    ]
    _buy_runtime_position(runtime, instrument_id, quotes, stop_loss=None, target_price=None)
    with closing(get_connection(database)) as connection:
        fills_before = connection.execute(
            """SELECT COUNT(*) FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
               WHERE o.account_id = ?""",
            (runtime.account_id,),
        ).fetchone()[0]
        realized_before = connection.execute(
            "SELECT COUNT(*) FROM paper_realized_pnl WHERE account_id = ?",
            (runtime.account_id,),
        ).fetchone()[0]
        cash_before = get_paper_account_report(database, runtime.account_id)["available_cash"]

    runtime.process_quote(quotes[2])
    falling = get_paper_account_report(database, runtime.account_id)
    assert Decimal(falling["positions"][0]["mark_price"]) == Decimal("99.9")
    runtime.process_quote(quotes[3])
    rising = get_paper_account_report(database, runtime.account_id)
    assert Decimal(rising["positions"][0]["mark_price"]) == Decimal("100.15")
    assert rising["available_cash"] == cash_before
    assert Decimal(rising["unrealized_gross_pnl"]) == Decimal("0.05")

    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
               WHERE o.account_id = ?""",
                (runtime.account_id,),
            ).fetchone()[0]
            == fills_before
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_realized_pnl WHERE account_id = ?",
                (runtime.account_id,),
            ).fetchone()[0]
            == realized_before
        )
    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    valuation = restored.portfolio_valuation("2026-01-05T09:21:00+05:30")
    assert valuation["valuation_status"] == "STALE"
    assert valuation["marks"][0]["price_basis"] == "LAST_TRADE"
    assert Decimal(str(valuation["marks"][0]["mark_price"])) == Decimal("100.15")


def test_runtime_honors_halt_sell_quantity_and_risk_limits(tmp_path: Path) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=4))
    runtime.process_quote(quotes[0])
    runtime.process_quote(quotes[1])
    with closing(get_connection(database)) as connection:
        first_event = connection.execute(
            """SELECT event_id FROM forward_scheduler_events
               WHERE runtime_id = ? ORDER BY candle_timestamp LIMIT 1""",
            (runtime.runtime_id,),
        ).fetchone()["event_id"]
    proposal = runtime.build_mock_proposal(first_event, "SELL", 1)
    rejected = runtime.route_event(
        first_event, proposal, runtime.execution_snapshot(instrument_id, quotes[1])
    )
    assert rejected.status == "REJECTED"
    assert "position" in (rejected.reason or "").lower()

    risk_quote = replace(quotes[2], quantity=200)
    risk_result = runtime.process_quote(risk_quote)
    risk_event = risk_result.scheduled_event_ids[0]
    risk_proposal = runtime.build_mock_proposal(risk_event, "BUY", 200)
    risk_result = runtime.route_event(
        risk_event, risk_proposal, runtime.execution_snapshot(instrument_id, risk_quote)
    )
    assert risk_result.status == "REJECTED"
    assert "maximum order value" in (risk_result.reason or "").lower()

    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE paper_accounts SET halted = 1,
               halt_reason = 'Maximum daily loss breached.',
               halt_class = 'RISK' WHERE id = ?""",
            (runtime.account_id,),
        )
        connection.commit()
    halted_quote = quotes[3]
    halted_result = runtime.process_quote(halted_quote)
    other_event = halted_result.scheduled_event_ids[0]
    halted_proposal = runtime.build_mock_proposal(other_event, "BUY", 1)
    halted = runtime.route_event(
        other_event, halted_proposal, runtime.execution_snapshot(instrument_id, halted_quote)
    )
    assert halted.status == "REJECTED"
    assert halted.reason == "Maximum daily loss breached."


def test_restart_recovers_state_and_reconciliation_detects_corruption(tmp_path: Path) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    for quote in quotes:
        result = runtime.process_quote(quote)
    event_id = result.scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    runtime.route_event(event_id, proposal, runtime.execution_snapshot(instrument_id, quotes[-1]))
    restored = ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    duplicate = restored.route_event(
        event_id, proposal, restored.execution_snapshot(instrument_id, quotes[-1])
    )
    assert duplicate.duplicate
    assert restored.process_quote(quotes[-1]).duplicate

    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE paper_positions SET average_entry_price = '999'
               WHERE account_id = ? AND instrument_id = ?""",
            (runtime.account_id, instrument_id),
        )
        connection.commit()
    with pytest.raises(ForwardRuntimeError, match="reconciliation"):
        ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    with closing(get_connection(database)) as connection:
        account = connection.execute(
            "SELECT halted, halt_reason FROM paper_accounts WHERE id = ?",
            (runtime.account_id,),
        ).fetchone()
        state = connection.execute(
            "SELECT status FROM forward_runtimes WHERE runtime_id = ?", (runtime.runtime_id,)
        ).fetchone()
    assert account["halted"] == 1
    assert "reconciliation" in account["halt_reason"].lower()
    assert state["status"] == "HALTED"


def test_runtime_state_checksum_fails_closed(tmp_path: Path) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    runtime.process_quote(next(_provider((instrument_id,), count=1)))
    with closing(get_connection(database)) as connection:
        connection.execute(
            "UPDATE forward_runtimes SET state_json = '{}' WHERE runtime_id = ?",
            (runtime.runtime_id,),
        )
        connection.commit()
    with pytest.raises(ForwardRuntimeError, match="corrupted"):
        ForwardRuntimeService.open(database, runtime.runtime_id, calendars)
    rejected = runtime.process_quote(
        _quote(instrument_id, "invalid-time", 2, Decimal(0), "after-corruption")
    )
    assert rejected.reason == "Runtime is halted."
    with closing(get_connection(database)) as connection:
        assert (
            connection.execute(
                "SELECT status FROM forward_runtimes WHERE runtime_id = ?", (runtime.runtime_id,)
            ).fetchone()[0]
            == "HALTED"
        )


def test_runtime_creation_rejects_unreconciled_account(tmp_path: Path) -> None:
    database, calendars, (instrument_id,), runtime = _setup(tmp_path)
    quotes = list(_provider((instrument_id,), count=2))
    runtime.process_quote(quotes[0])
    event_id = runtime.process_quote(quotes[1]).scheduled_event_ids[0]
    proposal = runtime.build_mock_proposal(event_id, "BUY", 1)
    assert (
        runtime.route_event(
            event_id, proposal, runtime.execution_snapshot(instrument_id, quotes[1])
        ).status
        == "FILLED"
    )
    with closing(get_connection(database)) as connection:
        connection.execute(
            """UPDATE paper_positions SET average_entry_price = '999'
               WHERE account_id = ? AND instrument_id = ?""",
            (runtime.account_id, instrument_id),
        )
        connection.commit()

    with pytest.raises(ForwardRuntimeError, match="reconciliation"):
        ForwardRuntimeService.create(
            database,
            "runtime-unreconciled-account",
            runtime.account_id,
            (instrument_id,),
            calendars,
            max_execution_price_deviation=Decimal("0.05"),
            created_at=_START.isoformat(),
        )


def test_dashboard_shows_runtime_status_without_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _, (instrument_id,), runtime = _setup(tmp_path)
    runtime.process_quote(next(_provider((instrument_id,), count=1)))
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(database))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(tmp_path / "runtime.log"))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=20)

    assert not app.exception
    assert any("Synthetic Forward Runtime — Read Only" in item.value for item in app.subheader)
    assert any("DETERMINISTIC_SYNTHETIC" in str(item.value) for item in app.dataframe)
    assert not any("submit simulated order" in item.label.lower() for item in app.button)
