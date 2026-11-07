# Development Roadmap

This roadmap maps the ten numbered implementation phases in the root `README.md` and
`tasks/PHASE_*.md`. Phase-specific scope and acceptance criteria remain in those task files.

| Roadmap milestone | Numbered phase(s) | Status |
|---|---|---|
| M0 — Planning and repository foundation | Phase 1 — Foundation | **Complete** |
| M1 — Local application foundation | Phase 1 — Foundation | **Complete** |
| M2 — Market-data pipeline | Phase 2 — Market Data | **In progress** |
| M3 — Strategy research laboratory | Phase 3 — Strategy Research Laboratory | **In progress** |
| M4 — Backtesting | Phase 4 — Backtesting | **Planned** |
| M5 — Validation and ML research | Phase 5 — ML Research (optional, evidence-gated) | **Planned** |
| M6 — Paper execution | Phase 6 — Paper Execution Foundation | **Planned** |
| M7 — AI controls, forward paper testing, and operational reliability | Phases 7, 8, and 9 — AI Agent and Tool Framework; Live-Data Forward Paper Trading; Cloud Deployment and Reliability | **Planned** |
| M8 — Live-readiness assessment (not activation) | Phase 10 — Live Readiness Assessment | **Planned; default decision is NO-GO** |

Phase 2 includes the implemented Instrument Master, local historical CSV ingestion,
and Stage 4 exchange-session/data-quality engine. Its remaining gates are source-use
verification, maintained official calendar coverage, and fail-closed quality/staleness
integration in dependent workflows. The checked-in calendar configuration is intentionally
empty. No verified official holiday dataset, approved external feed, or provider adapter is
included.

Phase 3 now includes a versioned SMA-crossover research specification, experiment/run
records, Decimal-based deterministic signal generation, provenance fingerprints, and a
dashboard. It remains in progress: AI-assisted hypothesis drafting and isolated generated-
code evaluation are deferred, and runs remain blocked until Phase 2 supplies confirmed
calendar coverage and complete, fresh data. This work does not implement backtesting or
execution; those belong to Phases 4 and 6 onward.

## Planning estimates

This is a staged project expected to take multiple weeks or months. Estimates must be updated
as phases progress. Do not promise a live-trading date.
