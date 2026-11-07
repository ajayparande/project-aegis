"""Project Aegis - local research dashboard."""

from pathlib import Path

import streamlit as st

from aegis.config import get_settings
from aegis.database import get_database_status, initialize_database
from aegis.instruments import list_instruments, search_instruments
from aegis.logging_config import setup_logging
from aegis.market_data import (
    SUPPORTED_INTERVALS,
    get_legacy_market_data_count,
    import_ohlcv_csv,
    list_historical_candles,
    list_historical_overview,
    list_quality_issues,
    list_recent_imports,
)

settings = get_settings()
logger = setup_logging(
    level=settings.log_level,
    log_file=settings.log_file,
)

st.set_page_config(
    page_title="Project Aegis",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ Project Aegis")
st.caption("AI-assisted Quantitative Research Platform")

# The application is restricted to research mode in Phase 1.
st.warning("RESEARCH ONLY — No broker connection, live orders, or automated trading is enabled.")

try:
    initialize_database(settings.database_path)
    database_status = get_database_status(settings.database_path)
    database_healthy = database_status["exists"]
    logger.info("Dashboard database check completed")
except Exception:
    database_healthy = False
    database_status = {
        "exists": False,
        "schema_version": None,
        "tables": [],
    }
    logger.exception("Dashboard database check failed")

st.divider()

# Main status indicators.
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("Application Mode", settings.mode.value)

with col2:
    st.metric(
        "Database Status",
        "Healthy" if database_healthy else "Unavailable",
    )

with col3:
    st.metric(
        "Schema Version",
        database_status["schema_version"]
        if database_status["schema_version"] is not None
        else "Not initialized",
    )

st.divider()

st.subheader("System Information")

info_col1, info_col2 = st.columns(2)

with info_col1:
    st.write("**Application:**", settings.app_name)
    st.write("**Operating Mode:**", settings.mode.value)
    st.write("**Log Level:**", settings.log_level)

with info_col2:
    st.write("**Database Location:**", str(settings.database_path))
    st.write(
        "**Database Health:**",
        "Operational" if database_healthy else "Unavailable",
    )

st.subheader("Research Database")

if database_healthy:
    tables = database_status["tables"]

    st.success(f"Database initialized. {len(tables)} tables available.")

    for table in tables:
        st.write(f"• {table}")
else:
    st.error("Database is unavailable. Check the application logs.")

st.divider()

st.subheader("Instrument Master")

if database_healthy:
    try:
        all_instruments = list_instruments(settings.database_path)
        active_count = sum(instrument.is_active for instrument in all_instruments)
        inactive_count = len(all_instruments) - active_count

        total_col, active_col, inactive_col = st.columns(3)
        total_col.metric("Total Instruments", len(all_instruments))
        active_col.metric("Active Instruments", active_count)
        inactive_col.metric("Inactive Instruments", inactive_count)

        exchange_distribution: dict[str, int] = {}
        for instrument in all_instruments:
            exchange_distribution[instrument.exchange] = (
                exchange_distribution.get(instrument.exchange, 0) + 1
            )
        st.write("**Exchange distribution:**", exchange_distribution or "No instruments")

        search_col, exchange_col, type_col, status_col = st.columns(4)
        search_query = search_col.text_input("Search symbol or name")
        exchange_filter = exchange_col.selectbox(
            "Exchange",
            ["All", *sorted({instrument.exchange for instrument in all_instruments})],
        )
        type_filter = type_col.selectbox(
            "Instrument type",
            [
                "All",
                *sorted({instrument.instrument_type for instrument in all_instruments}),
            ],
        )
        status_filter = status_col.selectbox("Status", ["All", "Active", "Inactive"])

        selected_exchange = exchange_filter if exchange_filter != "All" else None
        selected_type = type_filter if type_filter != "All" else None
        selected_active = None if status_filter == "All" else status_filter == "Active"

        if search_query.strip():
            displayed_instruments = search_instruments(
                settings.database_path,
                search_query,
                exchange=selected_exchange,
                instrument_type=selected_type,
                is_active=selected_active,
            )
        else:
            displayed_instruments = list_instruments(
                settings.database_path,
                exchange=selected_exchange,
                instrument_type=selected_type,
                is_active=selected_active,
            )

        if displayed_instruments:
            st.dataframe(
                [
                    {
                        "Exchange": instrument.exchange,
                        "Symbol": instrument.trading_symbol,
                        "Name": instrument.instrument_name,
                        "Type": instrument.instrument_type,
                        "Segment": instrument.segment or "",
                        "ISIN": instrument.isin or "",
                        "Lot size": instrument.lot_size,
                        "Tick size": (
                            str(instrument.tick_size) if instrument.tick_size is not None else ""
                        ),
                        "Expiry": (
                            instrument.expiry_date.isoformat()
                            if instrument.expiry_date is not None
                            else ""
                        ),
                        "Strike": (
                            str(instrument.strike_price)
                            if instrument.strike_price is not None
                            else ""
                        ),
                        "Option type": instrument.option_type or "",
                        "Active": instrument.is_active,
                    }
                    for instrument in displayed_instruments
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("No instruments match the selected filters.")
    except Exception:
        logger.exception("Instrument master dashboard view failed")
        st.error("Instrument Master is unavailable. Check the application logs.")
else:
    st.info("Instrument Master is unavailable while the database is unavailable.")

st.divider()

st.subheader("Historical Data / CSV Import")
st.caption("Historical local-file data only. This section does not display a live market feed.")
st.caption(
    "CSV columns: timestamp, open, high, low, close, volume. "
    "Timestamps require an ISO-8601 timezone offset."
)

if database_healthy:
    try:
        active_instruments = [item for item in all_instruments if item.is_active]
        instrument_labels = {
            f"{item.exchange}:{item.trading_symbol}": item for item in active_instruments
        }
        with st.form("historical_csv_import"):
            import_col, interval_col, path_col = st.columns([2, 1, 3])
            instrument_label = import_col.selectbox(
                "Registered active instrument",
                list(instrument_labels) or ["No active instruments"],
                disabled=not instrument_labels,
            )
            selected_interval = interval_col.selectbox("Interval", SUPPORTED_INTERVALS)
            csv_path_value = path_col.text_input("Local CSV file path")
            import_submitted = st.form_submit_button("Import historical CSV")

        if import_submitted:
            if not csv_path_value.strip() or instrument_label not in instrument_labels:
                st.error("Choose an active instrument and enter a local CSV file path.")
            else:
                selected_instrument = instrument_labels[instrument_label]
                try:
                    import_result = import_ohlcv_csv(
                        settings.database_path,
                        Path(csv_path_value.strip()),
                        selected_instrument.exchange,
                        selected_instrument.trading_symbol,
                        selected_interval,
                    )
                except Exception:
                    logger.exception("Historical CSV import failed unexpectedly")
                    st.error("The import failed unexpectedly. Review import history and logs.")
                else:
                    message = (
                        f"Import {import_result.status}: {import_result.accepted_rows} accepted, "
                        f"{import_result.rejected_rows} rejected, "
                        f"{import_result.skipped_rows} skipped, "
                        f"{import_result.duplicate_rows} duplicates."
                    )
                    if import_result.status == "FAILED":
                        st.error(import_result.error_summary or message)
                    elif import_result.rejected_rows:
                        st.warning(message)
                    else:
                        st.success(message)
                    st.caption(f"Tick-size validation: {import_result.tick_size_validation}.")

        overview = list_historical_overview(settings.database_path)
        recent_imports = list_recent_imports(settings.database_path)
        quality_issues = list_quality_issues(settings.database_path)
        legacy_market_data_count = get_legacy_market_data_count(settings.database_path)
        total_candles = sum(int(row["candle_count"]) for row in overview)
        metrics = st.columns(4)
        metrics[0].metric("Historical Candles", total_candles)
        metrics[1].metric("Instrument / Interval Series", len(overview))
        metrics[2].metric("Recent CSV Imports", len(recent_imports))
        metrics[3].metric("Legacy REAL Rows Preserved", legacy_market_data_count)
        if legacy_market_data_count:
            st.warning(
                "Legacy market_data rows are preserved but excluded from the exact-decimal CSV view."
            )

        if overview:
            st.markdown("**Candle counts and date coverage**")
            st.dataframe(overview, hide_index=True, width="stretch")
        else:
            st.info("No historical candles are stored yet. Import a local CSV to begin.")

        if recent_imports:
            st.markdown("**Recent CSV import history**")
            st.dataframe(recent_imports, hide_index=True, width="stretch")

        if quality_issues:
            st.markdown("**Recent rejected rows and data-quality issues**")
            st.dataframe(quality_issues, hide_index=True, width="stretch")

        if overview:
            view_col, interval_filter_col = st.columns(2)
            series_options = sorted(
                {f"{row['exchange']}:{row['trading_symbol']}" for row in overview}
            )
            selected_series = view_col.selectbox(
                "Candle table instrument", ["All", *series_options]
            )
            selected_view_interval = interval_filter_col.selectbox(
                "Candle table interval", ["All", *SUPPORTED_INTERVALS]
            )
            selected_exchange = selected_symbol = None
            if selected_series != "All":
                selected_exchange, selected_symbol = selected_series.split(":", maxsplit=1)
            candle_rows = list_historical_candles(
                settings.database_path,
                exchange=selected_exchange,
                trading_symbol=selected_symbol,
                interval=(selected_view_interval if selected_view_interval != "All" else None),
                limit=500,
            )
            if candle_rows:
                st.markdown("**Historical candles (newest first; up to 500 rows)**")
                st.dataframe(candle_rows, hide_index=True, width="stretch")
            else:
                st.info("No candles match the selected instrument and interval.")

    except Exception:
        logger.exception("Historical data dashboard view failed")
        st.error("Historical data is unavailable. Check the application logs.")
else:
    st.info("Historical data is unavailable while the database is unavailable.")

st.divider()

st.subheader("Development Status")

st.info(
    "Phase 2 Market Data Foundation in progress. "
    "Instrument Master and local historical CSV ingestion are available. "
    "Exchange-calendar gap checks and broader data workflows remain outstanding."
)

st.caption("Project Aegis | Local development | No paid services required")
