# M2 — Portfolio Accounting Specification

**Design status:** Approved M2 v1 design.
**Implementation status:** M2-A is owner-reported accepted. M2-B has owner-reported independent ACCEPT on 2026-11-16 for qualification-only
scope; the original audit artifact is not yet in the repository. Its schema-provenance
defect is corrected and requires focused independent review, not automatic reacceptance.
M2-C is independently accepted for qualified historical per-sleeve M1B composition.
M2-D is implemented and technically ACCEPTED; distinct-reviewer independent sign-off
remains pending. These M2-C/M2-D decisions are recorded from project-owner-supplied
acceptance history; see the evidence limitations in [`docs/PROJECT_STATE.md`](../docs/PROJECT_STATE.md).
M2-E has not started and is not authorized. Overall M2 is not accepted; it remains
historical research/backtesting only.
See [`docs/PROJECT_STATE.md`](../docs/PROJECT_STATE.md) for the dated snapshot.
**Mode:** Historical research/backtesting only. This specification does not authorize or
implement live execution, paper trading, or any Phase 8 workflow.

## 1. Purpose, scope, and non-goals

M2 v1 defines how to compose qualified M1B one-instrument executions into an immutable,
daily-valued, multi-instrument portfolio result. Every instrument is assigned a fixed cash
sleeve before execution. Sleeves are independent: unused sleeve cash stays idle, and cash
cannot be borrowed, transferred, or reallocated.

The v1 universe is frozen before a run and limited to active NSE cash EQUITY instruments. All
sleeves use daily candles, one shared set of cost assumptions and volume-participation policy,
one `as_of`, one approved calendar instance, and one read-only SQLite snapshot. Each
instrument's order quantity must satisfy its registered lot size.

M2 does not add a portfolio persistence schema. Its direct output is an immutable result.
M2 does not add a simulator, fill model, fee model, or risk/execution authority. BSE,
derivatives, shorts, leverage, partial fills, scale-ins, corporate-action-adjusted data, and
cross-currency accounting are out of scope.

## 2. M1B source-grounded interface and capabilities

### Public and internal interfaces

The public M1B entry point is:

```python
execute_one_instrument(
    database_path: Path,
    *,
    exchange: str,
    trading_symbol: str,
    interval: str,
    start_date: date,
    end_date: date,
    fast_window: int,
    slow_window: int,
    config: BacktestConfig,
    calendar_config_path: Path,
    as_of: datetime,
) -> ExecutionResult
```

Source: `src/aegis/backtesting.py:L1545-L1651`. It reads and qualifies its own instrument,
calendar, data quality, expected schedule, candles, and source-use provenance. It generates
the built-in SMA crossover signals and does not accept caller-supplied signals or qualification
evidence. It uses a read-only database transaction and does not persist a run.

`_ExecutionRequest` and `_execute_qualified_request(request, *, qualification_token)` are
internal interfaces (`src/aegis/backtesting.py:L248-L268, L1653-L1948`). A private token
guards the shared execution path. M2 must use a trusted coordinator in this module (or a
carefully extracted equivalent) to construct qualified inputs and invoke that same path. It
must not expose caller-built qualified requests or the token.

The registered single-strategy API is `run_backtest(database_path, backtest_experiment_id,
*, calendars, as_of) -> BacktestRunResult` (`src/aegis/backtesting.py:L1989-L2432`). It
persists a run result; it is not the M2 v1 execution interface. `register_backtest` binds one
strategy experiment and a `BacktestConfig` (`src/aegis/backtesting.py:L492-L565`).

### Reusable execution and result types

- `BacktestConfig` is frozen and contains starting cash, fixed integer quantity, maximum
  volume participation, and `CostAssumptions`. It validates positive finite Decimal cash
  representable to INR 0.01 and a valid participation fraction.
  `CostAssumptions` validates finite, non-negative Decimal rates and costs
  (`src/aegis/backtesting.py:L74-L184`).
