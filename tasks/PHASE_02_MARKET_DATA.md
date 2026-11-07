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

### Historical market-data pipeline — remaining
- [ ] Define candle and quote schemas with timezone-aware timestamps.
- [ ] Define provider adapter interface.
- [ ] Verify current data access, subscription, terms, and permitted use before selecting a provider.
- [ ] Implement local CSV/mock historical OHLCV ingestion first.
- [ ] Add duplicate, missing-bar, stale-data, and malformed-value checks.
- [ ] Record source, retrieval time, interval, and dataset version.
- [ ] Add fixtures and mock-provider tests.
- [ ] Consider a read-only live feed only after the historical pipeline passes and requirements are reviewed.

## Acceptance criteria
- All timestamps are timezone-aware and consistently represented.
- Data gaps and stale feeds are visible and halt affected experiments.
- Dataset provenance and source terms are documented.
- Tests use mocks/fixtures and cannot submit broker orders.

Completing the Instrument Master does not complete Phase 2; historical market-data ingestion and its acceptance criteria remain outstanding.
