# Phase 4 Independent Technical and Financial Audit

**Review type:** Strictly read-only, independent verification\
**Project:** Project Aegis\
**Branch:** `main`\
**Reference commit:** `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b` (`ecbd7da`)\
**Execution mode:** `RESEARCH_ONLY`\
**Final verdict:** **REJECT — FIXES REQUIRED**

## A. Executive verdict

**REJECT — FIXES REQUIRED.**

The simulator’s causal execution sequence, Decimal-based cash and position accounting, cost treatment, and data/calendar gates were verified by code inspection and isolated numerical checks. No Critical or High-severity accounting, timing, or safety defect was found.

Phase 4 should not be accepted yet. A public backtest can complete successfully when the available candles are insufficient for the strategy lookback, and registering a backtest can make the Strategy Lab listing fail. The exact instrument tick size used for fills is not archived in the result provenance. Market-data rights are also neither recorded nor enforced, which prevents treating results from real data as fully auditable. The test suite additionally lacks several useful causal and reproducibility regressions.

## B. Repository state at audit

- Branch: `main`
- HEAD: `ecbd7dad9c91b48b996d55c51cc833bd1dc23b5b`
- Upstream: `origin/main`, at the same commit; no ahead/behind commits.
- Modified tracked files:
  - `AGENTS.md`
  - `docs/DEVELOPMENT_ROADMAP.md`
  - `tasks/PHASE_04_BACKTESTING.md`
  - `tests/test_project_scaffold.py`
- Untracked files:
  - `src/aegis/backtesting.py`
  - `tests/test_backtesting.py`

This is the pre-existing worktree state observed at the audit. The Phase 4 files and scaffold test change were preserved.

## C. Architecture understanding

1. **Instrument Master:** SQLite stores exchange, trading symbol, instrument type, active status, lot size, and tick size. The backtest permits active NSE cash equities and requires an authoritative decimal tick size.
2. **Historical candles:** CSV ingestion stores new OHLC prices as canonical decimal text in `historical_candles`. Candle timestamps denote candle starts and are normalized to UTC.
3. **Import provenance:** Imports record file identity and SHA-256 metadata. Source/license terms are not recorded.
4. **Calendars:** Local JSON supplies exchange/segment sessions, coverage, confidence, holidays, and exceptional sessions. Expected candle starts are derived from this configuration.
5. **Quality gates:** The market-quality service evaluates calendar coverage, completeness, validity, duplicates, session boundaries, and freshness. The backtest requires confirmed, complete, fresh data.
6. **Strategy Lab:** Versioned strategy specifications and experiment snapshots are stored with hashes and relational strategy/hypothesis links.
7. **Signals:** The SMA crossover emits signals at candle close from completed candles; it does not simulate execution.
8. **Backtest configuration:** Backtests are registered as rows in the generic `experiments` table. Their JSON snapshot contains strategy identity/configuration and backtest settings; relational strategy and hypothesis links are retained.
9. **Runs:** Results use the generic `runs` table and explicit run statuses.
10. **Accounting:** Backtesting applies Decimal arithmetic to orders, fees, cash, positions, equity, trades, and metrics.
11. **Dashboard:** The dashboard exposes Strategy Lab signal generation, not backtest registration, execution, or run-history review. A registered backtest also triggers the Strategy Lab listing defect described in F-02.

## D. Phase 4 implementation inventory

| Area | Status | Audit conclusion |
|---|---|---|
| Strategy snapshot, version/hash, and relational-link validation | **Verified** | Tampered or broken strategy references are rejected. |
| Configuration and fee validation | **Verified** | Invalid, non-finite, negative, and inappropriate values are rejected, including booleans as quantities and unrepresentable sub-paisa starting cash. |
| Causal signal-to-order processing | **Verified** | Signals queue orders; fills occur at the next eligible candle open. |
| Liquidity limits | **Verified** | Capacity uses prior completed-bar volume, not execution-bar volume. Zero prior volume yields zero capacity. |
| Fill prices | **Verified** | Adverse spread/slippage and directional tick rounding are applied. |
| Cash, position, fees, realized/unrealized P&L | **Verified** | Internally consistent for the supported long-only, single-position, all-or-none model. |
| Open positions and final-bar orders | **Verified** | Open positions remain marked; a final-bar signal is not represented as a fictitious fill. |
| Metrics | **Verified** | Closed-trade statistics, daily returns, drawdown, volatility, Sharpe caveats, and net liquidation metrics are implemented. |
| Benchmark | **Verified, limited** | Same-instrument buy-and-hold is implemented; index benchmarking is not. |
| Insufficient strategy history | **Incorrect** | A run can complete with no orders/trades even though the slow lookback cannot be evaluated. |
| Dashboard workflow | **Not implemented** | Backtest UI is explicitly deferred. |
| Production calendar | **Blocked by external prerequisite** | Checked-in configuration has no calendars or verified production coverage. |

