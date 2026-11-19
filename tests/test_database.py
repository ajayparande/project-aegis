"""Tests for the Aegis SQLite database."""

import sqlite3
from contextlib import closing

import pytest

from aegis import database
from aegis.database import (
    get_connection,
    get_database_status,
    initialize_database,
)

ALL_TABLES = {
    "paper_equity_snapshots",
    "sources",
    "hypotheses",
    "strategies",
    "experiments",
    "runs",
    "schema_metadata",
    "instruments",
    "data_imports",
    "market_data",
    "data_quality_issues",
    "historical_candles",
    "import_source_use_history",
    "paper_accounts",
    "paper_orders",
    "paper_fills",
    "paper_ledger_entries",
    "paper_positions",
    "paper_realized_pnl",
    "paper_marks",
    "paper_order_events",
    "paper_daily_state",
    "forward_runtimes",
    "forward_runtime_imports",
    "forward_quotes",
    "forward_runtime_candles",
    "forward_scheduler_events",
    "forward_execution_authorizations",
    "forward_trade_controls",
    "paper_execution_snapshot_uses",
    "decision_journal_events",
    "decision_journal_audits",
    "decision_journal_outcomes",
    "decision_journal_health",
    "decision_journal_manifests",
}


def _create_version_1_database(database_path) -> None:
    """Build a representative pre-migration database with retained user data."""

    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(database.PHASE_1_SCHEMA)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (1)")
        connection.execute(
            "INSERT INTO sources (name, source_type) VALUES (?, ?)",
            ("Existing source", "manual"),
        )
        connection.execute(
            "INSERT INTO hypotheses (title, description) VALUES (?, ?)",
            ("Existing hypothesis", "Preserve this record"),
        )
        strategy = connection.execute(
            "INSERT INTO strategies (name, description) VALUES (?, ?)",
            ("Existing strategy", "Preserve this record"),
        )
        hypothesis = connection.execute(
            "SELECT id FROM hypotheses WHERE title = ?", ("Existing hypothesis",)
        ).fetchone()
        experiment = connection.execute(
            "INSERT INTO experiments (name, strategy_id, hypothesis_id) VALUES (?, ?, ?)",
            ("Existing experiment", strategy.lastrowid, hypothesis[0]),
        )
        connection.execute(
            "INSERT INTO runs (experiment_id, status) VALUES (?, ?)",
            (experiment.lastrowid, "COMPLETED"),
        )
        connection.commit()
    finally:
        connection.close()


def _create_version_2_database(database_path) -> None:
    """Create the earlier version 2 schema, including its redundant indexes."""

    _create_version_1_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(database.MIGRATIONS[1][0])
        connection.execute(
            "CREATE INDEX idx_market_data_lookup "
            "ON market_data (instrument_id, interval, candle_timestamp)"
        )
        connection.execute(
            "CREATE INDEX idx_instruments_exchange ON instruments (exchange, trading_symbol)"
        )
        connection.execute("INSERT INTO schema_metadata (version) VALUES (2)")
        connection.commit()
    finally:
        connection.close()


def _create_version_3_database(database_path) -> None:
    """Create the version 3 schema with a legacy REAL tick size and data."""

    _create_version_2_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        for statement in database.MIGRATIONS[2]:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (3)")
        instrument = connection.execute(
            "INSERT INTO instruments "
            "(exchange, trading_symbol, instrument_name, instrument_type, tick_size) "
            "VALUES (?, ?, ?, ?, ?)",
            ("NSE", "LEGACY", "Legacy Instrument", "EQUITY", 0.05),
        )
        imported = connection.execute(
            "INSERT INTO data_imports (source_id, import_type) VALUES (?, ?)",
            (1, "fixture"),
        )
        connection.execute(
            "INSERT INTO market_data "
            "(instrument_id, import_id, interval, candle_timestamp, open, high, low, close) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                instrument.lastrowid,
                imported.lastrowid,
                "1d",
                "2026-01-01T00:00:00Z",
                100,
                101,
                99,
                100.5,
            ),
        )
        connection.execute(
            "INSERT INTO data_quality_issues "
            "(import_id, instrument_id, issue_type, description) VALUES (?, ?, ?, ?)",
            (imported.lastrowid, instrument.lastrowid, "fixture", "Preserve this issue"),
        )
        connection.commit()
    finally:
        connection.close()


def _create_version_4_database(database_path) -> None:
    """Create a populated schema version 4 database."""

    _create_version_3_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        for statement in database.MIGRATIONS[3]:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (4)")
        connection.commit()
    finally:
        connection.close()


def _create_version_5_database(database_path) -> None:
    """Create the populated pre-configuration-integrity schema version 5."""

    _create_version_4_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        for script in database.MIGRATIONS[4]:
            database._execute_statements(connection, script)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (5)")
        connection.commit()
    finally:
        connection.close()


def _create_version_6_database(database_path) -> None:
    """Create the populated schema immediately before source-use permissions."""

    _create_version_5_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        for statement in database.MIGRATIONS[5]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (6)")
        connection.commit()
    finally:
        connection.close()


def _create_version_8_database(database_path) -> None:
    """Create the populated schema immediately before forward-runtime tables."""

    _create_version_6_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        for statement in database.MIGRATIONS[6]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (7)")
        for statement in database.MIGRATIONS[7]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (8)")
        connection.commit()
    finally:
        connection.close()


