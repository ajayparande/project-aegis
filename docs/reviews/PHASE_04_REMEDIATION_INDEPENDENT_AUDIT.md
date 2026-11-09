# Phase 4 Remediation Independent Verification Audit

**Review date:** 2026-11-09\
**Review type:** Read-only independent verification of remediations F-01 through F-05\
**Repository:** `ajayparande/project-aegis`\
**Branch / HEAD:** `main` / `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`\
**`origin/main`:** `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`\
**Overall verdict:** **REJECT — FIXES REQUIRED**

## Executive summary

The original F-01 through F-04 findings are resolved for the currently supported backtesting
entry point and strategy. F-05 is only partially resolved: the added tests cover many of the
requested regressions, but an independent public-API probe with a constrained caller Decimal
context raised `decimal.InvalidOperation` in the market-quality completeness calculation
before the backtest could complete. Therefore Decimal-context independence is not established
end to end.

One additional low-severity data-visibility issue was found: the separate backtest listing
silently skips malformed registered backtest rows instead of surfacing their corruption.

**Code-level readiness:** the engine is suitable for isolated synthetic-data research under
the default Decimal context, subject to the F-05 issue and documented limitations below.
**Actual historical-data readiness:** not established. The checked-in exchange calendar is
empty; the normal local database is schema version 2 and contains no CSV OHLCV imports.
No actual source-use declarations or confirmed calendar coverage were found. No migration was
run against that database.

## Scope and method

The audit inspected the original audit report, project instructions, architecture, roadmap,
validation criteria, Phase 4 task, relevant `src/aegis` implementation, dashboard wiring,
configuration, tests, migrations, and exchange-calendar configuration. It reviewed the current
Git state, ran the requested validation commands, and used isolated temporary databases for
the public Decimal-context probe. The normal database was queried read-only for schema/import
counts and its hash was checked before and after the audit validations.

No existing repository file was modified, removed, reformatted, committed, or pushed. This
report is the only file created by this audit. The original
`docs/reviews/PHASE_04_INDEPENDENT_AUDIT.md` was not modified.

## Finding summary

| ID | Severity | Finding | Status |
|---|---|---|---|
| F-01 | Medium | Insufficient strategy history could complete as a successful no-trade run | **RESOLVED** |
| F-02 | Medium | Backtest registration broke the Strategy Lab listing | **RESOLVED** |
| F-03 | Medium | The fill tick size and instrument snapshot were absent from run provenance | **RESOLVED** |
| F-04 | Medium | Dataset source-use permission was neither recorded nor enforced | **RESOLVED** |
| F-05 | Medium | Causal and reproducibility regression coverage was missing | **PARTIALLY RESOLVED** |
| F-06 | Low | Malformed registered backtests are silently omitted from the backtest listing | **NEW — unresolved** |

No Critical or High-severity finding was identified.

## F-01 — Insufficient strategy history

**Status: RESOLVED for the currently supported SMA-crossover strategy.**

`StrategySpec.minimum_history_candles` in `src/aegis/strategy_lab.py` derives the requirement
from the registered slow window (`slow_window + 1`). `run_backtest` in
`src/aegis/backtesting.py` loads and validates the expected completed candles, then applies
that specification-provided threshold before signal generation and simulation. The blocked
path records an explicit strategy name/version and required/available candle counts; it does
not produce a `COMPLETED` result. Tests exercise the public API:

- `test_backtest_blocks_when_strategy_history_is_below_required_minimum`
- `test_backtest_accepts_exact_and_above_minimum_strategy_history`
- `test_history_requirement_comes_from_registered_strategy_configuration`
- `test_sufficient_history_with_no_signals_is_a_valid_no_trade_run`

The no-signal test confirms that sufficient history can complete with no orders or trades.
Eligible candles are constrained to completed expected timestamps and checked against the
calendar-derived dataset before the history comparison.

