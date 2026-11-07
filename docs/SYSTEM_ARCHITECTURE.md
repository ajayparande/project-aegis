# System Architecture

## High-level components
1. **Instrument master:** provider-neutral exchange, symbol, contract metadata, and active status stored in SQLite.
2. **Data adapters:** retrieve permitted historical/live market data.
3. **Data validation:** check timestamps, missing bars, duplicates, outliers and stale feeds.
4. **Research workspace:** store references, hypotheses and strategy specifications.
5. **Strategy engine:** deterministic signal generation.
6. **Backtest engine:** chronological event simulation with costs.
7. **Validation engine:** out-of-sample, walk-forward and sensitivity analysis.
8. **Paper broker:** simulated orders, fills, positions and cash ledger.
9. **Risk engine:** hard limits and fail-closed controls for simulation.
10. **Experiment registry:** reproducibility metadata and results.
11. **AI orchestration:** bounded tools for research, coding assistance and report explanations.
12. **Dashboard/monitoring:** status, results, errors and cost reporting.

The Instrument Master is implemented in `src/aegis/instruments.py`. New registry operations accept tick sizes and strike prices as `Decimal`, integer, or decimal text and store them as decimal text; floating-point inputs are rejected. The legacy `tick_size REAL` column remains to preserve old records. Migration 3 to 4 copies legacy values to `tick_size_decimal` using SQLite text conversion. This provides a usable text representation of the value stored in SQLite, but cannot recover the exact original decimal supplied before conversion to `REAL`. Do not automatically overwrite or delete legacy values. Verify migrated values against an authoritative source before relying on them in financial calculations.

## Historical CSV ingestion

The local historical importer stores new OHLCV rows in the additive historical_candles table introduced by schema version 5. Open, high, low, and close values use canonical decimal text; parsing, OHLC comparisons, and reliable tick checks use Decimal. The importer never writes imported prices to SQLite REAL columns. The pre-existing market_data table, including its REAL columns and records, remains untouched for compatibility and preservation.

The CSV header is timestamp,open,high,low,close,volume. Exchange, trading symbol, and interval are import arguments. Prices use plain base-10 text without exponent notation; trailing fractional zeroes are removed for canonical storage without rounding. Volume is a non-negative base-10 integer within SQLite's signed integer range.

Timestamps must be ISO-8601 date-times with an explicit UTC offset and whole-minute precision. They are converted to Asia/Kolkata for alignment, then stored as UTC ISO text in YYYY-MM-DDTHH:MM:SSZ form. Intraday intervals (1m, 5m, 15m, 1h) align from 09:15 Asia/Kolkata. Daily (1d) timestamps must be midnight Asia/Kolkata. No exchange-session calendar, holiday, gap, or stale-feed checks are made.

Only active registered instruments are imported. Tick alignment is checked only when an exact registry tick size is present and the legacy REAL tick_size is NULL. Migrated REAL-derived tick sizes are not considered reliable; alignment is skipped with that reason in the import result. Existing migrated values remain unchanged and must be verified against an authoritative source before financial use.

Duplicate policy is deterministic and non-overwriting: the first valid row for a duplicate timestamp within a file wins; an existing candle is retained on later imports. Duplicates are skipped and recorded as quality issues. Identical file bytes for the same instrument and interval are recognized by SHA-256; different files with overlapping candle identities are counted as overlaps, while new rows are accepted.

Each import attempt stores the filename basename, SHA-256 checksum, requested and resolved instrument, interval, start/completion timestamps, status, row counts, duplicate counts, and error summary in data_imports. Rejected rows and duplicate rows are recorded in data_quality_issues with source row numbers. Candle inserts, row issues, and final import counts/status commit in one transaction. Unexpected transaction failures roll back the batch, then record the import as failed in a separate transaction.

The dashboard labels this view historical CSV data, shows per-instrument/interval counts and coverage, recent import outcomes, quality issues, and a bounded exact-text candle table. It does not present the data as live.

## AI roles (logical roles, not necessarily separate models)
- Research librarian: summarises cited educational material.
- Hypothesis researcher: proposes testable hypotheses.
- Strategy author: drafts candidate specifications and code for review.
- Experiment analyst: explains deterministic metrics.
- Risk reviewer: checks experiment design and risk reports.
- Supervisor: schedules approved workflows and reports status.

Initially, one LLM may serve all roles using separate prompts and restricted tools. Do not run many agents concurrently by default. Use a simple Python workflow first; introduce a multi-agent framework only when measured complexity warrants it.

## Trust boundaries
- LLM output is untrusted input.
- Strategy code generated by an LLM must be reviewed and tested in an isolated environment.
- The AI layer has no broker order-placement capability.
- Risk limits are configuration-controlled and cannot be changed by agents.
- Paper broker is the only execution interface in initial releases.
- Secrets and account data must not be sent to an LLM.

## Initial deployment
Local development on the user's computer. SQLite for metadata and experiment results. Parquet/CSV for controlled datasets where licensing permits. Streamlit dashboard locally. No cloud server required initially.

## Future broker integration
Any future Kite Connect adapter must be read-only at first. Verify current Zerodha terms, authentication, static-IP rules, exchange/SEBI requirements, rate limits and data-use rights before implementation. Live order placement must be a separately reviewed module, disabled by default, and require explicit human enablement.
