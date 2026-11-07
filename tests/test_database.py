"""Tests for the Aegis SQLite database."""

import sqlite3

from aegis.database import (
    get_connection,
    get_database_status,
    initialize_database,
)


def test_database_initialization(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"

    initialize_database(database_path)

    status = get_database_status(database_path)

    assert status["exists"] is True
    assert status["schema_version"] == 1

    expected_tables = {
        "sources",
        "hypotheses",
        "strategies",
        "experiments",
        "runs",
        "schema_metadata",
    }

    assert expected_tables.issubset(set(status["tables"]))


def test_database_initialization_is_repeatable(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"

    initialize_database(database_path)
    initialize_database(database_path)

    status = get_database_status(database_path)

    assert status["schema_version"] == 1


def test_foreign_keys_are_enforced(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"
    initialize_database(database_path)

    with get_connection(database_path) as connection:
        try:
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
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Foreign key constraint was not enforced")


def test_experiment_relationships(tmp_path) -> None:
    database_path = tmp_path / "test_aegis.db"
    initialize_database(database_path)

    with get_connection(database_path) as connection:
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