def _insert_phase_8_records(connection, account_id: int) -> None:
    instrument_id = connection.execute(
        """INSERT INTO instruments
           (exchange, trading_symbol, instrument_name, instrument_type, lot_size, is_active)
           VALUES ('NSE', 'MIGRATION', 'Migration Fixture', 'EQUITY', 1, 1)"""
    ).lastrowid
    order_id = "phase8-preserved-order"
    fill_id = "phase8-preserved-fill"
    connection.execute(
        """INSERT INTO paper_orders
           (order_id, account_id, idempotency_key, request_sha256, instrument_id,
            requested_instrument_id, side, quantity, status, signal_timestamp,
            submitted_timestamp, execution_timestamp, reference_price, fill_price, created_at)
           VALUES (?, ?, 'preserved-key', ?, ?, ?, 'BUY', 1, 'FILLED',
                   '2026-01-05T09:15:00Z', '2026-01-05T09:15:00Z',
                   '2026-01-05T09:15:00Z', '100.00', '100.00', '2026-01-05T09:15:00Z')""",
        (order_id, account_id, "a" * 64, instrument_id, instrument_id),
    )
    connection.execute(
        """INSERT INTO paper_fills
           (fill_id, order_id, instrument_id, quantity, fill_price, fees_json,
            execution_costs_json, execution_timestamp)
           VALUES (?, ?, ?, 1, '100.00', '{}', '{}', '2026-01-05T09:15:00Z')""",
        (fill_id, order_id, instrument_id),
    )
    connection.execute(
        """INSERT INTO paper_ledger_entries
           (entry_id, account_id, order_id, fill_id, entry_type, amount, created_at)
           VALUES ('phase8-preserved-ledger', ?, ?, ?, 'BUY_CASH_DEBIT', '-100.00',
                   '2026-01-05T09:15:00Z')""",
        (account_id, order_id, fill_id),
    )