**Scope limitation:** `StrategySpec` currently accepts only `sma_crossover`; its minimum
history implementation is consequently SMA-specific. The backtest gate consumes a strategy
property rather than embedding a slow-window constant, but a second, non-SMA strategy
implementation has not been demonstrated. This does not leave the original defect open for
the only supported strategy.

## F-02 — Strategy Lab integration

**Status: RESOLVED for Strategy Lab registration/listing integration.**

Backtests are inserted as a typed `BACKTEST` configuration with status
`BACKTEST_REGISTERED`. They preserve the original `strategy_id` and `hypothesis_id`
relationships. `list_strategy_experiments` excludes the registration status and explicitly
skips the backtest configuration type, while malformed Strategy Lab configurations still
raise `StrategyLabError` rather than being swallowed. The dashboard calls
`list_strategy_experiments`, so this backend correction applies to its listing path.

Evidence:

- `test_strategy_listing_excludes_backtests_but_preserves_relational_links`
- `test_strategy_listing_still_raises_for_malformed_strategy_and_reads_legacy_records`
- `app.py` calls `list_strategy_experiments` for the Strategy Lab view.

No artificial duplicate Strategy Lab strategy record is created by backtest registration.
The dashboard still has no backtest registration/run/history workflow; the Phase 4 task
document explicitly describes that workflow as outside this baseline.

The separate `list_backtests` issue is recorded below as F-06; it does not recreate the
original Strategy Lab failure.

## F-03 — Instrument snapshot and tick-size provenance

**Status: RESOLVED.**

`run_backtest` reads an active NSE equity instrument and rejects legacy REAL tick sizes or
missing exact decimal tick sizes. It parses one `Decimal` tick value, builds the instrument
snapshot from the same retrieved row, and passes that same tick value into `_simulate` and the
benchmark. The snapshot contains instrument ID, exchange, symbol, segment, instrument type,
lot size, and canonical decimal tick text; `instrument_snapshot_sha256` hashes that snapshot.
The result stores both snapshot and hash.

`test_run_backtest_end_to_end_with_confirmed_calendar_and_complete_data` verifies the
snapshot. The same test reruns the backtest to compare business results, changes the
Instrument Master tick size, and verifies that the new run records the changed tick/fill
while the prior run retains its old result. The legacy-provenance test verifies that missing
instrument/source snapshots are labeled `LEGACY_INCOMPLETE` without fabrication, including
legacy result JSON with no metadata.

Instrument names and unrelated registry fields are not snapshotted. The captured fields cover
the instrument identity and execution-relevant tick/quantity metadata used by this engine.

## F-04 — Source-use declarations and enforcement

**Status: RESOLVED for application backtest execution paths; declarations remain user
assertions, not legal verification.**

Schema version 7 adds `data_imports.source_use_status`, constrained to
`VERIFIED_PERMITTED`, `UNVERIFIED`, or `PROHIBITED`, with `UNVERIFIED` as the database
default. It adds `import_source_use_history` with a foreign key to the import and fields for
previous/new status, actor, rationale, and declaration time. The migration is additive and
runs in the existing transaction. Tests cover fresh schema, v6-to-v7 migration, legacy
defaulting, initialization idempotence, and rollback on failure to insert the migration
version.

`import_ohlcv_csv` does not set an import to permitted. The only application declaration
function, `declare_import_source_use_status`, validates the status, actor, rationale, and
target import, then records history and updates the import status in one transaction. The
function documentation explicitly states it does not establish legal rights or verify a
licence.

The backtest loader resolves the imports that actually contribute completed candles.
`run_backtest` blocks if any contributing import is prohibited or not explicitly
`VERIFIED_PERMITTED`; it has no override. Mixed-source enforcement, unverified/prohibited
blocks, explicit declaration validation, and unchanged provenance after a later declaration
change are covered by:

- `test_backtest_fails_closed_for_unverified_and_prohibited_imports`
- `test_mixed_source_dataset_requires_every_contributing_import_to_be_permitted`
- `test_source_use_declaration_requires_explicit_auditable_user_action`
- `test_run_backtest_end_to_end_with_confirmed_calendar_and_complete_data`

