# Project Aegis — Phase 4 Final Independent Acceptance Audit

**Review date:** 2026-11-09\
**Review type:** Strictly read-only independent technical audit\
**Target:** Phase 4 backtesting engine and F-05/F-06 remediation\
**Branch / HEAD:** `main` / `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`\
**`origin/main`:** `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`\
**Operating mode:** `RESEARCH_ONLY`\
**Overall code-level verdict:** **APPROVE WITH CONDITIONS**\
**Operational readiness:** **NOT YET READY FOR APPROVED REAL-DATA BACKTESTING**

## 1. Executive summary

The public backtest path now completes with identical persisted business results under the
default Decimal context, precision 3 / `ROUND_DOWN`, precision 5 / `ROUND_UP`, precision 10 /
`ROUND_FLOOR`, and precision 50 / `ROUND_HALF_EVEN`. The caller's full relevant Decimal
context state remained unchanged on completed and blocked runs. An additional isolated
exception probe confirmed context restoration when an exception is raised from inside the
public run path. The earlier precision-3 failure in market-quality completeness calculation
is resolved by integer-based percentage rounding.

The prior F-01 through F-04 findings remain resolved for the currently supported SMA
crossover implementation. F-05 is resolved. F-06 is partially resolved: malformed JSON,
unsupported/missing schema, and a missing or structurally incomplete strategy snapshot now
raise an experiment-specific `BacktestError`. However, `list_backtests()` checks required
snapshot keys but not their value types or configuration-hash integrity. An isolated
temporary-database probe showed that a snapshot with all required keys but
`strategy_experiment_id = "not-an-integer"` is returned as a successful listing entry without
a diagnostic. This is a Low-severity condition; it does not permit the run itself to bypass
the configuration and relational integrity checks in `run_backtest()`.

No Critical, High, or Medium finding was identified. The Low-severity F-06 condition should
be closed by validating snapshot field types/relationships and the stored configuration
hash, with regressions for malformed values. The code-level verdict is therefore conditional
rather than an unconditional approval.

Operational readiness is separate and remains **NOT YET READY**. The checked-in exchange
calendar has no calendars, and the normal local database is schema version 2 with zero CSV
OHLCV imports and no source-use status column. Synthetic fixtures establish code behavior
only; they do not establish verified exchange-calendar coverage, permitted real-data use, or
readiness for real-data backtesting.

## 2. Scope and method

This review inspected both previous Phase 4 audit reports, the current Phase 4 task and
validation criteria, backtesting and Strategy Lab code, market-data quality and ingestion,
database schema/migrations, test coverage, dashboard safety messaging, exchange-calendar
configuration, and the actual Git state. It independently ran the requested validation
commands and used isolated temporary databases for public Decimal-context and malformed
listing probes. The normal application database was queried through a read-only SQLite
connection only.

No source, test, configuration, database, log, or existing documentation file was modified.
No migration, commit, push, branch change, or dependency operation was performed. The only
repository file created by this audit is this report.

## 3. Review baseline and Git state

### Baseline

- Branch: `main`.
- HEAD: `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`.
- `origin/main`: `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`.
- HEAD matched `origin/main` at the start and remained unchanged at the end.
- The final report path did not exist before this audit.

### Worktree before report creation

Modified tracked files:

- `AGENTS.md`
- `docs/DEVELOPMENT_ROADMAP.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/VALIDATION_AND_ACCEPTANCE.md`
- `src/aegis/database.py`
- `src/aegis/market_data.py`
- `src/aegis/market_quality.py`
- `src/aegis/strategy_lab.py`
- `tasks/PHASE_04_BACKTESTING.md`
- `tests/test_database.py`
- `tests/test_market_data.py`
- `tests/test_project_scaffold.py`
- `tests/test_strategy_lab.py`

Untracked files:

- `docs/reviews/PHASE_04_INDEPENDENT_AUDIT.md`
- `docs/reviews/PHASE_04_REMEDIATION_INDEPENDENT_AUDIT.md`
- `src/aegis/backtesting.py`
- `tests/test_backtesting.py`

