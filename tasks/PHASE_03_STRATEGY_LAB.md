# Phase 3 — Strategy Research Laboratory

## Goal
Create versioned, testable strategy hypotheses and deterministic signal generators. Do not assume that a strategy works or is profitable.

## Tasks
- [x] Define a machine-readable, versioned strategy specification.
- [x] Create an experiment registry and immutable run metadata.
- [x] Implement a transparent SMA-crossover signal baseline for research.
- [x] Define the supported instrument universe and historical membership limitations.
- [x] Add deterministic signal-generation tests.
- [ ] Add AI-assisted hypothesis drafting with citations, evidence, and explicit uncertainty.
- [ ] Ensure generated code is isolated, reviewed, and tested before execution.
- [x] Record full strategy, source-file, dataset, calendar/quality, and configuration provenance for successful runs; retain available partial provenance for blocked or failed runs.
- [x] Keep registered strategy versions immutable; changed rules require a new version and the documented manual review workflow.

AI-assisted hypothesis drafting and generated-code isolation are not implemented in this
milestone. Generated code is not executed. These items remain deferred to the separately
scoped AI/tool phase and require review and isolated testing before any future execution.

Existing experiment configurations created before schema version 6 have no provable
historical integrity hash. Their nullable hash is established on the first structurally valid
run attempt (even if the later data-quality gate blocks it) and marked `FIRST_RUN_BACKFILL`;
it detects later edits only. New configurations store the canonical
configuration hash separately from their JSON. A privileged user who changes both the
configuration and its stored hash can defeat this application-level check.

Run records retain exact SHA-256 values for the strategy, market-quality and historical
ingestion modules, calendar file/effective calendar, configuration, dataset, and source files. Git revision is
recorded only for a clean worktree; otherwise it is explicitly unavailable/uncommitted.
Implementation source hashes are computed from files on disk. A running Python process may
already have loaded code before a source file changes, so a disk hash is not guaranteed to
identify the exact in-memory code that executed. Git revision and source hashes improve
traceability but are not a complete executable-artifact attestation mechanism. These hashes
identify inputs but cannot restore files or data that are later lost.

SMA averages use a local precision of `max(28, integer_places + fractional_places +
digits_in_input_count + 12)` significant digits and explicit `ROUND_HALF_EVEN`. Non-terminating
averages use that finite deterministic Decimal result; no quantization to a tick or price
precision is performed.

## Acceptance criteria
- Each strategy has a rationale, rules, parameters, intended timeframe, and known limitations.
- Successful runs record code, data, and configuration versions; blocked or failed runs retain the provenance available at the point execution stopped.
- No strategy can access future bars or information unavailable at decision time.
- AI output cannot directly trigger execution or change risk limits.
- A tested strategy version is immutable for the duration of a registered experiment.

## Current implementation boundary

The Strategy Lab emits deterministic long-only entry/exit research signals from completed
historical candles. It does not calculate returns, simulate fills, size positions, set risk
limits, or submit orders. Runs fail closed unless Stage 4 reports complete confirmed
calendar coverage, 100% candle completeness, and fresh, valid, non-duplicate data. The
checked-in calendar is intentionally unconfigured, so the dashboard cannot currently
produce an unblocked run from its default configuration. Phase 2 remains in progress.
Abrupt termination may leave a run `RUNNING`; reconciliation is manual after verifying the
owning process has ended. Runs are never automatically failed based only on elapsed time.
