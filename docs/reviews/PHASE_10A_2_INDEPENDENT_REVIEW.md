# Phase 10A-2 Independent Corrective Review

**Review date:** 2026-11-15
**Verdict:** **REJECT**

## 1. Executive verdict

The corrective implementation is **not acceptable**, even for the stated synthetic,
research-only scope. The requested tests and all repository checks pass, and the v8→v9
migration behaved safely in isolated populated-database tests. However, independent
reproductions found three high-severity safety defects:

1. A SELL filled after a runtime configuration checksum failure had halted both the
   runtime and its paper account.
2. The same independently accepted execution quote/snapshot was reused for two distinct
   proposals and filled both BUYs.
3. A successful direct SELL reducing a protected position deactivated its stop/target
   controls while a position remained open.

Other material gaps include missing proposal-to-snapshot binding, no price-deviation
limit, incomplete evidence provenance checks, writable dashboard database paths, and
runtime creation against an unreconciled account. These findings outweigh the passing
synthetic test suite. **Do not treat this implementation as phase-accepted or live-ready.**

## 2. Scope and repository baseline

This was a read-only review of the implementation, tests, and isolated reproductions.
The only requested deliverable created by this review is this report.

At baseline:

- Branch: `main`
- HEAD: `7bdd4cfe2c217e0628872affa74d25e4671db950`
- Staged paths: none
- Tracked modified paths: 19
- Untracked status entries: 14 (including directories containing additional untracked files)
- `git diff --stat`: 19 files changed, 1,466 insertions(+), 87 deletions(-)
- `git diff --cached --stat`: empty

The tracked modified paths at baseline were:

```text
README.md
app.py
docs/DEVELOPMENT_ROADMAP.md
docs/SYSTEM_ARCHITECTURE.md
docs/VALIDATION_AND_ACCEPTANCE.md
src/aegis/__init__.py
src/aegis/backtesting.py
src/aegis/database.py
src/aegis/market_quality.py
src/aegis/strategy_lab.py
tasks/PHASE_02_MARKET_DATA.md
tasks/PHASE_04_BACKTESTING.md
tasks/PHASE_08_PAPER_TRADING.md
tasks/PHASE_09_AI_AGENT_AND_TOOL_FRAMEWORK.md
tests/test_backtesting.py
tests/test_dashboard.py
tests/test_database.py
tests/test_market_data.py
tests/test_strategy_lab.py
```

The untracked status entries at baseline were:

```text
docs/reviews/PHASE_08_IMPLEMENTATION_REPORT.md
docs/reviews/PHASE_09_IMPLEMENTATION_REPORT.md
docs/reviews/PHASE_09_INDEPENDENT_REVIEW_AND_COMPLETION_REPORT.md
docs/reviews/PHASE_09_SECURITY_INSPECTION.md
docs/reviews/PHASE_10A_2_CORRECTIVE_IMPLEMENTATION_REPORT.md
src/aegis/ai_agent/
src/aegis/forward_paper_trading.py
src/aegis/forward_runtime.py
src/aegis/paper_trading.py
tests/integration/
tests/test_ai_agent.py
tests/test_ai_agent_security_regressions.py
tests/test_forward_runtime.py
tests/test_paper_trading.py
```

These are pre-existing baseline changes. The aggregate diff and worktree contents cannot
be attributed solely to Phase 10A-2. The requested review report did not exist in the
baseline inventory.

The existing [corrective implementation report](./PHASE_10A_2_CORRECTIVE_IMPLEMENTATION_REPORT.md)
claims acceptance and 540 passing tests. The suite count was independently reproduced;
the safety conclusions were not. Its Phase 8 migration regression seeds an account and
daily state but not existing orders, fills, and ledger entries. An additional isolated
test below covered those financial records.

## 3. Seven previously confirmed defects

### 3.1 Feed health — **PASS**

