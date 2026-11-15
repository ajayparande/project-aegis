"""Local SQLite database for Project Aegis."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SCHEMA_VERSION = 15

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
    7: (
        """
        CREATE TABLE paper_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            initial_cash TEXT NOT NULL,
            risk_limits_json TEXT NOT NULL,
            cost_assumptions_json TEXT NOT NULL,
            halted INTEGER NOT NULL DEFAULT 0 CHECK (halted IN (0, 1)),
            halt_reason TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE paper_orders (
            order_id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            instrument_id INTEGER,
            requested_instrument_id INTEGER NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            status TEXT NOT NULL CHECK (status IN ('FILLED', 'REJECTED')),
            reason TEXT,
            signal_timestamp TEXT NOT NULL,
            submitted_timestamp TEXT NOT NULL,
            execution_timestamp TEXT NOT NULL,
            reference_price TEXT NOT NULL,
            fill_price TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (account_id, idempotency_key),
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        "CREATE INDEX idx_paper_orders_account_time ON paper_orders (account_id, created_at)",
        """
        CREATE TABLE paper_fills (
            fill_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL UNIQUE,
            instrument_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            fill_price TEXT NOT NULL,
            fees_json TEXT NOT NULL,
            execution_costs_json TEXT NOT NULL,
            execution_timestamp TEXT NOT NULL,
            FOREIGN KEY (order_id) REFERENCES paper_orders(order_id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        """
        CREATE TABLE paper_ledger_entries (
            entry_id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            fill_id TEXT NOT NULL,
            entry_type TEXT NOT NULL CHECK (entry_type IN ('BUY_CASH_DEBIT', 'SELL_CASH_CREDIT')),
            amount TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (order_id, entry_type),
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (order_id) REFERENCES paper_orders(order_id),
            FOREIGN KEY (fill_id) REFERENCES paper_fills(fill_id)
        )
        """,
        "CREATE INDEX idx_paper_ledger_account_time ON paper_ledger_entries (account_id, created_at)",
        """
        CREATE TABLE paper_positions (
            account_id INTEGER NOT NULL,
            instrument_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            average_entry_price TEXT NOT NULL,
            remaining_entry_fees TEXT NOT NULL,
            realized_net_pnl TEXT NOT NULL,
            PRIMARY KEY (account_id, instrument_id),
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        """
        CREATE TABLE paper_realized_pnl (
            order_id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            instrument_id INTEGER NOT NULL,
            realized_net_pnl TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            FOREIGN KEY (order_id) REFERENCES paper_orders(order_id),
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        "CREATE INDEX idx_paper_realized_account_time ON paper_realized_pnl (account_id, closed_at)",
        """
        CREATE TABLE paper_marks (
            account_id INTEGER NOT NULL,
            instrument_id INTEGER NOT NULL,
            mark_price TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            source_import_id INTEGER NOT NULL,
            PRIMARY KEY (account_id, instrument_id),
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id),
            FOREIGN KEY (source_import_id) REFERENCES data_imports(id)
        )
        """,
        """
        CREATE TABLE paper_order_events (
            event_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            event_sequence INTEGER NOT NULL CHECK (event_sequence > 0),
            event_type TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            details_json TEXT NOT NULL,
            UNIQUE (order_id, event_sequence),
            FOREIGN KEY (order_id) REFERENCES paper_orders(order_id)
        )
        """,
        "CREATE INDEX idx_paper_order_events_order ON paper_order_events (order_id, event_sequence)",
        """
        CREATE TABLE paper_daily_state (
            account_id INTEGER NOT NULL,
            local_date TEXT NOT NULL,
            starting_equity TEXT NOT NULL,
            PRIMARY KEY (account_id, local_date),
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id)
        )
        """,
    ),
    8: (
        """
        CREATE TABLE forward_runtimes (
            runtime_id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            config_json TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            state_json TEXT NOT NULL,
            state_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('RUNNING', 'DEGRADED', 'HALTED')),
            last_event_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id)
        )
        """,
        """
        CREATE TABLE forward_runtime_imports (
            runtime_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            interval TEXT NOT NULL CHECK (interval IN ('1m', '5m', '15m')),
            import_id INTEGER NOT NULL UNIQUE,
            PRIMARY KEY (runtime_id, instrument_id, interval),
            FOREIGN KEY (runtime_id) REFERENCES forward_runtimes(runtime_id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id),
            FOREIGN KEY (import_id) REFERENCES data_imports(id)
        )
        """,
        """
        CREATE TABLE forward_quotes (
            runtime_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            sequence INTEGER,
            quote_timestamp TEXT,
            received_at TEXT NOT NULL,
            price_decimal TEXT,
            quantity INTEGER,
            accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
            reason TEXT,
            PRIMARY KEY (runtime_id, instrument_id, event_id),
            FOREIGN KEY (runtime_id) REFERENCES forward_runtimes(runtime_id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        """
        CREATE TABLE forward_runtime_candles (
            runtime_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            interval TEXT NOT NULL CHECK (interval IN ('1m', '5m', '15m')),
            candle_timestamp TEXT NOT NULL,
            open_decimal TEXT NOT NULL,
            high_decimal TEXT NOT NULL,
            low_decimal TEXT NOT NULL,
            close_decimal TEXT NOT NULL,
            volume INTEGER NOT NULL CHECK (volume >= 0),
            completed_at TEXT NOT NULL,
            PRIMARY KEY (runtime_id, instrument_id, interval, candle_timestamp),
            FOREIGN KEY (runtime_id) REFERENCES forward_runtimes(runtime_id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        """
        CREATE TABLE forward_scheduler_events (
            event_id TEXT PRIMARY KEY,
            runtime_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            interval TEXT NOT NULL,
            candle_timestamp TEXT NOT NULL,
            decision_timestamp TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('PENDING', 'HOLD', 'FILLED', 'REJECTED')),
            proposal_id TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (runtime_id) REFERENCES forward_runtimes(runtime_id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        """
        CREATE TABLE forward_trade_controls (
            runtime_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            proposal_id TEXT NOT NULL,
            stop_loss TEXT,
            target_price TEXT,
            active INTEGER NOT NULL CHECK (active IN (0, 1)),
            exit_event_id TEXT,
            PRIMARY KEY (runtime_id, instrument_id),
            FOREIGN KEY (runtime_id) REFERENCES forward_runtimes(runtime_id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id)
        )
        """,
        "CREATE INDEX idx_forward_quotes_cursor ON forward_quotes (runtime_id, instrument_id, sequence)",
        "CREATE INDEX idx_forward_candles_completed ON forward_runtime_candles (runtime_id, completed_at)",
    ),
    9: (
        """
        ALTER TABLE paper_accounts
        ADD COLUMN halt_class TEXT NOT NULL DEFAULT 'UNCLASSIFIED'
        CHECK (halt_class IN ('NONE', 'RISK', 'INTEGRITY', 'UNCLASSIFIED'))
        """,
        "UPDATE paper_accounts SET halt_class = 'NONE' WHERE halted = 0",
        """
        CREATE TABLE paper_execution_snapshot_uses (
            snapshot_id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            instrument_id INTEGER NOT NULL,
            import_id INTEGER NOT NULL,
            runtime_id TEXT,
            event_id TEXT,
            proposal_id TEXT,
            snapshot_sha256 TEXT NOT NULL,
            order_id TEXT NOT NULL UNIQUE,
            used_at TEXT NOT NULL,
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id),
            FOREIGN KEY (import_id) REFERENCES data_imports(id),
            FOREIGN KEY (runtime_id) REFERENCES forward_runtimes(runtime_id),
            FOREIGN KEY (order_id) REFERENCES paper_orders(order_id)
        )
        """,
        "CREATE INDEX idx_execution_snapshot_uses_account ON paper_execution_snapshot_uses (account_id, used_at)",
        "CREATE INDEX idx_execution_snapshot_uses_runtime_event ON paper_execution_snapshot_uses (runtime_id, event_id)",
    ),
    10: ("ALTER TABLE forward_scheduler_events ADD COLUMN proposal_sha256 TEXT",),
    11: (
        "ALTER TABLE forward_scheduler_events ADD COLUMN proposal_json TEXT",
        """
        CREATE TABLE forward_execution_authorizations (
            event_id TEXT PRIMARY KEY,
            runtime_id TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            proposal_id TEXT NOT NULL,
            proposal_sha256 TEXT NOT NULL,
            authorization_kind TEXT NOT NULL
                CHECK (authorization_kind IN ('PROPOSAL', 'STOP_TARGET_EXIT')),
            instrument_id INTEGER NOT NULL,
            interval TEXT NOT NULL,
            snapshot_id TEXT NOT NULL UNIQUE,
            snapshot_sha256 TEXT NOT NULL,
            quote_event_id TEXT NOT NULL,
            quote_timestamp TEXT NOT NULL,
            received_at TEXT NOT NULL,
            import_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('AUTHORIZED', 'CONSUMED', 'REVOKED')),
            authorized_at TEXT NOT NULL,
            consumed_order_id TEXT UNIQUE,
            FOREIGN KEY (event_id) REFERENCES forward_scheduler_events(event_id),
            FOREIGN KEY (runtime_id) REFERENCES forward_runtimes(runtime_id),
            FOREIGN KEY (account_id) REFERENCES paper_accounts(id),
            FOREIGN KEY (instrument_id) REFERENCES instruments(id),
            FOREIGN KEY (import_id) REFERENCES data_imports(id),
            FOREIGN KEY (consumed_order_id) REFERENCES paper_orders(order_id)
        )
        """,
        (
            "CREATE INDEX idx_forward_execution_authorizations_account "
            "ON forward_execution_authorizations (account_id, status)"
        ),
    ),
    12: (
        """
        ALTER TABLE forward_scheduler_events
        ADD COLUMN proposal_binding_state TEXT NOT NULL DEFAULT 'UNBOUND'
        CHECK (proposal_binding_state IN ('UNBOUND', 'BOUND', 'LEGACY_UNVERIFIABLE'))
        """,
        """
        UPDATE forward_scheduler_events
        SET proposal_binding_state = CASE
            WHEN proposal_sha256 IS NOT NULL AND proposal_json IS NOT NULL THEN 'BOUND'
            WHEN status = 'PENDING' THEN 'LEGACY_UNVERIFIABLE'
            ELSE 'UNBOUND'
        END
        """,
    ),
    13: (
        """
        CREATE TRIGGER protect_forward_event_proposal_binding
        BEFORE UPDATE OF proposal_binding_state, proposal_sha256, proposal_json
        ON forward_scheduler_events
        WHEN
            (
                OLD.proposal_binding_state = 'LEGACY_UNVERIFIABLE'
                AND (
                    NEW.proposal_binding_state != OLD.proposal_binding_state
                    OR NEW.proposal_sha256 IS NOT OLD.proposal_sha256
                    OR NEW.proposal_json IS NOT OLD.proposal_json
                )
            )
            OR (
                OLD.proposal_binding_state = 'BOUND'
                AND (
                    NEW.proposal_binding_state != OLD.proposal_binding_state
                    OR NEW.proposal_sha256 IS NOT OLD.proposal_sha256
                    OR NEW.proposal_json IS NOT OLD.proposal_json
                )
            )
            OR (
                OLD.proposal_binding_state = 'UNBOUND'
                AND (
                    NEW.proposal_binding_state NOT IN ('UNBOUND', 'BOUND')
                    OR (
                        (
                            NEW.proposal_binding_state = 'BOUND'
                            OR NEW.proposal_sha256 IS NOT OLD.proposal_sha256
                            OR NEW.proposal_json IS NOT OLD.proposal_json
                        )
                        AND NOT (
                            NEW.proposal_binding_state = 'BOUND'
                            AND OLD.status = 'PENDING'
                            AND NEW.status = 'PENDING'
                            AND OLD.proposal_sha256 IS NULL
                            AND OLD.proposal_json IS NULL
                            AND NEW.proposal_sha256 IS NOT NULL
                            AND NEW.proposal_json IS NOT NULL
                            AND _aegis_proposal_binding_authorized() = 1
                        )
                    )
                )
            )
        BEGIN
            SELECT RAISE(ABORT, 'proposal binding transition is immutable or unauthorized');
        END
        """,
    ),
    14: (
        "DROP TRIGGER protect_forward_event_proposal_binding",
        """
        CREATE TRIGGER protect_forward_event_proposal_binding
        BEFORE UPDATE OF proposal_binding_state, proposal_sha256, proposal_json
        ON forward_scheduler_events
        WHEN
            (
                OLD.proposal_binding_state = 'LEGACY_UNVERIFIABLE'
                AND (
                    NEW.proposal_binding_state != OLD.proposal_binding_state
                    OR NEW.proposal_sha256 IS NOT OLD.proposal_sha256
                    OR NEW.proposal_json IS NOT OLD.proposal_json
                )
            )
            OR (
                OLD.proposal_binding_state = 'BOUND'
                AND (
                    NEW.proposal_binding_state != OLD.proposal_binding_state
                    OR NEW.proposal_sha256 IS NOT OLD.proposal_sha256
                    OR NEW.proposal_json IS NOT OLD.proposal_json
                )
            )
            OR (
                OLD.proposal_binding_state = 'UNBOUND'
                AND (
                    NEW.proposal_binding_state NOT IN ('UNBOUND', 'BOUND')
                    OR (
                        (
                            NEW.proposal_binding_state = 'BOUND'
                            OR NEW.proposal_sha256 IS NOT OLD.proposal_sha256
                            OR NEW.proposal_json IS NOT OLD.proposal_json
                        )
                        AND NOT (
                            NEW.proposal_binding_state = 'BOUND'
                            AND OLD.status = 'PENDING'
                            AND NEW.status = 'PENDING'
                            AND OLD.proposal_sha256 IS NULL
                            AND OLD.proposal_json IS NULL
                            AND NEW.proposal_sha256 IS NOT NULL
                            AND NEW.proposal_json IS NOT NULL
                            AND _aegis_validate_event_write(
                                'BIND_PROPOSAL', NEW.event_id, NEW.runtime_id,
                                NEW.instrument_id, NEW.interval, NEW.candle_timestamp,
                                NEW.decision_timestamp, NEW.status, NEW.proposal_id,
                                NEW.proposal_sha256, NEW.proposal_json,
                                NEW.proposal_binding_state, NEW.result_json, NEW.created_at
                            ) = 1
                        )
                    )
                )
            )
        BEGIN
            SELECT RAISE(ABORT, 'proposal binding transition is immutable or unauthorized');
        END
        """,
        """
        CREATE TRIGGER authorize_forward_scheduler_event_insert
        BEFORE INSERT ON forward_scheduler_events
        WHEN
            _aegis_validate_event_write(
                'CREATE_SCHEDULER_EVENT', NEW.event_id, NEW.runtime_id,
                NEW.instrument_id, NEW.interval, NEW.candle_timestamp,
                NEW.decision_timestamp, NEW.status, NEW.proposal_id,
                NEW.proposal_sha256, NEW.proposal_json,
                NEW.proposal_binding_state, NEW.result_json, NEW.created_at
            ) != 1
            AND _aegis_validate_event_write(
                'CREATE_STOP_TARGET_EXIT', NEW.event_id, NEW.runtime_id,
                NEW.instrument_id, NEW.interval, NEW.candle_timestamp,
                NEW.decision_timestamp, NEW.status, NEW.proposal_id,
                NEW.proposal_sha256, NEW.proposal_json,
                NEW.proposal_binding_state, NEW.result_json, NEW.created_at
            ) != 1
        BEGIN
            SELECT RAISE(ABORT, 'scheduler event insert is unauthorized');
        END
        """,
        """
        CREATE TRIGGER consume_forward_scheduler_event_insert
        AFTER INSERT ON forward_scheduler_events
        WHEN
            _aegis_consume_event_write(
                'CREATE_SCHEDULER_EVENT', NEW.event_id, NEW.runtime_id,
                NEW.instrument_id, NEW.interval, NEW.candle_timestamp,
                NEW.decision_timestamp, NEW.status, NEW.proposal_id,
                NEW.proposal_sha256, NEW.proposal_json,
                NEW.proposal_binding_state, NEW.result_json, NEW.created_at
            ) != 1
            AND _aegis_consume_event_write(
                'CREATE_STOP_TARGET_EXIT', NEW.event_id, NEW.runtime_id,
                NEW.instrument_id, NEW.interval, NEW.candle_timestamp,
                NEW.decision_timestamp, NEW.status, NEW.proposal_id,
                NEW.proposal_sha256, NEW.proposal_json,
                NEW.proposal_binding_state, NEW.result_json, NEW.created_at
            ) != 1
        BEGIN
            SELECT RAISE(ABORT, 'scheduler event insert authorization was not consumed');
        END
        """,
        """
        CREATE TRIGGER consume_forward_event_proposal_binding
        AFTER UPDATE OF proposal_binding_state, proposal_sha256, proposal_json
        ON forward_scheduler_events
        WHEN OLD.proposal_binding_state = 'UNBOUND'
            AND NEW.proposal_binding_state = 'BOUND'
        BEGIN
            SELECT CASE WHEN _aegis_consume_event_write(
                'BIND_PROPOSAL', NEW.event_id, NEW.runtime_id,
                NEW.instrument_id, NEW.interval, NEW.candle_timestamp,
                NEW.decision_timestamp, NEW.status, NEW.proposal_id,
                NEW.proposal_sha256, NEW.proposal_json,
                NEW.proposal_binding_state, NEW.result_json, NEW.created_at
            ) != 1
            THEN RAISE(ABORT, 'proposal binding authorization was not consumed') END;
        END
        """,
    ),
}


def _execute_statements(connection: sqlite3.Connection, script: str) -> None:
    """Execute a semicolon-delimited schema script within the caller's transaction."""

    if script.lstrip().upper().startswith("CREATE TRIGGER "):
        connection.execute(script)
        return
    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)


