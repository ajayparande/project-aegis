"""Project Aegis - local research dashboard."""

import streamlit as st

from aegis.config import get_settings
from aegis.database import get_database_status, initialize_database
from aegis.instruments import list_instruments, search_instruments
from aegis.logging_config import setup_logging

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

st.subheader("Development Status")

st.info(
    "Phase 1 — Foundation development in progress. "
    "Research, hypothesis management, strategy development, "
    "and experiment tracking will be added in subsequent phases."
)

st.caption("Project Aegis | Local development | No paid services required")
