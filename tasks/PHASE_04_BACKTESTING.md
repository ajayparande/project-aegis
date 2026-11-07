# Phase 4 — Backtesting

## Goal
Build a realistic chronological simulator.

## Tasks
- [ ] Define order and fill model.
- [ ] Implement cash, positions, fees and portfolio accounting.
- [ ] Add configurable brokerage, taxes/charges and slippage assumptions.
- [ ] Model next-eligible execution rather than same-bar hindsight.
- [ ] Add liquidity/volume constraints and unfilled/rejected order handling where possible.
- [ ] Calculate returns, drawdown, volatility, Sharpe caveats, profit factor, expectancy and turnover.
- [ ] Add tests for accounting, timing, costs and look-ahead prevention.

## Acceptance criteria
- Hand-calculated fixtures match simulator output.
- Costs are included and itemised.
- No future information is available to a decision at the simulated time.
- Same version/data/configuration reproduces the same result.