def _add_metadata_insert_failure(database_path, version: int) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            f"""
            CREATE TRIGGER reject_schema_version_{version}
            BEFORE INSERT ON schema_metadata
            WHEN NEW.version = {version}
            BEGIN
                SELECT RAISE(ABORT, 'injected metadata insertion failure');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()


def _create_populated_version_10_database(database_path) -> int:
    _create_version_8_database(database_path)
    with closing(get_connection(database_path)) as connection:
        cursor = connection.execute(
            """INSERT INTO paper_accounts
               (name, initial_cash, risk_limits_json, cost_assumptions_json, created_at)
               VALUES ('v10 populated account', '12345.67', '{}', '{}', '2026-01-05T00:00:00Z')"""
        )
        account_id = int(cursor.lastrowid)
        _insert_phase_8_records(connection, account_id)
        for version in (8, 9):
            for statement in database.MIGRATIONS[version]:
                database._execute_statements(connection, statement)
            connection.execute("INSERT INTO schema_metadata (version) VALUES (?)", (version + 1,))
        instrument_id = int(
            connection.execute(
                "SELECT id FROM instruments WHERE trading_symbol = 'MIGRATION'"
            ).fetchone()["id"]
        )
        connection.execute(
            """INSERT INTO forward_runtimes
               (runtime_id, account_id, config_json, config_sha256, state_json,
                state_sha256, status, created_at, updated_at)
               VALUES ('migration-runtime', ?, '{}', ?, '{}', ?, 'RUNNING',
                       '2026-01-05T00:00:00Z', '2026-01-05T00:00:00Z')""",
            (account_id, "a" * 64, "b" * 64),
        )
        connection.execute(
            """INSERT INTO forward_scheduler_events
               (event_id, runtime_id, instrument_id, interval, candle_timestamp,
                decision_timestamp, status, proposal_id, result_json, created_at)
               VALUES ('migration-event', 'migration-runtime', ?, '1m',
                       '2026-01-05T00:00:00Z', '2026-01-05T00:01:00Z',
                       'PENDING', 'migration-proposal', NULL, '2026-01-05T00:01:00Z')""",
            (instrument_id,),
        )
        connection.commit()
    return account_id


def _create_populated_version_11_database(database_path) -> int:
    account_id = _create_populated_version_10_database(database_path)
    with closing(get_connection(database_path)) as connection:
        for statement in database.MIGRATIONS[10]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (11)")
        connection.commit()
    return account_id


def _create_populated_version_12_database(database_path) -> int:
    account_id = _create_populated_version_11_database(database_path)
    with closing(get_connection(database_path)) as connection:
        for statement in database.MIGRATIONS[11]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (12)")
        connection.commit()
    return account_id


def test_fresh_database_initialization(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"

    initialize_database(database_path)

    status = get_database_status(database_path)

    assert status["exists"] is True
    assert status["schema_version"] == database.SCHEMA_VERSION

    assert set(status["tables"]) == ALL_TABLES
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))


def test_version_1_migration_preserves_phase_1_records(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"
    _create_version_1_database(database_path)

    initialize_database(database_path)

    status = get_database_status(database_path)
    assert status["schema_version"] == database.SCHEMA_VERSION
    assert ALL_TABLES.issubset(set(status["tables"]))
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        source = connection.execute("SELECT name FROM sources").fetchone()
        hypothesis = connection.execute("SELECT title, description FROM hypotheses").fetchone()
        phase_1_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "hypotheses", "strategies", "experiments", "runs")
        }
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))
    assert source["name"] == "Existing source"
    assert hypothesis["title"] == "Existing hypothesis"
    assert hypothesis["description"] == "Preserve this record"
    assert set(phase_1_counts.values()) == {1}


def test_database_initialization_is_repeatable(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"

    initialize_database(database_path)
    initialize_database(database_path)

    status = get_database_status(database_path)

    assert status["schema_version"] == database.SCHEMA_VERSION
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        sources_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))
    assert sources_count == 0


def test_foreign_keys_are_enforced(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"
    initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO experiments (
                    name,
                    strategy_id
                )
                VALUES (?, ?)
                """,
                ("Invalid experiment", 9999),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO data_imports (source_id, import_type) VALUES (?, ?)",
                (9999, "historical"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO data_imports (instrument_id, import_type) VALUES (?, ?)",
                (9999, "historical"),
            )

        instrument = connection.execute(
            "INSERT INTO instruments "
            "(exchange, trading_symbol, instrument_name, instrument_type) "
            "VALUES (?, ?, ?, ?)",
            ("NSE", "TEST", "Test instrument", "EQUITY"),
        )
        instrument_id = instrument.lastrowid

        market_data_values = "(?, ?, ?, ?, ?, ?, ?, ?)"
        market_data_columns = (
            "instrument_id, import_id, interval, candle_timestamp, open, high, low, close"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO market_data ({market_data_columns}) VALUES {market_data_values}",
                (9999, None, "1d", "2026-01-01T00:00:00Z", 1, 1, 1, 1),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO market_data ({market_data_columns}) VALUES {market_data_values}",
                (instrument_id, 9999, "1d", "2026-01-01T00:00:00Z", 1, 1, 1, 1),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO data_quality_issues "
                "(instrument_id, issue_type, description) VALUES (?, ?, ?)",
                (9999, "TEST", "invalid instrument"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO data_quality_issues "
                "(import_id, issue_type, description) VALUES (?, ?, ?)",
                (9999, "TEST", "invalid import"),
            )
        imported = connection.execute(
            "INSERT INTO data_imports (import_type) VALUES (?)", ("test",)
        )
        historical_values = "(?, ?, ?, ?, ?, ?, ?, ?, ?)"
        historical_columns = (
            "instrument_id, import_id, interval, candle_timestamp, open_decimal, "
            "high_decimal, low_decimal, close_decimal, volume"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO historical_candles ({historical_columns}) VALUES {historical_values}",
                (
                    9999,
                    imported.lastrowid,
                    "1m",
                    "2026-01-01T03:45:00Z",
                    "1",
                    "1",
                    "1",
                    "1",
                    0,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO historical_candles ({historical_columns}) VALUES {historical_values}",
                (
                    instrument_id,
                    9999,
                    "1m",
                    "2026-01-01T03:45:00Z",
                    "1",
                    "1",
                    "1",
                    "1",
                    0,
                ),
            )


def test_expected_phase_2_indexes_exist_without_unique_duplicates(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"
    initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        index_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        unique_index_columns = {
            table: {
                tuple(
                    info["name"]
                    for info in connection.execute(
                        f"PRAGMA index_info('{index['name']}')"
                    ).fetchall()
                )
                for index in connection.execute(f"PRAGMA index_list('{table}')").fetchall()
                if index["unique"] and index["origin"] == "u"
            }
            for table in ("instruments", "market_data", "historical_candles")
        }

    assert {
        "idx_market_data_timestamp",
        "idx_data_imports_status",
        "idx_quality_issues_import",
        "idx_historical_candles_import",
        "idx_data_imports_file_identity",
    }.issubset(index_names)
    assert "idx_market_data_lookup" not in index_names
    assert "idx_instruments_exchange" not in index_names
    assert ("exchange", "trading_symbol") in unique_index_columns["instruments"]
    assert ("instrument_id", "interval", "candle_timestamp") in unique_index_columns["market_data"]
    assert (
        "instrument_id",
        "interval",
        "candle_timestamp",
    ) in unique_index_columns["historical_candles"]


def test_existing_version_2_migrates_to_version_6_and_removes_redundant_indexes(
    tmp_path,
) -> None:
    database_path = tmp_path / "test_aegis.db"
    _create_version_2_database(database_path)

    initialize_database(database_path)

    status = get_database_status(database_path)
    assert status["schema_version"] == database.SCHEMA_VERSION
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        source = connection.execute("SELECT name FROM sources").fetchone()
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        phase_1_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "hypotheses", "strategies", "experiments", "runs")
        }
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))
    assert source["name"] == "Existing source"
    assert ALL_TABLES.issubset(set(status["tables"]))
    assert set(phase_1_counts.values()) == {1}
    assert "idx_market_data_lookup" not in indexes
    assert "idx_instruments_exchange" not in indexes
    assert {
        "idx_market_data_timestamp",
        "idx_data_imports_status",
        "idx_quality_issues_import",
    }.issubset(indexes)


def test_existing_version_3_migrates_to_version_6_preserving_data(tmp_path) -> None:
    database_path = tmp_path / "version_3.db"
    _create_version_3_database(database_path)

    initialize_database(database_path)

    status = get_database_status(database_path)
    assert status["schema_version"] == database.SCHEMA_VERSION
    assert ALL_TABLES.issubset(set(status["tables"]))
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        instrument = connection.execute(
            "SELECT id, trading_symbol, tick_size, tick_size_decimal, isin, strike_price "
            "FROM instruments WHERE trading_symbol = ?",
            ("LEGACY",),
        ).fetchone()
        phase_1_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sources", "hypotheses", "strategies", "experiments", "runs")
        }
        phase_2_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("instruments", "data_imports", "market_data", "data_quality_issues")
        }
        instrument_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(instruments)").fetchall()
        }
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))
    assert instrument["trading_symbol"] == "LEGACY"
    assert instrument["tick_size"] == 0.05
    assert instrument["tick_size_decimal"] == "0.05"
    assert instrument["isin"] is None
    assert instrument["strike_price"] is None
    assert set(phase_1_counts.values()) == {1}
    assert set(phase_2_counts.values()) == {1}
    assert "isin" in instrument_columns


