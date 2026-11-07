# Phase 2 — Market Data

## Goal
Add a provider-neutral market-data interface and one verified, permitted source.

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

### Remaining Phase 2 work
- [ ] Define and verify permitted data-source use before using files from any external source.
- [ ] Add an exchange calendar/session policy for missing bars, holidays, and market-hours validation.
- [ ] Add stale-data rules for workflows that consume historical datasets.
- [ ] Consider a provider adapter or read-only feed only after requirements and permitted use are reviewed.

## Acceptance criteria
- Imported timestamps are timezone-aware, normalized to UTC, and checked against documented interval alignment.
- Exchange-calendar gaps and stale feeds remain outstanding and must be visible or fail closed before dependent workflows are built.
- Dataset provenance and source terms are documented.
- Tests use mocks/fixtures and cannot submit broker orders.

Stage 3 completes local CSV OHLCV ingestion, not all of Phase 2. Calendar-aware gap validation, stale-data policy, and source permissions remain outstanding.
