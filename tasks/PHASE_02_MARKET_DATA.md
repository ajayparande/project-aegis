# Phase 2 — Market Data

## Goal
Establish a provider-neutral, auditable market-data foundation. External data may only be used after its source, permitted use, licensing, and technical constraints have been verified.

## Tasks

### Instrument Master — implemented
- [x] Add provider-neutral NSE/BSE instrument records for equities, indexes, futures, and options.
- [x] Add registration, lookup, listing filters, metadata/status updates, search, and per-row bulk import.
- [x] Validate exchange/symbol identity, derivative metadata, and positive precise decimal fields.
- [x] Preserve legacy tick-size values while adding exact decimal text storage through a transactional migration.
- [x] Add Instrument Master dashboard counts, filters, search, and details.
- [x] Add deterministic CRUD, validation, duplicate, bulk-import, migration, and dashboard tests.

### Stage 3 — Historical CSV ingestion implemented
- [x] Add an exact-decimal historical candle table through an additive version 4 to 5 migration.
- [x] Import local CSV files for active registered instruments with supported intervals and explicit timestamp offsets.
- [x] Validate prices, OHLC relationships, volume, timestamp alignment, instrument state, and reliable tick-size alignment.
- [x] Preserve candle identity: first valid in-file row wins; existing rows are never overwritten.
- [x] Record file checksum, filename, instrument, interval, status, timestamps, row counts, and row-level quality issues.
- [x] Add deterministic migration, validation, duplicate, rollback, provenance, and dashboard tests.

### Stage 4 — Exchange sessions and quality implemented
- [x] Add local exchange/segment session configuration with explicit source, confidence, and date coverage.
- [x] Support holidays, non-trading weekdays, breaks, exceptional sessions, and early closes without bundling fabricated calendar data.
- [x] Calculate completed expected candle starts for supported intervals using Stage 3 timestamp semantics.
- [x] Report confirmed/provisional missing and unexpected candles, invalid values, duplicates, and dataset coverage read-only.
- [x] Add explicit-`as_of` staleness checks with configurable completed-candle thresholds.
- [x] Add deterministic calendar, boundary, quality, staleness, read-only, and dashboard tests.

### Evidence-readiness reporting — implemented
- [x] Provide a read-only `READY`/`BLOCKED` report that requires an active registered instrument, the administrator-approved `CONFIRMED` calendar, complete expected-candle coverage, and the existing dataset-quality checks.
- [x] Require complete, matching `VERIFIED_PERMITTED` source-use declaration provenance for every contributing import; omitting `source_import_id` does not bypass this gate or completeness checks.
- [x] Keep missing, stale, duplicate, invalid, rejected, or untrusted evidence blocked with deterministic reason codes. Synthetic test declarations are not external source-rights evidence.

### Cross-phase source-permission gate — implemented and verified
- [x] Strategy Lab (`run_strategy_experiment`): captures and validates every contributing source import before signal generation; `UNVERIFIED`, `PROHIBITED`, missing/null/malformed/unknown statuses, and missing provenance all block via `DataQualityGateError`; blocked runs persist `BLOCKED`/`PARTIAL_BLOCKED` with zero signals; completed runs persist full source-import provenance.
- [x] Backtesting: every contributing import is checked before simulation; missing provenance blocks explicitly; `UNVERIFIED`/`PROHIBITED` imports block; completed backtests persist `source_imports` provenance.
- [x] Paper Trading (`submit_paper_order`): BUY requires an explicitly `VERIFIED_PERMITTED` completed import plus matching complete/fresh confirmed-calendar quality evidence; missing/invalid provenance fails closed and halts the account; SELL reductions retain their own evidence/capacity/held-quantity checks.
- [x] New or updated regression tests cover permitted, unverified, prohibited, undeclared, missing, malformed, mixed multi-import, and persisted-provenance cases across Strategy Lab and Backtesting.

### Remaining operational prerequisites (require authoritative external evidence)
- [ ] Verify and document the permitted use, licensing, entitlements, rate limits, and retention rules for any proposed external market-data source. A source-use declaration is recorded provenance, not independent proof of legal licensing.
- [ ] Independently review and maintain authoritative NSE/BSE exchange-calendar coverage before claiming `CONFIRMED`. The checked-in configuration contains a `CONFIGURED` NSE cash candidate for 2025-2026 only; 2027 and other segments remain uncovered. See [calendar evidence](../docs/EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md). Calendar-dependent workflows remain fail-closed until confirmed coverage is supplied.
- [ ] Obtain a real historical dataset from a source with verified permitted use; synthetic `VERIFIED_PERMITTED` declarations in tests are test fixtures only, not licensing evidence.
- [ ] Add a provider adapter or read-only live feed only in a separately scoped milestone after source approval.
- [ ] Phase 4 backtesting acceptance remains pending; the source-permission gate is implemented but Phase 4 milestone acceptance is not yet satisfied.

## Acceptance criteria
- Imported timestamps are timezone-aware, normalized to UTC, and checked against documented interval alignment.
- Calendar-dependent results remain insufficient/provisional without complete verified calendar coverage; dependent workflows fail closed on that status.
- Every contributing source import must be explicitly `VERIFIED_PERMITTED` before Strategy Lab, Backtesting, or Paper Trading execution; undeclared, `UNVERIFIED`, `PROHIBITED`, or missing provenance blocks fail-closed.
- Dataset provenance and source terms are documented; declarations are recorded, not independently verified.
- Tests use mocks/fixtures and cannot submit broker orders.
- No external data source is treated as free or permitted without verification.
- Phase 2 software acceptance is limited to the reviewed implementation scope recorded below. It does not satisfy the outstanding operational prerequisites above (authoritative calendar coverage, permitted historical dataset, source entitlement evidence, and Phase 4 acceptance); operational readiness remains blocked.

Stages 3 and 4 provide local CSV ingestion and a configurable calendar-aware quality engine. They do not provide an approved live market-data feed or complete official calendar coverage.

## Software acceptance closure — 2026-11-20

**Final software verdict: ACCEPT, limited to the reviewed Phase 2 software scope.**
The initial independent audit returned REJECT because evidence-readiness assessment
combined separate SQLite snapshots. That sole reported blocker was corrected:
`assess_evidence_readiness()` now opens one read-only SQLite connection, executes
explicit `BEGIN`, and uses the same transaction for instrument activity, dataset
quality, source-import identity and provenance, source-use declarations, and supplied
import-ID checks.

The deterministic WAL concurrent-update regression failed before the correction and
passed afterward. Focused Phase 2 verification reported 170 tests passed; Ruff lint and
formatting passed; MyPy passed for 62 source files; `git diff --check` passed. This is
implementation verification, not hosted CI evidence. The corrected code was not
independently re-audited.

Software acceptance does **not** establish operational readiness. Authoritative
confirmed exchange-calendar coverage, actual permitted historical OHLCV availability,
independently verified data rights or broker entitlements, and Phase 4 operational
readiness remain outstanding. A source-use declaration is not independent proof of
legal rights. Affected workflows must continue to fail closed. No live-trading
authorization is granted.