def test_existing_version_4_migrates_additively_and_preserves_data(tmp_path) -> None:
    database_path = tmp_path / "version_4.db"
    _create_version_4_database(database_path)

    initialize_database(database_path)

    status = get_database_status(database_path)
    assert status["schema_version"] == database.SCHEMA_VERSION
    assert set(status["tables"]) == ALL_TABLES
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        instrument = connection.execute(
            "SELECT id, trading_symbol, tick_size, tick_size_decimal FROM instruments"
        ).fetchone()
        import_row = connection.execute(
            "SELECT id, import_type, records_inserted FROM data_imports"
        ).fetchone()
        market_data = connection.execute("SELECT id, close FROM market_data").fetchone()
        issue_count = connection.execute("SELECT COUNT(*) FROM data_quality_issues").fetchone()[0]
        candle_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(historical_candles)").fetchall()
        }
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))
    assert instrument["trading_symbol"] == "LEGACY"
    assert instrument["tick_size"] == 0.05
    assert instrument["tick_size_decimal"] == "0.05"
    assert import_row["import_type"] == "fixture"
    assert import_row["records_inserted"] == 0
    assert market_data["close"] == 100.5
    assert issue_count == 1
    assert {
        "open_decimal",
        "high_decimal",
        "low_decimal",
        "close_decimal",
    }.issubset(candle_columns)


def test_existing_version_5_migrates_and_preserves_unverified_experiments(tmp_path) -> None:
    database_path = tmp_path / "version_5.db"
    _create_version_5_database(database_path)
    legacy_provenance = '{"metadata":{"legacy_run":"retain exactly"}}'
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE runs SET result_json = ? WHERE status = 'COMPLETED'",
            (legacy_provenance,),
        )
        connection.commit()

    initialize_database(database_path)
    initialize_database(database_path)

    assert get_database_status(database_path)["schema_version"] == database.SCHEMA_VERSION
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        experiment = connection.execute(
            "SELECT name, configuration_json, configuration_sha256, configuration_hash_origin "
            "FROM experiments"
        ).fetchone()
        run = connection.execute(
            "SELECT status, result_json FROM runs WHERE status = 'COMPLETED'"
        ).fetchone()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(experiments)").fetchall()
        }
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))
    assert {"configuration_sha256", "configuration_hash_origin"}.issubset(columns)
    assert experiment["name"] == "Existing experiment"
    assert experiment["configuration_json"] == "{}"
    assert experiment["configuration_sha256"] is None
    assert experiment["configuration_hash_origin"] is None
    assert run["status"] == "COMPLETED"
    assert run["result_json"] == legacy_provenance


def test_existing_version_6_imports_migrate_to_unverified_permission_status(tmp_path) -> None:
    database_path = tmp_path / "version_6.db"
    _create_version_6_database(database_path)

    initialize_database(database_path)
    initialize_database(database_path)

    assert get_database_status(database_path)["schema_version"] == database.SCHEMA_VERSION
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        import_row = connection.execute("SELECT id, source_use_status FROM data_imports").fetchone()
        history_count = connection.execute(
            "SELECT COUNT(*) FROM import_source_use_history"
        ).fetchone()[0]
        preserved_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "data_imports",
                "market_data",
                "data_quality_issues",
                "historical_candles",
                "runs",
            )
        }
    assert [row["version"] for row in versions] == list(range(1, database.SCHEMA_VERSION + 1))
    assert import_row["source_use_status"] == "UNVERIFIED"
    assert history_count == 0
    assert preserved_counts == {
        "data_imports": 1,
        "market_data": 1,
        "data_quality_issues": 1,
        "historical_candles": 0,
        "runs": 1,
    }


def test_failed_migration_rolls_back_ddl_and_metadata(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "test_aegis.db"
    _create_version_1_database(database_path)
    monkeypatch.setitem(
        database.MIGRATIONS,
        1,
        (
            "CREATE TABLE partial_migration (id INTEGER PRIMARY KEY)",
            "THIS IS NOT VALID SQL",
        ),
    )

    with pytest.raises(sqlite3.OperationalError):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        partial_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'partial_migration'"
        ).fetchone()
        source = connection.execute("SELECT name FROM sources").fetchone()
    assert [row["version"] for row in versions] == [1]
    assert partial_table is None
    assert source["name"] == "Existing source"


def test_version_4_migration_ddl_failure_rolls_back_and_keeps_database_usable(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "version_4_ddl_failure.db"
    _create_version_4_database(database_path)
    monkeypatch.setitem(
        database.MIGRATIONS,
        4,
        (
            "CREATE TABLE partial_stage_3 (id INTEGER PRIMARY KEY)",
            "ALTER TABLE data_imports ADD COLUMN partial_metadata TEXT",
            "THIS IS NOT VALID SQL",
        ),
    )

    with pytest.raises(sqlite3.OperationalError, match='near "THIS": syntax error'):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        import_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(data_imports)").fetchall()
        }
        instrument = connection.execute(
            "SELECT trading_symbol, tick_size_decimal FROM instruments"
        ).fetchone()
        candle_count = connection.execute("SELECT COUNT(*) FROM market_data").fetchone()[0]
        connection.execute(
            "INSERT INTO sources (name, source_type) VALUES (?, ?)",
            ("Post-rollback check", "test"),
        )
        connection.commit()
        source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    assert [row["version"] for row in versions] == [1, 2, 3, 4]
    assert "partial_stage_3" not in table_names
    assert "partial_metadata" not in import_columns
    assert instrument["trading_symbol"] == "LEGACY"
    assert instrument["tick_size_decimal"] == "0.05"
    assert candle_count == 1
    assert source_count == 2