- `ExecutionResult` is a frozen result with recursively frozen orders, fills,
  rejected/unfilled orders, trades, equity curve, optional open position, summary,
  diagnostics, and one-instrument buy-and-hold benchmark. Its
  `to_simulation_output()` and `to_benchmark_output()` methods return detached mutable
  serialization values   (`src/aegis/backtesting.py:L289-L337`).
- Each fill includes execution time, side, quantity, fill price, itemized and total fees,
  cash before/after, and position quantity before/after. Equity points include timestamp,
  local date, cash, quantity, mark price, and equity
  (`src/aegis/backtesting.py:L1653-L1948`).
- `_fill_price`, `_fees`, `_simulate`, and `_buy_and_hold_benchmark` are the reusable
  execution/cost implementations (`src/aegis/backtesting.py:L794-L848, L924-L1263`). M2
  must not reimplement or bypass them.
- `StrategySpec`, `StrategyCandle`, and `StrategySignal` are frozen values. The supported
  SMA generator validates chronological candles and emits signals at candle close from
  current and prior closes only (`src/aegis/strategy_lab.py:L52-L153, L175-L215,
  L449-L565`).

### Existing execution limits and terminal valuation

M1B is long-only, fixed-quantity, and permits one open position per instrument. It queues
completed-close signals for the next eligible candle open and checks execution capacity using
the previous completed candle's volume (`src/aegis/backtesting.py:L924-L1207`). It does not
support scale-ins, partial fills, shorts, leverage, or derivatives.

At dataset end, M1B leaves the actual position and cash unchanged. It reports mark-to-market
equity and separately estimates adverse exit impact, exit charges, and net liquidation value
(`src/aegis/backtesting.py:L1141-L1198`). M2 must preserve this actual-versus-hypothetical
separation.

### Qualification and trust boundary

`_gate_reason` requires complete, fresh data, 100% confirmed completeness, and no invalid or
duplicate stored candles. `_validate_source_imports` checks each contributing import's
identity and latest declaration history and requires `VERIFIED_PERMITTED`
(`src/aegis/strategy_lab.py:L757-L850`). This is an auditable user declaration, not
independent legal verification.

`analyze_dataset` supports a caller-owned connection, and `_load_completed_candles` reads
exact-decimal candle rows and fingerprints the dataset and candle/source bindings
(`src/aegis/market_quality.py:L635-L969`; `src/aegis/strategy_lab.py:L868-L1042`).
M1B's calendar and quality values distinguish configured from confirmed coverage
(`src/aegis/market_quality.py:L31-L182`).

### Instrument identity and persistence

The instrument master normalizes exchange and trading symbol and stores lot size, type,
segment, active status, and precise tick data (`src/aegis/instruments.py:L42-L96,
L146-L257`). M1B requires an authoritative positive decimal tick and a lot-aligned order
quantity. Its current execution boundary is active NSE cash EQUITY only
(`src/aegis/backtesting.py:L1287-L1345, L1347-L1651`).

The database has a generic `runs.result_json` and an `experiments` relationship to a single
strategy (`src/aegis/database.py:L40-L90`). There is no M2 portfolio schema or portfolio
registration API. M2 v1 will not add one.

## 3. M2 v1 architecture and execution flow

The public M2 request carries user intent, not trusted evidence. A trusted portfolio
coordinator performs these steps:

1. Validate the ordered strategy/universe request, shared daily interval and date range,
   total capital, common execution settings, and timezone-aware `as_of`.
2. Resolve and freeze each canonical identity and instrument-master snapshot in a frozen
   `UniverseManifest`.
3. Load one approved calendar registry and open one read-only SQLite transaction. Using that
   same connection and calendar instance, qualify every instrument's master data, expected
   daily schedule, quality report, exact candles, and source-use history.
4. If any sleeve is ineligible, return a blocked portfolio result before simulating any
   sleeve. Do not return partial totals as a completed portfolio.