Registering or directly constructing a backtest experiment configuration does not supply or
override import permission. `run_backtest` resolves each contributing import from stored
candles and applies the same status gate. The Strategy Lab runner is a signal-generation
path, not the backtesting simulator; it does not produce backtest fills or P&L.

Completed runs store the declaration status and associated declaration fields in their
result metadata; subsequent changes do not rewrite the stored result. Enforcement and
history are application-level: a person directly editing the local SQLite database can
change database rows outside the declaration API. The software makes no claim to protect a
local database from its owner or to adjudicate permission.

The normal database inspected during this audit is schema version **2**, does not yet have
the `source_use_status` column, and contains **0** `CSV_OHLCV` imports. It therefore contains
no actual import declarations. It was not migrated.

## F-05 — Determinism and regression coverage

**Status: PARTIALLY RESOLVED; public Decimal-context independence is a remaining Medium
defect.**

Coverage added since the original audit includes:

- `test_strategy_signals_are_invariant_to_future_candle_changes`: changes only future candles
  and confirms earlier signals are unchanged. This tests the signal generator, not the full
  public backtest output under a future-data perturbation.
- `test_run_backtest_end_to_end_with_confirmed_calendar_and_complete_data`: repeats the
  public run with identical inputs and compares the complete persisted business result,
  excluding the separate run ID/timestamps. It also checks changed tick-size provenance.
- `test_simulation_is_independent_of_callers_decimal_context`: compares `_simulate` under
  differing precision and rounding modes. This is a private simulation-level test.
- `test_zero_missing_and_negative_volume_fail_closed` and
  `test_volume_liquidity_proxy_uses_prior_completed_bar_not_execution_bar`: exercise zero
  volume and the prior-bar volume proxy through `_simulate`.
- `test_daily_metrics_boundary_cases_keep_undefined_values_explicit`,
  `test_daily_metrics_report_daily_returns_and_trade_aggregates`, and
  `test_maximum_drawdown_includes_open_position_mark_to_market`: cover metric boundaries and
  drawdown, primarily through private calculation/simulation helpers.
- Public-API minimum-history and sufficient-history/no-signal tests are listed under F-01.

### Independent public-API Decimal-context probe

Using temporary databases and synthetic candles, the audit invoked the public
`run_backtest` path through the existing test fixture helper with caller Decimal precision 3
and `ROUND_DOWN`. It raised `decimal.InvalidOperation` in
`src/aegis/market_quality.py` at the completeness `quantize` calculation in
`analyze_dataset` (line 780 at audit time). This calculation runs before the backtest's local
precision-50 simulation context. The same ordinary fixture completed under the default
context, and the isolated precision-5/precision-50 comparison completed with matching
relative-performance values; neither result negates the precision-3 failure.

Thus the private `_simulate` test does not establish end-to-end Decimal-context independence.
The public API may fail instead of returning the same result under a valid altered caller
context. No source was changed to address this audit finding.

## Other technical and financial verification

### Causal execution timing and look-ahead

Inspection of `_simulate` confirmed this order: execute an order queued by the prior candle
at the current candle open; mark the current candle close; then evaluate the current
completed-candle signal and queue an order for the next eligible candle. No pending order
exists on the first candle. The execution candle's aggregate volume is not used at its open;
all-or-none capacity uses prior completed volume. A zero capacity rejects the order. The
held quantity is used for exits. A signal on the final candle is left unfilled when no next
eligible bar exists. No silent end-of-data liquidation occurs.

### Fill costs, brokerage, and financial arithmetic