New BUYs require the selected instrument’s persisted status to be exactly `HEALTHY`,
require a latest accepted quote and require the execution snapshot to match that quote.
The freshness calculation is also performed against the decision/snapshot time. A
subsequent valid quote resets that instrument’s feed state to `HEALTHY`; peer
instruments are evaluated independently. See
[ForwardRuntimeService._empty_state](../../src/aegis/forward_runtime.py#L488),
[ForwardRuntimeService._apply_quote](../../src/aegis/forward_runtime.py#L698),
[ForwardRuntimeService.monitor_feed_health](../../src/aegis/forward_runtime.py#L976),
and [ForwardRuntimeService._buy_feed_rejection](../../src/aegis/forward_runtime.py#L1556).

Evidence: `test_buy_requires_valid_healthy_instrument_feed` covers `DEGRADED`, `STALE`,
`DISCONNECTED`, unknown and missing status; `test_healthy_feed_recovery_allows_a_new_buy`
checks recovery; `test_expired_buy_quote_and_stale_peer_instrument_are_isolated` checks
freshness and per-instrument isolation. An additional isolated probe set the feed to
`STARTING`; route result was `REJECTED` with `BUY rejected: instrument feed is STARTING.`
Valid risk-reducing SELLs remain possible through Phase 8 under the permitted conditions
(see 3.2).

### 3.2 Halt-safe SELL — **FAIL**

Ordinary-risk-halt SELLs can reduce a valid long position and do not clear the account
halt. Phase 8 rejects oversells and checks reconciliation before executing an order.
However, the runtime’s integrity-halt check is a substring list for only
“reconciliation”, “integrity” and “corrupt” at
[_is_integrity_halt](../../src/aegis/forward_runtime.py#L194). A checksum failure sets
the account halt reason to `Runtime config checksum mismatch.` or
`Runtime state checksum mismatch.` at
[ForwardRuntimeService._halt](../../src/aegis/forward_runtime.py#L504). That reason is
not classified as an integrity halt. `route_event` does not independently reject a
HALTED runtime and allows a SELL when this check returns false:
[ForwardRuntimeService.route_event](../../src/aegis/forward_runtime.py#L1451).
Phase 8 also blocks a halted account’s SELL only when the reason contains
“reconciliation” ([submit_paper_order](../../src/aegis/paper_trading.py#L235)).

**Reproduction:** In a temporary database, create a long position and a pending SELL
event, corrupt `config_sha256`, and call `ForwardRuntimeService.open`. Startup correctly
raised `Persisted runtime configuration is corrupted.` and persisted
`runtime.status=HALTED` plus account halt reason `Runtime config checksum mismatch.`.
Calling `route_event` on the existing service with a valid SELL snapshot then returned
`FILLED`. The runtime remained `HALTED`. This violates the explicit integrity-halt block.

The ordinary-risk path itself passed: `test_risk_halt_allows_only_a_valid_direct_long_reduction`
and `test_risk_halt_rejects_sell_larger_than_held_position`; reconciliation protection is
covered by `test_reconciliation_halt_blocks_buy_and_sell_without_repair` and
`test_reconciliation_halt_prevents_automatic_exit_submission`.

### 3.3 Stop/target retry and protection — **PARTIAL**

The automatic exit retry/restart path is recoverable: a rejected or interrupted exit
keeps controls active, `_recover_exit_controls` clears a stale claim when no successful
order exists, and a later quote can retry. Successful automatic exits are not duplicated.
Evidence: `test_rejected_stop_or_target_exit_recovers_after_restart`,
`test_stop_loss_exit_triggers_at_most_once`, and
`test_scheduler_routing_is_idempotent_and_stop_or_target_exit_occurs_once`. The relevant
paths are [ForwardRuntimeService._recover_exit_controls](../../src/aegis/forward_runtime.py#L1207)
and [ForwardRuntimeService._monitor_position](../../src/aegis/forward_runtime.py#L1654).
The paper executor is all-or-none; it does not support partial fills.

But a successful **partial position reduction** silently disables protection:
`route_event` marks the trade control inactive after any filled direct SELL, without
checking whether the position is flat
([forward_runtime.py](../../src/aegis/forward_runtime.py#L1550)). Phase 8 correctly leaves
the unsold position in place when a sell quantity is smaller than the holding
([_apply_position_fill](../../src/aegis/paper_trading.py#L939)).

**Reproduction:** Buy 2 units with stop/target controls, then fill a direct SELL for 1
unit. The result was `FILLED`; the persisted position quantity was `1`, while the control
was `active=0`. This is a material protection gap not covered by the retry tests.

### 3.4 Exchange sessions and candle contamination — **PASS**

Accepted quotes pass `_session_rejection` before `_apply_quote`; rejected quotes cannot
update the active candle or be persisted as candle data. The exclusive close boundary,
holiday, configured special session, regular session, pre-open, closed weekday, and
uncovered-date cases are covered by
[`test_quotes_must_be_inside_the_configured_exchange_session`](../../tests/test_forward_runtime.py#L324).
The test also confirms the close-boundary rejection leaves zero completed candles.
Session enforcement is in
[ForwardRuntimeService._session_rejection](../../src/aegis/forward_runtime.py#L677)
and quote acceptance in [ForwardRuntimeService.process_quote](../../src/aegis/forward_runtime.py#L539).

This establishes behavior only for isolated synthetic calendars. The checked-in calendar
does not provide confirmed official NSE/BSE coverage; no real-market completeness or
session readiness is established.

### 3.5 Portfolio valuation and marks — **PASS**

Accepted quotes update `paper_marks` separately from fills and ledger entries. Mark
updates do not create cash/ledger transactions; `portfolio_valuation` reports last-trade
mark price, age, freshness (`FRESH`, `STALE`, `MISSING`, or `INVALID`) and reconciliation.
The report uses a persisted account report and fails closed if reconciliation is not
clean. See [record_paper_mark](../../src/aegis/paper_trading.py#L519) and
[ForwardRuntimeService.portfolio_valuation](../../src/aegis/forward_runtime.py#L1817).

Evidence: `test_quote_marks_change_without_mutating_ledger_and_survive_restart` and the
fresh integration workflow verify marks, ledger independence, restart persistence,
valuation equality, and reconciliation. These marks are last accepted trade prices, not
executable bids or liquidation values.

### 3.6 Scheduler crash recovery — **PASS**

Completed candles and runtime state commit before scheduler-event creation. On restart,
`open` reconciles the account and invokes event recovery; stable event IDs and
`INSERT OR IGNORE` make recovery idempotent. Recovery creates missing events without
routing proposals. See [ForwardRuntimeService.open](../../src/aegis/forward_runtime.py#L394),
[recover_scheduler_events](../../src/aegis/forward_runtime.py#L1238), and
[schedule_event](../../src/aegis/forward_runtime.py#L1364).

The isolated integration test deletes an event after its candle is persisted and reopens
the runtime, reproducing the durable state at the crash boundary. The event is restored
once and replay does not duplicate proposals or orders:
[`test_fresh_synthetic_stream_reaches_paper_ledger_and_dashboard`](../../tests/integration/test_full_synthetic_streaming_workflow.py#L66).
This is a state-based crash simulation, not an operating-system process-kill test.

### 3.7 Exact evidence provenance — **PARTIAL**

The runtime binds evidence to the mapped import, checks instrument, interval,
`COMPLETED` status, `VERIFIED_PERMITTED`, fingerprint, runtime ID, exact candle/import
identity, and candle completion relative to `as_of`. `analyze_dataset` can filter by
exact `source_import_id`. See
[ForwardRuntimeService.prepare_evidence](../../src/aegis/forward_runtime.py#L1098),
[_assert_verified_permitted_import](../../src/aegis/forward_paper_trading.py#L316), and
[analyze_dataset](../../src/aegis/market_quality.py#L564).

Mismatched import and instrument attempts were rejected. A mapped interval mismatch is
rejected by the runtime import identity checks; unsupported intervals are rejected at
the boundary. Stale evidence is returned with `staleness_status=STALE` rather than
rejected by `prepare_evidence`, but proposal validation rejects it with the incomplete
quality gate. Evidence is therefore safe to route through the engine’s quality
validation, but callers must not treat a returned evidence object as already approved.

The source’s **data origin and import completion timestamp are not actually verified**.
Runtime creation inserts synthetic imports as `COMPLETED` and stamps `completed_at`
immediately, before streamed candles are added
([ForwardRuntimeService.create](../../src/aegis/forward_runtime.py#L235), insert at
[forward_runtime.py](../../src/aegis/forward_runtime.py#L339)). Later candle persistence
increments accepted/received/inserted counts but does not update that completion
timestamp ([forward_runtime.py](../../src/aegis/forward_runtime.py#L877)). Evidence
checks only `metadata.runtime_id`; it does not verify the declared provider or reconcile
`completed_at` with the candle/import contents ([forward_runtime.py](../../src/aegis/forward_runtime.py#L1139)).

**Reproduction:** In an isolated runtime import, change `completed_at` to a future date
and change metadata provider to `NOT_SYNTHETIC` while retaining the matching
`runtime_id`. `prepare_evidence` still returned `FRESH`/`COMPLETE` evidence referencing
that import. The existing tests verify runtime ID, permission status and exact import
mapping, but not provider origin or completion timestamp.

## 4. Additional safety requirements

### 4.1 Execution snapshots — **FAIL**

The runtime checks that a snapshot matches an independently accepted, latest quote for
the requested instrument, and checks its timestamp, `as_of`, price, quantity, interval
and import identity in
[_snapshot_validation_error](../../src/aegis/forward_runtime.py#L1611). Phase 8 applies
freshness and quality gates in
[_validate_execution_evidence](../../src/aegis/paper_trading.py#L800).

There is no persisted snapshot-consumption identity, no single-use enforcement, and no
binding between a snapshot and the proposal/event. `_snapshot_validation_error` receives
only the instrument and snapshot; after that, `route_event` separately validates the
proposal ID but does not associate the quote with that event or proposal.

**Reproduction:** Two pending events with two distinct proposal IDs were routed using
the same accepted quote and the same snapshot with `available_quantity=1`. Both BUYs
returned `FILLED`, creating two fills at one execution timestamp. The first proposal’s
`entry_price` was also changed to `5000` while the execution quote was about `100`; it
still filled. The runtime snapshot check is stronger than direct calls to the public
Phase 8 `submit_paper_order`, which accepts caller-supplied snapshot objects and does not
prove that a snapshot was independently accepted by the runtime.

### 4.2 Expiry, price deviation, and Phase 8 risk

Expired proposals with an explicit `expires_at` are rejected by
[validate_decision_proposal](../../src/aegis/forward_paper_trading.py#L587); an isolated
expired-proposal validation returned `valid=False` with
`Proposal has expired and cannot be routed.` Expiry is optional, however.

There is **no proposal-entry-price versus execution-price deviation limit** in the
proposal validator, router, or Phase 8 risk path. A proposal with `entry_price=5000`
filled against the roughly `100` execution quote in the reproduction above. Phase 8 does
enforce order value, position quantity, exposure, cash, daily-loss entry gates, long-only
SELL limits, fees and execution costs using the actual snapshot price
([_risk_rejection](../../src/aegis/paper_trading.py#L873)); those controls do not
substitute for a proposal/execution deviation bound.

### 4.3 Accounting, stop controls, and source separation

The BUY/SELL accounting path is transactionally recorded as orders, fills, fees, ledger
entries and position projections. Reconciliation checks those relationships; focused
tests cover accounting, fee assumptions, oversells, tampering, failed fills and restart
reconciliation. Stop/target automated exits are idempotent for successful exits, subject
to the partial-reduction defect in 3.3.

Runtime market evidence filters completed candles by the selected import ID. A collision
with an existing instrument/interval/timestamp is rejected rather than overwritten.
Thus accidental mixing was not reproduced in the runtime’s exact-source evidence path.
This does not cure the missing origin/completion checks in 3.7.

### 4.4 Dashboard read-only and runtime mutation — **PARTIAL**

The synthetic runtime summary and paper-account report use SQLite read-only helpers.
`get_readonly_connection` itself rejects an INSERT in
`test_read_only_dashboard_connection_rejects_database_writes`.

The dashboard as a whole is **not** read-only: startup calls `initialize_database` at
[app.py](../../app.py#L61), which may create or migrate the database; `get_database_status`
uses the writable `get_connection` at [database.py](../../src/aegis/database.py#L555);
and dashboard forms invoke CSV import, experiment registration and strategy execution at
[app.py](../../app.py#L255), [app.py](../../app.py#L578), and [app.py](../../app.py#L639).
Read-only report sections do not make those other connections read-only. The named test
verifies only the helper, not that every dashboard database connection is read-only.

The dashboard **does not expose an order-submission control or mutate forward runtime
state** in the reviewed app. Runtime data is displayed through
`list_forward_runtime_summaries`; the paper report is labeled read-only. Research data
and strategy records can still be changed through their separate dashboard forms.

### 4.5 Startup reconciliation — **PARTIAL**

`ForwardRuntimeService.open` reconciles the paper account before returning the restored
service and before scheduler/exit recovery, which is the correct restart order.
`ForwardRuntimeService.create`, however, only verifies that the account exists; it does
not reconcile an existing account before persisting a new `RUNNING` runtime.

**Reproduction:** In an isolated database, fill a BUY, tamper with the persisted position
projection, then call `create` for a second runtime on that account. The new runtime
accepted a quote while the account remained `halted=0` and the persisted position still
failed reconciliation. Order submission later has a Phase 8 reconciliation guard, but
new runtime activity was accepted first.

### 4.6 No live/broker order path

No live feed, broker connection, credentials, or real broker-order route was introduced
in the reviewed runtime/paper execution path. The app’s optional AI feature is a bounded
research assistant, not a trading brain or order authorization path. No external
provider, live feed or broker API was called during this review. Nothing here establishes
live readiness, predictive performance, or profitability.

## 5. Migration audit: schema v8→v9

The v8→v9 migration adds forward-runtime tables in `MIGRATIONS[8]`
([database.py](../../src/aegis/database.py#L365)); `initialize_database` applies
migrations in a transaction ([database.py](../../src/aegis/database.py#L495)).
The existing Phase 8 tables are not altered by the v9 migration.

All migration reproductions used disposable temporary SQLite databases, never the
application database:

| Case | Result |
|---|---|
| Populated v8→v9 with account, FILLED order, fill, and ledger entry | **PASS** — exact rows matched before/after; schema advanced to 9 |
| Repeat initialization | **PASS** — records unchanged; no duplicate version-9 metadata |
| Injected failure at version-9 metadata insert | **PASS** — exception raised; transaction rolled back, max schema version remained 8, no `forward_*` tables remained, Phase 8 rows unchanged |
| Fresh initialization and repeat | **PASS** — schema version 9, nine version records, six runtime tables |

The repository tests `test_version_9_migration_preserves_phase_8_records_and_is_idempotent`
and `test_version_9_migration_failure_rolls_back_runtime_tables_and_preserves_phase_8`
passed. The extra populated reproduction fills the gap in the current preservation test,
which does not seed orders, fills or ledger entries.

## 6. Confirmed defects and severity

Severity reflects the safety contract and affected scope; execution remains simulated
only. No finding is a claim of live-market impact.

| ID | Severity | Confirmed defect |
|---|---|---|
| F1 | **High** | Runtime/account checksum-integrity halt can be bypassed by routing a SELL; runtime status is not checked in `route_event`, and checksum reasons are not classified as integrity halts. |
| F2 | **High** | Execution snapshot is neither proposal-bound nor single-use; one accepted quote/capacity was consumed for two filled BUYs. |
| F3 | **High** | Any filled direct SELL deactivates stop/target controls even when a long position remains. |
| F4 | **Medium** | Evidence accepts a wrong provider origin and ignores a future import completion timestamp; synthetic imports are marked completed before streaming ends. |
| F5 | **Medium** | No proposal-to-execution price-deviation limit is implemented; a 5000 entry proposal filled against a roughly 100 quote. |
| F6 | **Medium** | Dashboard-wide SQLite read-only requirement is not met; startup, status checks and research forms use writable database paths. |
| F7 | **Medium** | New runtime creation accepts quotes on an unreconciled existing account; reconciliation occurs on `open`, but not `create`. |

## 7. Exact validation results

Commands used the project `.venv` and Python 3.11.9:

| Command | Result |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -v tests\test_forward_runtime.py tests\test_paper_trading.py tests\test_database.py tests\test_dashboard.py tests\integration\test_full_synthetic_streaming_workflow.py` | **104 passed in 37.64s** |
| `.\.venv\Scripts\python.exe -m pytest -v` | **540 passed in 84.14s** |
| `.\.venv\Scripts\python.exe -m ruff check .` | **Passed** |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | **Passed; 76 files already formatted** |
| `.\.venv\Scripts\python.exe -m mypy src` | **Passed; no issues in 21 source files** (mypy notes that untyped function bodies are not checked by default) |
| `git diff --check` | **Passed; no whitespace errors** |

Independent probes were run as Python submitted via stdin against unique temporary
databases. They were not added to project tests and left no test files behind. Results:

```text
SELL_AFTER_INTEGRITY_HALT: FILLED
REUSE_SNAPSHOT: two distinct BUY proposals both FILLED
REUSE_ORDERS: 2 fills, 1 distinct execution timestamp
PARTIAL_DIRECT_SELL: FILLED; position quantity 1; control active 0
EVIDENCE_WITH_FUTURE_COMPLETION_AND_WRONG_ORIGIN: returned FRESH / COMPLETE
STALE_EVIDENCE_RETURNED: STALE / INCOMPLETE; proposal validation rejected it
EXPIRED_PROPOSAL_VALIDATION: rejected
MISMATCHED_INSTRUMENT: rejected
STARTING_FEED_BUY: rejected
CREATE_RUNTIME_ON_UNRECONCILED_ACCOUNT: quote accepted; account remained unhalted
POPULATED_V8_TO_V9: Phase 8 rows identical
ROLLBACK_V8_PRESERVED: version 8; no forward tables; Phase 8 rows unchanged
FRESH_SCHEMA: version 9; nine schema version records
```

## 8. Remaining operational limitations

- The checked-in exchange calendar does not establish confirmed official NSE/BSE session
  and holiday coverage.
- Synthetic quote simulations cannot establish real-feed quality, execution behavior,
  order-book capacity, predictive performance, profitability or live readiness.
- Market-data permissions remain user-declared; the application does not adjudicate
  source rights.
- The runtime remains `RESEARCH_ONLY`; no live order or broker path is approved.
- Execution snapshot consumption, price-deviation enforcement, data-origin verification,
  direct partial-reduction protection handling, integrity-halt SELL blocking, dashboard
  read-only boundaries, and reconciliation-before-create need correction and focused
  regression coverage before acceptance.

## 9. Change and data-preservation confirmation

No source code or tracked project test was changed. No repository files were staged,
committed, pushed, reset, stashed, checked out or cleaned. Temporary directories created
for the isolated reproductions were removed afterward. The normal application database
was not opened through SQLite, queried, migrated, or modified. Disclosure: the required
dashboard test `test_dashboard_does_not_modify_development_database_or_log` checked the
default development database/WAL/SHM and log paths and, for any path that existed,
hashed its bytes before and after the test. This was read-only file hashing, but it is
technically a read access to any existing database files. The only new repository file
is this requested review report.