5. Allocate total capital in paise and derive each sleeve's `BacktestConfig` from its fixed
   quantity and allocated cash, using the portfolio's shared cost assumptions and
   volume-participation policy.
6. Generate each sleeve's signals from that sleeve's qualified candles using the accepted
   generator. Construct internal qualified requests and execute each through M1B's existing
   `_execute_qualified_request` path.
7. Compose the immutable sleeve results into daily portfolio valuations and totals. Serialize
   without writing to the database.

Repeated calls to public `execute_one_instrument()` do not satisfy step 3: each call has its
own database snapshot. A batch coordinator must share qualification context while reusing the
same checks and simulator. This is the central M2-B implementation constraint.

## 4. Immutable contract proposals and invariants

All proposed types are frozen and slotted. They must use tuples and recursively immutable
nested values; `frozen=True` alone does not freeze contained dictionaries. Public serialization
uses canonical decimal text and returns detached values, following M1B's result pattern.

### `PortfolioRequest`

Proposed fields: total capital; ordered `SleeveRequest` intents; common start and end dates;
timezone-aware `as_of`; shared `CostAssumptions`; shared maximum volume participation; and a
versioned `PortfolioPolicy`. The trusted coordinator obtains calendar authorization from
application configuration; the request must not accept a caller-selected calendar registry,
calendar authorization, candles, signals, quality reports, or source provenance.

Validate positive capital representable to paise; nonempty, unique instrument identities;
common daily interval and period; correct NSE EQUITY strategy identities; and compatible
shared settings. Sleeve intents preserve frozen universe order. Their quantities may differ
by instrument, but each must satisfy that instrument's lot size.

### `UniverseManifest`

Proposed fields: schema version; ordered immutable members; and canonical manifest hash.
Each member records sequence index, normalized `exchange:trading_symbol`, instrument-master
row ID, segment, type, active state, lot size, authoritative decimal tick, and a member
snapshot hash. The natural key is exchange plus trading symbol; database row ID is snapshot
evidence, not a replacement for canonical identity.

Validate unique natural keys, stable order, active NSE cash EQUITY type, positive lot size,
authoritative positive Decimal tick, and exact match with each strategy specification. Freeze
the manifest before allocation or execution; never reorder it based on query order or results.

### `PortfolioPolicy`

Proposed fields: policy schema/version and the fixed v1 choices: equal fixed sleeves; remainder
to earliest frozen members; no transfers/borrowing/reallocation; missing expected-session
price blocks; confirmed non-trading-day carry-forward with stale flag; and hypothetical-only
terminal liquidation. These are fixed v1 semantics, not user-selectable alternatives.

### `SleeveRequest` and `SleeveResult`

`SleeveRequest` is a caller-intent value containing canonical exchange/symbol, its matching
`StrategySpec`, and a positive fixed integer order quantity no greater than the signed
64-bit maximum (`2^63 - 1`). Validate its identity against the strategy and, after trusted
instrument resolution, validate quantity against the registered lot size. The M2-A contract
does not establish lot-size validity. It carries no caller-supplied qualification material.

After allocation and qualification, the trusted coordinator builds an internal sleeve
execution request and a `BacktestConfig` using that member's allocated cash and quantity,
with the portfolio's common costs and volume participation. The coordinator—not the caller—
supplies the qualified candles, signals, quality report, provenance, calendar, and expected
schedule to M1B's private qualified path.

`SleeveResult` contains its manifest index and identity, cash allocation, exact M1B
`ExecutionResult`, and reconciliation summary. Require one result per manifest member and
preserve manifest order. Do not construct a second order/fill/fee object model.

### `PortfolioValuation` and `PortfolioExecutionResult`

`PortfolioValuation` contains a local valuation date, ordered per-sleeve cash, quantity, mark,
mark timestamp, stale flag, and sleeve equity, plus total cash, market value, and equity.
Every held position must have either a mark from an eligible completed daily candle or an
explicitly permitted holiday carry-forward.

