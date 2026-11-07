"""Tests for deterministic Phase 3 strategy specifications and signal research."""

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aegis.database import get_connection, get_database_status, initialize_database
from aegis.instruments import InstrumentInput, register_instrument
from aegis.market_data import import_ohlcv_csv
from aegis.market_quality import CalendarRegistry, load_calendar_registry
from aegis.strategy_lab import (
    StrategyCandle,
    StrategyLabError,
    _canonical_hash,
    create_sma_crossover_spec,
    generate_sma_crossover_signals,
    list_strategy_experiments,
    register_experiment,
    run_strategy_experiment,
    simple_moving_average,
)

_DAY = date(2026, 1, 5)
_AS_OF = datetime(2026, 1, 5, 9, 22, tzinfo=ZoneInfo("Asia/Kolkata"))
_CLOSES = (30, 20, 10, 20, 40, 5, 4)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "strategy_lab.db"
    initialize_database(path)
    register_instrument(
        path,
        InstrumentInput("NSE", "TCS", "Tata Consultancy Services", "EQUITY", segment="NSE"),
    )
    return path


def _spec(**overrides: object):
    values: dict[str, object] = {
        "exchange": "NSE",
        "trading_symbol": "TCS",
        "instrument_type": "EQUITY",
        "interval": "1m",
        "fast_window": 2,
        "slow_window": 3,
        "hypothesis": "A short moving average crossing above a long average is a testable trend signal.",
    }
    values.update(overrides)
    return create_sma_crossover_spec(**values)  # type: ignore[arg-type]


def _candle(index: int, close: Decimal | int) -> StrategyCandle:
    value = Decimal(close)
    local_minute = 15 + index
    timestamp = f"2026-01-05T{9 + local_minute // 60:02d}:{local_minute % 60:02d}:00Z"
    return StrategyCandle(
        timestamp=timestamp,
        open=value,
        high=value + 1,
        low=value - 1,
        close=value,
        volume=100,
    )


def _write_csv(
    path: Path, closes: tuple[int, ...] = _CLOSES, *, missing: int | None = None
) -> Path:
    lines = ["timestamp,open,high,low,close,volume"]
    for index, close in enumerate(closes):
        if index == missing:
            continue
        minute = 15 + index
        lines.append(
            f"2026-01-05T09:{minute:02d}:00+05:30,{close},{close + 1},{close - 1},{close},100"
        )
    csv_path = path / "strategy_candles.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path