def test_version_3_migration_ddl_failure_rolls_back_columns_and_preserves_records(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "version_3_ddl_failure.db"
    _create_version_3_database(database_path)
    with closing(get_connection(database_path)) as connection:
        original_instrument = tuple(
            connection.execute(
                "SELECT * FROM instruments WHERE trading_symbol = ?", ("LEGACY",)
            ).fetchone()
        )
        original_market_data = tuple(connection.execute("SELECT * FROM market_data").fetchone())
    monkeypatch.setitem(
        database.MIGRATIONS,
        3,
        (
            "ALTER TABLE instruments ADD COLUMN isin TEXT",
            "ALTER TABLE instruments ADD COLUMN tick_size_decimal TEXT",
            "THIS IS NOT VALID SQL",
        ),
    )

    with pytest.raises(sqlite3.OperationalError, match='near "THIS": syntax error'):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(instruments)").fetchall()
        }
        instrument = tuple(
            connection.execute(
                "SELECT * FROM instruments WHERE trading_symbol = ?", ("LEGACY",)
            ).fetchone()
        )
        market_data = tuple(connection.execute("SELECT * FROM market_data").fetchone())
        connection.execute(
            "INSERT INTO sources (name, source_type) VALUES (?, ?)",
            ("Usability check", "test"),
        )
        connection.commit()
        source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    assert [row["version"] for row in versions] == [1, 2, 3]
    assert not {"isin", "tick_size_decimal", "strike_price", "option_type"} & columns
    assert instrument == original_instrument
    assert market_data == original_market_data
    assert source_count == 2


def test_version_5_migration_ddl_failure_rolls_back_additive_columns(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "version_5_ddl_failure.db"
    _create_version_5_database(database_path)
    monkeypatch.setitem(
        database.MIGRATIONS,
        5,
        (
            "ALTER TABLE experiments ADD COLUMN configuration_sha256 TEXT",
            "THIS IS NOT VALID SQL",
        ),
    )

    with pytest.raises(sqlite3.OperationalError, match='near "THIS": syntax error'):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        experiment = connection.execute(
            "SELECT name, configuration_json FROM experiments"
        ).fetchone()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(experiments)").fetchall()
        }
        connection.execute(
            "INSERT INTO sources (name, source_type) VALUES (?, ?)",
            ("Post-rollback check", "test"),
        )
        connection.commit()
        source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    assert [row["version"] for row in versions] == [1, 2, 3, 4, 5]
    assert "configuration_sha256" not in columns
    assert experiment["name"] == "Existing experiment"
    assert experiment["configuration_json"] == "{}"
    assert source_count == 2


@pytest.mark.parametrize(
    ("previous_version", "target_version"),
    [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)],
)
def test_metadata_insert_failure_rolls_back_migration(
    tmp_path, previous_version: int, target_version: int
) -> None:
    database_path = tmp_path / f"schema_{previous_version}.db"
    if previous_version == 1:
        _create_version_1_database(database_path)
    elif previous_version == 2:
        _create_version_2_database(database_path)
    elif previous_version == 3:
        _create_version_3_database(database_path)
    elif previous_version == 4:
        _create_version_4_database(database_path)
    elif previous_version == 5:
        _create_version_5_database(database_path)
    else:
        _create_version_6_database(database_path)
    _add_metadata_insert_failure(database_path, target_version)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        source = connection.execute("SELECT name FROM sources").fetchone()
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        index_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        instrument_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(instruments)").fetchall()
        }
        experiment_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(experiments)").fetchall()
        }
        import_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(data_imports)").fetchall()
        }
    assert [row["version"] for row in versions] == list(range(1, previous_version + 1))
    assert source["name"] == "Existing source"
    if previous_version == 1:
        assert "instruments" not in table_names
    elif previous_version == 2:
        assert "idx_market_data_lookup" in index_names
        assert "idx_instruments_exchange" in index_names
    elif previous_version == 3:
        assert "isin" not in instrument_columns
    elif previous_version == 4:
        assert "historical_candles" not in table_names
    elif previous_version == 5:
        assert "configuration_sha256" not in experiment_columns
    else:
        assert "import_source_use_history" not in table_names
        assert "source_use_status" not in import_columns


def test_unsupported_future_schema_version_is_rejected(tmp_path) -> None:
    database_path = tmp_path / "future_aegis.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE schema_metadata ("
            "version INTEGER PRIMARY KEY, "
            "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute("INSERT INTO schema_metadata (version) VALUES (99)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RuntimeError, match=f"newer than supported version {database.SCHEMA_VERSION}"
    ):
        initialize_database(database_path)


