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
- Hand-calculated fixtures match simulator output.
- Costs are included, itemised, configurable, and appropriate to the instrument and order type.
- No future information is available to a decision at simulated time.
- Same strategy version, data, and configuration reproduce the same result.
- Backtest results are labelled simulated research results, not evidence of guaranteed future returns.
