"""Regression tests for the bounded continuous synthetic pilot."""

from __future__ import annotations

from contextlib import closing

import pytest

from aegis.continuous_synthetic_pilot import run_batch
from aegis.database import get_connection


def test_first_batch_and_restart_resume(tmp_path):
    first = run_batch(tmp_path, 5)

    assert first["mode"] == "RESEARCH_ONLY"
    assert first["broker"] == "NONE"
    assert first["live_orders"] is False
    assert first["quotes_processed"] == 5
    assert first["completed_quote_sequence"] == 5
    assert first["reconciliation"] == "RECONCILED"

    second = run_batch(tmp_path, 2)

    assert second["quotes_processed"] == 2
    assert second["completed_quote_sequence"] == 7
    assert second["reconciliation"] == "RECONCILED"

    with closing(get_connection(tmp_path / "pilot.db")) as connection:
        quotes = connection.execute(
            "SELECT COUNT(*) FROM forward_quotes WHERE accepted = 1"
        ).fetchone()[0]

        duplicates = connection.execute(
            """SELECT COUNT(*) FROM (
                 SELECT instrument_id, sequence
                 FROM forward_quotes
                 WHERE accepted = 1
                 GROUP BY instrument_id, sequence
                 HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0]

        orders = connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]

        states = {
            (row["interval"], row["status"]): row["total"]
            for row in connection.execute(
                """SELECT interval, status, COUNT(*) AS total
                   FROM forward_scheduler_events
                   GROUP BY interval, status"""
            )
        }

    assert quotes == 7
    assert duplicates == 0
    assert orders == 0
    assert states.get(("1m", "HOLD")) == 6
    assert states.get(("5m", "HOLD")) == 1


def test_session_limit_preserves_existing_progress(tmp_path):
    result = run_batch(tmp_path, 5)
    assert result["completed_quote_sequence"] == 5

    with pytest.raises(ValueError, match="20-quote"):
        run_batch(tmp_path, 16)

    with closing(get_connection(tmp_path / "pilot.db")) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM forward_quotes WHERE accepted = 1"
        ).fetchone()[0]

    assert count == 5


def test_invalid_batch_size_rejected(tmp_path):
    for count in (0, -1, 21, True):
        with pytest.raises(ValueError):
            run_batch(tmp_path, count)

    assert not (tmp_path / "pilot.db").exists()