The engine uses `Decimal` and a local precision-50/`ROUND_HALF_UP` context for simulation.
Fill prices apply half-spread and slippage adversely and round buys up / sells down to the
exact tick. Brokerage is rate × order turnover, limited by a positive per-order cap; zero
cap means uncapped, and zero rate means zero brokerage. Exchange and regulatory charges apply
to both sides; transaction tax has buy/sell rates; stamp duty applies to buys; GST applies to
rounded brokerage, exchange, and regulatory charges. Each charge is rounded to INR 0.01 with
`ROUND_HALF_UP`. These are configurable research assumptions, not official fee schedules.

The cash/position path debits buys by turnover plus fees and credits sells by proceeds less
fees. Closed-trade net P&L includes entry and exit fees; open positions remain marked to
market and show estimated liquidation costs separately. The public end-to-end fixture
checks open-position reporting and repeatability; isolated simulation tests cover closed
trades, rejections, and cash reconciliation.

The prior audit's charged round-trip arithmetic remains consistent with the reviewed
formulas: buy 10 at 100.10 incurs 1.54 in rounded fees; sell at 109.90 incurs 1.54; gross
P&L is 98.00 and net P&L is 94.92, giving ending cash/equity 100,094.92 from 100,000. The
previous independent audit had also checked this against `_simulate`; the current audit did
not re-run that exact example as a separate script.

### Metrics and benchmark methodology

Metrics use closed trades for win/loss counts, win rate, profit factor, and expectancy.
Breakeven trades count in the win-rate denominator but are neither winners nor losers.
Profit factor is undefined when there are no net losing trades. Daily returns use the last
recorded exchange-local equity mark per trading date; volatility uses sample standard
deviation annualized by √252; Sharpe assumes zero risk-free rate and is undefined with too
few observations or zero variance. Maximum drawdown is calculated across recorded equity
marks. The tests exercise no trades, winners, losers, breakeven, undefined profit factor,
and open-position drawdown, but most are direct helper tests rather than all being public
API integration assertions.

The benchmark is fixed-quantity same-instrument buy-and-hold, entering at the first bar open
and exiting at the last close with configured costs. Unaffordable benchmarks are explicitly
unavailable; flat-price behavior is tested. It is not an index benchmark and does not apply
the strategy's historical liquidity proxy.

### Data quality, exchange calendar, and provenance

The backtest requires confirmed calendar coverage, complete expected candles, freshness, and
valid/nonduplicate data through the market-quality gate. Source permission, data quality,
and calendar confirmation are independent conditions: permission does not confer calendar
confirmation or data quality.

`config/exchange_calendars.json` remains configured with `"calendars": []`. Synthetic test
calendars do not supply real exchange coverage. A successful actual-data backtest cannot be
established using the checked-in calendar.

Successful result metadata includes configuration/spec/source hashes, dataset and file
hashes, calendar input/effective-calendar hashes, cost assumptions, source declarations, and
the instrument snapshot/hash. These fingerprints improve traceability but do not protect
against privileged local database modification or reconstruct missing source files/data.

### Database and migration integrity

The migration increments schema 6 to 7, adds a constrained non-null defaulted status, and
creates a history table with an import foreign key and index. No old rows are updated to
permitted. Tests validate preserved record counts and legacy defaults, repeated
initialization, and transactional rollback when schema-version insertion fails. Migration
tests use temporary databases only. No migration was run against the normal database.

### Dashboard, operating mode, and execution safety

The dashboard uses the Strategy Lab listing but does not expose a Phase 4 registration/run
workflow; this remains a documented limitation rather than an undisclosed feature. The
application mode enum allows only `RESEARCH_ONLY` and rejects other modes. The audit found
no broker adapter, credential, live-order path, AI-generated code execution, paid service,
or added dependency. SQL in the reviewed paths uses bound parameters; no new unsafe file
operation was identified. The normal database/log configuration was not changed.

## F-06 — Malformed registered backtests can be hidden

**Severity: Low; confidence: 9/10.**