def test_experiment_relationships(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"
    initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        strategy = connection.execute(
            """
            INSERT INTO strategies (name, description)
            VALUES (?, ?)
            """,
            ("Test Strategy", "A test strategy"),
        )

        hypothesis = connection.execute(
            """
            INSERT INTO hypotheses (title, description)
            VALUES (?, ?)
            """,
            ("Test Hypothesis", "A test hypothesis"),
        )

        connection.execute(
            """
            INSERT INTO experiments (
                name,
                strategy_id,
                hypothesis_id
            )
            VALUES (?, ?, ?)
            """,
            ("Test Experiment", strategy.lastrowid, hypothesis.lastrowid),
        )

        result = connection.execute("SELECT name FROM experiments").fetchone()

        assert result["name"] == "Test Experiment"


def test_version_7_migration_adds_paper_tables_and_preserves_records(tmp_path) -> None:
    database_path = tmp_path / "version_7_paper.db"
    _create_version_6_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        for statement in database.MIGRATIONS[6]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (7)")
        connection.commit()
    finally:
        connection.close()

    initialize_database(database_path)

    status = get_database_status(database_path)
    assert status["schema_version"] == database.SCHEMA_VERSION
    assert set(status["tables"]) == ALL_TABLES
    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert [
            row[0]
            for row in connection.execute("SELECT version FROM schema_metadata ORDER BY version")
        ] == list(range(1, database.SCHEMA_VERSION + 1))


def test_version_8_migration_failure_rolls_back_all_paper_tables(tmp_path) -> None:
    database_path = tmp_path / "version_7_rollback.db"
    _create_version_6_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        for statement in database.MIGRATIONS[6]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (7)")
        connection.execute(
            """CREATE TRIGGER reject_schema_version_8 BEFORE INSERT ON schema_metadata
               WHEN NEW.version = 8 BEGIN SELECT RAISE(ABORT, 'injected v8 failure'); END"""
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected v8 failure"):
        initialize_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        version = connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert version == 7
        assert not any(name.startswith("paper_") for name in tables)
        assert connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 1
    finally:
        connection.close()


def test_upgrade_preserves_phase_8_records_and_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "version_8_forward_runtime.db"
    _create_version_8_database(database_path)
    with closing(get_connection(database_path)) as connection:
        cursor = connection.execute(
            """INSERT INTO paper_accounts
               (name, initial_cash, risk_limits_json, cost_assumptions_json, created_at)
               VALUES ('preserved account', '12345.67', '{}', '{}', '2026-01-05T00:00:00Z')"""
        )
        account_id = cursor.lastrowid
        _insert_phase_8_records(connection, account_id)
        connection.execute(
            """INSERT INTO paper_daily_state (account_id, local_date, starting_equity)
               VALUES (?, '2026-01-05', '12345.67')""",
            (account_id,),
        )
        connection.commit()

    initialize_database(database_path)
    first_status = get_database_status(database_path)
    with closing(get_connection(database_path)) as connection:
        assert connection.execute(
            "SELECT name, initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
        ).fetchone()[:] == ("preserved account", "12345.67")
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT starting_equity FROM paper_daily_state WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )
        assert [
            row[0]
            for row in connection.execute("SELECT version FROM schema_metadata ORDER BY version")
        ] == list(range(1, database.SCHEMA_VERSION + 1))

    initialize_database(database_path)
    second_status = get_database_status(database_path)
    assert first_status == second_status
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_metadata WHERE version = ?",
                (database.SCHEMA_VERSION,),
            ).fetchone()[0]
            == 1
        )


def test_version_9_migration_failure_rolls_back_runtime_tables_and_preserves_phase_8(
    tmp_path,
) -> None:
    database_path = tmp_path / "version_8_forward_runtime_rollback.db"
    _create_version_8_database(database_path)
    with closing(get_connection(database_path)) as connection:
        cursor = connection.execute(
            """INSERT INTO paper_accounts
               (name, initial_cash, risk_limits_json, cost_assumptions_json, created_at)
               VALUES ('rollback account', '100', '{}', '{}', '2026-01-05T00:00:00Z')"""
        )
        account_id = cursor.lastrowid
        connection.commit()
    _add_metadata_insert_failure(database_path, 9)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 8
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == 1
        )
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert not any(name.startswith("forward_") for name in tables)


def test_version_10_migration_failure_rolls_back_only_new_schema_and_preserves_phase_8(
    tmp_path,
) -> None:
    database_path = tmp_path / "version_9_v10_rollback.db"
    _create_version_8_database(database_path)
    with closing(get_connection(database_path)) as connection:
        cursor = connection.execute(
            """INSERT INTO paper_accounts
               (name, initial_cash, risk_limits_json, cost_assumptions_json, created_at)
               VALUES ('v10 rollback account', '12345.67', '{}', '{}', '2026-01-05T00:00:00Z')"""
        )
        account_id = int(cursor.lastrowid)
        _insert_phase_8_records(connection, account_id)
        for statement in database.MIGRATIONS[8]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (9)")
        connection.commit()
    _add_metadata_insert_failure(database_path, 10)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 9
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(paper_accounts)")}
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "halt_class" not in columns
        assert "paper_execution_snapshot_uses" not in tables
        for table in ("paper_accounts", "paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_version_11_migration_preserves_populated_v10_and_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "version_10_to_11.db"
    account_id = _create_populated_version_10_database(database_path)

    initialize_database(database_path)
    first_status = get_database_status(database_path)
    with closing(get_connection(database_path)) as connection:
        versions = [
            row[0]
            for row in connection.execute("SELECT version FROM schema_metadata ORDER BY version")
        ]
        event = connection.execute(
            """SELECT event_id, proposal_id, status, proposal_sha256
               FROM forward_scheduler_events WHERE event_id = 'migration-event'"""
        ).fetchone()
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )
    assert first_status["schema_version"] == database.SCHEMA_VERSION
    assert versions == list(range(1, database.SCHEMA_VERSION + 1))
    assert tuple(event) == ("migration-event", "migration-proposal", "PENDING", None)

    initialize_database(database_path)

    assert get_database_status(database_path) == first_status
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_metadata WHERE version = ?",
                (database.SCHEMA_VERSION,),
            ).fetchone()[0]
            == 1
        )


