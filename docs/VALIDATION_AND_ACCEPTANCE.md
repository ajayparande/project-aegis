# Validation and Acceptance Standard

## Software quality
- Unit tests for calculations, data validation, portfolio accounting and order state.
- Integration tests using fixtures/mocks; never place real broker orders in tests.
- Static typing/linting/formatting and dependency vulnerability checks.
- Reproducible environment and documented setup.
- CI must pass before merge.

## Historical CSV ingestion acceptance
- Migrate a version 4 database transactionally to version 5 without changing existing instruments, imports, legacy market_data rows, or quality issues.
- Store newly imported OHLC prices only as canonical decimal text in historical_candles; do not write imported prices to legacy REAL columns.
- Reject non-positive or malformed prices, invalid OHLC relationships, invalid volume, naive/misaligned timestamps, unsupported intervals, unknown instruments, and inactive instruments.
- Enforce tick alignment only when an exact, non-legacy tick size is present; otherwise report that tick validation was not applied.
- Make imports idempotent through the candle identity constraint. Preserve previously accepted candles on duplicate or overlapping imports and report all duplicates.
- Persist checksum, filename basename, instrument identity, interval, timestamps, status, row counts, rejected-row context, and import errors.
- Roll back candle and row-issue writes on unexpected batch failure while retaining a failed import record.
- Exercise local CSV fixtures and dashboard tests only with temporary database and log paths.

This milestone does not claim missing-session, exchange-calendar, market-hours-close, or stale-feed validation. Those checks require explicit calendar/source policy before they can be acceptance criteria.

## Quantitative research quality
Every candidate report must include:
- Data source, licence/terms status, date range, interval and universe definition.
- Code commit, strategy version and parameter set.
- Chronological train/validation/test periods.
- Net returns after stated costs and slippage assumptions.
- Maximum drawdown, volatility, Sharpe (with caveats), profit factor, expectancy, turnover and trade count.
- Benchmark comparison.
- Walk-forward and parameter sensitivity results.
- Liquidity/capacity and execution limitations.
- Known biases and missing data limitations.

## Mandatory rejection conditions
Reject or rerun if:
- Look-ahead or leakage is detected.
- Costs are omitted or implausible.
- Dataset provenance or use rights are unclear.
- Results depend on a tiny number of trades or one narrow period without justification.
- Strategy fails untouched out-of-sample validation.
- Small parameter changes destroy performance.
- Paper results materially diverge from assumptions and the reason is unresolved.
- Reproducibility fails.

## No universal magic thresholds
Acceptance thresholds must be defined before evaluating each experiment and justified for the instrument/timeframe. Do not choose thresholds after seeing results to make a strategy pass.

## Reporting
AI may explain results, but all numerical metrics must be calculated by deterministic code and traceable to experiment records. Reports must label hypotheses, estimates and observed results separately.