The previous audit reports describe the earlier F-01 through F-05 implementation state and
the F-05/F-06 remediation state, respectively. Since the implementation and test files are
untracked at the current Git baseline, Git history cannot establish their precise
introduction point or attribute every current tracked edit to a particular remediation. No
such attribution is assumed here. The listed pre-existing worktree changes and both prior
reports were preserved.

### Protected files and audit end state

The normal database and log hashes at the end of validation were:

- `data/aegis.db`: `0AF4CE61875B3B65DBB1D2F6C355787F39123F0C00E006070CC5145FA7AFD890`
- `logs/aegis.log`: `73E76DC2E1BB7688A03473FD6D81136F529FD06752452F5879085D7CA2C2E07C`

These match the hashes recorded by the prior audit. No migration or database write was
performed. Branch and HEAD remained unchanged; no commit or push occurred. The final
worktree consists of the same pre-existing changes/untracked implementation files plus this
new report.

## 4. Finding summary

| ID | Severity | Finding | Verdict |
|---|---|---|---|
| F-01 | Medium, previously | Insufficient strategy history could complete as a no-trade run | **RESOLVED** |
| F-02 | Medium, previously | Backtest registration interfered with Strategy Lab listing | **RESOLVED** |
| F-03 | Medium, previously | Exact instrument tick size was absent from run provenance | **RESOLVED** |
| F-04 | Medium, previously | Source-use permissions were not recorded or enforced | **RESOLVED** |
| F-05 | Medium, previously | Public calculations were not Decimal-context independent | **RESOLVED** |
| F-06 | Low, previously | Malformed backtest registrations could disappear from listing | **PARTIALLY RESOLVED** |

No Critical, High, or Medium defect remains in the reviewed implementation. F-06 is the one
remaining Low-severity condition. No separate additional finding was confirmed.

## 5. F-01 — Minimum strategy history

**Verdict: RESOLVED for the currently supported SMA-crossover strategy.**

`StrategySpec.minimum_history_candles` in `src/aegis/strategy_lab.py` derives the minimum
from the configured slow window. `run_backtest()` checks the count of eligible completed
candles before signal generation and simulation. The blocked result identifies the strategy,
version, required count, and available count; it does not return a misleading successful
no-trade result.

Evidence includes:

- `test_backtest_blocks_when_strategy_history_is_below_required_minimum`
- `test_backtest_accepts_exact_and_above_minimum_strategy_history`
- `test_history_requirement_comes_from_registered_strategy_configuration`
- `test_sufficient_history_with_no_signals_is_a_valid_no_trade_run`

The no-signal test confirms that a valid completed no-trade run remains possible when
minimum history is satisfied. The implementation is not generic strategy support: the
registered specification and execution currently support SMA crossover, and no broader
strategy capability is claimed.

## 6. F-02 — Strategy Lab registration isolation

**Verdict: RESOLVED.**

Backtests use the `BACKTEST` experiment type and `BACKTEST_REGISTERED` status while
preserving their strategy/hypothesis relational links. `list_strategy_experiments()` excludes
the backtest registration status and typed backtest configurations. It still raises
`StrategyLabError` for malformed Strategy Lab configurations and accepts the explicitly
tested legacy Strategy Lab record format.

Evidence:

- `test_strategy_listing_excludes_backtests_but_preserves_relational_links`
- `test_strategy_listing_still_raises_for_malformed_strategy_and_reads_legacy_records`
- `app.py` continues to use the Strategy Lab listing for its Strategy Lab view.

The dashboard still has no full backtest registration, execution, or run-history workflow;
the Phase 4 task documentation identifies this as a baseline limitation. It is not the
original Strategy Lab listing regression.

## 7. F-03 — Instrument tick-size provenance

**Verdict: RESOLVED.**

