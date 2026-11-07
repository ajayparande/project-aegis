"""Provider-neutral instrument master operations backed by local SQLite."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypeAlias, TypedDict

from aegis.database import get_connection

DecimalInput: TypeAlias = Decimal | int | str
DateInput: TypeAlias = date | str

SUPPORTED_EXCHANGES = frozenset({"NSE", "BSE"})
OPTION_TYPES = frozenset({"CE", "PE"})
ISIN_PATTERN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]\Z")


class InstrumentError(Exception):
    """Base class for instrument master errors."""


class InstrumentValidationError(InstrumentError, ValueError):
    """Raised when instrument fields are missing or invalid."""


class DuplicateInstrumentError(InstrumentError):
    """Raised when an exchange and trading symbol already exist."""


class InstrumentNotFoundError(InstrumentError):
    """Raised when an instrument update targets no existing instrument."""


@dataclass(frozen=True, slots=True)
class InstrumentInput:
    """Input values validated when passed to the instrument registry."""

    exchange: str
    trading_symbol: str
    instrument_name: str
    instrument_type: str
    instrument_token: str | None = None
    segment: str | None = None
    isin: str | None = None
    lot_size: int = 1
    tick_size: DecimalInput | None = None
    expiry_date: DateInput | None = None
    strike_price: DecimalInput | None = None
    option_type: str | None = None
    is_active: bool = True


class InstrumentUpdate(TypedDict, total=False):
    """Fields that can be changed without changing instrument identity."""

    instrument_name: str
    instrument_type: str
    instrument_token: str | None
    segment: str | None
    isin: str | None
    lot_size: int
    tick_size: DecimalInput | None
    expiry_date: DateInput | None
    strike_price: DecimalInput | None
    option_type: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class Instrument:
    """Typed instrument record; decimal fields are never exposed as floats."""

    id: int
    exchange: str
    trading_symbol: str
    instrument_name: str
    instrument_type: str
    instrument_token: str | None
    segment: str | None
    isin: str | None
    lot_size: int
    tick_size: Decimal | None
    expiry_date: date | None
    strike_price: Decimal | None
    option_type: str | None
    is_active: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class BulkImportIssue:
    """A skipped or rejected input row and its reason."""

    row_number: int
    reason: str
    exchange: str | None = None
    trading_symbol: str | None = None


@dataclass(frozen=True, slots=True)
class BulkImportSummary:
    """Counts and row-level details from a per-record bulk import."""

    inserted: int
    skipped: tuple[BulkImportIssue, ...]
    rejected: tuple[BulkImportIssue, ...]

    @property
    def skipped_count(self) -> int:
        """Number of duplicate rows skipped without overwriting existing data."""

        return len(self.skipped)

    @property
    def rejected_count(self) -> int:
        """Number of rows rejected for validation or database integrity errors."""

        return len(self.rejected)


@contextmanager
def _write_transaction(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open and close a connection with an immediate, rollback-safe transaction."""

    connection = get_connection(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _required_text(value: object, field: str, *, uppercase: bool = False) -> str:
    if not isinstance(value, str):
        raise InstrumentValidationError(f"{field} must be text.")
    result = value.strip()
    if not result:
        raise InstrumentValidationError(f"{field} is required.")
    return result.upper() if uppercase else result


def _optional_text(value: object, field: str, *, uppercase: bool = False) -> str | None:
    if value is None:
        return None
    result = _required_text(value, field, uppercase=uppercase)
    return result or None


def _positive_decimal(value: object, field: str) -> tuple[Decimal | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, (bool, float)):
        raise InstrumentValidationError(
            f"{field} must be supplied as Decimal, integer, or decimal text; floats are rejected."
        )
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InstrumentValidationError(f"{field} must be a valid decimal value.") from None
    if not result.is_finite() or result <= 0:
        raise InstrumentValidationError(f"{field} must be finite and greater than zero.")
    decimal_text = format(result, "f")
    if "." in decimal_text:
        decimal_text = decimal_text.rstrip("0").rstrip(".")
    return result, decimal_text


def _optional_date(value: object, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise InstrumentValidationError(f"{field} must be a date without a time component.")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized) is None:
            raise InstrumentValidationError(f"{field} must be an ISO date (YYYY-MM-DD).")
        try:
            return date.fromisoformat(normalized)
        except ValueError:
            pass
    raise InstrumentValidationError(f"{field} must be an ISO date (YYYY-MM-DD).")


def _validate_instrument(
    instrument: InstrumentInput,
) -> dict[str, object]:
    exchange = _required_text(instrument.exchange, "exchange", uppercase=True)
    if exchange not in SUPPORTED_EXCHANGES:
        raise InstrumentValidationError(
            f"Unsupported exchange {exchange!r}; supported exchanges are NSE and BSE."
        )

    trading_symbol = _required_text(instrument.trading_symbol, "trading_symbol", uppercase=True)
    instrument_name = _required_text(instrument.instrument_name, "instrument_name")
    instrument_type = _required_text(instrument.instrument_type, "instrument_type", uppercase=True)

    if isinstance(instrument.lot_size, bool) or not isinstance(instrument.lot_size, int):
        raise InstrumentValidationError("lot_size must be a positive integer.")
    if instrument.lot_size <= 0:
        raise InstrumentValidationError("lot_size must be a positive integer.")

    tick_size, tick_size_text = _positive_decimal(instrument.tick_size, "tick_size")
    expiry_date = _optional_date(instrument.expiry_date, "expiry_date")
    strike_price, strike_price_text = _positive_decimal(instrument.strike_price, "strike_price")
    option_type = _optional_text(instrument.option_type, "option_type", uppercase=True)
    if option_type is not None and option_type not in OPTION_TYPES:
        raise InstrumentValidationError("option_type must be CE or PE.")
    if instrument_type == "OPTION":
        if expiry_date is None or strike_price is None or option_type is None:
            raise InstrumentValidationError(
                "OPTION instruments require expiry_date, strike_price, and option_type."
            )
    elif option_type is not None:
        raise InstrumentValidationError("option_type is only valid for OPTION instruments.")
    if instrument_type == "FUTURE" and expiry_date is None:
        raise InstrumentValidationError("FUTURE instruments require expiry_date.")

    isin = _optional_text(instrument.isin, "isin", uppercase=True)
    if isin is not None and ISIN_PATTERN.fullmatch(isin) is None:
        raise InstrumentValidationError("isin must use the standard 12-character ISIN format.")

    if not isinstance(instrument.is_active, bool):
        raise InstrumentValidationError("is_active must be a boolean.")

    return {
        "exchange": exchange,
        "trading_symbol": trading_symbol,
        "instrument_name": instrument_name,
        "instrument_type": instrument_type,
        "instrument_token": _optional_text(instrument.instrument_token, "instrument_token"),
        "segment": _optional_text(instrument.segment, "segment", uppercase=True),
        "isin": isin,
        "lot_size": instrument.lot_size,
        "tick_size": tick_size,
        "tick_size_decimal": tick_size_text,
        "expiry_date": expiry_date.isoformat() if expiry_date else None,
        "strike_price": strike_price_text,
        "option_type": option_type,
        "is_active": int(instrument.is_active),
    }


def _coerce_input(record: InstrumentInput | Mapping[str, object]) -> InstrumentInput:
    if isinstance(record, InstrumentInput):
        return record
    if isinstance(record, Mapping):
        try:
            return InstrumentInput(**record)  # type: ignore[arg-type]
        except TypeError as exc:
            raise InstrumentValidationError(str(exc)) from exc
    raise InstrumentValidationError("Each import row must be an InstrumentInput or mapping.")


def _row_to_instrument(row: sqlite3.Row) -> Instrument:
    exact_tick = row["tick_size_decimal"]
    legacy_tick = row["tick_size"]
    tick_size = (
        Decimal(exact_tick)
        if exact_tick is not None
        else Decimal(str(legacy_tick))
        if legacy_tick is not None
        else None
    )
    return Instrument(
        id=row["id"],
        exchange=row["exchange"],
        trading_symbol=row["trading_symbol"],
        instrument_name=row["instrument_name"],
        instrument_type=row["instrument_type"],
        instrument_token=row["instrument_token"],
        segment=row["segment"],
        isin=row["isin"],
        lot_size=row["lot_size"],
        tick_size=tick_size,
        expiry_date=date.fromisoformat(row["expiry_date"]) if row["expiry_date"] else None,
        strike_price=Decimal(row["strike_price"]) if row["strike_price"] else None,
        option_type=row["option_type"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_SELECT_COLUMNS = """
id, exchange, trading_symbol, instrument_name, instrument_type,
instrument_token, segment, isin, lot_size, tick_size, tick_size_decimal,
expiry_date, strike_price, option_type, is_active, created_at, updated_at
"""
_INSERT_COLUMNS = (
    "exchange, trading_symbol, instrument_name, instrument_type, instrument_token, "
    "segment, isin, lot_size, tick_size_decimal, expiry_date, strike_price, "
    "option_type, is_active"
)
_INSERT_FIELDS = (
    "exchange",
    "trading_symbol",
    "instrument_name",
    "instrument_type",
    "instrument_token",
    "segment",
    "isin",
    "lot_size",
    "tick_size_decimal",
    "expiry_date",
    "strike_price",
    "option_type",
    "is_active",
)


def _find_existing(
    connection: sqlite3.Connection, exchange: str, trading_symbol: str
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT " + _SELECT_COLUMNS + " FROM instruments "
        "WHERE exchange = ? COLLATE NOCASE AND trading_symbol = ? COLLATE NOCASE",
        (exchange, trading_symbol),
    ).fetchone()


def _insert_instrument(
    connection: sqlite3.Connection, values: dict[str, object]
) -> Instrument | None:
    placeholders = ", ".join("?" for _ in _INSERT_FIELDS)
    cursor = connection.execute(
        f"INSERT INTO instruments ({_INSERT_COLUMNS}) VALUES ({placeholders}) "
        "ON CONFLICT(exchange, trading_symbol) DO NOTHING",
        tuple(values[field] for field in _INSERT_FIELDS),
    )
    if cursor.rowcount == 0:
        return None
    row = connection.execute(
        "SELECT " + _SELECT_COLUMNS + " FROM instruments WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return _row_to_instrument(row)


def register_instrument(database_path: Path, instrument: InstrumentInput) -> Instrument:
    """Register one instrument without overwriting an existing identity."""

    values = _validate_instrument(instrument)
    with _write_transaction(database_path) as connection:
        if _find_existing(connection, str(values["exchange"]), str(values["trading_symbol"])):
            raise DuplicateInstrumentError(
                f"{values['exchange']}:{values['trading_symbol']} is already registered."
            )
        registered = _insert_instrument(connection, values)
        if registered is None:
            raise DuplicateInstrumentError(
                f"{values['exchange']}:{values['trading_symbol']} is already registered."
            )
        return registered


def get_instrument(database_path: Path, exchange: str, trading_symbol: str) -> Instrument | None:
    """Find an instrument by its exchange and trading symbol."""

    normalized_exchange = _required_text(exchange, "exchange", uppercase=True)
    normalized_symbol = _required_text(trading_symbol, "trading_symbol", uppercase=True)
    with closing(get_connection(database_path)) as connection:
        row = _find_existing(connection, normalized_exchange, normalized_symbol)
    return _row_to_instrument(row) if row else None


def _build_filters(
    *,
    exchange: str | None,
    instrument_type: str | None,
    is_active: bool | None,
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if exchange is not None:
        clauses.append("exchange = ? COLLATE NOCASE")
        params.append(_required_text(exchange, "exchange", uppercase=True))
    if instrument_type is not None:
        clauses.append("instrument_type = ? COLLATE NOCASE")
        params.append(_required_text(instrument_type, "instrument_type", uppercase=True))
    if is_active is not None:
        if not isinstance(is_active, bool):
            raise InstrumentValidationError("is_active filter must be a boolean or None.")
        clauses.append("is_active = ?")
        params.append(int(is_active))
    return clauses, params


def list_instruments(
    database_path: Path,
    *,
    exchange: str | None = None,
    instrument_type: str | None = None,
    is_active: bool | None = None,
) -> list[Instrument]:
    """List instruments with optional filters and stable exchange/symbol ordering."""

    clauses, params = _build_filters(
        exchange=exchange,
        instrument_type=instrument_type,
        is_active=is_active,
    )
    query = "SELECT " + _SELECT_COLUMNS + " FROM instruments"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY exchange COLLATE NOCASE, trading_symbol COLLATE NOCASE, id"
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(query, params).fetchall()
    return [_row_to_instrument(row) for row in rows]


def get_instruments_by_exchange(
    database_path: Path,
    exchange: str,
    *,
    is_active: bool | None = None,
) -> list[Instrument]:
    """Return instruments for one exchange in deterministic symbol order."""

    return list_instruments(database_path, exchange=exchange, is_active=is_active)


def search_instruments(
    database_path: Path,
    query: str,
    *,
    exchange: str | None = None,
    instrument_type: str | None = None,
    is_active: bool | None = None,
) -> list[Instrument]:
    """Search symbols and names case-insensitively, with optional exact filters."""

    search_term = _required_text(query, "query")
    clauses, params = _build_filters(
        exchange=exchange,
        instrument_type=instrument_type,
        is_active=is_active,
    )
    escaped = search_term.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    clauses.append(
        "(trading_symbol LIKE ? ESCAPE '!' COLLATE NOCASE "
        "OR instrument_name LIKE ? ESCAPE '!' COLLATE NOCASE)"
    )
    pattern = f"%{escaped}%"
    params.extend((pattern, pattern))
    query_sql = "SELECT " + _SELECT_COLUMNS + " FROM instruments WHERE "
    query_sql += " AND ".join(clauses)
    query_sql += " ORDER BY exchange COLLATE NOCASE, trading_symbol COLLATE NOCASE, id"
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(query_sql, params).fetchall()
    return [_row_to_instrument(row) for row in rows]


def _updates_to_input(current: Instrument, updates: InstrumentUpdate) -> InstrumentInput:
    allowed = {
        "instrument_name",
        "instrument_type",
        "instrument_token",
        "segment",
        "isin",
        "lot_size",
        "tick_size",
        "expiry_date",
        "strike_price",
        "option_type",
        "is_active",
    }
    unknown = set(updates) - allowed
    if unknown:
        raise InstrumentValidationError(
            f"Identity fields cannot be updated; unsupported fields: {', '.join(sorted(unknown))}."
        )
    if not updates:
        raise InstrumentValidationError("At least one metadata field must be updated.")
    current_values: dict[str, object] = {
        "exchange": current.exchange,
        "trading_symbol": current.trading_symbol,
        "instrument_name": current.instrument_name,
        "instrument_type": current.instrument_type,
        "instrument_token": current.instrument_token,
        "segment": current.segment,
        "isin": current.isin,
        "lot_size": current.lot_size,
        "tick_size": current.tick_size,
        "expiry_date": current.expiry_date,
        "strike_price": current.strike_price,
        "option_type": current.option_type,
        "is_active": current.is_active,
    }
    current_values.update(updates)
    return InstrumentInput(**current_values)  # type: ignore[arg-type]


def update_instrument(
    database_path: Path,
    exchange: str,
    trading_symbol: str,
    updates: InstrumentUpdate,
) -> Instrument:
    """Update permitted metadata and status without changing instrument identity."""

    normalized_exchange = _required_text(exchange, "exchange", uppercase=True)
    normalized_symbol = _required_text(trading_symbol, "trading_symbol", uppercase=True)
    with _write_transaction(database_path) as connection:
        row = _find_existing(connection, normalized_exchange, normalized_symbol)
        if row is None:
            raise InstrumentNotFoundError(
                f"{normalized_exchange}:{normalized_symbol} is not registered."
            )
        current = _row_to_instrument(row)
        values = _validate_instrument(_updates_to_input(current, updates))
        changed_fields = [
            field for field in _INSERT_FIELDS if field not in {"exchange", "trading_symbol"}
        ]
        assignments = ", ".join(f"{field} = ?" for field in changed_fields)
        connection.execute(
            f"UPDATE instruments SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*[values[field] for field in changed_fields], current.id),
        )
        updated = connection.execute(
            "SELECT " + _SELECT_COLUMNS + " FROM instruments WHERE id = ?",
            (current.id,),
        ).fetchone()
        return _row_to_instrument(updated)


def _issue_identity(record: object) -> tuple[str | None, str | None]:
    if isinstance(record, InstrumentInput):
        return record.exchange, record.trading_symbol
    if isinstance(record, Mapping):
        exchange = record.get("exchange")
        symbol = record.get("trading_symbol")
        return (
            exchange if isinstance(exchange, str) else None,
            symbol if isinstance(symbol, str) else None,
        )
    return None, None


def bulk_import_instruments(
    database_path: Path,
    records: Iterable[InstrumentInput | Mapping[str, object]],
) -> BulkImportSummary:
    """Import rows per-record: duplicates are skipped, invalid rows are reported."""

    inserted = 0
    skipped: list[BulkImportIssue] = []
    rejected: list[BulkImportIssue] = []
    with _write_transaction(database_path) as connection:
        for row_number, record in enumerate(records, start=1):
            raw_exchange, raw_symbol = _issue_identity(record)
            try:
                values = _validate_instrument(_coerce_input(record))
            except (InstrumentValidationError, TypeError, ValueError) as exc:
                rejected.append(BulkImportIssue(row_number, str(exc), raw_exchange, raw_symbol))
                continue

            connection.execute("SAVEPOINT instrument_import_row")
            try:
                existing = _find_existing(
                    connection,
                    str(values["exchange"]),
                    str(values["trading_symbol"]),
                )
                if existing is not None:
                    connection.execute("RELEASE instrument_import_row")
                    skipped.append(
                        BulkImportIssue(
                            row_number,
                            "Duplicate exchange and trading symbol.",
                            str(values["exchange"]),
                            str(values["trading_symbol"]),
                        )
                    )
                    continue

                result = _insert_instrument(connection, values)
                connection.execute("RELEASE instrument_import_row")
                if result is None:
                    skipped.append(
                        BulkImportIssue(
                            row_number,
                            "Duplicate exchange and trading symbol.",
                            str(values["exchange"]),
                            str(values["trading_symbol"]),
                        )
                    )
                else:
                    inserted += 1
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK TO instrument_import_row")
                connection.execute("RELEASE instrument_import_row")
                rejected.append(
                    BulkImportIssue(
                        row_number,
                        str(exc),
                        str(values["exchange"]),
                        str(values["trading_symbol"]),
                    )
                )

    return BulkImportSummary(inserted, tuple(skipped), tuple(rejected))
