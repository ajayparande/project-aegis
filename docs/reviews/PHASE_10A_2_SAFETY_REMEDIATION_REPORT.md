# Phase 10A-2 Safety Remediation Report

**Verdict: CONDITIONALLY ACCEPT**

The corrective implementation closes the seven findings in the independent
review and adds focused regression coverage. Acceptance is limited to the
deterministic, research-only paper runtime. It is not approval for live trading,
and does not resolve the project's calendar-coverage or source-rights
limitations.

The independent report at
[`PHASE_10A_2_INDEPENDENT_REVIEW.md`](./PHASE_10A_2_INDEPENDENT_REVIEW.md)
was preserved unchanged.

## Repository baseline and worktree

- Branch: `main`
- HEAD: `7bdd4cfe2c217e0628872affa74d25e4671db950`
- Baseline was already dirty: 19 modified tracked paths and 14 untracked status
  entries were recorded before remediation; nothing was staged.
- At final inspection, Git reported 21 modified tracked paths and 14 untracked
  status entries (before adding this report). The worktree includes prior
  Phase 8/9/10A-2 work. These are not all attributed to this remediation.
- `git diff --stat` at final inspection, before the report was added: 21 tracked
  paths changed, 1,598 insertions and 113 deletions. Untracked files are not
  included in that statistic.
- The remediation changes were made within the existing runtime, paper-trading,
  database, dashboard and related test files. No unrelated worktree changes
  were reverted or cleaned.

## Findings and remediation evidence

Severity labels below match the independent review. The six defects listed
there as directly reproduced were reproduced against disposable databases
before fixes; the dashboard read-only defect was additionally established by
source inspection and write-rejection testing.