def _registry(path: Path, *, close: str = "09:22"):
    config = path / "exchange_calendars.json"
    config.write_text(
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
                        "coverage_start": _DAY.isoformat(),
                        "coverage_end": _DAY.isoformat(),
                        "source": "Deterministic test fixture",
                        "weekdays": [0, 1, 2, 3, 4],
                        "sessions": [{"open": "09:15", "close": close}],
                        "holidays": [],
                        "special_sessions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_calendar_registry(config)


def _register(database_path: Path, spec=None) -> int:
    registration = register_experiment(
        database_path,
        spec or _spec(),
        start_date=_DAY,
        end_date=_DAY,
    )
    return registration.experiment_id


def _import(database_path: Path, tmp_path: Path, *, missing: int | None = None) -> None:
    result = import_ohlcv_csv(
        database_path,
        _write_csv(tmp_path, missing=missing),
        "NSE",
        "TCS",
        "1m",
    )
    assert result.accepted_rows == len(_CLOSES) - (missing is not None)


def test_simple_moving_average_uses_trailing_decimal_values() -> None:
    assert simple_moving_average((Decimal(1), Decimal(2), Decimal(3), Decimal(4)), 3) == (
        None,
        None,
        Decimal(2),
        Decimal(3),
    )
    assert simple_moving_average((Decimal("0.1"), Decimal("0.2")), 2)[1] == Decimal("0.15")


def test_nonterminating_sma_uses_documented_half_even_rounding() -> None:
    values = (Decimal(1), Decimal(0), Decimal(0))
    with localcontext() as context:
        context.rounding = ROUND_DOWN
        rounded_down_context = simple_moving_average(values, 3)[-1]
    with localcontext() as context:
        context.rounding = ROUND_UP
        rounded_up_context = simple_moving_average(values, 3)[-1]

    expected = Decimal("0.3333333333333333333333333333")
    assert rounded_down_context == expected
    assert rounded_up_context == expected


def test_sma_crossover_signals_are_deterministic_and_long_only() -> None:
    candles = tuple(_candle(index, close) for index, close in enumerate(_CLOSES))

    first = generate_sma_crossover_signals(candles, fast_window=2, slow_window=3)
    second = generate_sma_crossover_signals(candles, fast_window=2, slow_window=3)

    assert first == second
    assert [(signal.timestamp, signal.action) for signal in first] == [
        ("2026-01-05T09:19:00Z", "ENTER_LONG"),
        ("2026-01-05T09:21:00Z", "EXIT_LONG"),
    ]
    assert all(isinstance(signal.close, Decimal) for signal in first)


def test_appending_future_candles_does_not_change_prior_signals() -> None:
    candles = tuple(_candle(index, close) for index, close in enumerate(_CLOSES))
    prefix_signals = generate_sma_crossover_signals(candles[:-1], fast_window=2, slow_window=3)
    all_signals = generate_sma_crossover_signals(candles, fast_window=2, slow_window=3)

    assert prefix_signals == tuple(
        signal for signal in all_signals if signal.timestamp < candles[-1].timestamp
    )


@pytest.mark.parametrize(
    ("candles", "message"),
    [
        ((_candle(1, 10), _candle(0, 11)), "strictly increasing"),
        ((_candle(0, 10), _candle(0, 11)), "strictly increasing"),
        (
            (StrategyCandle("bad-time", Decimal(10), Decimal(11), Decimal(9), Decimal(10), 1),),
            "canonical UTC",
        ),
        (
            (
                StrategyCandle(
                    "2026-01-05T09:15:00Z", Decimal(10), Decimal(9), Decimal(8), Decimal(10), 1
                ),
            ),
            "high must",
        ),
        (
            (
                StrategyCandle(
                    "2026-01-05T09:15:00Z", Decimal(10), Decimal(11), Decimal(9), Decimal(10), -1
                ),
            ),
            "volume",
        ),
    ],
)
def test_signal_generator_rejects_invalid_or_out_of_order_candles(
    candles: tuple[StrategyCandle, ...], message: str
) -> None:
    with pytest.raises(StrategyLabError, match=message):
        generate_sma_crossover_signals(candles, fast_window=2, slow_window=3)


def test_signal_generator_handles_insufficient_history_and_invalid_windows() -> None:
    assert (
        generate_sma_crossover_signals(
            (_candle(0, 10), _candle(1, 11)), fast_window=2, slow_window=3
        )
        == ()
    )
    with pytest.raises(StrategyLabError, match="fast_window"):
        generate_sma_crossover_signals((_candle(0, 10),), fast_window=3, slow_window=3)
    with pytest.raises(StrategyLabError, match="positive integer"):
        simple_moving_average((Decimal(1),), 0)


@pytest.mark.parametrize(
    "arguments",
    [
        {"fast_window": 3, "slow_window": 3},
        {"fast_window": True, "slow_window": 5},
        {"interval": "2m"},
        {"instrument_type": "FUTURE"},
        {"version": "latest"},
    ],
)
def test_strategy_spec_validates_universe_version_and_parameters(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(StrategyLabError):
        _spec(**arguments)


def test_experiment_registry_uses_existing_schema_and_freezes_strategy_version(
    database_path: Path,
) -> None:
    original = _spec()
    registered = register_experiment(database_path, original, start_date=_DAY, end_date=_DAY)
    repeated = register_experiment(database_path, original, start_date=_DAY, end_date=_DAY)
    changed = _spec(hypothesis="A changed hypothesis cannot reuse this immutable version.")

    assert repeated.strategy_id == registered.strategy_id
    assert repeated.experiment_id != registered.experiment_id
    with pytest.raises(StrategyLabError, match="immutable"):
        register_experiment(database_path, changed, start_date=_DAY, end_date=_DAY)

    experiments = list_strategy_experiments(database_path)
    assert len(experiments) == 2
    assert all(item["strategy_spec_sha256"] for item in experiments)
    assert get_database_status(database_path)["schema_version"] == 6


def test_experiment_registry_scopes_versions_by_instrument_and_interval(
    database_path: Path,
) -> None:
    register_instrument(
        database_path,
        InstrumentInput("BSE", "TCS", "Tata Consultancy Services", "EQUITY", segment="BSE"),
    )
    nse_registration = register_experiment(database_path, _spec(), start_date=_DAY, end_date=_DAY)
    bse_registration = register_experiment(
        database_path,
        _spec(exchange="BSE", interval="5m"),
        start_date=_DAY,
        end_date=_DAY,
    )

    assert nse_registration.strategy_id != bse_registration.strategy_id
    experiments = list_strategy_experiments(database_path)
    assert {
        (item["exchange"], item["trading_symbol"], item["interval"]) for item in experiments
    } == {
        ("NSE", "TCS", "1m"),
        ("BSE", "TCS", "5m"),
    }


@pytest.mark.parametrize(
    "dimension",
    [
        "strategy_version",
        "strategy_parameter",
        "instrument",
        "interval",
        "start_date",
        "end_date",
        "other_configuration_field",
    ],
)
def test_modified_experiment_configuration_is_rejected_before_run(
    database_path: Path, dimension: str
) -> None:
    experiment_id = _register(database_path)
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT configuration_json FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        configuration = json.loads(row["configuration_json"])
        if dimension == "strategy_version":
            configuration["strategy_spec"]["version"] = "2.0.0"
        elif dimension == "strategy_parameter":
            configuration["strategy_spec"]["slow_window"] = 4
        elif dimension == "instrument":
            configuration["instrument"]["exchange"] = "BSE"
        elif dimension == "interval":
            configuration["interval"] = "5m"
        elif dimension == "start_date":
            configuration["start_date"] = "2026-01-04"
        elif dimension == "end_date":
            configuration["end_date"] = "2026-01-06"
        else:
            configuration["new_execution_setting"] = "modified"
        connection.execute(
            "UPDATE experiments SET configuration_json = ? WHERE id = ?",
            (json.dumps(configuration), experiment_id),
        )
        connection.commit()

    with pytest.raises(StrategyLabError, match="configuration integrity check failed"):
        run_strategy_experiment(
            database_path, experiment_id, calendars=CalendarRegistry(()), as_of=_AS_OF
        )
    with closing(get_connection(database_path)) as connection:
        run_count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()[0]
    assert run_count == 0


def test_legacy_experiment_hash_is_backfilled_on_first_run_without_historical_claim(
    database_path: Path, tmp_path: Path
) -> None:
    _import(database_path, tmp_path)
    experiment_id = _register(database_path)
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT configuration_json FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        legacy_configuration = json.loads(row["configuration_json"])
        legacy_configuration["configuration_schema_version"] = 1
        legacy_configuration.pop("strategy_identity")
        connection.execute(
            """UPDATE experiments
               SET configuration_json = ?, configuration_sha256 = NULL,
                   configuration_hash_origin = NULL
               WHERE id = ?""",
            (json.dumps(legacy_configuration), experiment_id),
        )
        connection.commit()

    result = run_strategy_experiment(
        database_path, experiment_id, calendars=_registry(tmp_path), as_of=_AS_OF
    )

    assert result.status == "COMPLETED"
    with closing(get_connection(database_path)) as connection:
        experiment = connection.execute(
            "SELECT configuration_sha256, configuration_hash_origin FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        run = connection.execute(
            "SELECT result_json FROM runs WHERE id = ?", (result.run_id,)
        ).fetchone()
    provenance = json.loads(run["result_json"])["metadata"]
    assert experiment["configuration_sha256"]
    assert experiment["configuration_hash_origin"] == "FIRST_RUN_BACKFILL"
    assert provenance["configuration_hash_origin"] == "FIRST_RUN_BACKFILL"
    assert provenance["provenance_status"] == "COMPLETE"


def test_legacy_backfill_rejects_specification_changed_from_persisted_strategy(
    database_path: Path,
) -> None:
    experiment_id = _register(database_path)
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT strategy_id, configuration_json FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        original_strategy = connection.execute(
            "SELECT description FROM strategies WHERE id = ?", (row["strategy_id"],)
        ).fetchone()["description"]
        configuration = json.loads(row["configuration_json"])
        configuration["configuration_schema_version"] = 1
        configuration.pop("strategy_identity")
        configuration["strategy_spec"]["slow_window"] = 4
        configuration["strategy_spec_sha256"] = _canonical_hash(configuration["strategy_spec"])
        connection.execute(
            "UPDATE experiments SET configuration_json = ?, configuration_sha256 = NULL, "
            "configuration_hash_origin = NULL WHERE id = ?",
            (json.dumps(configuration), experiment_id),
        )
        connection.commit()

    with pytest.raises(StrategyLabError, match="does not match its persisted strategy record"):
        run_strategy_experiment(
            database_path, experiment_id, calendars=CalendarRegistry(()), as_of=_AS_OF
        )

    with closing(get_connection(database_path)) as connection:
        experiment = connection.execute(
            "SELECT configuration_sha256, configuration_hash_origin FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        run_count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()[0]
        strategy = connection.execute(
            "SELECT description FROM strategies WHERE id = ?", (row["strategy_id"],)
        ).fetchone()
    assert experiment["configuration_sha256"] is None
    assert experiment["configuration_hash_origin"] is None
    assert run_count == 0
    assert strategy["description"] == original_strategy


def test_legacy_backfill_rejects_missing_persisted_strategy(database_path: Path) -> None:
    experiment_id = _register(database_path)
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT strategy_id, configuration_json FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        configuration = json.loads(row["configuration_json"])
        configuration["configuration_schema_version"] = 1
        configuration.pop("strategy_identity")
        connection.execute(
            "UPDATE experiments SET configuration_json = ?, configuration_sha256 = NULL, "
            "configuration_hash_origin = NULL WHERE id = ?",
            (json.dumps(configuration), experiment_id),
        )
        connection.commit()

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("DELETE FROM strategies WHERE id = ?", (row["strategy_id"],))
        connection.commit()

    with pytest.raises(StrategyLabError, match="references missing strategy record"):
        run_strategy_experiment(
            database_path, experiment_id, calendars=CalendarRegistry(()), as_of=_AS_OF
        )

    with closing(get_connection(database_path)) as connection:
        experiment = connection.execute(
            "SELECT configuration_sha256, configuration_hash_origin FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        run_count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()[0]
    assert experiment["configuration_sha256"] is None
    assert experiment["configuration_hash_origin"] is None
    assert run_count == 0


def test_run_persists_reproducible_signal_and_provenance_snapshots(
    database_path: Path, tmp_path: Path
) -> None:
    _import(database_path, tmp_path)
    experiment_id = _register(database_path)
    calendars = _registry(tmp_path)

    first = run_strategy_experiment(database_path, experiment_id, calendars=calendars, as_of=_AS_OF)
    second = run_strategy_experiment(
        database_path, experiment_id, calendars=calendars, as_of=_AS_OF
    )

    assert first.status == second.status == "COMPLETED"
    assert first.signals == second.signals
    assert first.dataset_sha256 == second.dataset_sha256
    assert [(signal.action, signal.timestamp) for signal in first.signals] == [
        ("ENTER_LONG", "2026-01-05T03:49:00Z"),
        ("EXIT_LONG", "2026-01-05T03:51:00Z"),
    ]
    with closing(get_connection(database_path)) as connection:
        runs = connection.execute(
            "SELECT id, status, result_json FROM runs WHERE experiment_id = ? ORDER BY id",
            (experiment_id,),
        ).fetchall()
    assert [row["status"] for row in runs] == ["COMPLETED", "COMPLETED"]
    first_result = json.loads(runs[0]["result_json"])
    second_result = json.loads(runs[1]["result_json"])
    assert first_result["metadata"]["dataset_sha256"] == second_result["metadata"]["dataset_sha256"]
    assert first_result["metadata"]["source_file_sha256s"]
    assert first_result["metadata"]["strategy_implementation_sha256"]
    assert first_result["metadata"]["market_quality_implementation_sha256"]
    assert first_result["metadata"]["market_data_ingestion_implementation_sha256"]
    assert first_result["metadata"]["calendar_configuration_sha256"]
    assert first_result["metadata"]["effective_calendar_configuration_sha256"]
    assert first_result["metadata"]["experiment_configuration_sha256"]
    assert first_result["metadata"]["signals_are_execution_orders"] is False


def test_calendar_file_change_changes_run_provenance(database_path: Path, tmp_path: Path) -> None:
    _import(database_path, tmp_path)
    experiment_id = _register(database_path)
    calendars_before = _registry(tmp_path)
    first = run_strategy_experiment(
        database_path, experiment_id, calendars=calendars_before, as_of=_AS_OF
    )

    config_path = tmp_path / "exchange_calendars.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["calendars"][0]["sessions"][0]["close"] = "09:23"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    calendars_after = load_calendar_registry(config_path)
    second = run_strategy_experiment(
        database_path, experiment_id, calendars=calendars_after, as_of=_AS_OF
    )

    assert first.status == second.status == "COMPLETED"
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            "SELECT result_json FROM runs WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()
    first_metadata = json.loads(rows[0]["result_json"])["metadata"]
    second_metadata = json.loads(rows[1]["result_json"])["metadata"]
    assert (
        first_metadata["calendar_configuration_sha256"]
        != second_metadata["calendar_configuration_sha256"]
    )
    assert (
        first_metadata["effective_calendar_configuration_sha256"]
        != second_metadata["effective_calendar_configuration_sha256"]
    )


def test_market_quality_source_change_changes_run_provenance(
    database_path: Path, tmp_path: Path, monkeypatch
) -> None:
    _import(database_path, tmp_path)
    experiment_id = _register(database_path)
    calendars = _registry(tmp_path)
    implementation_path = tmp_path / "market_quality.py"
    implementation_path.write_text("QUALITY_IMPL = 'first'\n", encoding="utf-8")
    monkeypatch.setattr(
        "aegis.strategy_lab._market_quality_source_path", lambda: implementation_path
    )
    first = run_strategy_experiment(database_path, experiment_id, calendars=calendars, as_of=_AS_OF)

    implementation_path.write_text("QUALITY_IMPL = 'second'\n", encoding="utf-8")
    second = run_strategy_experiment(
        database_path, experiment_id, calendars=calendars, as_of=_AS_OF
    )

    assert first.status == second.status == "COMPLETED"
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            "SELECT result_json FROM runs WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()
    first_metadata = json.loads(rows[0]["result_json"])["metadata"]
    second_metadata = json.loads(rows[1]["result_json"])["metadata"]
    assert (
        first_metadata["market_quality_implementation_sha256"]
        != second_metadata["market_quality_implementation_sha256"]
    )


def test_run_is_blocked_without_confirmed_calendar_and_does_not_change_candles(
    database_path: Path, tmp_path: Path
) -> None:
    _import(database_path, tmp_path)
    experiment_id = _register(database_path)
    with closing(get_connection(database_path)) as connection:
        before = connection.execute(
            "SELECT candle_timestamp, open_decimal, high_decimal, low_decimal, close_decimal "
            "FROM historical_candles ORDER BY candle_timestamp"
        ).fetchall()

    result = run_strategy_experiment(
        database_path, experiment_id, calendars=CalendarRegistry(()), as_of=_AS_OF
    )

    assert result.status == "BLOCKED"
    assert result.signals == ()
    assert "CONFIRMED" in (result.reason or "")
    with closing(get_connection(database_path)) as connection:
        after = connection.execute(
            "SELECT candle_timestamp, open_decimal, high_decimal, low_decimal, close_decimal "
            "FROM historical_candles ORDER BY candle_timestamp"
        ).fetchall()
        run = connection.execute(
            "SELECT status, error_message, result_json FROM runs WHERE id = ?", (result.run_id,)
        ).fetchone()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert run["status"] == "BLOCKED"
    blocked_result = json.loads(run["result_json"])
    assert blocked_result["status"] == "BLOCKED"
    assert blocked_result["provenance_status"] == "PARTIAL_BLOCKED"


def test_run_is_blocked_for_missing_expected_candle(database_path: Path, tmp_path: Path) -> None:
    _import(database_path, tmp_path, missing=3)
    experiment_id = _register(database_path)

    result = run_strategy_experiment(
        database_path,
        experiment_id,
        calendars=_registry(tmp_path),
        as_of=_AS_OF,
    )

    assert result.status == "BLOCKED"
    assert result.signals == ()
    assert "INCOMPLETE" in (result.reason or "")


def test_failed_run_has_only_partial_failure_provenance(database_path: Path, monkeypatch) -> None:
    experiment_id = _register(database_path)

    def fail_quality_analysis(*args, **kwargs):
        raise RuntimeError("injected quality analyzer failure")

    monkeypatch.setattr("aegis.strategy_lab.analyze_dataset", fail_quality_analysis)
    with pytest.raises(RuntimeError, match="injected quality analyzer failure"):
        run_strategy_experiment(
            database_path, experiment_id, calendars=CalendarRegistry(()), as_of=_AS_OF
        )

    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            "SELECT status, result_json FROM runs WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
    result = json.loads(row["result_json"])
    assert row["status"] == "FAILED"
    assert result["status"] == "FAILED"
    assert result["provenance_status"] == "PARTIAL_FAILED"
    assert "metadata" not in result


def test_register_experiment_rejects_inactive_and_mismatched_instruments(
    database_path: Path,
) -> None:
    inactive_path = database_path.parent / "inactive.db"
    initialize_database(inactive_path)
    register_instrument(
        inactive_path,
        InstrumentInput("NSE", "TCS", "Tata Consultancy Services", "EQUITY", is_active=False),
    )
    with pytest.raises(StrategyLabError, match="active"):
        _register(inactive_path)
    with pytest.raises(StrategyLabError, match="does not match"):
        _register(database_path, _spec(instrument_type="INDEX"))
