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


def test_fresh_database_initialization(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"

    initialize_database(database_path)

    status = get_database_status(database_path)

    assert status["exists"] is True
    assert status["schema_version"] == 3

    assert set(status["tables"]) == ALL_TABLES
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
    assert [row["version"] for row in versions] == [1, 2, 3]


def test_version_1_migration_preserves_phase_1_records(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"
    _create_version_1_database(database_path)

    initialize_database(database_path)

    status = get_database_status(database_path)
    assert status["schema_version"] == 3
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
    assert [row["version"] for row in versions] == [1, 2, 3]
    assert source["name"] == "Existing source"
    assert hypothesis["title"] == "Existing hypothesis"
    assert hypothesis["description"] == "Preserve this record"
    assert set(phase_1_counts.values()) == {1}


def test_database_initialization_is_repeatable(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"

    initialize_database(database_path)
    initialize_database(database_path)

    status = get_database_status(database_path)

    assert status["schema_version"] == 3
    with closing(get_connection(database_path)) as connection:
        versions = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        sources_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    assert [row["version"] for row in versions] == [1, 2, 3]
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
            for table in ("instruments", "market_data")
        }

    assert {
        "idx_market_data_timestamp",
        "idx_data_imports_status",
        "idx_quality_issues_import",
    }.issubset(index_names)
    assert "idx_market_data_lookup" not in index_names
    assert "idx_instruments_exchange" not in index_names
    assert ("exchange", "trading_symbol") in unique_index_columns["instruments"]
    assert ("instrument_id", "interval", "candle_timestamp") in unique_index_columns["market_data"]


def test_existing_version_2_migrates_to_version_3_and_removes_redundant_indexes(
    tmp_path,
) -> None:
    database_path = tmp_path / "test_aegis.db"
    _create_version_2_database(database_path)

    initialize_database(database_path)

    status = get_database_status(database_path)
    assert status["schema_version"] == 3
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
    assert [row["version"] for row in versions] == [1, 2, 3]
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


@pytest.mark.parametrize(
    ("previous_version", "target_version"),
    [(1, 2), (2, 3)],
)
def test_metadata_insert_failure_rolls_back_migration(
    tmp_path, previous_version: int, target_version: int
) -> None:
    database_path = tmp_path / f"schema_{previous_version}.db"
    if previous_version == 1:
        _create_version_1_database(database_path)
    else:
        _create_version_2_database(database_path)
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
    assert [row["version"] for row in versions] == list(range(1, previous_version + 1))
    assert source["name"] == "Existing source"
    if previous_version == 1:
        assert "instruments" not in table_names
    else:
        assert "idx_market_data_lookup" in index_names
        assert "idx_instruments_exchange" in index_names


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

    with pytest.raises(RuntimeError, match="newer than supported version 3"):
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
