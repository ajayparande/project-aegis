"""Project Aegis - local research dashboard."""

import streamlit as st

from aegis.config import get_settings
from aegis.database import get_database_status, initialize_database
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

st.subheader("Development Status")

st.info(
    "Phase 1 — Foundation development in progress. "
    "Research, hypothesis management, strategy development, "
    "and experiment tracking will be added in subsequent phases."
)

st.caption("Project Aegis | Local development | No paid services required")
