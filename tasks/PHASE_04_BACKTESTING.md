# Phase 4 — Backtesting

## Goal
Build a realistic, deterministic chronological simulator to evaluate strategies before forward paper trading.

## Tasks

- [x] Define order types, order lifecycle, and fill model.
- [x] Implement cash, positions, portfolio accounting, and reconciliation.
- [x] Add configurable brokerage, taxes/charges, spread, and slippage assumptions.
- [x] Model next-eligible execution rather than same-bar hindsight.
- [x] Add liquidity/volume constraints and unfilled/rejected order handling where feasible.
- [x] Calculate returns, drawdown, volatility, Sharpe caveats, profit factor, expectancy, and turnover.
- [x] Compare results with appropriate benchmarks.
- [x] Add tests for accounting, timing, costs, partial/unfilled orders, and look-ahead prevention.
- [x] Preserve reproducible experiment inputs and outputs.

## Acceptance status

Engineering implementation: **complete.** Automated correctness tests: **passing** (synthetic fixtures only).

Phase 4 milestone acceptance remains **pending** until the outstanding operational prerequisites below are satisfied; passing synthetic tests do not constitute milestone acceptance.

### Acceptance criteria (verified against synthetic fixtures)

- Hand-calculated fixtures match simulator output (entry/exit round trip with itemised costs; see `test_entry_exit_round_trip_with_costs_hand_calculated`).
- Costs are included, itemised, configurable, and appropriate to the instrument and order type. All charge rates are configurable user assumptions, not an official or broker-verified schedule.
- No future information is available to a decision at simulated time (causal next-bar execution; prior-bar volume liquidity proxy; see `test_causal_execution_timeline_and_holding_updates` and `test_volume_liquidity_proxy_uses_prior_completed_bar_not_execution_bar`).
- Same strategy version, data, and configuration reproduce the same result (determinism tests; `test_public_run_backtest_is_independent_of_decimal_context`).
- Backtest results are labelled simulated research results, not evidence of guaranteed future returns.
- Every contributing historical import must have an explicit `VERIFIED_PERMITTED` source-use declaration; undeclared, `UNVERIFIED`, `PROHIBITED`, missing, malformed, or empty provenance blocks execution (`test_backtest_fails_closed_for_unverified_and_prohibited_imports`, `test_mixed_source_dataset_requires_every_contributing_import_to_be_permitted`, `test_backtest_blocks_missing_source_import_provenance`).
- Runs snapshot the registered instrument/tick inputs and permission provenance; legacy results without those snapshots remain visibly incomplete (`list_backtest_runs` `LEGACY_INCOMPLETE` handling).
- A strategy's minimum required candle history must be met before signal generation.
- Public calculations are independent of ambient Decimal precision/rounding. Backtest listings validate canonical configuration hashes, snapshot field types and references, and report malformed registrations with experiment-specific diagnostics while preserving legacy typed backtests and excluding unrelated null-strategy records.
- Blocked runs cannot produce completed trades or misleading successful results; completed runs persist `provenance_status == COMPLETE` and full `source_imports` provenance.

### Current baseline assumptions

The initial simulator is limited to long-only NSE cash equities, a single open position, and
fixed integer order quantity. Completed-candle signals submit market orders for the next
eligible candle open. Orders use an all-or-none capacity check based on the prior completed
candle's volume, never the execution candle's eventual volume.

All charge rates are configurable user assumptions, not an official or broker-verified
schedule. Brokerage is per-order turnover times `brokerage_rate`; a positive
`brokerage_cap_per_order` limits that amount, while a zero cap means uncapped. A zero
brokerage rate produces no brokerage regardless of the cap. Exchange and regulatory charges
apply to both sides, transaction tax uses separate buy/sell rates, stamp duty applies to buys,
and GST is applied to rounded brokerage, exchange and regulatory charges. Each charge is
rounded to INR 0.01 using `ROUND_HALF_UP`.

The strategy's final open position remains marked to market, with estimated liquidation costs
reported separately. The same-instrument buy-and-hold benchmark exits at the final close;
relative performance therefore compares the strategy's net-liquidation return against that
liquidated benchmark return. This baseline does not provide index benchmarking, partial fills,
shorting, margin, corporate-action adjustment, or a full dashboard workflow.

The simulator is research-only. It does not connect to a broker or place orders. Source-use
status is an explicit user declaration, not a legal determination or an automatic licence
check. The checked-in exchange calendar has no confirmed holiday/session coverage, so
calendar-dependent runs remain blocked until an administrator supplies and verifies coverage.

### Remaining acceptance blockers (require authoritative external evidence)

- [ ] Authoritative NSE/BSE exchange-calendar coverage (holidays, sessions, coverage range) before claiming `CONFIRMED` calendar coverage; calendar-dependent backtests remain fail-closed until verified coverage is supplied.
- [ ] A real historical dataset from a source with verified permitted use; synthetic `VERIFIED_PERMITTED` declarations in tests are fixtures only, not licensing evidence.
- [ ] Source entitlement/licensing evidence for any external market-data source.
- [ ] Real-data validation of the simulator against a permitted historical dataset (synthetic tests verify software behavior, not predictive performance).

The initial simulator is limited to long-only NSE cash equities, a single open position, and
fixed integer order quantity. Completed-candle signals submit market orders for the next
eligible candle open. Orders use an all-or-none capacity check based on the prior completed
candle's volume, never the execution candle's eventual volume.

All charge rates are configurable user assumptions, not an official or broker-verified
schedule. Brokerage is per-order turnover times `brokerage_rate`; a positive
`brokerage_cap_per_order` limits that amount, while a zero cap means uncapped. A zero
brokerage rate produces no brokerage regardless of the cap. Exchange and regulatory charges
apply to both sides, transaction tax uses separate buy/sell rates, stamp duty applies to buys,
and GST is applied to rounded brokerage, exchange and regulatory charges. Each charge is
rounded to INR 0.01 using `ROUND_HALF_UP`.

The strategy's final open position remains marked to market, with estimated liquidation costs
reported separately. The same-instrument buy-and-hold benchmark exits at the final close;
relative performance therefore compares the strategy's net-liquidation return against that
liquidated benchmark return. This baseline does not provide index benchmarking, partial fills,
shorting, margin, corporate-action adjustment, or a full dashboard workflow.

The simulator is research-only. It does not connect to a broker or place orders. Source-use
status is an explicit user declaration, not a legal determination or an automatic licence
check. The checked-in exchange calendar has no confirmed holiday/session coverage, so
calendar-dependent runs remain blocked until an administrator supplies and verifies coverage.

Metrics use closed trades only for win/loss counts, win rate, profit factor, and expectancy.
Winners have positive net P&L after fees; breakeven trades count in the win-rate denominator
but are neither winners nor losers. Gross profit/loss sum pre-fee closed-trade P&L. Profit
factor is net winning P&L divided by absolute net losing P&L and is undefined when there are
no net losses. Daily returns use the last recorded exchange-local equity mark each trading
date. Annualized daily volatility is sample standard deviation times `sqrt(252)`; Sharpe is
mean daily return divided by sample standard deviation times `sqrt(252)`, with zero
risk-free rate. Volatility and Sharpe are undefined with fewer than two daily observations,
and Sharpe is also undefined at zero variance.