def test_version_12_migration_preserves_populated_v11_and_is_idempotent(tmp_path) -> None:
    database_path = tmp_path / "version_11_to_12.db"
    account_id = _create_populated_version_11_database(database_path)

    initialize_database(database_path)
    first_status = get_database_status(database_path)
    with closing(get_connection(database_path)) as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(forward_scheduler_events)")
        }
        event = connection.execute(
            """SELECT event_id, proposal_id, status, proposal_sha256, proposal_json,
                      proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = 'migration-event'"""
        ).fetchone()
        assert (
            connection.execute("SELECT COUNT(*) FROM forward_execution_authorizations").fetchone()[
                0
            ]
            == 0
        )
        assert {
            "proposal_sha256",
            "proposal_json",
            "proposal_binding_state",
        }.issubset(columns)
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )
    assert first_status["schema_version"] == database.SCHEMA_VERSION
    assert tuple(event) == (
        "migration-event",
        "migration-proposal",
        "PENDING",
        None,
        None,
        "LEGACY_UNVERIFIABLE",
    )

    initialize_database(database_path)

    assert get_database_status(database_path) == first_status
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_metadata WHERE version = ?",
                (database.SCHEMA_VERSION,),
            ).fetchone()[0]
            == 1
        )


def test_version_13_migration_classifies_pending_event_bindings_and_is_idempotent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "version_12_to_13.db"
    account_id = _create_populated_version_12_database(database_path)
    with closing(get_connection(database_path)) as connection:
        legacy_event = connection.execute(
            "SELECT * FROM forward_scheduler_events WHERE event_id = 'migration-event'"
        ).fetchone()
        connection.execute(
            """INSERT INTO forward_scheduler_events
               (event_id, runtime_id, instrument_id, interval, candle_timestamp,
                decision_timestamp, status, proposal_id, proposal_sha256, proposal_json,
                result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, NULL, ?)""",
            (
                "migration-bound-event",
                legacy_event["runtime_id"],
                legacy_event["instrument_id"],
                legacy_event["interval"],
                legacy_event["candle_timestamp"],
                legacy_event["decision_timestamp"],
                "bound-migration-proposal",
                "a" * 64,
                "{}",
                legacy_event["created_at"],
            ),
        )
        connection.execute(
            """INSERT INTO forward_scheduler_events
               (event_id, runtime_id, instrument_id, interval, candle_timestamp,
                decision_timestamp, status, proposal_id, proposal_sha256, proposal_json,
                result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, NULL, ?)""",
            (
                "migration-partial-event",
                legacy_event["runtime_id"],
                legacy_event["instrument_id"],
                legacy_event["interval"],
                legacy_event["candle_timestamp"],
                legacy_event["decision_timestamp"],
                "partial-migration-proposal",
                "b" * 64,
                legacy_event["created_at"],
            ),
        )
        connection.commit()

    monkeypatch.setattr(database, "SCHEMA_VERSION", 13)
    initialize_database(database_path)
    first_status = get_database_status(database_path)
    with closing(get_connection(database_path)) as connection:
        states = {
            row["event_id"]: row["proposal_binding_state"]
            for row in connection.execute(
                """SELECT event_id, proposal_binding_state FROM forward_scheduler_events
                   WHERE event_id IN (
                       'migration-event', 'migration-partial-event', 'migration-bound-event'
                   )"""
            )
        }
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )
    assert first_status["schema_version"] == 13
    assert states == {
        "migration-event": "LEGACY_UNVERIFIABLE",
        "migration-partial-event": "LEGACY_UNVERIFIABLE",
        "migration-bound-event": "BOUND",
    }

    initialize_database(database_path)

    assert get_database_status(database_path) == first_status
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_metadata WHERE version = 13"
            ).fetchone()[0]
            == 1
        )


def _create_populated_version_13_database(database_path) -> int:
    account_id = _create_populated_version_12_database(database_path)
    with closing(get_connection(database_path)) as connection:
        for statement in database.MIGRATIONS[12]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (13)")
        connection.commit()
    return account_id


def test_version_14_migration_installs_binding_guards_idempotently(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "version_13_to_14.db"
    _create_populated_version_13_database(database_path)

    monkeypatch.setattr(database, "SCHEMA_VERSION", 14)
    initialize_database(database_path)
    first_status = get_database_status(database_path)
    with closing(get_connection(database_path)) as connection:
        triggers = connection.execute(
            """SELECT name FROM sqlite_master WHERE type = 'trigger'
               AND name = 'protect_forward_event_proposal_binding'"""
        ).fetchall()
        event = connection.execute(
            """SELECT status, proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = 'migration-event'"""
        ).fetchone()
    assert first_status["schema_version"] == 14
    assert len(triggers) == 1
    assert tuple(event) == ("PENDING", None, None, "LEGACY_UNVERIFIABLE")

    initialize_database(database_path)

    assert get_database_status(database_path) == first_status
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_metadata WHERE version = 14"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'
                   AND name = 'protect_forward_event_proposal_binding'"""
            ).fetchone()[0]
            == 1
        )


def test_version_14_migration_failure_rolls_back_guards_and_marker(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "version_13_to_14_rollback.db"
    account_id = _create_populated_version_13_database(database_path)
    monkeypatch.setattr(database, "SCHEMA_VERSION", 14)
    _add_metadata_insert_failure(database_path, 14)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 13
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'
                   AND name = 'protect_forward_event_proposal_binding'"""
            ).fetchone()[0]
            == 0
        )
        event = connection.execute(
            """SELECT status, proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = 'migration-event'"""
        ).fetchone()
        assert tuple(event) == ("PENDING", None, None, "LEGACY_UNVERIFIABLE")
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )

    with closing(get_connection(database_path)) as connection:
        connection.execute("DROP TRIGGER reject_schema_version_14")
        connection.commit()
    initialize_database(database_path)
    assert get_database_status(database_path)["schema_version"] == 14