`run_backtest()` obtains the active registered NSE equity instrument and requires an exact
decimal tick size; legacy REAL-derived or missing tick sizes fail closed. The same parsed
tick value is used in simulation and benchmark fill rounding. Successful result metadata
captures instrument ID, exchange, symbol, segment, instrument type, lot size, decimal tick
text, and a snapshot hash. Existing run results are immutable when the live instrument
record changes.

`test_run_backtest_end_to_end_with_confirmed_calendar_and_complete_data` verifies the
snapshot, reruns the same experiment, changes the registered tick size, and confirms that
the new run reflects the new tick while the earlier result remains unchanged.
`test_run_backtest_blocks_without_authoritative_decimal_tick_size` covers fail-closed
behavior. `test_legacy_backtest_provenance_is_marked_incomplete_without_fabrication`
confirms that missing legacy provenance is labeled rather than inferred.

## 8. F-04 — Source-use permission enforcement

**Verdict: RESOLVED for application backtest execution; declarations are not legal
verification.**

Schema version 7 adds `data_imports.source_use_status` with a database default of
`UNVERIFIED` and a CHECK constraint limiting values to `VERIFIED_PERMITTED`, `UNVERIFIED`,
or `PROHIBITED`. The `import_source_use_history` table records import ID, previous and new
status, declaring actor, rationale, and declaration time. Migration 6-to-7 is additive and
runs within the existing transactional migration mechanism.

`declare_import_source_use_status()` requires a valid explicit status, non-empty actor and
rationale, and an existing import; it records history and changes the status in one
transaction. Importing data does not mark it permitted. `_load_completed_candles()` resolves
the source imports contributing to the actual selected candles, and `run_backtest()` blocks
if any contributing import is prohibited or not explicitly `VERIFIED_PERMITTED`. Completed
results preserve the declaration metadata; later status changes do not rewrite old results.
Legacy imports default to unverified.

Evidence includes:

- `test_backtest_fails_closed_for_unverified_and_prohibited_imports`
- `test_mixed_source_dataset_requires_every_contributing_import_to_be_permitted`
- `test_source_use_declaration_requires_explicit_auditable_user_action`
- Schema migration and rollback/preservation tests in `tests/test_database.py`.

This is an auditable user declaration, not automatic licence verification or legal advice.
Privileged direct edits to the local SQLite database are outside the application-level
protection boundary.

## 9. F-05 — Decimal-context independence

**Verdict: RESOLVED.**

### Public execution path

The public path begins with Decimal text parsing and candle-quality analysis, checks
completed data and minimum history, generates strategy signals, simulates fills and
accounting, computes the benchmark and metrics, computes benchmark-relative performance,
and serializes the result.

- Candle values are parsed from canonical decimal text. Stored invalid/non-finite prices are
  rejected by quality and candle validation; ingestion rejects invalid numeric inputs.
- `_completeness_percentage()` in `src/aegis/market_quality.py` now uses integer `divmod`
  arithmetic and explicit half-up rounding to a hundredth. It avoids ambient Decimal
  division/quantization. The expected denominator is guarded by the caller.
- Market-quality candle validity checks parse Decimal values, check finiteness, and compare
  OHLC values; no context-sensitive Decimal arithmetic is performed in those checks.
- `simple_moving_average()` in `src/aegis/strategy_lab.py` uses a local context with
  precision derived from the input values and explicit `ROUND_HALF_EVEN`.
- `_simulate()` and `_buy_and_hold_benchmark()` use local precision-50 contexts with
  explicit `ROUND_HALF_UP`. Fee rounding, fill calculation, position/cash arithmetic,
  equity, realized/unrealized P&L, and daily metrics execute within these contexts.
- Benchmark-relative subtraction in `run_backtest()` is now also inside an explicit local
  precision-50 / `ROUND_HALF_UP` context.
- Parsing, finiteness checks, Decimal comparisons, and canonical text serialization do not
  introduce caller-context arithmetic.

### Regression and independent reproduction

`test_public_run_backtest_is_independent_of_decimal_context` calls the public
`run_backtest()` API under:

- The active default Decimal context.
- Precision 3 with `ROUND_DOWN`.
- Precision 5 with `ROUND_UP`.
- Precision 10 with `ROUND_FLOOR`.
- Precision 50 with `ROUND_HALF_EVEN`.

