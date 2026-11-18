# Project Aegis Development Instructions

These instructions apply to all future development work in this repository.

For Claude and other coding agents, read [`CLAUDE.md`](./CLAUDE.md) first. It defines
the document hierarchy, status vocabulary, required workflow, and conflict-handling
rules. Current milestone status is maintained in
[`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md); the detailed M2 contract is
[`tasks/M2_PORTFOLIO_ACCOUNTING.md`](./tasks/M2_PORTFOLIO_ACCOUNTING.md).

## Project purpose

Project Aegis is a local, AI-assisted quantitative trading research and paper-trading platform for Indian markets.

Deterministic Python components handle market data, data validation, strategy research, backtesting, risk management, and simulated execution. AI may assist with research, hypothesis generation, experiment analysis, and development. AI must never independently authorize or execute trades.

## Mandatory safety rules

- Keep the application in `RESEARCH_ONLY` mode.
- No broker integration is authorized in the current scope. Do not connect to Zerodha or
  another broker; any future broker or live-data connection requires its own explicit,
  separately reviewed approval.
- Never place live orders.
- Never access, expose, or request broker credentials.
- Never weaken risk controls.
- Do not spend money or subscribe to paid services without explicit approval.
- Never claim profitability or guaranteed returns.
- Do not commit or push unless explicitly requested.
- Normal application databases must never be deleted or recreated as part of routine
  development or testing. Tests must use isolated disposable databases.
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

- Schema and migration history are defined in `src/aegis/database.py`; its source
  constant is not evidence of the user's on-disk database version or contents.
- Do not access, initialize, migrate, or modify the normal application database without
  explicit approval for the exact operation. Destructive operations are never implied by
  general approval. Any exceptional destructive operation requires separate explicit
  approval identifying the exact database and operation.
- Do not delete or recreate a normal application database as part of routine
  development or testing; use isolated disposable databases for tests.
- Preserve migration history and all existing records.
- New historical OHLC prices belong in historical_candles as canonical decimal text; do not use SQLite REAL for new financial price storage.
- The legacy market_data REAL table is preserved and is not used by the CSV importer.
- Migrated REAL-derived instrument tick sizes cannot recover their original decimal input and require authoritative verification before financial use.
- Exchange calendars are local JSON in `config/exchange_calendars.json`; the checked-in configuration intentionally has no claimed official holiday coverage.
- Calendar-dependent reports must preserve `CONFIGURED` versus `CONFIRMED` versus unavailable status and must not score incomplete coverage.
- Historical imports default to `UNVERIFIED` source-use status. Backtests require an explicit audited `VERIFIED_PERMITTED` declaration for every contributing import; the application records the user's declaration and does not determine legal rights.

## Current development phase

Use [`docs/PROJECT_STATE.md`](./docs/PROJECT_STATE.md) for the dated milestone snapshot.
M1B is recorded as accepted, according to project-owner-supplied history, only for
bounded qualified one-instrument execution; no dated independent acceptance artifact is
identified in the repository, and full Phase 4 acceptance remains separate. M2-A is
recorded as accepted according to project-owner-supplied history; no dated independent
acceptance artifact is identified in the repository. M2-B has owner-reported independent ACCEPT on 2026-11-16 for qualification-only
scope; the original audit artifact is not yet in the repository. Its schema-provenance
defect is corrected and requires focused independent review, not automatic reacceptance.
M2-C is independently accepted for qualified historical per-sleeve M1B composition.
M2-D is implemented and technically ACCEPTED; distinct-reviewer independent sign-off
remains pending. These M2-C/M2-D decisions are recorded from project-owner-supplied
acceptance history; see the evidence limitations in `docs/PROJECT_STATE.md`.
M2-E has not started and is not authorized. Overall M2 is not accepted; it remains
historical research/backtesting only.

The application remains `RESEARCH_ONLY`. Phase 10 forward paper is planned and
prerequisite-gated; Phase 11 and Phase 12 are future scope. No broker or live trading
connectivity is authorized. Do not treat roadmap priorities as permission to begin a
later phase.

The full workflow, prohibition list, and status distinctions are in
[`CLAUDE.md`](./CLAUDE.md) and [`docs/DEVELOPMENT_GUARDRAILS.md`](./docs/DEVELOPMENT_GUARDRAILS.md).

## General quality-check baseline

Task-specific validation restrictions override this general checklist. If the full test
suite is prohibited, do not run it. Report which checks were and were not executed.
Never claim completion or acceptance without the required evidence; if required checks
are prohibited, report the limitation and do not claim completion or acceptance.

When permitted and applicable, run these checks before declaring a feature complete:

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
