"""Synthetic tests for trusted M2 batch qualification on a shared snapshot."""

import copy
import gc
import inspect
import json
import sqlite3
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis import backtesting, portfolio, portfolio_qualification
from aegis.database import get_connection, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import declare_import_source_use_status, import_ohlcv_csv
from aegis.portfolio import PortfolioPolicy, PortfolioRequest, SleeveRequest
from aegis.portfolio_qualification import (
    PortfolioSnapshotChangedError,
    _PortfolioQualification,
    _QualifiedBatch,
    _qualify_portfolio_request,
)
from aegis.strategy_lab import create_sma_crossover_spec

_DATES = (
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
)
_AS_OF = datetime(2026, 1, 8, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


@pytest.fixture
def qualification_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "synthetic.db"
    initialize_database(database_path)
    calendar_path = tmp_path / "approved-calendars.json"
    calendar_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "staleness_policy": {"potential_after_missing": 1, "stale_after_missing": 3},
                "calendars": [
                    {
                        "exchange": "NSE",
                        "segment": "NSE",
                        "timezone": "Asia/Kolkata",
                        "confidence": "CONFIRMED",
                        "coverage_start": "2026-01-05",
                        "coverage_end": "2026-01-08",
                        "source": "Synthetic test calendar",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": "15:30"}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(calendar_path))
    for symbol in ("TCS", "INFY"):
        register_instrument(
            database_path,
            InstrumentInput(
                "NSE",
                symbol,
                f"Synthetic {symbol}",
                "EQUITY",
                segment="NSE",
                lot_size=1,
                tick_size=Decimal("0.05"),
            ),
        )
    return database_path, calendar_path, tmp_path


def _csv_for(tmp_path: Path, symbol: str) -> Path:
    rows = ["timestamp,open,high,low,close,volume"]
    for index, trading_day in enumerate(_DATES, start=1):
        timestamp = datetime.combine(
            trading_day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata")
        ).isoformat()
        price = 100 + index
        rows.append(f"{timestamp},{price},{price + 2},{price - 2},{price},1000")
    path = tmp_path / f"{symbol}.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _import_symbol(
    database_path: Path,
    tmp_path: Path,
    symbol: str,
    *,
    declare_source_use: bool = True,
) -> None:
    imported = import_ohlcv_csv(
        database_path,
        _csv_for(tmp_path, symbol),
        "NSE",
        symbol,
        "1d",
    )
    assert imported.status == "COMPLETED"
    assert imported.accepted_rows == len(_DATES)
    if declare_source_use:
        declare_import_source_use_status(
            database_path,
            imported.import_id,
            "VERIFIED_PERMITTED",
            declared_by="synthetic test user",
            rationale="Synthetic test fixture declaration only.",
        )


def _request(
    *symbols: str,
    order_quantities: dict[str, int] | None = None,
    slow_windows: dict[str, int] | None = None,
) -> PortfolioRequest:
    sleeves = tuple(
        SleeveRequest(
            "NSE",
            symbol,
            create_sma_crossover_spec(
                exchange="NSE",
                trading_symbol=symbol,
                instrument_type="EQUITY",
                interval="1d",
                fast_window=2,
                slow_window=(slow_windows or {}).get(symbol, 3),
                hypothesis="Synthetic M2 qualification fixture.",
            ),
            (order_quantities or {}).get(symbol, 1),
        )
        for symbol in symbols
    )
    return PortfolioRequest(
        total_capital=Decimal("1000.00"),
        sleeves=sleeves,
        start_date=_DATES[0],
        end_date=_DATES[-1],
        as_of=_AS_OF,
        costs=backtesting.zero_cost_assumptions(),
        max_volume_participation=Decimal("0.5"),
        policy=PortfolioPolicy(),
    )


def test_batch_uses_one_readonly_transaction_and_one_calendar_instance(
    qualification_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    _import_symbol(database_path, tmp_path, "INFY")
    observed_connections: list[sqlite3.Connection] = []
    observed_calendars: list[object] = []
    observed_transaction_flags: list[bool] = []
    observed_query_only_flags: list[int] = []
    original_analyze = backtesting.analyze_dataset

    def observe_analyze(*args, **kwargs):
        connection = kwargs["connection"]
        observed_connections.append(connection)
        observed_calendars.append(kwargs["calendars"])
        observed_transaction_flags.append(connection.in_transaction)
        observed_query_only_flags.append(connection.execute("PRAGMA query_only").fetchone()[0])
        return original_analyze(*args, **kwargs)

    monkeypatch.setattr(backtesting, "analyze_dataset", observe_analyze)
    result = _qualify_portfolio_request(database_path, _request("INFY", "TCS"))

    assert result.status == "QUALIFIED"
    assert result.qualified_batch is not None
    assert len(observed_connections) == 2
    assert observed_connections[0] is observed_connections[1]
    assert observed_transaction_flags == [True, True]
    assert observed_query_only_flags == [1, 1]
    assert observed_calendars[0] is observed_calendars[1]
    assert result.qualified_batch.manifest.members[0].trading_symbol == "INFY"
    assert result.qualified_batch.manifest.members[1].trading_symbol == "TCS"
    assert [sleeve.member.sequence for sleeve in result.qualified_batch.sleeves] == [0, 1]


def test_batch_qualifies_all_sleeves_before_returning_and_never_simulates(
    qualification_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    _import_symbol(database_path, tmp_path, "INFY")
    qualified_symbols: list[str] = []
    original_qualify = backtesting._qualify_one_instrument

    def observe_qualification(*args, **kwargs):
        result = original_qualify(*args, **kwargs)
        qualified_symbols.append(result.instrument.trading_symbol)
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("M2-B must not generate signals or simulate execution.")

    monkeypatch.setattr(backtesting, "_qualify_one_instrument", observe_qualification)
    monkeypatch.setattr(backtesting, "generate_sma_crossover_signals", forbidden)
    monkeypatch.setattr(backtesting, "_simulate", forbidden)

    result = _qualify_portfolio_request(database_path, _request("TCS", "INFY"))

    assert result.status == "QUALIFIED"
    assert qualified_symbols == ["TCS", "INFY"]
    assert result.qualified_batch is not None
    assert len(result.qualified_batch.sleeves) == 2


def test_one_source_use_failure_blocks_batch_without_partial_qualified_sleeves(
    qualification_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    _import_symbol(database_path, tmp_path, "INFY", declare_source_use=False)
    attempted: list[str] = []
    original_qualify = backtesting._qualify_one_instrument

    def observe_qualification(*args, **kwargs):
        attempted.append(kwargs["trading_symbol"])
        return original_qualify(*args, **kwargs)

    monkeypatch.setattr(backtesting, "_qualify_one_instrument", observe_qualification)
    result = _qualify_portfolio_request(database_path, _request("TCS", "INFY"))

    assert result.status == "BLOCKED"
    assert result.qualified_batch is None
    assert attempted == ["TCS", "INFY"]
    assert len(result.block_reasons) == 1
    assert result.block_reasons[0].trading_symbol == "INFY"
    assert result.block_reasons[0].category == "SOURCE_USE"
    assert "recorded declaration" in result.block_reasons[0].reason


def test_batch_revalidates_trusted_request_and_accepts_no_external_evidence(
    qualification_setup,
) -> None:
    database_path, _, _ = qualification_setup
    request = _request("TCS")
    parameters = inspect.signature(_qualify_portfolio_request).parameters

    assert tuple(parameters) == ("database_path", "request")
    with pytest.raises(TypeError):
        _qualify_portfolio_request(database_path, request, calendars=object())


def test_database_change_during_qualification_cannot_return_qualified_batch(
    qualification_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    original_loader = backtesting._load_completed_candles
    changed = False

    def change_database_after_read(*args, **kwargs):
        nonlocal changed
        result = original_loader(*args, **kwargs)
        if not changed:
            changed = True
            with closing(get_connection(database_path)) as writer:
                writer.execute(
                    "UPDATE instruments SET instrument_name = ? WHERE trading_symbol = ?",
                    ("Changed after snapshot", "TCS"),
                )
                writer.commit()
        return result

    monkeypatch.setattr(backtesting, "_load_completed_candles", change_database_after_read)

    with pytest.raises(PortfolioSnapshotChangedError, match="database changed"):
        _qualify_portfolio_request(database_path, _request("TCS"))


def test_calendar_file_change_during_qualification_cannot_return_batch(
    qualification_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, calendar_path, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    original_analyze = backtesting.analyze_dataset
    changed = False

    def change_calendar_after_read(*args, **kwargs):
        nonlocal changed
        report = original_analyze(*args, **kwargs)
        if not changed:
            changed = True
            document = json.loads(calendar_path.read_text(encoding="utf-8"))
            document["calendars"][0]["source"] = "Changed during qualification"
            calendar_path.write_text(json.dumps(document), encoding="utf-8")
        return report

    monkeypatch.setattr(backtesting, "analyze_dataset", change_calendar_after_read)

    with pytest.raises(backtesting.BacktestError, match="changed after it was loaded"):
        _qualify_portfolio_request(database_path, _request("TCS"))


def test_identical_snapshots_produce_deterministic_manifest_and_fingerprint(
    qualification_setup,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    _import_symbol(database_path, tmp_path, "INFY")
    request = _request("INFY", "TCS")

    first = _qualify_portfolio_request(database_path, request)
    second = _qualify_portfolio_request(database_path, request)

    assert first.status == second.status == "QUALIFIED"
    assert first.qualified_batch is not None
    assert second.qualified_batch is not None
    assert first.qualified_batch.manifest == second.qualified_batch.manifest
    assert first.qualified_batch.fingerprint_sha256 == second.qualified_batch.fingerprint_sha256
    assert (
        first.qualified_batch.provenance_document() == second.qualified_batch.provenance_document()
    )


def test_portfolio_source_changes_are_bound_to_batch_provenance(
    qualification_setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path, _, _ = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    source_path = tmp_path / "portfolio_source.py"
    source_path.write_text("allocation_revision = 1\n", encoding="utf-8")
    monkeypatch.setattr(portfolio, "__file__", str(source_path))

    first = _qualify_portfolio_request(database_path, _request("TCS"))
    unchanged = _qualify_portfolio_request(database_path, _request("TCS"))
    assert first.qualified_batch is not None
    assert unchanged.qualified_batch is not None
    source_hash = first.qualified_batch.provenance_document()["implementation_source_sha256"][
        "portfolio_contracts_and_allocation"
    ]
    assert (
        source_hash
        == unchanged.qualified_batch.provenance_document()["implementation_source_sha256"][
            "portfolio_contracts_and_allocation"
        ]
    )
    assert first.qualified_batch.fingerprint_sha256 == unchanged.qualified_batch.fingerprint_sha256

    source_path.write_text("allocation_revision = 2\n", encoding="utf-8")
    changed = _qualify_portfolio_request(database_path, _request("TCS"))

    assert changed.qualified_batch is not None
    changed_provenance = changed.qualified_batch.provenance_document()
    assert (
        changed_provenance["implementation_source_sha256"]["portfolio_contracts_and_allocation"]
        != source_hash
    )
    assert changed.qualified_batch.fingerprint_sha256 != first.qualified_batch.fingerprint_sha256


def test_missing_portfolio_source_fingerprint_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(portfolio, "__file__", str(tmp_path / "missing_portfolio.py"))

    with pytest.raises(FileNotFoundError):
        portfolio_qualification._implementation_fingerprints()


def test_unreadable_portfolio_source_fingerprint_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = (tmp_path / "unreadable_portfolio.py").resolve()
    monkeypatch.setattr(portfolio, "__file__", str(source_path))
    original_hash_file = portfolio_qualification.strategy_lab._sha256_file

    def deny_source_read(path: Path) -> str:
        if path == source_path:
            raise PermissionError("Synthetic unreadable source.")
        return original_hash_file(path)

    monkeypatch.setattr(portfolio_qualification.strategy_lab, "_sha256_file", deny_source_read)

    with pytest.raises(PermissionError, match="Synthetic unreadable source"):
        portfolio_qualification._implementation_fingerprints()


@pytest.mark.parametrize(
    ("failure", "expected_category"),
    (
        ("missing_candle", "DATA_QUALITY"),
        ("invalid_candle", "DATA_QUALITY"),
        ("insufficient_history", "STRATEGY_HISTORY"),
        ("calendar_mapping", "CALENDAR"),
        ("lot_size", "INSTRUMENT"),
        ("qualification_exception", "DATA_QUALITY"),
    ),
)
def test_one_member_qualification_failure_blocks_entire_batch(
    qualification_setup,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_category: str,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    _import_symbol(database_path, tmp_path, "INFY")
    known_good = _qualify_portfolio_request(database_path, _request("TCS"))
    assert known_good.status == "QUALIFIED"
    assert known_good.qualified_batch is not None

    if failure == "missing_candle":
        with closing(get_connection(database_path)) as connection:
            row = connection.execute(
                """SELECT id FROM historical_candles
                   WHERE instrument_id = (
                       SELECT id FROM instruments WHERE exchange = 'NSE' AND trading_symbol = 'INFY'
                   )
                   ORDER BY candle_timestamp LIMIT 1 OFFSET 2"""
            ).fetchone()
            assert row is not None
            connection.execute("DELETE FROM historical_candles WHERE id = ?", (row["id"],))
            connection.commit()
    elif failure == "invalid_candle":
        with closing(get_connection(database_path)) as connection:
            row = connection.execute(
                """SELECT c.id FROM historical_candles AS c
                   JOIN instruments AS i ON i.id = c.instrument_id
                   WHERE i.exchange = 'NSE' AND i.trading_symbol = 'INFY'
                   ORDER BY c.candle_timestamp LIMIT 1"""
            ).fetchone()
            assert row is not None
            connection.execute(
                "UPDATE historical_candles SET close_decimal = '0' WHERE id = ?",
                (row["id"],),
            )
            connection.commit()
    elif failure == "calendar_mapping":
        with closing(get_connection(database_path)) as connection:
            connection.execute(
                "UPDATE instruments SET segment = ? WHERE exchange = ? AND trading_symbol = ?",
                ("NO_CALENDAR", "NSE", "INFY"),
            )
            connection.commit()
    elif failure == "lot_size":
        with closing(get_connection(database_path)) as connection:
            connection.execute(
                "UPDATE instruments SET lot_size = ? WHERE exchange = ? AND trading_symbol = ?",
                (2, "NSE", "INFY"),
            )
            connection.commit()

    attempted: list[str] = []
    original_qualify = backtesting._qualify_one_instrument

    def observe_qualification(*args, **kwargs):
        symbol = kwargs["trading_symbol"]
        attempted.append(symbol)
        if failure == "qualification_exception" and symbol == "INFY":
            raise backtesting._QualificationFailure(
                expected_category, "Injected member qualification failure."
            )
        return original_qualify(*args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("M2-B must not generate signals or execute a sleeve.")

    monkeypatch.setattr(backtesting, "_qualify_one_instrument", observe_qualification)
    monkeypatch.setattr(backtesting, "generate_sma_crossover_signals", forbidden)
    monkeypatch.setattr(backtesting, "_simulate", forbidden)
    monkeypatch.setattr(backtesting, "_execute_qualified_request", forbidden)

    request = _request(
        "TCS",
        "INFY",
        slow_windows={"INFY": 4} if failure == "insufficient_history" else None,
    )
    result = _qualify_portfolio_request(database_path, request)

    assert result.status == "BLOCKED"
    assert result.qualified_batch is None
    assert len(result.block_reasons) == 1
    assert result.block_reasons[0].trading_symbol == "INFY"
    assert result.block_reasons[0].category == expected_category
    if failure == "lot_size":
        assert attempted == []
    else:
        assert attempted == ["TCS", "INFY"]


def test_qualified_batch_constructors_validate_provenance_and_evidence(
    qualification_setup,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    result = _qualify_portfolio_request(database_path, _request("TCS"))
    assert result.qualified_batch is not None

    with pytest.raises(TypeError, match="trusted coordinator"):
        _QualifiedBatch(
            request=result.qualified_batch.request,
            allocation=result.qualified_batch.allocation,
            manifest=result.qualified_batch.manifest,
            sleeves=result.qualified_batch.sleeves,
            provenance_json=result.qualified_batch.provenance_json,
            fingerprint_sha256=result.qualified_batch.fingerprint_sha256,
            _token=None,
        )
    with pytest.raises(TypeError):
        _QualifiedBatch(
            request=result.qualified_batch.request,
            allocation=result.qualified_batch.allocation,
            manifest=result.qualified_batch.manifest,
            sleeves=result.qualified_batch.sleeves,
            provenance_json=result.qualified_batch.provenance_json,
            fingerprint_sha256=result.qualified_batch.fingerprint_sha256,
        )
    changed_provenance = json.loads(result.qualified_batch.provenance_json)
    changed_provenance["allocation"]["total_capital"] = "1"
    with pytest.raises(ValueError, match="fingerprint"):
        _QualifiedBatch(
            request=result.qualified_batch.request,
            allocation=result.qualified_batch.allocation,
            manifest=result.qualified_batch.manifest,
            sleeves=result.qualified_batch.sleeves,
            provenance_json=json.dumps(
                changed_provenance, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
            fingerprint_sha256=result.qualified_batch.fingerprint_sha256,
            _token=portfolio_qualification._BATCH_TOKEN,
        )
    with pytest.raises(ValueError, match="every request member"):
        replace(result.qualified_batch, sleeves=())
    with pytest.raises(TypeError, match="trusted coordinator"):
        _PortfolioQualification("QUALIFIED", (), result.qualified_batch, None)
    with pytest.raises(ValueError, match="Only a fully qualified outcome"):
        _PortfolioQualification("QUALIFIED", (), None, portfolio_qualification._BATCH_TOKEN)


def test_registered_coordinator_batch_is_accepted_and_unregistered_reuse_is_rejected(
    qualification_setup,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    result = _qualify_portfolio_request(database_path, _request("TCS"))
    assert result.qualified_batch is not None

    batch = result.qualified_batch
    assert portfolio_qualification._validate_coordinator_batch(batch) is batch
    assert portfolio_qualification._validate_coordinator_batch(batch) is batch
    assert portfolio_qualification._BATCH_REGISTRY[id(batch)]() is batch
    assert (
        _PortfolioQualification(
            "QUALIFIED", (), batch, portfolio_qualification._BATCH_TOKEN
        ).qualified_batch
        is batch
    )

    duplicate = _QualifiedBatch(
        request=batch.request,
        allocation=batch.allocation,
        manifest=batch.manifest,
        sleeves=batch.sleeves,
        provenance_json=batch.provenance_json,
        fingerprint_sha256=batch.fingerprint_sha256,
        _token=portfolio_qualification._BATCH_TOKEN,
    )
    with pytest.raises(ValueError, match="exact coordinator-registered batch"):
        portfolio_qualification._validate_coordinator_batch(duplicate)
    with pytest.raises(ValueError, match="exact coordinator-registered batch"):
        _PortfolioQualification("QUALIFIED", (), duplicate, portfolio_qualification._BATCH_TOKEN)
    copied = copy.copy(batch)
    assert copied is not batch
    with pytest.raises(ValueError, match="exact coordinator-registered batch"):
        portfolio_qualification._validate_coordinator_batch(copied)


def test_qualified_batch_remains_valid_after_more_than_64_registrations(
    qualification_setup,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    original = _qualify_portfolio_request(database_path, _request("TCS"))
    assert original.qualified_batch is not None
    batch = original.qualified_batch

    for _ in range(65):
        subsequent = _qualify_portfolio_request(database_path, _request("TCS"))
        assert subsequent.status == "QUALIFIED"
        assert subsequent.qualified_batch is not None

    assert portfolio_qualification._validate_coordinator_batch(batch) is batch
    assert (
        _PortfolioQualification(
            "QUALIFIED", (), batch, portfolio_qualification._BATCH_TOKEN
        ).qualified_batch
        is batch
    )


def test_batch_registration_is_released_when_no_longer_live(qualification_setup) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    result = _qualify_portfolio_request(database_path, _request("TCS"))
    assert result.qualified_batch is not None
    batch = result.qualified_batch
    batch_id = id(batch)
    batch_reference = weakref.ref(batch)
    copied = copy.copy(batch)

    del batch
    del result
    gc.collect()

    assert batch_reference() is None
    assert batch_id not in portfolio_qualification._BATCH_REGISTRY
    with pytest.raises(ValueError, match="exact coordinator-registered batch"):
        portfolio_qualification._validate_coordinator_batch(copied)


def test_recomputed_forged_evidence_is_rejected_even_with_reused_token(
    qualification_setup,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    result = _qualify_portfolio_request(database_path, _request("TCS"))
    assert result.qualified_batch is not None
    batch = result.qualified_batch
    forged_provenance = json.loads(batch.provenance_json)
    forged_provenance["sleeves"][0]["dataset_sha256"] = "0" * 64
    forged_json = portfolio_qualification._canonical_json(forged_provenance)
    forged_fingerprint = portfolio_qualification._canonical_hash(forged_provenance)

    with pytest.raises(ValueError, match="provenance does not match its frozen evidence"):
        _QualifiedBatch(
            request=batch.request,
            allocation=batch.allocation,
            manifest=batch.manifest,
            sleeves=batch.sleeves,
            provenance_json=forged_json,
            fingerprint_sha256=forged_fingerprint,
            _token=portfolio_qualification._BATCH_TOKEN,
        )


def test_concurrent_batch_registration_and_validation_preserve_live_batch(
    qualification_setup,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    active = _qualify_portfolio_request(database_path, _request("TCS")).qualified_batch
    assert active is not None

    registration_count = 3
    start = threading.Barrier(registration_count + 1)
    registrations_finished = threading.Event()
    remaining = registration_count
    remaining_lock = threading.Lock()

    def qualify_another_batch() -> _QualifiedBatch:
        nonlocal remaining
        start.wait(timeout=10)
        try:
            outcome = _qualify_portfolio_request(database_path, _request("TCS"))
            assert outcome.status == "QUALIFIED"
            assert outcome.qualified_batch is not None
            return outcome.qualified_batch
        finally:
            with remaining_lock:
                remaining -= 1
                if remaining == 0:
                    registrations_finished.set()

    def validate_active_batch() -> None:
        start.wait(timeout=10)
        while not registrations_finished.is_set():
            assert portfolio_qualification._validate_coordinator_batch(active) is active
        assert portfolio_qualification._validate_coordinator_batch(active) is active

    with ThreadPoolExecutor(max_workers=registration_count + 1) as executor:
        registration_futures = [
            executor.submit(qualify_another_batch) for _ in range(registration_count)
        ]
        validation_future = executor.submit(validate_active_batch)
        registered_batches = [future.result(timeout=60) for future in registration_futures]
        validation_future.result(timeout=60)

    assert len(registered_batches) == registration_count
    assert all(
        portfolio_qualification._validate_coordinator_batch(batch) is batch
        for batch in registered_batches
    )
    assert portfolio_qualification._validate_coordinator_batch(active) is active


def test_required_implementation_fingerprints_are_complete_and_current(
    qualification_setup,
) -> None:
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    result = _qualify_portfolio_request(database_path, _request("TCS"))
    assert result.qualified_batch is not None

    provenance = result.qualified_batch.provenance_document()
    expected = portfolio_qualification._implementation_fingerprints()
    assert set(provenance["implementation_source_sha256"]) == set(expected)
    portfolio_qualification._validate_required_implementation_fingerprints(provenance)

    mutated = dict(provenance)
    impl = dict(provenance["implementation_source_sha256"])
    impl.pop("portfolio_contracts_and_allocation")
    mutated["implementation_source_sha256"] = impl
    with pytest.raises(ValueError, match="complete required implementation fingerprint set"):
        portfolio_qualification._validate_required_implementation_fingerprints(mutated)

    replaced = dict(provenance)
    replaced["implementation_source_sha256"] = dict(expected)
    replaced["implementation_source_sha256"]["portfolio_contracts_and_allocation"] = "0" * 64
    with pytest.raises(ValueError, match="implementation fingerprint"):
        portfolio_qualification._validate_required_implementation_fingerprints(replaced)


def test_direct_lower_level_execution_rejects_an_untrusted_call() -> None:
    with pytest.raises(backtesting.BacktestError, match="trusted loader"):
        backtesting._execute_qualified_request(object(), qualification_token=object())


def test_latest_schema_version_is_recorded_deterministically_and_readonly(qualification_setup):
    database_path, _, tmp_path = qualification_setup
    _import_symbol(database_path, tmp_path, "TCS")
    with closing(get_connection(database_path)) as connection:
        before = tuple(connection.execute("SELECT version FROM schema_metadata ORDER BY version"))
        assert tuple(row[0] for row in before) == tuple(range(1, 17))
    first = _qualify_portfolio_request(database_path, _request("TCS")).qualified_batch
    second = _qualify_portfolio_request(database_path, _request("TCS")).qualified_batch
    assert first is not None and second is not None
    assert first.provenance_document()["database"]["schema_version"] == 16
    assert first.fingerprint_sha256 == second.fingerprint_sha256
    with closing(get_connection(database_path)) as connection:
        after = tuple(connection.execute("SELECT version FROM schema_metadata ORDER BY version"))
    assert tuple(map(tuple, before)) == tuple(map(tuple, after))


@pytest.mark.parametrize("version", [None, 0, -1, "invalid", 1.5])
def test_empty_or_invalid_schema_metadata_fails_safely(qualification_setup, version):
    database_path, _, _ = qualification_setup
    # Only this isolated fixture's metadata is replaced to exercise malformed values
    # that the normal INTEGER PRIMARY KEY would otherwise reject before the reader.
    with closing(get_connection(database_path)) as connection:
        connection.execute("DROP TABLE schema_metadata")
        connection.execute("CREATE TABLE schema_metadata (version)")
        if version is not None:
            connection.execute("INSERT INTO schema_metadata VALUES (?)", (version,))
        connection.commit()
    with pytest.raises(RuntimeError, match="schema metadata is missing or invalid"):
        _qualify_portfolio_request(database_path, _request("TCS"))
