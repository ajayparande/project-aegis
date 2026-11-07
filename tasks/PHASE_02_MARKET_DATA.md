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

### Remaining work
- [ ] Verify and document the permitted use, licensing, entitlements, rate limits, and retention rules for any proposed external market-data source.
- [ ] Verify and maintain an official calendar dataset before claiming official calendar coverage.
- [ ] Add a provider adapter or read-only live feed only in a separately scoped milestone after source approval.
- [ ] Integrate stale-data and calendar-confidence status with dependent strategy and execution workflows; fail closed when required data quality is insufficient.

## Acceptance criteria
- Imported timestamps are timezone-aware, normalized to UTC, and checked against documented interval alignment.
- Calendar-dependent results remain insufficient/provisional without complete verified calendar coverage; dependent workflows fail closed on that status.
- Dataset provenance and source terms are documented.
- Tests use mocks/fixtures and cannot submit broker orders.
- No external data source is treated as free or permitted without verification.

Stages 3 and 4 provide local CSV ingestion and a configurable calendar-aware quality engine. They do not provide an approved live market-data feed or complete official calendar coverage.
