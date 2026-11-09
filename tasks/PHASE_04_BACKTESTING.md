# Phase 4 — Backtesting

## Goal
Build a realistic, deterministic chronological simulator to evaluate strategies before forward paper trading.

## Tasks
- [ ] Define order types, order lifecycle, and fill model.
- [ ] Implement cash, positions, portfolio accounting, and reconciliation.
- [ ] Add configurable brokerage, taxes/charges, spread, and slippage assumptions.
- [ ] Model next-eligible execution rather than same-bar hindsight.
- [ ] Add liquidity/volume constraints and unfilled/rejected order handling where feasible.
- [ ] Calculate returns, drawdown, volatility, Sharpe caveats, profit factor, expectancy, and turnover.
- [ ] Compare results with appropriate benchmarks.
- [ ] Add tests for accounting, timing, costs, partial/unfilled orders, and look-ahead prevention.
- [ ] Preserve reproducible experiment inputs and outputs.

## Acceptance criteria

Phase 4 acceptance remains pending; completion of these scoped remediation checks does not
constitute milestone acceptance.

- Hand-calculated fixtures match simulator output.
- Costs are included, itemised, configurable, and appropriate to the instrument and order type.
- No future information is available to a decision at simulated time.
- Same strategy version, data, and configuration reproduce the same result.
- Backtest results are labelled simulated research results, not evidence of guaranteed future returns.
- Every contributing historical import must have an explicit `VERIFIED_PERMITTED` source-use declaration; undeclared or prohibited imports block execution.
- Runs must snapshot the registered instrument/tick inputs and permission provenance; legacy results without those snapshots remain visibly incomplete.
- A strategy's minimum required candle history must be met before signal generation.
- Public calculations must be independent of ambient Decimal precision/rounding. Backtest listings must validate canonical configuration hashes, snapshot field types and references, and report malformed registrations with experiment-specific diagnostics while preserving legacy typed backtests and excluding unrelated null-strategy records.

## Current baseline assumptions

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