## E. Critical financial audit

### Causal execution timing and look-ahead

In `_simulate` (`src/aegis/backtesting.py`, approximately line 553), each candle is processed chronologically:

1. At candle T open, execute any pending order created from an earlier candle’s close.
2. At candle T close, mark equity using T’s close.
3. Evaluate T’s signal and queue an order for the next eligible candle, or record it as unfilled if no next candle exists.

For intraday bars, the recorded execution time is the next candle’s start. Daily bars use the configured session open. This agrees with the candle-start convention in the importer. Signals alone do not mutate cash or holdings. The exit order quantity comes from the held position. A final-bar signal is not filled without a subsequent bar.

The strategy decision uses completed candles. The benchmark and final-period valuations use later observations only for reporting, not for strategy decisions. Code inspection found no future-data use in the event sequence. The test suite does not include a future-candle-perturbation regression (F-05).

### Orders, liquidity, and fills

Orders are market orders with an all-or-none capacity check based on the previous completed candle’s volume and the configured maximum participation. Execution-bar aggregate volume is not used at the open. This avoids hindsight leakage but remains a historical-liquidity proxy, not a fill guarantee.

The reference price is the execution candle open. Half-spread and slippage move the price adversely. Buy fills round up to a tick; sell fills round down. Non-finite/non-positive references, invalid sides, non-positive tick sizes, and non-positive results are rejected.

### Fees and brokerage

In `_fees` and `_calculate_brokerage` (approximately lines 416–449):

- Brokerage is turnover times the configured rate; a positive cap limits it, while a **zero cap means uncapped**.
- Exchange and regulatory charges are turnover-based on both sides.
- Transaction tax uses separate buy/sell rates.
- Stamp duty applies to buys only.
- GST applies to rounded brokerage, exchange, and regulatory charges.
- Each charge is rounded to ₹0.01 using `ROUND_HALF_UP`.

Rates are explicitly user assumptions; the code does not claim that they are current official broker or exchange schedules.

### Independent charged round trip

For starting cash ₹100,000, 10 shares, tick size ₹0.05, spread 2 bps, slippage 5 bps, and the documented default fee assumptions:

**BUY at reference ₹100.00**

- Half-spread: ₹100 × 2 bps ÷ 2 = ₹0.01/share.
- Slippage: ₹100 × 5 bps = ₹0.05/share.
- Raw price: ₹100.06; adverse tick rounding gives **₹100.10**.
- Turnover: ₹100.10 × 10 = **₹1,001.00**.
- Brokerage: ₹1,001 × 0.0003 = ₹0.3003 → **₹0.30**.
- Exchange: ₹1,001 × 0.0000345 = ₹0.0345345 → **₹0.03**.
- Regulatory: ₹1,001 × 0.000001 = ₹0.001001 → **₹0.00**.
- Transaction tax: ₹1,001 × 0.001 = **₹1.00**.
- Stamp duty: ₹1,001 × 0.00015 = ₹0.15015 → **₹0.15**.
- GST: (₹0.30 + ₹0.03 + ₹0.00) × 0.18 = ₹0.0594 → **₹0.06**.
- Total buy charges: **₹1.54**; cash after buy: ₹100,000 − ₹1,001 − ₹1.54 = **₹98,997.46**.

**SELL at reference ₹110.00**

- Half-spread: ₹0.011/share; slippage: ₹0.055/share.
- Raw price: ₹109.934; adverse tick rounding gives **₹109.90**.
- Turnover: ₹109.90 × 10 = **₹1,099.00**.
- Brokerage: ₹1,099 × 0.0003 = ₹0.3297 → **₹0.33**.
- Exchange: ₹1,099 × 0.0000345 = ₹0.0379155 → **₹0.04**.
- Regulatory: ₹1,099 × 0.000001 = ₹0.001099 → **₹0.00**.
- Transaction tax: ₹1,099 × 0.001 = ₹1.099 → **₹1.10**.
- Stamp duty: **₹0.00**.
- GST: (₹0.33 + ₹0.04 + ₹0.00) × 0.18 = ₹0.0666 → **₹0.07**.
- Total sell charges: **₹1.54**; final cash: ₹98,997.46 + ₹1,099 − ₹1.54 = **₹100,094.92**.

