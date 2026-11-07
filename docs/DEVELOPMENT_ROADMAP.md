# Development Roadmap

## M0 — Planning and repository foundation
Deliver: approved requirements, architecture, budget, risk rules, Git hygiene and CI test skeleton.
Exit gate: documentation reviewed; no secrets; tests/CI run.

## M1 — Local application foundation
Deliver: Python package, configuration validation, structured logging, SQLite schema, dashboard shell and unit-test framework.
Exit gate: clean install from documented instructions; CI passes.

## M2 — Market-data pipeline
Deliver: instrument master and local historical CSV ingestion with exact decimal prices, import provenance, row validation, deterministic duplicate handling, and dashboard visibility.
Exit gate for local ingestion: migration preserves prior data; timestamps and decimal serialization are deterministic; malformed and duplicate records are diagnosed; imports are reproducible from their checksums.
Remaining M2 gate: define an exchange calendar and stale-data policy, and verify permissions before selecting an external source. No provider adapter or external feed is introduced by the local CSV milestone.

## M3 — Strategy research laboratory
Deliver: strategy specification format, baseline strategies for research, experiment registry and reproducible runs.
Exit gate: strategy definitions versioned; no future-data access; experiment reproducible.

## M4 — Backtesting
Deliver: chronological simulator, portfolio accounting, transaction-cost model, slippage assumptions and performance metrics.
Exit gate: deterministic unit tests for orders, positions, cash, costs, corporate actions as applicable; no look-ahead tests.

## M5 — Validation and ML research
Deliver: chronological train/validation/test splits, walk-forward analysis, baseline comparisons, feature pipeline and simple ML baseline only if justified.
Exit gate: untouched test set protected; leakage checks; sensitivity analysis; report limitations.

## M6 — Paper trading
Deliver: market-data-driven simulated execution, order lifecycle, partial/unfilled simulation where possible, daily reconciliation and alerts.
Exit gate: no broker order calls exist in runtime path; paper ledger reconciles; stale data halts new simulated entries.

## M7 — Monitoring and review
Deliver: dashboard, daily reports, budget tracker, error alerts, experiment comparisons and strategy disable rules.
Exit gate: failure drills pass; all displayed metrics trace to records.

## M8 — Live-readiness assessment (not activation)
Deliver: independent review of strategy evidence, broker/API requirements, legal/regulatory requirements, security, hosting, static IP, operational controls and user approval process.
Exit gate: written go/no-go assessment. Default is NO-GO unless every requirement is independently satisfied.

## Planning estimates
This is a staged project expected to take multiple weeks or months. Estimates must be updated after M0/M1 based on actual progress. Do not promise a live-trading date.
