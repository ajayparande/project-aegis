# Project Aegis
## AI-assisted quantitative trading research and paper-trading platform

Project Aegis is a controlled research platform for Indian-market strategy research. AI agents assist with learning, hypothesis generation, experiment analysis, and software development. Deterministic Python components handle market-data processing, backtesting, risk checks, and simulated execution.

**Initial objective:** Build a reproducible research and paper-trading system within an initial total budget ceiling of ₹10,000.

**Important:** Project Aegis starts in research-only mode. It must not place live orders. No strategy is assumed profitable. Live trading is out of scope until separate validation, regulatory/broker checks, and explicit human approval.

## Non-negotiable principles

1. No live orders in the initial release.
2. No claim of profitability without reproducible out-of-sample evidence.
3. Include brokerage, taxes/charges where applicable, slippage, liquidity, and execution assumptions in tests.
4. Never expose broker/API credentials in source code, logs, notebooks, prompts, or Git history.
5. AI agents cannot change risk limits, approve their own work, or enable live execution.
6. All experiments, datasets, code versions, assumptions, and results must be recorded.
7. Fail closed: stale data, missing data, API errors, unexpected state, or failed checks stop the relevant workflow.
8. Preserve the ₹10,000 budget ceiling. No paid service is activated without a documented need and user approval.

## Default technical decisions

- Language: Python.
- Development: local computer first.
- Source control: Git and GitHub.
- Data/analysis: pandas, NumPy, scikit-learn.
- Storage: SQLite initially; PostgreSQL only if justified later.
- Dashboard: Streamlit.
- Agent orchestration: begin with simple Python workflows; evaluate LangGraph only when workflows need it.
- LLM: one low-cost API model with hard usage limits; model-provider adapter keeps it replaceable.
- Broker: Zerodha Kite Connect may be integrated for data after current pricing, API access, data-use, and regulatory requirements are verified.
- Initial execution: custom paper-trading simulator; no live order endpoint.

## Milestone sequence

1. Requirements, repository, and secure development foundation.
2. Market-data ingestion and data-quality controls.
3. Strategy research and experiment registry.
4. Event-aware backtesting with costs.
5. Out-of-sample validation and robustness checks.
6. Paper trading with live market data.
7. Monitoring, reporting, and operational review.
8. Separate live-readiness assessment (not automatic activation).

See `docs/DEVELOPMENT_ROADMAP.md` and `tasks/` for acceptance criteria.

## Start here

Read these in order:

1. `docs/PROJECT_VISION.md`
2. `docs/PRODUCT_REQUIREMENTS.md`
3. `docs/SYSTEM_ARCHITECTURE.md`
4. `docs/BUDGET_AND_COST_CONTROLS.md`
5. `docs/RISK_MANAGEMENT.md`
6. `docs/VALIDATION_AND_ACCEPTANCE.md`
7. `docs/DEVELOPMENT_ROADMAP.md`

## Current status

**Phase 1 — Foundation: implementation completed; local tests and quality checks passed, and GitHub Actions CI reported green.**

Implemented foundation includes:

- Python package and project configuration.
- `.gitignore` and safe `.env.example`.
- Typed configuration restricted to `RESEARCH_ONLY`.
- Structured JSON logging with basic secret redaction.
- SQLite database schema for research records, instruments, and market-data storage.
- Streamlit system-status dashboard.
- Automated pytest tests.
- Ruff linting and formatting, and MyPy type checks.
- GitHub Actions CI with Gitleaks secret scanning and `.gitignore` validation.

**Current development phase: Phase 2 — Market Data Foundation.**

The provider-neutral Instrument Master is implemented for NSE and BSE. It supports equities, indexes, futures, and options; registration, lookup, filtering, metadata updates, search, and validated bulk import; and exact decimal text storage for tick size and strike price. The dashboard shows instrument counts, exchange distribution, filters, search, and instrument details.

Local historical OHLCV CSV ingestion is implemented for registered active NSE/BSE instruments. Stage 3 adds version 5 of the SQLite schema and stores newly imported candles in the historical_candles table using canonical decimal text. The earlier market_data table and its REAL values are preserved unchanged; the CSV importer does not write to that legacy table.

CSV columns are timestamp, open, high, low, close, and volume; select exchange, symbol, and interval when importing. Prices must be plain base-10 decimal strings (no exponent notation), and volume must be a non-negative integer. Example:

    timestamp,open,high,low,close,volume
    2026-01-05T09:15:00+05:30,100.10,101.20,99.90,100.50,1200

Timestamps require an ISO-8601 timezone offset. Intraday starts are aligned from 09:15 Asia/Kolkata for 1m, 5m, 15m, and 1h intervals; 1d timestamps must be midnight Asia/Kolkata. Stored timestamps are UTC ISO text ending in Z. The importer checks OHLC relationships, positive prices, integer volume, interval alignment, and—only for exact registry tick sizes that are not legacy REAL-derived—price alignment to tick size.

Within one CSV, the first valid row for a candle identity wins. Existing candles are never overwritten: duplicate identities are skipped and counted. Reimporting identical file bytes is identified by SHA-256; overlapping files are identified by candle identity, so new candles can still be added. Import history stores filename (not its absolute path), checksum, requested and resolved instrument, interval, row counts, status, timestamps, and error summary. Invalid rows and duplicates are retained in data-quality issue records with source row numbers.

This is local historical file ingestion only. No market calendar, exchange holiday, missing-session/gap, or stale-feed validation is implemented. Users are responsible for verifying source permissions and confirming migrated legacy tick sizes against an authoritative source before financial use. Stage 3 does not complete all Phase 2 work.

No broker connection, live market-data integration, live orders, or automated trading is enabled.