`list_backtests` in `src/aegis/backtesting.py` selects rows with
`status = 'BACKTEST_REGISTERED'` but silently `continue`s when their JSON is malformed or
does not contain the expected schema version/strategy snapshot. A corrupted or incomplete
registered backtest can therefore disappear from the listing without an error or diagnostic.
This is not a broad `except Exception`, and it does not reopen F-02's Strategy Lab breakage,
but it is a silent data-visibility failure. The current tests verify normal registration
separation and malformed Strategy Lab records; they do not assert malformed registered
backtest listing behavior.

## Validation results

Commands executed with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src/aegis
git diff --check
```

Actual results:

- Pytest: **216 passed in 28.04s**.
- Ruff check: **All checks passed!**
- Ruff format: **41 files already formatted**.
- Mypy: **Success: no issues found in 9 source files**.
- `git diff --check`: exit 0. Git emitted line-ending conversion warnings for pre-existing
  modified working-tree files.
- The previous audit's baseline was **198 passed**; this tree has 18 more collected passing
  tests. Test count alone is not evidence that the behavioral requirements are satisfied.
- The separate precision-3 public API probe failed as described under F-05; it is not part
  of the 216-test suite.

## Readiness and acceptance

1. **F-01 through F-04:** Resolved for the current supported backtesting API and SMA strategy.
2. **F-05:** Partially resolved; public Decimal-context independence fails under the isolated
   precision-3 probe, and several edge regressions remain private-helper tests.
3. **Synthetic-data research:** Technically usable under default Decimal settings and a
   deliberately supplied confirmed test calendar; not accepted as fully regression-protected.
4. **Actual historical-data backtesting:** Not ready/established. The normal database is
   schema v2 with zero CSV OHLCV imports; it has no v7 permission field or source declarations.
5. **Verified exchange-calendar coverage:** Unavailable in the checked-in configuration.
6. **Actual source permissions declared:** No; no CSV imports are present in the inspected
   normal database, and no legal rights are inferred.
7. **Formal Phase 4 acceptance:** **Not met.** At minimum, resolve public Decimal-context
   independence and its integration regression. Operational acceptance additionally
   requires maintained confirmed calendar coverage and explicit permission declarations for
   actual datasets.

## Git and data integrity at audit

Branch and revision:

- Branch: `main`
- HEAD: `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`
- `origin/main`: `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`
- HEAD matches `origin/main`.

Modified tracked files observed:

- `AGENTS.md`
- `docs/DEVELOPMENT_ROADMAP.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/VALIDATION_AND_ACCEPTANCE.md`
- `src/aegis/database.py`
- `src/aegis/market_data.py`
- `src/aegis/strategy_lab.py`
- `tasks/PHASE_04_BACKTESTING.md`
- `tests/test_database.py`
- `tests/test_market_data.py`
- `tests/test_project_scaffold.py`
- `tests/test_strategy_lab.py`

Untracked files observed before creating this report:

- `docs/reviews/PHASE_04_INDEPENDENT_AUDIT.md`
- `src/aegis/backtesting.py`
- `tests/test_backtesting.py`

This report, `docs/reviews/PHASE_04_REMEDIATION_INDEPENDENT_AUDIT.md`, is the only file
created by this audit. The pre-existing `docs/DEVELOPMENT_ROADMAP.md` and
`tests/test_project_scaffold.py` changes were preserved. `git diff --stat` for tracked files
reported **12 files changed, 311 insertions(+), 49 deletions(-)**; untracked files are not
included in that stat.

The normal database SHA-256 before and after validation and read-only inspection was
`0AF4CE61875B3B65DBB1D2F6C355787F39123F0C00E006070CC5145FA7AFD890`. The log SHA-256
before and after was
`73E76DC2E1BB7688A03473FD6D81136F529FD06752452F5879085D7CA2C2E07C`. No Git status
change was reported for either protected file. The immediately preceding remediation report
also noted that its starting hashes were unavailable, so no stronger comparison to an
earlier remediation baseline is claimed here.

No commit, push, branch change, migration, or modification to an existing repository file
was made during this audit.