Gross P&L is ₹98.00; realized net P&L is **₹94.92**. The independent computation matched the simulator in the audit’s isolated round-trip check.

### Cash, positions, and P&L

Buys debit turnover plus charges; sells credit proceeds less charges. The supported model is long-only, one position, fixed integer quantity, all-or-none, with no shorting, margin, or partial exits. Cash affordability and held quantity are checked. Fees are counted once at the corresponding fill and entry fees are included when calculating closed-trade net P&L.

An independent zero-cost open-position check—buy 2 shares at ₹100 and mark at ₹105—gave cash ₹800, market value ₹210, equity ₹1,010, and unrealized gross P&L ₹10. The position remained open. The final equity includes marked holdings; estimated liquidation costs and net liquidation value are separately reported. The engine does not silently liquidate an open position.

A final-bar exit with no subsequent eligible candle is recorded as unfilled, not as a completed trade.

### Metrics

Win/loss counts, win rate, profit factor, and expectancy use closed trades. Gross profit/loss use pre-fee trade P&L; net winners/losers use after-fee P&L. Breakeven trades remain in the win-rate denominator but are neither winners nor losers. Undefined values are `None`, including metrics with no trades/no losses or insufficient observations.

Daily returns use the last recorded exchange-local equity mark per trading date. Annualized volatility uses sample standard deviation × √252. Sharpe uses mean daily return divided by sample standard deviation × √252 and assumes zero risk-free rate. Both are undefined with fewer than two daily observations; Sharpe is also undefined at zero variance.

### Benchmark methodology

The benchmark is fixed-quantity, same-instrument buy-and-hold: entry at the first bar open, exit at the final bar close, with the same configured costs and liquidation convention. Relative performance compares the strategy’s net-liquidation return against the liquidated benchmark return.

This is **not** an index benchmark. There is no separate benchmark instrument, index-data alignment, or missing benchmark-candle policy. The benchmark does not establish support for trading index instruments.

## F. Database integrity and provenance

The audit observed schema version 6; no migration is needed for the current engine. Registration retains strategy and hypothesis foreign-key relationships rather than nulling them to fit generic tables. Writes use parameterized SQL and explicit transactions; failed writes roll back, connections close, and analytical listings use read-only connections. Blocked runs retain a reason; failed runs retain an error and re-raise it.

Run provenance includes strategy/configuration hashes, dataset and source-file hashes, calendar hashes, cost assumptions, and implementation hashes. A dirty worktree is identified as `UNCOMMITTED` rather than attributed to a clean commit. Hashes provide content-change detection but do not by themselves preserve the underlying inputs.

The exact decimal tick size used to round fills is read from the Instrument Master but is not stored in the backtest configuration or run result metadata. If the instrument record changes, the original tick size cannot be reconstructed from that result (F-03).

## G. Market-data quality and exchange calendar

The backtest gate requires full `CONFIRMED` calendar coverage, complete data, 100% completeness, fresh data, valid candles, and no duplicate stored identities. A failed gate records a `BLOCKED` run and reason. Tests use synthetic calendars in temporary directories; these are not evidence of official production coverage.

The checked-in `config/exchange_calendars.json` has an empty `calendars` list. Consequently, the current checked-in production configuration cannot support a confirmed-calendar backtest. Verified NSE/BSE coverage remains an external prerequisite; no holiday data was invented.

File hashes are recorded for imported data, but license/use-right status is not recorded or enforced. The validation standard requires reporting source and license/terms status and rejects results when use rights are unclear. A run can therefore complete without establishing that real input data is licensed for the intended research use (F-04).

## H. Test quality and validation