It compares the complete persisted result document to a baseline, including status, orders,
fills, fees, trades, summary/accounting, equity curve, benchmark, and provenance. The test
also compares caller precision, rounding, exponent limits, capitals, clamp, flags, and traps
before and after each run. Its blocked-run branch checks the same context state.

The full test suite was independently rerun. In addition, an isolated temporary database was
used to inject an exception from `_simulate()` during the public run. The exception
propagated, and the caller's full relevant Decimal context state remained unchanged. This
exception-specific probe was audit-only and did not add or modify a test file.

The previous precision-3 / `ROUND_DOWN` completeness failure was independently
re-exercised through the current public API as part of the context regression. The run
completed and matched the baseline. Invalid input handling remains in place: non-finite,
negative, malformed, or otherwise invalid financial values are not accepted merely to
achieve context independence.

## 10. F-06 — Malformed `list_backtests()` registrations

**Verdict: PARTIALLY RESOLVED. Severity: Low.**

`list_backtests()` now raises `BacktestError` with the experiment ID for malformed JSON,
unsupported or missing schema information, a wrong experiment type, and missing or
structurally incomplete strategy snapshots. JSON parsing errors are caught narrowly;
database query errors are not converted into malformed-JSON diagnostics. Listing is
all-or-nothing: when a malformed backtest is encountered in descending ID order, the
function raises instead of returning a partial list. The error makes the corrupt registration
visible and identifies it.

`test_list_backtests_reports_malformed_registered_records` covers malformed JSON, missing
or unsupported schema, malformed schema type, and missing/non-dictionary/incomplete
snapshot. The test includes a valid registration alongside the malformed one and asserts
that Strategy Lab listing remains unaffected. Existing null-strategy rows that are not
typed as backtests continue to be skipped, while a typed backtest row is validated.
Database exceptions occur outside the JSON parsing catch and are not swallowed.

### Remaining Low-severity condition

The snapshot validation checks that `strategy_snapshot` is a dictionary containing a set of
required keys, but it does not validate field value types or consistency. It also selects
`configuration_sha256` without checking that hash against the JSON. In an isolated
temporary database, an otherwise complete snapshot with
`strategy_experiment_id = "not-an-integer"` was returned by `list_backtests()` as a
successful result, with that malformed value exposed and no diagnostic. The same isolated
probe confirmed that the Strategy Lab list remained isolated. `run_backtest()` separately
checks the configuration hash and linked strategy snapshot before execution, so this probe
does not demonstrate an execution-integrity bypass.

The original silent-omission defect is fixed for the tested malformed JSON/schema/missing
snapshot cases, but malformed snapshot values and hash mismatch are not reported by the
listing API. Close this condition by validating required snapshot value types/relationships
and configuration-hash integrity, with focused tests. No explicit supported legacy
backtest-registration schema was found; legacy run-result provenance is separately labeled
`LEGACY_INCOMPLETE`.

## 11. Causal execution, fills, and costs

### Event timing and look-ahead

`_simulate()` processes candles chronologically:

1. At candle T open, execute any pending order produced from an earlier completed candle.
2. Use candle T's close to mark the position and equity.
3. After the close, evaluate the completed-candle signal and queue an order for the next
   eligible candle.

No signal executes at the close that generated it. Intraday execution references the next
candle's open; daily execution uses the configured session open. The all-or-none capacity
check uses the prior completed candle's volume, which is available at the signal close; it
does not use the execution candle's eventual aggregate volume. Zero/insufficient capacity
rejects the order. Exit quantity is the held position quantity. A final-bar signal is
explicitly marked unfilled when no next eligible candle exists. Open positions are not
silently liquidated at the end of the range.

Evidence includes `test_volume_liquidity_proxy_uses_prior_completed_bar_not_execution_bar`,
`test_exit_volume_rejection_preserves_position`, and the causal order-sequencing tests in
`tests/test_backtesting.py`. No code path reviewed used future candles to make a past
decision.