`PortfolioExecutionResult` contains a schema version; `COMPLETED` or `BLOCKED` status;
block reasons; immutable manifest and policy snapshots; ordered sleeve results; daily
valuations; actual portfolio summary; separate hypothetical terminal-liquidation summary;
and provenance/fingerprints. A blocked result has no completed portfolio totals or partial
sleeve execution results. Its serializer returns detached values.

## 5. Equal-sleeve allocation

Capital is allocated in integer paise. Convert the validated Decimal total to exact paise
without binary floating point. For `N` frozen members and `C` total paise:

```text
base, remainder = divmod(C, N)
allocation[i] = base + 1 paise, when i < remainder
allocation[i] = base,          otherwise
```

The manifest order is the tie-breaker. Require `N > 0`, `C >= N` (at least one paise per
sleeve), and `sum(allocation) == C`.

Examples:

| Total capital | Sleeves | Allocations in frozen order |
|---|---:|---|
| INR 100.07 | 3 | INR 33.36, INR 33.36, INR 33.35 |
| INR 1,000.00 | 4 | INR 250.00 each |
| INR 0.02 | 3 | Invalid: cannot allocate at least one paise to each sleeve |

This allocates initial capital across sleeves; it does not require full deployment. Unused
cash remains in that sleeve.

## 6. Sleeve cash/position accounting and portfolio reconciliation

M1B v1 has a single fixed-quantity open position per instrument. Therefore M2 v1 does not
need a separate weighted-average ledger: for the supported one-entry-at-a-time behavior, the
weighted-average basis is the M1B entry price. If future execution adds scale-ins or partial
fills, weighted-average cost basis and fee allocation require a separately reviewed feature
and tests; do not claim that capability in v1. The fixed quantity may differ between sleeves,
but each sleeve still uses M1B's fixed-quantity execution model.

Use M1B outputs as the accounting source of truth:

- Sleeve starting cash is its exact allocation.
- Filled buy cash debits and sell cash credits, itemized fees, actual fills, positions, and
  equity marks come from `ExecutionResult`.
- Idle cash is included in sleeve cash and cannot cross sleeve boundaries.
- Do not subtract fees from cash or equity a second time; M1B has already included fill costs
  in the execution result.

For sleeve `i` and daily valuation date `d`:

```text
sleeve_equity[i, d] = sleeve_cash[i, d]
                      + position_quantity[i, d] * eligible_mark[i, d]

portfolio_cash[d]   = sum(sleeve_cash[i, d])
portfolio_value[d]  = sum(position_quantity[i, d] * eligible_mark[i, d])
portfolio_equity[d] = portfolio_cash[d] + portfolio_value[d]
portfolio_PnL[d]    = portfolio_equity[d] - total_initial_capital
```

At terminal date, `portfolio_equity` must equal the sum of sleeve actual ending equities,
subject only to an explicitly reported Decimal cash-rounding residual if reconciliation
requires it. Portfolio actual P&L must equal the sum of sleeve `absolute_pnl`; total actual
fees must equal the sum of M1B `total_fees` exactly once. Keep actual P&L distinct from
`net_liquidation_pnl`, which includes hypothetical exit costs.

M1B also reports realized and unrealized fields. In particular, its open-position
`unrealized_net_pnl` includes estimated liquidation costs; it must not be used as if it were
actual mark-to-market P&L. Use actual ending/equity-curve values for actual equity and report
the liquidation estimate separately.

## 7. Daily valuation, missing prices, holidays, and stale marks

M2 v1 is daily-only. The portfolio valuation grid is the ordered set of relevant local
calendar dates over the common run period. On each confirmed expected trading session, every
instrument must have its eligible completed daily candle. If any expected session is missing
or fails M1B qualification, block the entire portfolio; do not substitute a prior mark.