Audit-time validation, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe src
git diff --check
```

Actual results:

- Pytest: **198 passed in 21.61s**.
- Ruff check: **All checks passed**.
- Ruff format check: **40 files already formatted**.
- Mypy: **Success: no issues found in 9 source files**.
- `git diff --check`: passed; Git printed line-ending conversion warnings for existing modified files.

The reported 198 tests consist of the previously reported 173 tests plus 25 tests in `tests/test_backtesting.py`. The existing scaffold test was strengthened from a hard-coded assertion to checking `Settings.from_env()` is actually `RESEARCH_ONLY`.

Backtesting tests meaningfully cover fee/fill calculations, causal queueing, prior-bar liquidity, rejections, final-bar orders, open positions, benchmark behavior, strategy links, and a public end-to-end run. Many detailed accounting assertions invoke `_simulate` directly. The public end-to-end case ends with an open position rather than exercising a charged closed round trip.

Missing test evidence includes future-candle invariance, repeated public-run determinism, independence from an altered caller Decimal context, and several metric/zero-volume boundaries. There is no public-API insufficient-lookback regression (F-01, F-05).

## I. Findings

No Critical or High findings were identified. There were no Low-severity findings. Findings are ordered by severity; all are Medium.

### F-01 — Insufficient strategy history can be reported as a successful run

- **Severity:** Medium
- **Confidence:** 10/10
- **Location:** `src/aegis/backtesting.py`, `run_backtest` around line 1093; `src/aegis/strategy_lab.py`, `generate_sma_crossover_signals` around line 531.
- **Behavior:** The engine does not verify that loaded candles meet the strategy’s slow lookback. The signal generator starts evaluating at index `slow_window`.
- **Reproduction:** In a temporary database with six complete candles and `slow_window=99`, the public backtest API returned `COMPLETED`, with zero orders and trades. The benchmark still produced a nonzero result.
- **Impact:** A user cannot distinguish a valid no-signal experiment from a run that lacked enough history to compute the strategy.
- **Recommendation:** Before simulation, block with an explicit insufficient-history reason when fewer than `slow_window + 1` completed candles are available.
- **Required regression:** Public-API test that asserts `BLOCKED` for insufficient history, plus a positive test at the minimum sufficient history.

### F-02 — Registering a backtest breaks the Strategy Lab experiment listing

- **Severity:** Medium
- **Confidence:** 10/10
- **Location:** `src/aegis/backtesting.py`, `register_backtest` around line 297; `src/aegis/strategy_lab.py`, `list_strategy_experiments` around line 687; `app.py` around line 582.
- **Behavior:** Backtest registration keeps a non-null `strategy_id`, but stores `strategy_snapshot` rather than the top-level `strategy_spec` expected by the Strategy Lab listing. The listing selects every row with non-null `strategy_id` and raises `StrategyLabError: Experiment 2 has malformed configuration.` on the backtest row.
- **Reproduction:** Confirmed in an isolated temporary database by registering one strategy experiment, registering a backtest, and calling `list_strategy_experiments`.
- **Impact:** Once a backtest is registered, Strategy Lab listing and its dashboard view can become unavailable.
- **Recommendation:** Exclude backtest-typed experiments from the Strategy Lab query or explicitly parse supported experiment types.
- **Required regression:** Test Strategy Lab listing with both a strategy experiment and a backtest registration, including the dashboard-facing listing path.

### F-03 — Tick size used for fills is absent from run provenance

- **Severity:** Medium
- **Confidence:** 9/10
- **Location:** `src/aegis/backtesting.py`, tick-size lookup around line 1041 and run metadata around line 1129.
- **Behavior:** Fill prices depend on the exact decimal tick size read from the Instrument Master. The persisted configuration/result records the instrument identity but not the tick size used.
- **Impact:** If instrument metadata changes later, an auditor cannot reconstruct the original fill-rounding input from the run record.
- **Recommendation:** Persist the exact tick size and relevant instrument metadata with each run.
- **Required regression:** Verify the archived tick size remains available after the current Instrument Master record changes.

### F-04 — Dataset use rights are neither recorded nor enforced

- **Severity:** Medium
- **Confidence:** 9/10
- **Location:** `src/aegis/database.py` source/import schema around lines 9 and 85; `docs/VALIDATION_AND_ACCEPTANCE.md` around line 43.
- **Behavior:** Imports retain file hashes, but the source schema has no license/terms status and `run_backtest` does not gate on one.
- **Impact:** A run can be marked complete without establishing that the source is permitted for research. This is a data-governance issue, not a live-trading risk.
- **Recommendation:** Verify source permissions before using real datasets. Define a source-rights status and block or clearly label runs on unverified data.
- **Required regression:** Test both verified and unverified source-rights states.

### F-05 — Causal and reproducibility regression tests are missing

- **Severity:** Medium
- **Confidence:** 9/10
- **Location:** `tests/test_backtesting.py`, causal timing tests around line 368.
- **Behavior:** Code tracing supports causal processing, but the suite does not prove that perturbing future candles leaves earlier signals/orders/equity unchanged, that identical public runs reproduce identical results, or that outcomes are independent of the caller’s Decimal context. Several metric and zero-volume boundaries are also not directly covered.
- **Impact:** Important properties are supported by inspection and targeted examples but not guarded against future regression.
- **Recommendation:** Add integration tests for future-candle invariance, repeated-run determinism, Decimal-context independence, zero-volume rejection, and the F-01 insufficient-history case.

## J. Documentation and dashboard

`tasks/PHASE_04_BACKTESTING.md` accurately documents the limited baseline: no index benchmark, partial fills, shorting, margin, corporate-action adjustment, or complete dashboard workflow. The phase-status documentation identifies Phase 4 as in progress.

Users can register Strategy Lab experiments and generate research signals. They cannot register/run a backtest or inspect backtest history in the dashboard. The task document explicitly defers the full dashboard workflow, so it is not independently an acceptance blocker under the current engine scope. F-02 remains a backend integration defect because backtest registration breaks the existing Strategy Lab listing.

## K. Security and operating mode

`RESEARCH_ONLY` remains enforced by configuration, and the dashboard displays a research-only warning. The audit found no broker connector, broker credentials, live-order function, arbitrary generated-code execution, paid service, or new dependency. SQL uses bound parameters. No real trading capability was introduced.

## L. Acceptance checklist

| Criterion | Status | Explanation |
|---|---|---|
| Hand-calculated accounting matches simulator | **PASS** | Independent charged round trip and open-position arithmetic reconcile. |
| Costs are itemized and configurable | **PASS** | Charges, rounding, and zero-cap semantics are explicit; rates are assumptions. |
| Signals execute only at the next eligible open | **PASS** | Confirmed by event-flow trace and timing tests. |
| No future candle affects an earlier execution | **PASS** | Code trace supports causal behavior; a perturbation regression remains missing (F-05). |
| Liquidity constraints avoid execution-bar volume hindsight | **PASS** | Prior completed-bar volume is used and directly tested. |
| Accounting, open positions, and final-bar orders are handled honestly | **PASS** | Open positions remain marked; final-bar orders remain unfilled. |
| Metrics use documented definitions and undefined values | **PASS** | Null handling and annualization assumptions match implementation. |
| Benchmark comparison | **PASS** | Same-instrument buy-and-hold exists; index benchmarking does not. |
| Same strategy/data/config reproducibility | **BLOCKED** | Tick size is not archived (F-03); repeatability lacks a regression test (F-05). |
| Insufficient strategy history is rejected | **FAIL** | It currently produces a successful zero-order/zero-trade run (F-01). |
| Backtest registration preserves Strategy Lab behavior | **FAIL** | The Strategy Lab listing raises on a registered backtest (F-02). |
| Dataset license/terms are established | **BLOCKED** | Rights are not stored or gated (F-04). |
| Production confirmed calendar coverage | **BLOCKED** | The checked-in calendar configuration is empty. |
| Tests and static validation | **PASS** | 198 tests and all required static checks passed. |
| Backtest dashboard workflow | **NOT IN SCOPE** | Explicitly deferred by the Phase 4 task document. |

## M. Recommended remediation sequence

### Required before Phase 4 acceptance

1. Add an insufficient-lookback guard and public regression test.
2. Exclude backtest registrations from the Strategy Lab listing and test both types together.
3. Persist the exact tick size used for each run and test provenance after instrument metadata changes.
4. Add future-candle-invariance and repeated-run tests; cover Decimal-context independence and zero-volume rejection.
5. Before accepting results from actual data, establish and record source-use permissions and verified exchange-calendar coverage. Do not invent calendar or license status.

### Optional improvements

- Add an index benchmark with explicit date alignment and missing-candle behavior.
- Add a dashboard for backtest registration, execution, and run review.
- Consider walk-forward validation, parameter sensitivity, corporate-action handling, and richer execution models under separately scoped requirements.

## N. Final repository integrity at audit

The audit was strictly read-only: no repository files were edited, deleted, renamed, reformatted, committed, or pushed. No migration was run against the normal database.

The normal database SHA-256 was `0AF4CE61875B3B65DBB1D2F6C355787F39123F0C00E006070CC5145FA7AFD890`; the backup database had the same hash. The log SHA-256 was `73E76DC2E1BB7688A03473FD6D81136F529FD06752452F5879085D7CA2C2E07C`. HEAD and the original tracked/untracked worktree state were unchanged. Reproduction checks used temporary isolated data.