def _create_populated_version_14_database(database_path) -> int:
    account_id = _create_populated_version_13_database(database_path)
    with closing(get_connection(database_path)) as connection:
        for statement in database.MIGRATIONS[13]:
            database._execute_statements(connection, statement)
        connection.execute("INSERT INTO schema_metadata (version) VALUES (14)")
        connection.commit()
    return account_id


def test_version_15_migration_installs_event_insert_guards_idempotently(tmp_path) -> None:
    database_path = tmp_path / "version_14_to_15.db"
    _create_populated_version_14_database(database_path)

    initialize_database(database_path)
    first_status = get_database_status(database_path)
    with closing(get_connection(database_path)) as connection:
        triggers = {
            row["name"]
            for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type = 'trigger'
                   AND name IN (
                       'authorize_forward_scheduler_event_insert',
                       'consume_forward_scheduler_event_insert',
                       'consume_forward_event_proposal_binding',
                       'protect_forward_event_proposal_binding'
                   )"""
            )
        }
    assert first_status["schema_version"] == database.SCHEMA_VERSION
    assert triggers == {
        "authorize_forward_scheduler_event_insert",
        "consume_forward_scheduler_event_insert",
        "consume_forward_event_proposal_binding",
        "protect_forward_event_proposal_binding",
    }

    initialize_database(database_path)

    assert get_database_status(database_path) == first_status
    with closing(get_connection(database_path)) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_metadata WHERE version = 15"
            ).fetchone()[0]
            == 1
        )


def test_version_15_migration_failure_rolls_back_insert_guards_and_marker(tmp_path) -> None:
    database_path = tmp_path / "version_14_to_15_rollback.db"
    account_id = _create_populated_version_14_database(database_path)
    _add_metadata_insert_failure(database_path, 15)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 14
        trigger_names = {
            row["name"]
            for row in connection.execute(
                """SELECT name FROM sqlite_master WHERE type = 'trigger'
                   AND name IN (
                       'authorize_forward_scheduler_event_insert',
                       'consume_forward_scheduler_event_insert',
                       'consume_forward_event_proposal_binding',
                       'protect_forward_event_proposal_binding'
                   )"""
            )
        }
        assert trigger_names == {"protect_forward_event_proposal_binding"}
        event = connection.execute(
            """SELECT status, proposal_sha256, proposal_json, proposal_binding_state
               FROM forward_scheduler_events WHERE event_id = 'migration-event'"""
        ).fetchone()
        assert tuple(event) == ("PENDING", None, None, "LEGACY_UNVERIFIABLE")
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )

    with closing(get_connection(database_path)) as connection:
        connection.execute("DROP TRIGGER reject_schema_version_15")
        connection.commit()
    initialize_database(database_path)
    assert get_database_status(database_path)["schema_version"] == database.SCHEMA_VERSION


def test_version_13_migration_failure_rolls_back_marker_and_preserves_v12_data(
    tmp_path,
) -> None:
    database_path = tmp_path / "version_12_to_13_rollback.db"
    account_id = _create_populated_version_12_database(database_path)
    _add_metadata_insert_failure(database_path, 13)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 12
        event_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(forward_scheduler_events)")
        }
        assert "proposal_binding_state" not in event_columns
        event = connection.execute(
            """SELECT status, proposal_sha256, proposal_json FROM forward_scheduler_events
               WHERE event_id = 'migration-event'"""
        ).fetchone()
        assert tuple(event) == ("PENDING", None, None)
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_version_12_migration_failure_rolls_back_new_schema_and_preserves_v11_data(
    tmp_path,
) -> None:
    database_path = tmp_path / "version_11_to_12_rollback.db"
    account_id = _create_populated_version_11_database(database_path)
    _add_metadata_insert_failure(database_path, 12)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 11
        event_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(forward_scheduler_events)")
        }
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "proposal_json" not in event_columns
        assert "forward_execution_authorizations" not in tables
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_version_11_migration_failure_rolls_back_column_and_preserves_v10_data(tmp_path) -> None:
    database_path = tmp_path / "version_10_to_11_rollback.db"
    account_id = _create_populated_version_10_database(database_path)
    _add_metadata_insert_failure(database_path, 11)

    with pytest.raises(sqlite3.IntegrityError, match="injected metadata insertion failure"):
        initialize_database(database_path)

    with closing(get_connection(database_path)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_metadata").fetchone()[0] == 10
        event_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(forward_scheduler_events)")
        }
        assert "proposal_sha256" not in event_columns
        event = connection.execute(
            """SELECT event_id, proposal_id, status FROM forward_scheduler_events
               WHERE event_id = 'migration-event'"""
        ).fetchone()
        assert tuple(event) == ("migration-event", "migration-proposal", "PENDING")
        assert (
            connection.execute(
                "SELECT initial_cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            == "12345.67"
        )
        for table in ("paper_orders", "paper_fills", "paper_ledger_entries"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
