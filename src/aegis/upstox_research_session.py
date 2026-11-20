"""Bounded Upstox collection bridge to existing qualified historical execution.

Historical results are HISTORICAL_SIMULATION. Phase 8 forward execution is not
invoked with old quotes: its freshness and runtime authorization rules remain intact.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aegis.backtesting import (
    BacktestConfig,
    BacktestError,
    default_cost_assumptions,
    execute_one_instrument,
)
from aegis.database import get_connection, get_readonly_connection
from aegis.market_data import import_ohlcv_csv, list_historical_candles_in_range
from aegis.market_quality import analyze_dataset, load_approved_calendar_registry
from aegis.providers.collection_store import HistoricalCollectionStore
from aegis.providers.upstox_collection import UpstoxCollectionRequest, collect_upstox_history
from aegis.providers.upstox_http import UpstoxHistoricalHTTPClient
from aegis.strategy_lab import StrategyCandle, generate_sma_crossover_signals


def qualified_historical_simulation(
    database: Path,
    *,
    symbol: str,
    from_date: date,
    to_date: date,
    calendar_path: Path,
    as_of: datetime,
) -> dict[str, Any]:
    """Use M1B's read-only, qualified, next-bar executor; never promote source rights."""
    result: dict[str, Any] = {
        "label": "HISTORICAL_SIMULATION",
        "status": "BLOCKED",
        "reason_codes": [],
        "starting_cash": "10000",
        "orders": 0,
        "fills": 0,
        "rejected_or_unfilled_orders": 0,
        "fees": "0",
        "ending_cash": "10000",
        "realized_net_pnl": "0",
        "decisions": {"status": "NOT_EVALUATED", "BUY": 0, "SELL": 0, "HOLD": 0},
    }
    try:
        calendars = load_approved_calendar_registry(calendar_path)
        quality = analyze_dataset(
            database, "NSE", symbol, "1m", from_date, to_date, as_of=as_of, calendars=calendars
        )
        result.update(
            calendar_confidence=quality.calendar_confidence,
            calendar_configuration_sha256=calendars.configuration_sha256,
            calendar_coverage_status=quality.calendar_status,
            quality_status=quality.overall_status,
            quality_as_of=quality.as_of.isoformat(),
            quality_reasons=list(quality.reasons[:10]),
            quality_reasons_truncated=len(quality.reasons) > 10,
            expected_candles=quality.expected_candles,
            provisional_expected_candles=quality.provisional_expected_candles,
            actual_candles=quality.actual_candles,
            confirmed_completeness_percentage=(
                str(quality.completeness_percentage)
                if quality.completeness_percentage is not None
                else None
            ),
            invalid_candles=quality.invalid_candle_count,
            duplicate_candle_identities=quality.duplicate_identity_count,
            duplicate_import_rows=quality.duplicate_import_row_count,
            rejected_import_rows=quality.rejected_import_row_count,
            unexpected_candles=len(quality.unexpected_timestamps),
            provisional_unexpected_candles=len(quality.provisional_unexpected_timestamps),
            first_candle_timestamp=quality.first_candle_timestamp,
            last_candle_timestamp=quality.last_candle_timestamp,
            staleness_status=quality.staleness_status,
            missing_candles=len(quality.missing_timestamps),
            provisional_missing_candles=len(quality.provisional_missing_timestamps),
        )
        if quality.calendar_confidence != "CONFIRMED":
            result["reason_codes"].append("CALENDAR_NOT_CONFIRMED")
        if quality.missing_timestamps or quality.provisional_missing_timestamps:
            result["reason_codes"].append("MISSING_COMPLETED_CANDLES")
        if quality.overall_status != "COMPLETE":
            result["reason_codes"].append("HISTORICAL_QUALITY_NOT_COMPLETE")
        if quality.staleness_status != "FRESH":
            result["reason_codes"].append("DATA_FRESHNESS_NOT_ESTABLISHED")
        start_boundary = (
            datetime.combine(from_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
            .astimezone(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        end_boundary = (
            datetime.combine(to_date, datetime.max.time(), tzinfo=ZoneInfo("Asia/Kolkata"))
            .astimezone(UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        with closing(get_readonly_connection(database)) as connection:
            sources = connection.execute(
                """SELECT d.id AS import_id, d.source_use_status, d.file_sha256,
                          COUNT(c.id) AS contributing_candles,
                          (SELECT COUNT(*) FROM import_source_use_history sh
                           WHERE sh.import_id=d.id) AS declaration_count,
                          h.id AS latest_declaration_id,
                          h.new_status AS latest_declaration_status
                   FROM historical_candles c
                   JOIN data_imports d ON d.id=c.import_id
                   JOIN instruments i ON i.id=c.instrument_id
                   LEFT JOIN import_source_use_history h ON h.id=(
                       SELECT MAX(sh.id) FROM import_source_use_history sh WHERE sh.import_id=d.id
                   )
                   WHERE i.exchange='NSE' AND i.trading_symbol=? AND c.interval='1m'
                     AND c.candle_timestamp >= ? AND c.candle_timestamp <= ?
                   GROUP BY d.id ORDER BY d.id""",
                (symbol, start_boundary, end_boundary),
            ).fetchall()
        result["source_imports"] = [dict(row) for row in sources]
        if not sources or any(row["source_use_status"] != "VERIFIED_PERMITTED" for row in sources):
            result["reason_codes"].append("SOURCE_USE_UNVERIFIED")
        # Always delegate the final decision to the authoritative boundary,
        # including its audited source history, costs, tick and volume gates.
        execution = execute_one_instrument(
            database,
            exchange="NSE",
            trading_symbol=symbol,
            interval="1m",
            start_date=from_date,
            end_date=to_date,
            fast_window=2,
            slow_window=3,
            config=BacktestConfig(Decimal(10000), 1, Decimal("0.10"), default_cost_assumptions()),
            calendar_config_path=calendar_path,
            as_of=as_of,
        )
    except (BacktestError, ValueError):
        if not result["reason_codes"]:
            result["reason_codes"].append("HISTORICAL_QUALIFICATION_BLOCKED")
        return result
    # This database is owned by one session. Read the qualified imported candles
    # for explanatory signal counts using the same existing SMA implementation.
    rows: list[dict[str, object]] = []
    for _ in range(3):
        page = list_historical_candles_in_range(
            database,
            exchange="NSE",
            trading_symbol=symbol,
            interval="1m",
            start_timestamp=datetime.combine(
                from_date, datetime.min.time(), tzinfo=ZoneInfo("Asia/Kolkata")
            ).isoformat(),
            end_timestamp=datetime.combine(
                to_date, datetime.max.time(), tzinfo=ZoneInfo("Asia/Kolkata")
            ).isoformat(),
            limit=1000,
            after_timestamp=str(rows[-1]["candle_timestamp"]) if rows else None,
            after_id=int(str(rows[-1]["id"])) if rows else None,
        )
        rows.extend(page)
        if len(page) < 1000:
            break
    candles = tuple(
        StrategyCandle(
            timestamp=str(r["candle_timestamp"]),
            open=Decimal(str(r["open_decimal"])),
            high=Decimal(str(r["high_decimal"])),
            low=Decimal(str(r["low_decimal"])),
            close=Decimal(str(r["close_decimal"])),
            volume=int(str(r["volume"])),
        )
        for r in rows
    )
    signals = generate_sma_crossover_signals(candles, fast_window=2, slow_window=3)
    counts = Counter(s.action for s in signals)
    result.update(
        status="COMPLETED",
        reason_codes=[],
        orders=len(execution.orders),
        fills=len(execution.fills),
        rejected_or_unfilled_orders=len(execution.rejected_or_unfilled_orders),
        fees=execution.summary["total_fees"],
        ending_cash=execution.summary["ending_cash"],
        realized_net_pnl=execution.summary["realized_net_pnl"],
        position=execution.open_position is not None,
        decisions={
            "status": "EVALUATED",
            "BUY": counts["ENTER_LONG"],
            "SELL": counts["EXIT_LONG"],
            "HOLD": max(0, len(candles) - 3 - len(signals)),
            "NO_DECISION": min(3, len(candles)),
        },
        execution_engine="EXISTING_M1B_HISTORICAL",
        signals_sha256=execution.diagnostics["signals_sha256"],
        order_evidence=[
            {
                key: order.get(key)
                for key in (
                    "side",
                    "status",
                    "signal_candle_timestamp",
                    "execution_bar_timestamp",
                    "execution_time",
                    "fill_price",
                    "total_fees",
                )
            }
            for order in execution.orders[:10]
        ],
        evidence_truncated=len(execution.orders) > 10,
    )
    return result


def collect_and_attempt_simulation(
    database: Path,
    storage: Path,
    *,
    client: UpstoxHistoricalHTTPClient,
    instrument_key: str,
    symbol: str,
    from_date: date,
    to_date: date,
    calendar_path: Path,
    operator_authorized: bool = False,
    data_use_reviewed: bool = False,
    as_of: datetime,
) -> dict[str, Any]:
    """Retrieve one bounded window, preserve source evidence, import UNVERIFIED and qualify."""
    if (to_date - from_date).days not in range(7):
        raise ValueError("Historical sample must contain one to seven calendar days.")
    if to_date > as_of.astimezone(ZoneInfo("Asia/Kolkata")).date():
        raise ValueError("Historical window cannot extend into future sessions.")
    request = UpstoxCollectionRequest(instrument_key, "1m", from_date, to_date, max_windows=1)
    store = HistoricalCollectionStore(storage)
    progress = collect_upstox_history(
        store=store,
        client=client,
        request=request,
        network_authorized=operator_authorized,
        data_use_reviewed=data_use_reviewed,
    )
    batch = store.load_verified(
        provider_id="upstox",
        instrument_key=instrument_key,
        interval="1m",
        window=request.windows()[0],
    )
    if batch is None:
        raise ValueError("Historical source evidence is unavailable.")
    if len(batch.candles) > 3000:
        raise ValueError("Historical sample exceeds the bounded candle budget.")
    # The collection store preserves the normalized source and raw candle-array
    # fingerprint. CSV and SQLite are canonical exact-decimal projections.
    csv_path = storage / "import.csv"
    with csv_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        for c in batch.candles:
            writer.writerow(
                (
                    c.timestamp.isoformat(),
                    str(c.open),
                    str(c.high),
                    str(c.low),
                    str(c.close),
                    c.volume,
                )
            )
    imported = import_ohlcv_csv(database, csv_path, "NSE", symbol, "1m")
    with closing(get_connection(database)) as connection:
        metadata = json.loads(
            connection.execute(
                "SELECT metadata_json FROM data_imports WHERE id=?", (imported.import_id,)
            ).fetchone()[0]
        )
        metadata.update(
            provider="upstox",
            provider_instrument_key=instrument_key,
            raw_candle_array_sha256=batch.response_sha256,
            retrieval_kind="V3_HISTORICAL",
            source_use_status="UNVERIFIED",
        )
        connection.execute(
            "UPDATE data_imports SET metadata_json=? WHERE id=?",
            (json.dumps(metadata), imported.import_id),
        )
        connection.commit()
    return {
        "status": "RETRIEVED",
        "candles_retrieved": progress.candle_count,
        "accepted_rows": imported.accepted_rows,
        "rejected_rows": imported.rejected_rows,
        "duplicate_rows": imported.duplicate_rows,
        "source_use_status": "UNVERIFIED",
        "raw_candle_array_sha256": batch.response_sha256,
        "simulation": qualified_historical_simulation(
            database,
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            calendar_path=calendar_path,
            as_of=as_of,
        ),
    }