### Fill assumptions and transaction costs

Fill prices use the execution candle open, apply adverse half-spread and slippage, and round
buys upward and sells downward to the exact tick. Orders are all-or-none. Brokerage is
turnover times the configured rate; a positive cap limits per-order brokerage, while a zero
cap is explicitly uncapped. Exchange and regulatory charges apply to both sides, transaction
tax uses side-specific rates, stamp duty applies to buys, and GST applies to rounded
brokerage/exchange/regulatory charges. Individual charges round to INR 0.01 using
`ROUND_HALF_UP`.

These are configurable research assumptions, not official or guaranteed current exchange or
broker schedules. Partial fills, shorting, margin, corporate actions, and a broker fee
integration are outside the documented baseline.

### Cash, positions, and P&L

Starting cash must be a positive finite Decimal representable to paisa. A buy is rejected if
cash cannot cover turnover and charges; there is no margin/negative-cash path. A sell uses
the held quantity and cannot create a short position. Cash movements, position quantity,
average/entry price, entry fees, closed-trade gross/net P&L, and total fees/turnover are
calculated using Decimal arithmetic in the simulation's local context.

At the end of the dataset, an open position remains on the books and is marked to market.
Estimated adverse liquidation impact and charges are reported separately and used to
calculate net liquidation value; they are not represented as an executed sell. Drawdown
uses the recorded marked-to-market equity curve. The previous independent audit documented
a hand-calculated fee round trip; this audit rechecked the formulas and the relevant
regressions but did not independently rerun that exact numeric example.

## 12. Metrics and benchmark methodology

Metrics use closed trades for win/loss counts, win rate, profit factor, and expectancy.
Breakeven trades remain in the win-rate denominator but are neither winners nor losers.
Profit factor is undefined when there are no net losing trades. Daily returns use the last
equity mark for each exchange-local trading date. Annualized volatility uses sample standard
deviation times square root of 252; Sharpe assumes zero risk-free rate and is undefined with
fewer than two daily observations or zero variance. Maximum drawdown is calculated over
recorded equity marks. Open-position return and liquidation cost are reported separately
from closed-trade metrics.

Relevant coverage includes `test_daily_metrics_boundary_cases_keep_undefined_values_explicit`,
`test_daily_metrics_report_daily_returns_and_trade_aggregates`, and
`test_maximum_drawdown_includes_open_position_mark_to_market`.

The benchmark is fixed-quantity, same-instrument buy-and-hold: it enters at the first bar
open, exits at the final bar close, and applies the configured costs. An unaffordable
benchmark is explicitly unavailable. It is not a Nifty 50 or other index benchmark, and it
does not apply the strategy's prior-bar liquidity proxy. It must not be interpreted as an
index-relative result or evidence of profitability. `test_buy_and_hold_benchmark_happy_path_and_unaffordable`
covers the principal benchmark boundary.

## 13. Data quality, exchange calendar, and provenance

`config/exchange_calendars.json` still contains `"calendars": []`. The analyzer distinguishes
unavailable, configured, and confirmed coverage; incomplete or unconfirmed calendar
coverage cannot authorize a completed backtest. The engine requires complete expected
candles, confirmed full calendar coverage, freshness, and valid/nonduplicate data. Rejected,
duplicate, unexpected, or stale data is handled through the market-quality gate rather than
being treated as verified completeness. Synthetic confirmed calendars in tests are
fixtures, not production NSE calendar evidence.

Successful result metadata includes the strategy/configuration identity and hashes,
implementation source hashes, source revision status, dataset and source-file hashes,
instrument snapshot/hash, source-use declarations, raw/effective calendar hashes, `as_of`,
execution configuration, cost assumptions, and metric methodology. Blocked and failed
results are marked `PARTIAL_BLOCKED` or `PARTIAL_FAILED`; they do not claim complete
provenance. Hashes improve traceability but do not protect against privileged local database
edits or recreate missing data/source artifacts.

### Code-level acceptance versus operational readiness