On a calendar-confirmed non-trading date, carry each sleeve's last eligible mark and mark that
valuation `stale_price=true`, including the original mark date/timestamp. Such carry is
valuation-only: it cannot generate a signal or a fill. An uncovered or merely configured
calendar cannot establish a non-trading day and therefore cannot authorize carry-forward.

`calculate_expected_candles` and `analyze_dataset` provide the schedule/quality foundations
(`src/aegis/market_quality.py:L508-L545, L635-L969`). M1B's quality gate remains in force for
every sleeve.

## 8. Hypothetical terminal liquidation

M1B marks open positions at the last close and separately reports estimated adverse exit
impact, exit charges, and net liquidation value (`src/aegis/backtesting.py:L1141-L1198`).
M2 aggregates those M1B estimates per sleeve:

```text
actual_portfolio_equity       = sum(sleeve actual ending_equity)
estimated_liquidation_costs   = sum(sleeve estimated_liquidation_costs)
hypothetical_net_liquidation  = actual_portfolio_equity
                                - estimated_liquidation_costs
```

The hypothetical result must not alter actual fills, cash, positions, equity curves, or trade
counts. It must not append a simulated closing fill or count estimated exit charges in actual
fees. If a sleeve has no open position, its hypothetical terminal costs are zero.

## 9. Shared snapshot, source-use, calendar, provenance, and no-look-ahead

### Shared snapshot and qualification

All master, quality, expected-schedule, candle, and source-use reads for all members must use
one query-only SQLite connection and one explicit read transaction. Do not call the public
M1B API repeatedly to create sleeves. Qualify every sleeve before executing any sleeve, so a
later data failure cannot leave a misleading partial portfolio result.

Use one approved `CalendarRegistry` instance for all members and validate that each member's
calendar mapping, coverage, confidence, session, and schedule match its quality report.
`ExchangeCalendar` and `CalendarRegistry` are frozen dataclasses, but nested mappings need
defensive freezing/copying in M2 result snapshots.

### Source-use and provenance

Require complete source-import provenance and latest matching `VERIFIED_PERMITTED`
declaration history for every contributing candle. Preserve the limitation that this is a
recorded user declaration, not an automatic legal determination.

Bind the result to:

- Ordered universe manifest, instrument snapshots, policy, allocations, common execution
  config, dates, and `as_of`.
- Per-sleeve strategy spec and signal hashes, dataset hash, candle/source-binding hash,
  source-file/import hashes, source-use declaration metadata, and quality-report hash.
- Shared calendar-configuration hash, effective calendar hash, and expected daily-schedule
  hash.
- M1B execution implementation hash and relevant strategy/quality/market-data implementation
  hashes.

Use canonical JSON and Decimal text; do not use floats for financial values. The Git revision
must not be treated as sufficient when the worktree is dirty; implementation fingerprints
must identify the actual executed source content.

### No-look-ahead and replay

All sleeves share the same timezone-aware cutoff. Only completed expected candles at or before
`as_of` may enter signals. Signals use the built-in causal generator; execution remains next
eligible candle open using the M1B prior-completed-bar volume proxy. Carried marks are never
decision inputs. Deterministic replay requires identical manifest order, snapshot contents,
calendar bytes, strategy/config/policy versions, cutoff, and implementation bytes.

## 10. Deterministic ordering

Freeze execution order in the manifest before allocation. Allocate remainders and serialize
sleeve results in that order. Within each sleeve, preserve M1B order and fill order. If a
combined event stream is required for reporting, use:

```text
(UTC event instant, event phase, manifest index, M1B order ID)
```

This order is for deterministic representation only; sleeve execution must not consume
another sleeve's cash or otherwise depend on cross-sleeve sequencing.

## 11. Blocked and failed result semantics

- Invalid request or inconsistent frozen configuration: raise an explicit validation error
  (`BacktestError`-style); do not return a success-shaped fallback.