| ID / severity | Finding | Result and evidence |
|---|---|---|
| F1 / High | Integrity/reconciliation halts could be bypassed for SELLs; runtime status was not enforced consistently. | **PASS.** `PaperHaltClass` and `halt_blocks_order` distinguish risk halts from integrity/unclassified halts. A risk halt permits only a long-position-reducing SELL; unknown and integrity halts fail closed. Runtime readiness verifies checksums, runtime status, and reconciliation before processing activity. See [`paper_trading.py`](../../src/aegis/paper_trading.py#L37), [`forward_runtime.py`](../../src/aegis/forward_runtime.py#L591), and [`route_event`](../../src/aegis/forward_runtime.py#L1604). Tests: `test_risk_halt_allows_only_risk_reducing_sell_and_stays_halted`, `test_risk_halt_rejects_sell_larger_than_held_position`, `test_reconciliation_halt_blocks_buy_and_sell_without_repair`, `test_reconciliation_halt_prevents_automatic_exit_submission`, and `test_runtime_state_checksum_fails_closed`. |
| F2 / High | An accepted execution snapshot could be reused across orders and was not fully bound to a proposal/runtime event. | **PASS.** Snapshot identity and content hash are recorded in a unique, transactional use table; runtime orders are bound to their pending event and proposal at the Phase 8 execution boundary. Tests: `test_filled_execution_snapshot_is_single_use_across_distinct_orders`, `test_execution_snapshot_must_match_an_independently_accepted_quote`, and duplicate routing in `test_scheduler_routing_is_idempotent_and_stop_or_target_exit_occurs_once`. See [`ExecutionSnapshot` and `submit_paper_order`](../../src/aegis/paper_trading.py#L166), [`paper_execution_snapshot_uses` migration](../../src/aegis/database.py#L466), and [`route_proposal`](../../src/aegis/forward_paper_trading.py#L779). |
| F3 / High | Partial SELLs could disable active stop/target protection while a long position remained. | **PASS.** Trade controls remain active while quantity remains; automatic exits are persisted, de-duplicated and retried after rejection/restart. Tests: `test_partial_direct_sell_keeps_remaining_position_protected`, `test_stop_loss_exit_triggers_at_most_once`, and `test_rejected_stop_or_target_exit_recovers_after_restart`. See [`route_event`](../../src/aegis/forward_runtime.py#L1604) and [`_monitor_position`](../../src/aegis/forward_runtime.py#L1836). |
| F4 / Medium | Evidence could accept a wrong provider origin or future import-completion time; runtime import provenance was not sufficiently exact. | **PASS.** Evidence is bound to the exact runtime import, instrument and interval; provider metadata, user-declared source-use permission, completion time, and as-of constraints are checked. Runtime import completion is advanced as candles persist. Tests: `test_evidence_stays_bound_to_the_exact_runtime_import`, `test_runtime_evidence_rejects_unverified_or_prohibited_source`, `test_runtime_evidence_rejects_wrong_provider_origin`, and `test_runtime_evidence_rejects_future_import_completion`. See [`_assert_verified_permitted_import`](../../src/aegis/forward_paper_trading.py#L331) and runtime provenance checks in [`forward_runtime.py`](../../src/aegis/forward_runtime.py#L1221). |
| F5 / Medium | BUYs had no proposal-to-execution price-deviation bound. | **PASS.** The maximum deviation is an explicit finite non-negative `Decimal`; missing configuration rejects BUY routing, and an excessive difference is rejected before order creation. Proposal expiry is evaluated against an explicit, timezone-aware evaluation time. Tests: `test_buy_deviation_limit_fails_closed_before_order_creation` and `test_expired_proposal_is_rejected_before_order_creation`. See [`ForwardPaperTradingEngine`](../../src/aegis/forward_paper_trading.py#L499), expiry validation around [`validate_decision_proposal`](../../src/aegis/forward_paper_trading.py#L689), and deviation checks in [`route_proposal`](../../src/aegis/forward_paper_trading.py#L890). |
| F6 / Medium | Dashboard startup/status and query paths could use writable database connections. | **PASS for dashboard reads and runtime/account controls.** Startup no longer initializes or migrates the database; status and dashboard query helpers use SQLite `mode=ro` and `PRAGMA query_only=ON`. Tests: `test_read_only_dashboard_connection_rejects_database_writes`, `test_dashboard_does_not_modify_development_database_or_log`, `test_dashboard_shows_simulated_reports_without_execution_controls`, and `test_dashboard_shows_runtime_status_without_controls`. See [`get_readonly_connection`](../../src/aegis/database.py#L512), [`get_database_status`](../../src/aegis/database.py#L584), and dashboard startup in [`app.py`](../../app.py#L47). The dashboard retains intended research workflows that write through their business services; it exposes no runtime order-submission control. |
| F7 / Medium | Runtime creation could accept activity without first reconciling an existing account. | **PASS.** Creation reconciles the account before persisting a runtime; readiness repeats reconciliation before quote processing/routing. Tests: `test_runtime_creation_rejects_unreconciled_account`, `test_restart_recovers_state_and_reconciliation_detects_corruption`, and `test_runtime_state_checksum_fails_closed`. See [`ForwardRuntimeService.create`](../../src/aegis/forward_runtime.py#L234) and [`_ensure_runtime_ready`](../../src/aegis/forward_runtime.py#L591). |

### Additional safety checks

- Feed health: BUYs are rejected unless the instrument feed is healthy; the
  parameterized `test_buy_requires_valid_healthy_instrument_feed` now includes
  `STARTING`, `DEGRADED`, `STALE`, `DISCONNECTED`, unknown and missing states.
  `test_healthy_feed_recovery_allows_a_new_buy` verifies fresh accepted evidence
  is needed before resuming entries. Phase 8 risk controls still permit only
  valid risk-reducing SELLs during a classified risk halt.
- Exchange sessions and candle integrity: `test_quotes_must_be_inside_the_configured_exchange_session`
  covers regular open, pre-open, holiday, weekday closure, special-session
  boundaries and post-close; `test_streaming_aggregates_only_completed_1m_5m_and_15m_candles`
  and related streaming tests cover completed-candle handling.
- Valuation: accepted quote marks are stored separately from cash/ledger
  accounting, carry explicit freshness, and survive restart.
  `test_quote_marks_change_without_mutating_ledger_and_survive_restart` and
  `test_scheduler_routing_is_idempotent_and_stop_or_target_exit_occurs_once`
  check marks, reconciliation and equity arithmetic.
- Phase 8 remains the authoritative order/risk boundary. The runtime does not
  bypass Phase 8 risk limits; accounting, fees, cash, positions, ledger
  reconciliation, and transactional rollback remain covered by paper-trading
  tests such as `test_buy_sell_accounting_idempotency_and_restart_reconciliation`
  and `test_failed_fill_transaction_rolls_back_order_fill_cash_and_position`.
- Scheduler recovery: `test_restart_recovers_missing_scheduler_event_exactly_once`
  injects failure after candle persistence and before event creation, then
  verifies one recovered pending event and idempotent subsequent recovery.
  Stop/target exit recovery is covered by
  `test_rejected_stop_or_target_exit_recovers_after_restart`.
- The runtime uses deterministic synthetic imports with explicit provider
  provenance and exact import identity. CSV/historical imports are not silently
  substituted into that runtime. Permission status is checked, not inferred.
- Dashboard status/report paths are read-only and do not expose order
  submission or runtime mutation controls. Existing research data-entry
  workflows remain intentionally available.
- No live broker/feed connection or AI trading authorization path was added.
  The application remains `RESEARCH_ONLY`; AI functionality remains a research
  feature, not trade authorization or execution.

## Migration audit

Schema version is now 10. Migration 9 adds explicit account halt classification
and the unique execution-snapshot-use table. The v10 migration preserves
existing halted accounts as `UNCLASSIFIED` (fail-closed) and leaves unhalted
accounts at `NONE`. See [`MIGRATIONS[9]`](../../src/aegis/database.py#L457).

Isolated temporary-database tests verify:

- `test_version_10_migration_preserves_phase_8_records_and_is_idempotent`:
  initializes a v8 database through the current schema, preserving account,
  order, fill, ledger and daily-state records; a second initialization leaves
  schema and records unchanged.
- `test_version_9_migration_failure_rolls_back_runtime_tables_and_preserves_phase_8`:
  injected metadata failure rolls back the v8-to-v9 migration.
- `test_version_10_migration_failure_rolls_back_only_new_schema_and_preserves_phase_8`:
  injected metadata failure rolls back v10 DDL/metadata while preserving v9
  state and Phase 8 records.
- `test_fresh_database_initialization` and the full database suite verify
  initialization reaches the latest schema. (No migration was run against the
  normal application database.)

The migration remains inside the existing `BEGIN IMMEDIATE` transaction and
rollback path in [`initialize_database`](../../src/aegis/database.py#L535).

## Validation results

Commands were run using the repository virtual environment on Windows:

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q tests\test_forward_runtime.py tests\test_paper_trading.py tests\test_database.py tests\test_dashboard.py tests\integration\test_full_synthetic_streaming_workflow.py tests\integration\test_full_synthetic_trading_workflow.py tests\test_ai_agent_security_regressions.py` | **135 passed**. |
| `.\.venv\Scripts\python.exe -m pytest -q tests\test_forward_runtime.py` | **39 passed**, including the final runtime/feed/provenance/expiry/reconciliation additions. |
| `.\.venv\Scripts\python.exe -m pytest -v` | **551 passed in 96.04s**. |
| `.\.venv\Scripts\python.exe -m ruff check .` | **Passed** after resolving the reported issues. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | **Passed**; 78 files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy src` | **Passed**; 21 source files, no issues. |
| `git diff --check` | **Passed** (Git emitted only existing LF-to-CRLF working-copy warnings). |

The first run of the new future-completion test correctly rejected the input but
its expected-message regex was too broad; the assertion was corrected to the
actual explicit rejection message. The final focused and full suites then
passed.

## Remaining limitations and risk

- BUY routing requires an explicit configured `Decimal` deviation limit; there
  is intentionally no implicit financial threshold.
- Calendar configuration must be locally verified before claiming confirmed
  completeness. The checked-in configuration does not establish official
  exchange holiday coverage.
- `VERIFIED_PERMITTED` remains the user's audited source-use declaration. The
  application validates and records it; it does not determine legal rights.
- This remediation and its tests use deterministic synthetic/paper workflows.
  They establish neither live readiness nor predictive performance,
  profitability, or guaranteed returns.
- Opening SQLite in read-only mode can cause SQLite to create WAL/SHM sidecars
  depending on journal state; the dashboard test asserts the isolated database
  content is unchanged and separately verifies that writes through its
  read-only connection fail.

## Change and safety confirmation

Source and test files were changed to implement the remediation; the independent
review and pre-existing corrective implementation report were not overwritten.
No files were staged or committed, no changes were pushed, and no normal
application database was accessed, migrated, or modified. Existing unrelated
worktree changes and untracked files were preserved.
