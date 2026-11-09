# Project Aegis Development Instructions

These instructions apply to all future development work in this repository.

## Project purpose

Project Aegis is a local, AI-assisted quantitative trading research and paper-trading platform for Indian markets.

Deterministic Python components handle market data, data validation, strategy research, backtesting, risk management, and simulated execution. AI may assist with research, hypothesis generation, experiment analysis, and development. AI must never independently authorize or execute trades.

## Mandatory safety rules

- Keep the application in `RESEARCH_ONLY` mode.
- Do not connect to Zerodha or another broker unless a separate live-readiness phase has been explicitly approved.
- Never place live orders.
- Never access, expose, or request broker credentials.
- Never weaken risk controls.
- Do not spend money or subscribe to paid services without explicit approval.
- Never claim profitability or guaranteed returns.
- Do not commit or push unless explicitly requested.
- Never delete or recreate the user's database.
- Protect existing research data.

## Development environment

- Windows 11 and PowerShell.
- Python 3.11.
- VS Code.
- Local SQLite database.
- Streamlit dashboard.
- Use the existing project virtual environment.
- Do not require Docker, WSL, Node.js, Kubernetes, or PostgreSQL.

## Architecture and coding standards

- Follow the existing `src/aegis` package structure.
- Use type hints and clear docstrings.
- Use `pathlib` for filesystem paths.
- Use parameterized SQL queries.
- Write deterministic, isolated tests.
- Keep database access and business logic separate from the dashboard.
- Never hardcode secrets.
- Keep `.env.example` free of real credentials.
- Use appropriate financial precision; do not rely on binary floating-point for financial calculations.
- Review database migrations for transactional safety and data preservation.
- Add tests for every new feature and regression.
- Avoid unnecessary dependencies.
- Dashboard tests must use temporary database and log paths and must not modify `data/aegis.db` or `logs/aegis.log`.

## Current database

- Schema version: 7.
- Tables: sources, hypotheses, strategies, experiments, runs, schema_metadata, instruments, data_imports, market_data, data_quality_issues, historical_candles, and import_source_use_history.
- Preserve migration history and all existing records.
- New historical OHLC prices belong in historical_candles as canonical decimal text; do not use SQLite REAL for new financial price storage.
- The legacy market_data REAL table is preserved and is not used by the CSV importer.
- Migrated REAL-derived instrument tick sizes cannot recover their original decimal input and require authoritative verification before financial use.
- Exchange calendars are local JSON in `config/exchange_calendars.json`; the checked-in configuration intentionally has no claimed official holiday coverage.
- Calendar-dependent reports must preserve `CONFIGURED` versus `CONFIRMED` versus unavailable status and must not score incomplete coverage.
- Historical imports default to `UNVERIFIED` source-use status. Backtests require an explicit audited `VERIFIED_PERMITTED` declaration for every contributing import; the application records the user's declaration and does not determine legal rights.

## Current development phase

Phase 1 foundation is complete. The Phase 2 market-data components and deterministic
Phase 3 Strategy Lab are implemented; Phase 2 remains in progress while verified calendar
coverage and source-use permissions are outstanding. Phase 3 AI-assisted hypothesis drafting
and generated-code isolation remain deferred and are not prerequisites for backtesting.

Phase 4 Backtesting is the current development milestone. An initial deterministic
implementation exists and must meet the acceptance criteria in `tasks/PHASE_04_BACKTESTING.md`
before the phase is considered complete.

Next objectives:

1. Maintain locally verified NSE/BSE session and holiday coverage before using confirmed completeness reports.
2. Verify market-data source permissions and integrate freshness and quality gates into dependent workflows.
3. Complete and validate Phase 4 backtesting without weakening data-quality or financial controls.
4. Keep deferred AI-assisted hypothesis drafting and generated-code review under their separately scoped safety controls.

Do not implement Zerodha connectivity yet.

## Required quality checks

Run these before declaring a feature complete:

```powershell
python -m pytest -v
python -m ruff check .
python -m ruff format --check .
python -m mypy src
```

Report actual results. Never claim a check passed unless it was executed.

## Working method

- Inspect existing code before editing.
- Work incrementally.
- Explain the implementation plan before substantial changes.
- Modify only files necessary for the task.
- Inspect the final Git diff.
- Report modified files, tests, limitations, and risks.
- Never silently overwrite existing user work.
- Do not commit or push without explicit permission.
