"""Local SQLite database for Project Aegis."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 7

PHASE_1_SCHEMA = """
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

MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    trading_symbol TEXT NOT NULL,
    instrument_token TEXT,
    instrument_name TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    segment TEXT,
    lot_size INTEGER NOT NULL DEFAULT 1,
    tick_size REAL,
    expiry_date TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (exchange, trading_symbol)
);

CREATE TABLE IF NOT EXISTS data_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    import_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    records_received INTEGER NOT NULL DEFAULT 0,
    records_inserted INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS market_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL,
    import_id INTEGER,
    interval TEXT NOT NULL,
    candle_timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id),
    FOREIGN KEY (import_id) REFERENCES data_imports(id),
    UNIQUE (instrument_id, interval, candle_timestamp),
    CHECK (open > 0),
    CHECK (high > 0),
    CHECK (low > 0),
    CHECK (close > 0),
    CHECK (volume >= 0),
    CHECK (high >= low),
    CHECK (high >= open AND high >= close),
    CHECK (low <= open AND low <= close)
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER,
    instrument_id INTEGER,
    interval TEXT,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'WARNING',
    description TEXT NOT NULL,
    affected_timestamp TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    FOREIGN KEY (import_id) REFERENCES data_imports(id),
    FOREIGN KEY (instrument_id) REFERENCES instruments(id)
);

CREATE INDEX IF NOT EXISTS idx_market_data_timestamp
ON market_data (candle_timestamp);

CREATE INDEX IF NOT EXISTS idx_data_imports_status
ON data_imports (status);

CREATE INDEX IF NOT EXISTS idx_quality_issues_import
ON data_quality_issues (import_id, severity);
""",
    ),
    2: (
        "DROP INDEX IF EXISTS idx_market_data_lookup",
        "DROP INDEX IF EXISTS idx_instruments_exchange",
    ),
    3: (
        "ALTER TABLE instruments ADD COLUMN isin TEXT",
        "ALTER TABLE instruments ADD COLUMN tick_size_decimal TEXT",
        "ALTER TABLE instruments ADD COLUMN strike_price TEXT",
        "ALTER TABLE instruments ADD COLUMN option_type TEXT",
        (
            "UPDATE instruments SET tick_size_decimal = CAST(tick_size AS TEXT) "
            "WHERE tick_size IS NOT NULL"
        ),
    ),
    4: (
        "ALTER TABLE data_imports ADD COLUMN instrument_id INTEGER REFERENCES instruments(id)",
        "ALTER TABLE data_imports ADD COLUMN original_filename TEXT",
        "ALTER TABLE data_imports ADD COLUMN file_sha256 TEXT",
        "ALTER TABLE data_imports ADD COLUMN requested_exchange TEXT",
        "ALTER TABLE data_imports ADD COLUMN requested_trading_symbol TEXT",
        "ALTER TABLE data_imports ADD COLUMN interval TEXT",
        "ALTER TABLE data_imports ADD COLUMN total_rows INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE data_imports ADD COLUMN accepted_rows INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE data_imports ADD COLUMN rejected_rows INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE data_imports ADD COLUMN skipped_rows INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE data_imports ADD COLUMN duplicate_rows INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE data_imports ADD COLUMN same_file_reimport INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE data_imports ADD COLUMN error_summary TEXT",
        "ALTER TABLE data_quality_issues ADD COLUMN source_row_number INTEGER",
        """
CREATE TABLE historical_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL,
    import_id INTEGER NOT NULL,
    interval TEXT NOT NULL CHECK (interval IN ('1m', '5m', '15m', '1h', '1d')),
    candle_timestamp TEXT NOT NULL,
    open_decimal TEXT NOT NULL,
    high_decimal TEXT NOT NULL,
    low_decimal TEXT NOT NULL,
    close_decimal TEXT NOT NULL,
    volume INTEGER NOT NULL CHECK (typeof(volume) = 'integer' AND volume >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (instrument_id) REFERENCES instruments(id),
    FOREIGN KEY (import_id) REFERENCES data_imports(id),
    UNIQUE (instrument_id, interval, candle_timestamp)
);

CREATE INDEX idx_historical_candles_import
ON historical_candles (import_id);

CREATE INDEX idx_data_imports_file_identity
ON data_imports (file_sha256, requested_exchange, requested_trading_symbol, interval, status);
""",
    ),
    5: (
        "ALTER TABLE experiments ADD COLUMN configuration_sha256 TEXT",
        "ALTER TABLE experiments ADD COLUMN configuration_hash_origin TEXT",
    ),
    6: (
        """
        ALTER TABLE data_imports
        ADD COLUMN source_use_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (source_use_status IN ('VERIFIED_PERMITTED', 'UNVERIFIED', 'PROHIBITED'))
        """,
        """
        CREATE TABLE import_source_use_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            previous_status TEXT CHECK (
                previous_status IS NULL OR previous_status IN (
                    'VERIFIED_PERMITTED', 'UNVERIFIED', 'PROHIBITED'
                )
            ),
            new_status TEXT NOT NULL CHECK (
                new_status IN ('VERIFIED_PERMITTED', 'UNVERIFIED', 'PROHIBITED')
            ),
            declared_by TEXT NOT NULL,
            rationale TEXT NOT NULL,
            declared_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (import_id) REFERENCES data_imports(id)
        )
        """,
        """
        CREATE INDEX idx_import_source_use_history_import
        ON import_source_use_history (import_id, id)
        """,
    ),
}


def _execute_statements(connection: sqlite3.Connection, script: str) -> None:
    """Execute a semicolon-delimited schema script within the caller's transaction."""

    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)


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
    """Create or upgrade the Aegis database schema."""

    connection = get_connection(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        if "schema_metadata" not in table_names:
            existing_tables = table_names - {"sqlite_sequence"}
            if existing_tables:
                raise RuntimeError(
                    "Database has tables but no schema_metadata; refusing to infer its version."
                )
            _execute_statements(connection, PHASE_1_SCHEMA)
            connection.execute("INSERT INTO schema_metadata (version) VALUES (1)")

        version_rows = connection.execute(
            "SELECT version FROM schema_metadata ORDER BY version"
        ).fetchall()
        if not version_rows:
            raise RuntimeError("Database schema_metadata contains no applied version.")

        current_version = int(version_rows[-1]["version"])
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current_version} is newer than supported "
                f"version {SCHEMA_VERSION}."
            )
        if current_version < 1:
            raise RuntimeError(f"Unsupported database schema version {current_version}.")

        while current_version < SCHEMA_VERSION:
            statements = MIGRATIONS.get(current_version)
            if statements is None:
                raise RuntimeError(
                    f"No migration is available from schema version {current_version}."
                )
            for statement in statements:
                _execute_statements(connection, statement)
            current_version += 1
            connection.execute(
                "INSERT INTO schema_metadata (version) VALUES (?)",
                (current_version,),
            )

        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def get_database_status(database_path: Path) -> dict[str, object]:
    """Return basic database health and table information."""

    if not database_path.exists():
        return {
            "exists": False,
            "schema_version": None,
            "tables": [],
        }

    connection = get_connection(database_path)
    try:
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
    finally:
        connection.close()
