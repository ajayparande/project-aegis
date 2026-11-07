"""Tests for provider-neutral instrument master operations."""

from contextlib import closing
from datetime import date
from decimal import Decimal

import pytest

from aegis.database import get_connection, initialize_database
from aegis.instruments import (
    BulkImportSummary,
    DuplicateInstrumentError,
    InstrumentInput,
    InstrumentNotFoundError,
    InstrumentValidationError,
    bulk_import_instruments,
    get_instrument,
    get_instruments_by_exchange,
    list_instruments,
    register_instrument,
    search_instruments,
    update_instrument,
)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "instruments.db"
    initialize_database(path)
    return path


def equity(
    *,
    exchange: str = "NSE",
    symbol: str = "TCS",
    name: str = "Tata Consultancy Services",
    active: bool = True,
) -> InstrumentInput:
    return InstrumentInput(
        exchange=exchange,
        trading_symbol=symbol,
        instrument_name=name,
        instrument_type="EQUITY",
        isin="INE467B01029",
        lot_size=1,
        tick_size=Decimal("0.05"),
        is_active=active,
    )


def test_register_nse_and_bse_instruments_and_preserve_decimal_precision(database_path):
    nse = register_instrument(database_path, equity())
    bse = register_instrument(database_path, equity(exchange="BSE"))

    assert nse.exchange == "NSE"
    assert bse.exchange == "BSE"
    assert nse.trading_symbol == "TCS"
    assert nse.tick_size == Decimal("0.05")
    with closing(get_connection(database_path)) as connection:
        stored_tick = connection.execute(
            "SELECT tick_size_decimal FROM instruments WHERE id = ?", (nse.id,)
        ).fetchone()[0]
    assert stored_tick == "0.05"


def test_decimal_storage_does_not_round_large_values_to_context_precision(database_path):
    exact_tick = Decimal("123456789012345678901234567890.123456789")
    instrument = register_instrument(
        database_path,
        InstrumentInput(
            exchange="NSE",
            trading_symbol="PRECISE",
            instrument_name="Precise tick size fixture",
            instrument_type="EQUITY",
            tick_size=exact_tick,
        ),
    )

    assert instrument.tick_size == exact_tick
    with closing(get_connection(database_path)) as connection:
        stored_tick = connection.execute(
            "SELECT tick_size_decimal FROM instruments WHERE id = ?", (instrument.id,)
        ).fetchone()[0]
    assert stored_tick == "123456789012345678901234567890.123456789"


def test_registration_normalizes_identity_and_prevents_duplicate_overwrite(database_path):
    registered = register_instrument(
        database_path,
        equity(exchange=" nSe ", symbol=" tcs ", name="First name"),
    )

    with pytest.raises(DuplicateInstrumentError):
        register_instrument(
            database_path,
            equity(exchange="NSE", symbol="TCS", name="Replacement name"),
        )

    current = get_instrument(database_path, "nse", " tCs ")
    assert current is not None
    assert current.id == registered.id
    assert current.instrument_name == "First name"
    assert len(list_instruments(database_path)) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exchange", ""),
        ("exchange", "NASDAQ"),
        ("trading_symbol", "  "),
        ("instrument_name", ""),
        ("instrument_type", " "),
        ("instrument_token", 123),
        ("segment", 3),
        ("isin", "not-an-isin"),
        ("lot_size", 0),
        ("lot_size", True),
        ("tick_size", Decimal(0)),
        ("tick_size", Decimal("NaN")),
        ("tick_size", 0.05),
        ("strike_price", Decimal(-1)),
        ("expiry_date", "not-a-date"),
        ("is_active", "false"),
    ],
)
def test_registration_rejects_invalid_fields(database_path, field, value):
    values = {
        "exchange": "NSE",
        "trading_symbol": "TCS",
        "instrument_name": "Tata Consultancy Services",
        "instrument_type": "EQUITY",
    }
    values[field] = value

    with pytest.raises(InstrumentValidationError):
        register_instrument(database_path, InstrumentInput(**values))

    assert list_instruments(database_path) == []


def test_futures_options_and_optional_contract_fields(database_path):
    future = register_instrument(
        database_path,
        InstrumentInput(
            exchange="NSE",
            trading_symbol="NIFTY26JUNFUT",
            instrument_name="NIFTY June Future",
            instrument_type="FUTURE",
            segment="NFO",
            lot_size=65,
            tick_size=Decimal("0.05"),
            expiry_date=date(2026, 6, 25),
        ),
    )
    option = register_instrument(
        database_path,
        InstrumentInput(
            exchange="BSE",
            trading_symbol="SENSEX26JUN80000CE",
            instrument_name="SENSEX June 80000 Call",
            instrument_type="OPTION",
            segment="BFO",
            lot_size=20,
            tick_size="0.05",
            expiry_date="2026-06-25",
            strike_price=Decimal("80000.00"),
            option_type="ce",
        ),
    )

    assert future.instrument_type == "FUTURE"
    assert future.expiry_date == date(2026, 6, 25)
    assert future.strike_price is None
    assert option.exchange == "BSE"
    assert option.strike_price == Decimal(80000)
    assert option.option_type == "CE"

    with pytest.raises(InstrumentValidationError, match="OPTION instruments require"):
        register_instrument(
            database_path,
            InstrumentInput(
                exchange="NSE",
                trading_symbol="INCOMPLETEOPTION",
                instrument_name="Incomplete option",
                instrument_type="OPTION",
            ),
        )