- Any instrument, source-use, schedule, quality, calendar, or expected-session mark failure:
  return immutable `BLOCKED`, with deterministic reasons and no completed portfolio totals.
  Qualify all sleeves before simulation; do not expose partial execution results as complete.
- Unexpected M1B execution or result-integrity error: propagate an explicit error and return no
  portfolio result. V1 has no portfolio run persistence in which to record a `FAILED` row.
- Only a fully qualified and reconciled portfolio may return `COMPLETED`.

The precise public exception class and whether multiple qualification reasons are accumulated
or the first deterministic reason is returned remain open. Resolve and document this before
introducing or accepting a public portfolio-result API; either behavior must fail closed
and remain deterministic.

## 12. Independent hand-calculated unit, integration, and regression test plan

These are planned tests, not executed results.

### Unit tests

- Allocation: INR 100.07 across three members yields 33.36 / 33.36 / 33.35 in manifest
  order; reversing manifest order moves the remainder deterministically; allocations sum
  exactly to capital; insufficient paise count is rejected.
- Sleeve independence: idle cash in one sleeve cannot make an otherwise unaffordable order
  in another sleeve fill.
- Accounting parity: for each sleeve, summed cash, quantity, marks, fees, fills, and ending
  equity equal its M1B result; portfolio actual equity and P&L equal sums of sleeves with no
  duplicated fee subtraction.
- Valuation: a missing expected daily candle blocks the whole portfolio; a calendar-confirmed
  closure carries the prior eligible mark with a stale flag; an uncovered/configured calendar
  cannot authorize carry.
- Terminal liquidation: aggregate hypothetical exit costs without adding fills or changing
  actual cash, positions, or equity.
- Immutability and serialization: mutate detached serialized output and prove the result
  remains unchanged; compare canonical output on identical inputs.

### Integration and regression tests

- Use temporary database and calendar paths with generated synthetic data; do not touch the
  normal application database or logs.
- Verify all instrument, candle, quality, and source-use reads share the same query-only
  transaction and one calendar registry instance.
- Inject a deterministic failure in one member and verify no sleeve execution is returned as
  a completed portfolio.
- Verify every sleeve matches direct M1B output for its allocated starting cash, including
  fills, fees, positions, and benchmark shape.
- Preserve existing direct-versus-registered M1B parity, no-look-ahead, source-use, calendar,
  tick/lot, fee, and immutable-result regressions.
- Verify a changed database snapshot cannot mix data across sleeves and a changed calendar or
  implementation fingerprint prevents claiming deterministic replay.

## 13. Staged implementation sequence

1. **M2-A — immutable contracts and allocation.** Add value types, strict validation,
   canonical manifest ordering, and the exact paise allocation rule. Unit-test allocation and
   immutability without database access.
2. **M2-B — shared batch qualification snapshot.** Add the trusted coordinator that qualifies
   all members through one read-only SQLite transaction and one approved calendar instance.
   Do not execute until all members pass. Keep public M1B behavior and direct/registered
   parity unchanged.
3. **M2-C — M1B sleeve composition.** Construct an internal qualified request and
   `BacktestConfig` for each member using its allocated cash and lot-valid fixed quantity,
   while sharing the portfolio cost/participation settings. Call the existing shared
   execution path and preserve all M1B fill/cost behavior.
4. **M2-D — portfolio aggregation and reconciliation.** Aggregate immutable sleeve outputs,
   actual cash/positions/equity, fees, and P&L; validate all conservation and parity
   invariants. Do not add a weighted-average ledger in v1.
5. **M2-E — valuation and hypothetical liquidation.** Build daily valuations with the
   expected-session block and confirmed-holiday carry rules. Aggregate hypothetical
   liquidation separately from actual portfolio values.
6. **M2-F — synthetic integration, replay, and failure tests.** Add isolated temp-database
   fixtures for snapshots, source-use, calendar gating, deterministic ordering, failures,
   reconciliation, serialization, and M1B parity.
