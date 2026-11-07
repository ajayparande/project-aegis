"""Local SQLite database for Project Aegis."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0',
    status TEXT NOT NULL DEFAULT 'DRAFT',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    strategy_id INTEGER,
    hypothesis_id INTEGER,
    configuration_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'CREATED',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id),
    FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'CREATED',
    result_json TEXT,
    error_message TEXT,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

CREATE TABLE IF NOT EXISTS schema_metadata (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection(database_path: Path) -> sqlite3.Connection:
    """Create a safe SQLite connection and ensure its parent exists."""

    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")

    return connection


def initialize_database(database_path: Path) -> None:
    """Create the Aegis database schema if it does not exist."""

    with get_connection(database_path) as connection:
        connection.executescript(SCHEMA)

        connection.execute(
            """
            INSERT OR IGNORE INTO schema_metadata (version)
            VALUES (?)
            """,
            (SCHEMA_VERSION,),
        )


def get_database_status(database_path: Path) -> dict[str, object]:
    """Return basic database health and table information."""

    if not database_path.exists():
        return {
            "exists": False,
            "schema_version": None,
            "tables": [],
        }

    with get_connection(database_path) as connection:
        version_row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_metadata"
        ).fetchone()

        table_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        return {
            "exists": True,
            "schema_version": version_row["version"],
            "tables": [row["name"] for row in table_rows],
        }
