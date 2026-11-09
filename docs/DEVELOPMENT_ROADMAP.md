# Development Roadmap

This roadmap defines the current 12-phase sequence. Phase-specific scope and acceptance
criteria remain in the linked task files. Statuses distinguish completed work,
implemented-but-not-accepted foundations, planned work, and blocked prerequisites.

| Phase | Milestone | Status and evidence |
|---:|---|---|
| 1 | Foundation | **Complete.** The historical M0 planning/repository foundation and M1 local application foundation are recorded complete; evidence is in [the Phase 1 task](../tasks/PHASE_01_FOUNDATION.md), including `RESEARCH_ONLY` mode. |
| 2 | Instrument Master, Historical OHLCV Ingestion, Exchange Calendar and Market Data Quality | **In progress.** The Instrument Master, local CSV ingestion, and configurable session-aware quality engine are implemented. Source-use verification, maintained official calendar coverage, and fail-closed integration in dependent workflows remain. See [the Phase 2 task](../tasks/PHASE_02_MARKET_DATA.md). |
| 3 | Strategy Lab | **Deterministic core complete; AI assistance deferred.** AI-assisted hypothesis drafting and isolated generated-code evaluation remain deferred; runs depend on Phase 2 data gates. See [the Phase 3 task](../tasks/PHASE_03_STRATEGY_LAB.md). |
| 4 | Backtesting Engine | **In progress; acceptance pending.** Existing scoped implementation and remediation evidence do not establish full milestone acceptance. See [the Phase 4 task](../tasks/PHASE_04_BACKTESTING.md) and its historical review reports. |
| 5 | Candlestick Patterns and Price Action | **Accepted with documented limitations.** The deterministic, research-only library meets the Phase 5 acceptance contract. Synthetic tests do not establish predictive performance or operational market-data readiness. See [the Phase 5 task](../tasks/PHASE_05_CANDLESTICK_PRICE_ACTION.md) and [the implementation report](./reviews/PHASE_05_IMPLEMENTATION_REPORT.md). |
| 6 | Technical Indicators | **Accepted with documented limitations.** The approved v1 subset is implemented and validated as a deterministic research library. Batch APIs do not provide streaming state; VWAP requires caller session metadata and does not verify calendars. See [the Phase 6 task](../tasks/PHASE_06_TECHNICAL_INDICATORS.md) and [implementation report](./reviews/PHASE_06_IMPLEMENTATION_REPORT.md). |
| 7 | ML Research | **Research harness implemented; acceptance pending permitted, quality-gated data and after-cost out-of-sample evidence.** See [the Phase 7 task](../tasks/PHASE_07_ML_RESEARCH.md) and [implementation report](./reviews/PHASE_07_IMPLEMENTATION_REPORT.md). |
| 8 | Paper Trading | **Planned.** Depends on validated backtesting, risk controls, and a deterministic paper ledger. See [the Phase 8 task](../tasks/PHASE_08_PAPER_TRADING.md). |
| 9 | AI Agent and Tool Framework | **Planned.** AI remains advisory and cannot authorize or execute trades. See [the Phase 9 task](../tasks/PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md). |
| 10 | Live Data Forward Paper Trading | **Planned; prerequisite-gated.** Requires an approved permitted read-only data source and the preceding paper-trading and AI-control foundations. See [the Phase 10 task](../tasks/PHASE_10_LIVE_DATA_FORWARD_PAPER_TRADING.md). |
| 11 | Cloud Deployment and Reliability | **Planned; prerequisite-gated.** See [the Phase 11 task](../tasks/PHASE_11_CLOUD_DEPLOYMENT_AND_RELIABILITY.md). |
| 12 | Live Readiness | **Planned and blocked on independent evidence and explicit approval; default decision is NO-GO.** This phase is an assessment, not authorization or activation. See [the Phase 12 task](../tasks/PHASE_12_LIVE_READINESS.md). |

## Phase boundaries and dependencies

Phase 2 includes the implemented Instrument Master, local historical CSV ingestion, and
Stage 4 exchange-session/data-quality engine. The checked-in calendar configuration is
intentionally empty. No verified official holiday dataset, approved external feed, or
provider adapter is included. Source-use verification, maintained official calendar
coverage, and fail-closed quality/staleness integration remain prerequisites for dependent
workflows.

Phase 3's deterministic Strategy Lab includes a versioned SMA-crossover research
specification, experiment/run records, Decimal-based signal generation, provenance
fingerprints, and a dashboard. AI-assisted hypothesis drafting and isolated
generated-code evaluation are deferred to Phase 9 and are not prerequisites for
backtesting. Strategy runs remain blocked until Phase 2 supplies confirmed calendar
coverage and complete, fresh data.

Phase 4 backtesting remains acceptance-pending and does not authorize paper or live
execution. Phase 5 and Phase 6 are accepted as bounded research libraries with
documented limitations. Phase 6 acceptance does not change Phase 4's status or make
indicators independently validated trading strategies. Verified exchange-calendar
coverage, source-use permissions and trustworthy market-data availability remain
separate operational gates. Phase 7's in-memory, provenance-gated classifier harness
does not satisfy those operational gates or establish predictive value; milestone
acceptance is pending real permitted data and after-cost out-of-sample evidence. Phases
8–12 retain their planned dependency order; none changes the
application's current `RESEARCH_ONLY` execution mode.

## Planning estimates

This is a staged project expected to take multiple weeks or months. Estimates must be
updated as phases progress. Do not promise a live-trading date.
