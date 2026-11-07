# Phase 2 — Market Data

## Goal
Add a provider-neutral market-data interface and one verified, permitted source.

## Tasks
- [ ] Define instrument, candle and quote schemas with timezone-aware timestamps.
- [ ] Define provider adapter interface.
- [ ] Verify current data access, subscription, terms and permitted use before selecting provider.
- [ ] Implement historical data ingestion first.
- [ ] Add duplicate, missing-bar, stale-data and malformed-value checks.
- [ ] Record source, retrieval time, interval and dataset version.
- [ ] Add fixtures and mock-provider tests.
- [ ] Add read-only live feed only after historical pipeline passes.

## Acceptance criteria
- All timestamps are timezone-aware and consistently represented.
- Data gaps and stale feeds are visible and halt affected experiments.
- Dataset provenance and source terms are documented.
- Tests use mocks/fixtures and cannot submit broker orders.