class _AegisConnection(sqlite3.Connection):
    _event_write_capability: "_EventWriteCapability | None"


_EventWriteOperation = Literal[
    "CREATE_SCHEDULER_EVENT",
    "CREATE_STOP_TARGET_EXIT",
    "BIND_PROPOSAL",
]


@dataclass(frozen=True)
class _SchedulerEventWrite:
    event_id: str
    runtime_id: str
    instrument_id: int
    interval: str
    candle_timestamp: str
    decision_timestamp: str
    status: str
    proposal_id: str
    proposal_sha256: str | None
    proposal_json: str | None
    proposal_binding_state: str
    result_json: str | None
    created_at: str

    def values(self) -> tuple[object, ...]:
        return (
            self.event_id,
            self.runtime_id,
            self.instrument_id,
            self.interval,
            self.candle_timestamp,
            self.decision_timestamp,
            self.status,
            self.proposal_id,
            self.proposal_sha256,
            self.proposal_json,
            self.proposal_binding_state,
            self.result_json,
            self.created_at,
        )


@dataclass
class _EventWriteCapability:
    operation: _EventWriteOperation
    expected_values: tuple[object, ...]
    validated: bool = False
    consumed: bool = False


def _validate_proposal_document(event: _SchedulerEventWrite) -> bool:
    if event.proposal_sha256 is None or event.proposal_json is None:
        return False
    try:
        document = json.loads(event.proposal_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(document, dict) or document.get("proposal_id") != event.proposal_id:
        return False
    canonical_json = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(event.proposal_json.encode("utf-8")).hexdigest()
    return canonical_json == event.proposal_json and digest == event.proposal_sha256


def _validate_event_write_scope(
    operation: _EventWriteOperation, event: _SchedulerEventWrite
) -> None:
    if event.status != "PENDING" or not event.event_id or not event.runtime_id:
        raise ValueError("Scheduler event authorization has invalid identity or status.")
    if operation == "CREATE_SCHEDULER_EVENT":
        if (
            event.proposal_id != f"synthetic-{event.event_id[:40]}"
            or event.proposal_sha256 is not None
            or event.proposal_json is not None
            or event.proposal_binding_state != "UNBOUND"
        ):
            raise ValueError("Normal scheduler event authorization has invalid proposal fields.")
    elif operation == "CREATE_STOP_TARGET_EXIT":
        if (
            event.proposal_id != f"stop-target-{event.event_id}"
            or event.proposal_binding_state != "BOUND"
            or event.interval != "1m"
            or not _validate_proposal_document(event)
        ):
            raise ValueError("Stop/target event authorization has invalid proposal fields.")
    elif event.proposal_binding_state != "BOUND" or not _validate_proposal_document(event):
        raise ValueError("Proposal binding authorization has invalid proposal fields.")


@contextmanager
def _authorize_scheduler_event_write(
    connection: sqlite3.Connection,
    operation: _EventWriteOperation,
    event: _SchedulerEventWrite,
) -> Iterator[None]:
    """Grant one connection-local capability for one exact scheduler-event write."""

    if not isinstance(connection, _AegisConnection):
        raise TypeError("Scheduler event authorization requires an Aegis database connection.")
    if connection._event_write_capability is not None:
        raise RuntimeError("A scheduler event authorization is already active.")
    _validate_event_write_scope(operation, event)
    capability = _EventWriteCapability(operation, event.values())
    connection._event_write_capability = capability
    try:
        yield
    finally:
        connection._event_write_capability = None


def _validate_authorized_event_write(
    connection: _AegisConnection, operation: str, values: tuple[object, ...]
) -> int:
    capability = connection._event_write_capability
    if (
        capability is None
        or capability.operation != operation
        or capability.expected_values != values
        or capability.validated
        or capability.consumed
    ):
        return 0
    capability.validated = True
    return 1


def _consume_authorized_event_write(
    connection: _AegisConnection, operation: str, values: tuple[object, ...]
) -> int:
    capability = connection._event_write_capability
    if (
        capability is None
        or capability.operation != operation
        or capability.expected_values != values
        or not capability.validated
        or capability.consumed
    ):
        return 0
    capability.consumed = True
    return 1


def get_connection(database_path: Path) -> sqlite3.Connection:
    """Create a safe SQLite connection and ensure its parent exists."""

    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path, factory=_AegisConnection)
    connection._event_write_capability = None
    connection.create_function(
        "_aegis_validate_event_write",
        14,
        lambda operation, *values: _validate_authorized_event_write(connection, operation, values),
    )
    connection.create_function(
        "_aegis_consume_event_write",
        14,
        lambda operation, *values: _consume_authorized_event_write(connection, operation, values),
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")

    return connection


def get_readonly_connection(database_path: Path) -> sqlite3.Connection:
    """Open an existing database with SQLite read-only and query-only protections."""
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
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

    connection = get_readonly_connection(database_path)
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
