# Phase 10A-3 Task 1: NSE Cash Calendar Candidate

## Outcome

Implemented a source-backed NSE cash-market calendar candidate for 2025 and 2026. The candidate
uses the existing schema, remains `CONFIGURED`, and is not operationally accepted. It records
the 2026-01-15 Maharashtra municipal-election closure and models both Diwali Muhurat sessions as
special-session exceptions rather than ordinary days. No 2027 coverage is configured.

The annual holiday circulars, subsequent cash-session circulars, holiday-master updates, page
retrievals, source URLs, dates, hashes, and known limitations are documented in
[`EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md`](../EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md).
The candidate does not apply the supplied F&O Muhurat timing to the cash segment.

## Coverage and behavior

- Exchange/segment: `NSE` / `NSE` (cash equities only).
- Inclusive configured range: 2025-01-01 through 2026-12-31.
- Normal Equity hours: 09:15-15:30 IST, from NSE's current Market Timings page.
- 2025 holidays: the NSE annual cash holiday circular, with later NSE holiday-master descriptions
  retained as subsequent evidence.
- 2026 holidays: the NSE annual cash holiday circular, plus the 2026-01-15 closure in the later
  NSE `CM` holiday-master update.
- Special sessions: cash-specific `NSE/CMTR/70319` (2025-10-21, 13:45-14:45) and
  `NSE/CMTR/76941` (2026-11-08, 14:20-15:30) override their annual-holiday/weekend closures.
- 2027 and other segments are not covered. The 2026 CAS schedule cannot be represented at
  instrument level in the current schema.

The quality analyzer does not produce a confirmed completeness score for this `CONFIGURED`
calendar. Paper order authorization still requires `calendar_status=FULL` and
`calendar_confidence=CONFIRMED`; the test suite includes a regression check for these gates.
No execution, risk, provider, or broker code was changed.

## Validation

Validation:

- Focused dashboard, calendar, and paper-trading tests: **102 passed**.
- Full test suite: **620 passed**.
- `ruff check .`: passed.
- `ruff format --check .`: passed (80 files).
- `mypy src`: passed (21 source files).

## Changed-file inventory

The changes are limited to:

- `config/exchange_calendars.json`
- `app.py`
- `docs/DEVELOPMENT_ROADMAP.md`
- `docs/EXCHANGE_CALENDAR_CONFIGURATION.md`
- `docs/EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/reviews/PHASE_10A_3_TASK_1_IMPLEMENTATION_REPORT.md`
- `tasks/PHASE_02_MARKET_DATA.md`
- `tasks/PHASE_03_STRATEGY_LAB.md`
- `tests/test_dashboard.py`
- `tests/test_market_quality.py`
- `tests/test_paper_trading.py`

No normal application database was opened, initialized, or migrated. Automated tests used only
isolated temporary SQLite databases. No normal database files were modified. No files were
staged or committed.
