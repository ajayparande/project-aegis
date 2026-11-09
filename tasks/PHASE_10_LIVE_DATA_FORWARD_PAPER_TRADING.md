# Phase 10 — Live-Data Forward Paper Trading

## Goal
Run the approved strategy and paper-execution system against current permitted market data for 30–60 trading days, without placing real broker orders or risking real trading capital.

This is forward testing, not historical backtesting. The objective is to measure behaviour under actual market conditions and collect trustworthy evidence before any live-trading decision.

## Prerequisites
- Historical data, calendar confidence, and data-quality checks are implemented to the required level.
- Strategy specifications and backtesting are reproducible.
- Paper broker, portfolio ledger, risk checks, and reconciliation are implemented and tested.
- AI agent tools are permissioned; AI cannot directly submit orders or change risk limits.
- A live-data source has been approved for the intended use after checking provider terms, exchange licensing, entitlements, rate limits, and costs.

## Tasks
- [ ] Integrate an approved read-only market-data feed; do not add broker order placement.
- [ ] Validate feed timestamps, exchange timezone, sequence/continuity where available, stale data, reconnects, and missing updates.
- [ ] Run strategies automatically on configured instruments and intervals during their valid sessions.
- [ ] Route signals only to the paper broker.
- [ ] Simulate order lifecycle, fills, gaps, spreads, slippage, partial/unfilled orders, brokerage, taxes, and other applicable charges using documented assumptions.
- [ ] Prevent hindsight fills: a signal must not be filled at a price that was only known after the decision.
- [ ] Track virtual capital, cash, positions, exposure, realised/unrealised P&L, and all simulated costs.
- [ ] Record every signal, recommendation, decision, simulated order, fill, exit, rejection, and strategy/configuration version.
- [ ] Record AI recommendations separately from deterministic strategy signals and paper orders, including whether recommendations were accepted or rejected.
- [ ] Support multiple strategies with separate virtual portfolios or clearly isolated allocations and comparable starting assumptions.
- [ ] Produce daily, weekly, and full-period reports.
- [ ] Compare strategies against suitable benchmarks (for example, Nifty 50 where appropriate) and report limitations.
- [ ] Measure CPU, memory, disk, feed latency, processing latency, missed cycles, uptime, crashes, and recovery on the development laptop.
- [ ] Test internet interruption, provider outage, stale feed, process restart, system restart, database errors, duplicate events, and reconciliation failures.
- [ ] Preserve immutable experiment settings during the observation period. Proposed changes must create a new strategy/experiment version rather than silently altering the running experiment.
- [ ] Export reports to a reviewable format such as CSV/XLSX without making spreadsheets the authoritative transaction ledger.
- [ ] Verify that the selected data provider permits automated strategy evaluation, storage, derived data, and report export for the intended use.
- [ ] Define the authoritative exchange-session calendar and behaviour for holidays, special sessions, and unscheduled closures.
- [ ] Verify that restart recovery resumes the same experiment without duplicating signals, orders, fills, or observation intervals.
- [ ] Define minimum data-quality and observation-coverage thresholds before starting the 30–60 trading-day measurement period.

## Evaluation metrics
- Net simulated P&L after itemised estimated costs.
- Gross profit, gross loss, profit factor, expectancy, win rate, and average win/loss.
- Maximum drawdown, volatility, exposure, turnover, and time in market.
- Benchmark-relative performance where meaningful.
- Slippage and fill-model sensitivity.
- Results by instrument, strategy, interval, and market regime where sample size permits.
- Data availability, stale-feed events, execution simulation errors, uptime, and recovery performance.
- AI recommendation quality and its measurable contribution, assessed separately from strategy performance.

## Acceptance criteria
- Complete 30–60 trading days of observation, with actual observation coverage and interruptions reported. A calendar-day count does not satisfy this requirement.
- No real broker orders are possible through the paper-trading runtime.
- Every simulated trade is traceable to its data, signal, strategy version, configuration, simulated order, and fill assumptions.
- Costs and fill assumptions are disclosed; optimistic assumptions are stress-tested.
- Daily and full-period reports reconcile to the paper ledger.
- Missing data, stale data, uncertain calendar status, and reconciliation errors fail closed for new entries.
- Laptop resource and reliability measurements demonstrate whether the local machine can sustain the tested workload.
- Positive simulated P&L alone is not a pass. Statistical limitations, drawdown, costs, benchmark comparison, and operational reliability must be reviewed.
- No automatic transition to live trading is permitted after this phase.
- Observation days count only when the configured strategy, instruments, data feed, and paper-execution pipeline were operational for the required session coverage.
- The observation period is restarted or explicitly qualified if a material strategy, configuration, data-source, or execution-model change invalidates comparability.
- All exported reports preserve experiment identifiers and clearly distinguish estimated values from actual market or broker transactions.