**Code-level acceptance:** synthetic tests demonstrate that the engine works against
explicitly permitted fixture data and a test-only confirmed calendar, subject to the Low
F-06 listing-validation condition above.

**Operational readiness:** not established. A read-only query of `data/aegis.db` found schema
version 2, zero `CSV_OHLCV` imports, and no `source_use_status` column. The checked-in
calendar has no entries. No actual source declarations or confirmed calendar coverage are
present in the inspected normal database/configuration. No migration was run against the
normal database. Therefore the application is **NOT YET READY FOR APPROVED REAL-DATA
BACKTESTING**.

## 14. Database integrity and migration

The reviewed migration path is additive through schema version 7. The source-use status
constraint and history table preserve legacy imports as `UNVERIFIED`. Migration execution
uses `BEGIN IMMEDIATE`, applies each schema step before recording the new version, and
rolls back on exceptions. Existing migration tests use temporary databases to check
preservation, idempotence, and failure rollback. This audit did not execute migrations
against any database.

Backtest configuration hashes are checked by `run_backtest()` before run creation, and the
stored strategy snapshot is compared with the current linked Strategy Lab snapshot. Broken
relational links or altered snapshots are rejected. Successful instrument and source
provenance are immutable in completed result JSON.

## 15. Dashboard and safety boundaries

`src/aegis/config.py` permits only `RESEARCH_ONLY`; `app.py` visibly warns that there is no
broker connection, live orders, or automated trading. The dashboard exposes market-data
and Strategy Lab workflows but not a full Phase 4 backtest workflow. The reviewed execution
path is a local simulator and contains no broker order submission. No broker credentials,
paid market-data integration, paid infrastructure, arbitrary AI-generated code execution,
or AI authority to alter risk/execution modes was identified in the reviewed surfaces.

No future candlestick-pattern, technical-indicator, chart-pattern, or agentic strategy
execution feature was implemented as part of this audit.

## 16. Validation results

Commands run independently using the existing project environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe src\aegis
git diff --check
```

Results:

- Pytest: **224 passed in 30.36s**.
- Ruff check: **All checks passed**.
- Ruff format: **42 files already formatted**.
- Mypy: **Success: no issues found in 9 source files**.
- `git diff --check`: **passed**. Git emitted line-ending conversion warnings for existing
  modified files; no whitespace error was reported.
- Additional isolated probes: public Decimal-context and exception preservation passed;
  malformed snapshot value probe confirmed the remaining F-06 condition.

The implementation team's prior chat response reported 224 tests and all required checks
passing. No separate implementation validation report file was identified; the command
results above are this audit's independent rerun.

## 17. Remaining limitations and acceptance condition

Documented Phase 4 limitations remain: only SMA crossover, long-only single-position NSE
cash equities, fixed integer order quantity, all-or-none fills, configurable non-official
cost assumptions, no index benchmark, no partial fills/shorting/margin/corporate-action
adjustment, no full backtest dashboard workflow, and no verified production holiday/session
coverage. These limitations are not represented as implemented features.

**Condition for unconditional code-level approval:** make `list_backtests()` reject
experiment-specific malformed snapshot values and configuration-hash mismatches with an
experiment-ID diagnostic, and add tests for those cases. Keep real-data use blocked until
maintained verified calendar coverage and explicit permission declarations exist for the
actual datasets.

## 18. Final acceptance decision

**Code-level verdict: APPROVE WITH CONDITIONS.**

The F-01 through F-05 criteria are resolved, and no Critical, High, or Medium defect was
confirmed. The remaining F-06 issue is Low severity and limited to malformed snapshot value
and configuration-integrity diagnostics in the listing surface. The condition above must be
addressed before calling the F-06 diagnostic requirement fully resolved.

**Operational readiness: NOT YET READY FOR APPROVED REAL-DATA BACKTESTING.** The empty
checked-in calendar and the normal database's schema-v2/zero-import state do not support
real-data readiness claims. This is separate from the code-level review and is not inferred
from passing synthetic tests.