7. **M2-G — independent read-only audit.** Review the implementation and evidence for shared
   snapshot integrity, use of the trusted M1B execution path, no look-ahead, fee conservation,
   terminal separation, and boundary compliance. Do not access or modify the normal
   application database during the audit.

## 14. Explicit open risks and acceptance criteria

### Open risks

- **Calendar readiness:** the checked-in NSE candidate is `CONFIGURED`, not `CONFIRMED`.
  The evidence document says it is awaiting independent review and notes that the current
  calendar schema cannot represent the instrument-specific CAS schedule
  (`config/exchange_calendars.json:L12-L15`;
  `docs/EXCHANGE_CALENDAR_NSE_CASH_2025_2026_EVIDENCE.md:L3-L6, L55-L58, L72-L76`).
  Synthetic readiness is not operational calendar readiness.
- **Source rights:** `VERIFIED_PERMITTED` remains a user declaration. It does not prove
  licensing or legal entitlement.
- **M1B boundary:** M1B's deterministic/synthetic correctness does not establish real-data
  validity, profitability, predictive performance, legal source permissions, official
  calendar completeness, or operational readiness. The Phase 4 task records these as
  separate acceptance prerequisites (`tasks/PHASE_04_BACKTESTING.md`, “Acceptance status”
  and “Remaining acceptance blockers”).
- **Quantity provenance:** quantities are fixed per sleeve for the run and must be lot-valid.
  Their exact requested values must be frozen in the manifest/request and included in
  provenance; never infer or silently change them. M2-A also caps the request value at the
  signed 64-bit maximum (`2^63 - 1`) for integer interoperability; this does not establish
  the later instrument lot-size check.
- **Daily marks:** carry-forward age and the maximum allowable gap are not bounded by the
  approved decisions. Any additional cap would be a new policy decision; until set, carry is
  allowed only across calendar-confirmed non-trading days, never missing expected sessions.
- **Failure reporting:** public validation exception type and whether all qualification
  reasons or the first stable reason are returned remain to be finalized, as stated above.
- **No portfolio benchmark:** M1B's benchmark is a fixed-quantity one-instrument
  buy-and-hold result, not a portfolio-level equal-sleeve benchmark. M2 v1 does not claim
  an aggregate benchmark.
- **Snapshot boundary:** a SQLite transaction snapshots database reads, not changes to
  external calendar or source files. M2 must bind file hashes and verify the approved
  calendar/implementation content has not changed during qualification and execution.

### M2 v1 acceptance criteria

M2 v1 is engineering-complete only when:

1. Allocation is exact to the paise, deterministic by frozen universe order, and conserves
   total initial capital.
2. Every member is frozen and qualified against one read-only database snapshot and one
   approved calendar instance before any sleeve executes.
3. Every sleeve uses the trusted M1B qualification/simulation path. No second simulator,
   fill model, fee calculation, slippage rule, or tick-rounding rule is introduced.
4. The universe is active NSE cash EQUITY only; candles are daily; each quantity satisfies
   its lot size; and common cost/participation assumptions are recorded.
5. Any missing expected-session price blocks the portfolio. Holiday carry is allowed only
   with confirmed coverage and an explicit stale mark.
6. Sleeve actual cash, positions, fills, fees, and equity reconcile to M1B without
   double-counting costs. Portfolio totals equal the sum of sleeve totals.
7. Hypothetical liquidation is separately reported and does not mutate actual execution
   records or actual equity.
8. Result and provenance serialization is immutable, deterministic, and replay-bound to the
   same manifest, data, policy, calendar, and implementation fingerprints.
9. Hand-calculated unit tests, isolated synthetic integration tests, and existing M1B
   regression tests pass, followed by the independent M2-G read-only audit.

Engineering acceptance does not upgrade the calendar candidate, verify source rights, or
establish real-market correctness or profitability. This specification records the
approved design and acceptance criteria; implementation, independent review, operational
readiness, empirical validation, and authorization remain distinct statuses.