def test_list_filters_and_predictable_ordering(database_path):
    register_instrument(database_path, equity(symbol="ZETA"))
    register_instrument(database_path, equity(symbol="ALPHA", active=False))
    register_instrument(database_path, equity(exchange="BSE", symbol="BETA"))
    register_instrument(
        database_path,
        InstrumentInput("NSE", "NIFTY", "NIFTY 50", "INDEX"),
    )

    assert [item.trading_symbol for item in list_instruments(database_path)] == [
        "BETA",
        "ALPHA",
        "NIFTY",
        "ZETA",
    ]
    assert [item.trading_symbol for item in get_instruments_by_exchange(database_path, "nse")] == [
        "ALPHA",
        "NIFTY",
        "ZETA",
    ]
    assert [
        item.trading_symbol for item in list_instruments(database_path, instrument_type="index")
    ] == ["NIFTY"]
    assert [item.trading_symbol for item in list_instruments(database_path, is_active=False)] == [
        "ALPHA"
    ]
    assert [
        item.trading_symbol
        for item in list_instruments(database_path, exchange="NSE", is_active=True)
    ] == ["NIFTY", "ZETA"]


def test_update_metadata_status_and_identity_protection(database_path):
    registered = register_instrument(database_path, equity())
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            "UPDATE instruments SET updated_at = ? WHERE id = ?",
            ("2000-01-01 00:00:00", registered.id),
        )

    updated = update_instrument(
        database_path,
        "NSE",
        "TCS",
        {
            "instrument_name": "Tata Consultancy Services Ltd",
            "segment": "EQ",
            "isin": None,
            "is_active": False,
        },
    )

    assert updated.id == registered.id
    assert updated.exchange == "NSE"
    assert updated.trading_symbol == "TCS"
    assert updated.instrument_name.endswith("Ltd")
    assert updated.segment == "EQ"
    assert updated.isin is None
    assert not updated.is_active
    assert updated.updated_at != "2000-01-01 00:00:00"

    with pytest.raises(InstrumentValidationError, match="Identity fields cannot be updated"):
        update_instrument(database_path, "NSE", "TCS", {"exchange": "BSE"})
    with pytest.raises(InstrumentNotFoundError):
        update_instrument(database_path, "BSE", "TCS", {"is_active": False})


def test_search_matches_symbol_and_name_case_insensitively(database_path):
    register_instrument(database_path, equity())
    register_instrument(
        database_path,
        equity(exchange="BSE", symbol="INFY", name="Infosys Limited"),
    )

    assert [item.trading_symbol for item in search_instruments(database_path, "tCs")] == ["TCS"]
    assert [
        item.trading_symbol for item in search_instruments(database_path, "infosys", exchange="bse")
    ] == ["INFY"]
    with pytest.raises(InstrumentValidationError):
        search_instruments(database_path, " ")


def test_bulk_import_reports_inserted_duplicates_and_invalid_rows(database_path):
    summary = bulk_import_instruments(
        database_path,
        [
            equity(symbol="TCS"),
            equity(exchange="nse", symbol=" tcs ", name="Duplicate"),
            {"exchange": "BSE", "trading_symbol": "INFY"},
            equity(exchange="BSE", symbol="INFY", name="Infosys Limited"),
        ],
    )

    assert isinstance(summary, BulkImportSummary)
    assert summary.inserted == 2
    assert summary.skipped_count == 1
    assert summary.rejected_count == 1
    assert summary.skipped[0].row_number == 2
    assert summary.rejected[0].row_number == 3
    assert len(list_instruments(database_path)) == 2


def test_bulk_import_unexpected_failure_rolls_back_batch(database_path):
    def records():
        yield equity(symbol="TCS")
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError, match="fixture failure"):
        bulk_import_instruments(database_path, records())

    assert list_instruments(database_path) == []


def test_bulk_import_integrity_error_rejects_one_row_and_commits_others(database_path):
    existing = register_instrument(database_path, equity(symbol="EXISTING", name="Existing"))
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_test_instrument
            BEFORE INSERT ON instruments
            WHEN NEW.trading_symbol = 'REJECTED'
            BEGIN
                SELECT RAISE(ABORT, 'injected row integrity failure');
            END
            """
        )
        connection.commit()

    summary = bulk_import_instruments(
        database_path,
        [
            equity(symbol="ACCEPTED1", name="Accepted one"),
            equity(symbol="REJECTED", name="Rejected row"),
            equity(symbol="ACCEPTED2", name="Accepted two"),
        ],
    )

    assert summary.inserted == 2
    assert summary.skipped_count == 0
    assert summary.rejected_count == 1
    assert summary.rejected[0].row_number == 2
    assert summary.rejected[0].trading_symbol == "REJECTED"
    assert "injected row integrity failure" in summary.rejected[0].reason

    with closing(get_connection(database_path)) as connection:
        assert not connection.in_transaction
        rows = connection.execute(
            "SELECT trading_symbol, instrument_name FROM instruments ORDER BY trading_symbol"
        ).fetchall()
        connection.execute(
            "INSERT INTO sources (name, source_type) VALUES (?, ?)",
            ("Connection usability check", "test"),
        )
        connection.commit()
        source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    assert [(row["trading_symbol"], row["instrument_name"]) for row in rows] == [
        ("ACCEPTED1", "Accepted one"),
        ("ACCEPTED2", "Accepted two"),
        ("EXISTING", "Existing"),
    ]
    assert get_instrument(database_path, "NSE", "EXISTING") == existing
    assert get_instrument(database_path, "NSE", "REJECTED") is None
    assert source_count == 1


def test_instrument_persists_after_reconnecting(database_path):
    registered = register_instrument(database_path, equity())

    loaded = get_instrument(database_path, "NSE", "TCS")

    assert loaded == registered
