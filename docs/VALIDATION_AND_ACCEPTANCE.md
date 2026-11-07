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

## Exchange calendar and market-data quality acceptance
- Load local exchange-and-segment-specific calendar configuration without external dependencies; an empty calendar configuration is explicitly unavailable.
- Do not infer official holidays or claim complete coverage without a declared, administrator-verified source and date range.
- Calculate expected 1m, 5m, 15m, 1h, and 1d candle starts within configured sessions, breaks, holidays, and exceptional sessions while preserving Stage 3 timestamp labels.
- Exclude in-progress intervals and handle clipped final intervals at early closes deterministically.
- Analyze stored candle data read-only; report missing, unexpected, invalid, duplicate, and previously rejected/imported rows without changing candles or provenance.
- Require an explicit timezone-aware `as_of`; use configurable completed-candle gap thresholds for freshness.
- Return no completeness percentage when calendar coverage is unavailable, provisional, partial for the requested range, or has a zero expected denominator.
- Run dashboard tests with isolated temporary database and log paths.

Calendar results remain limited by administrator-supplied coverage and source verification. Calendar configuration does not establish external data permissions or official completeness by itself.

## Strategy Lab reproducibility and auditability
- Migrate schema version 5 to 6 additively by adding nullable experiment configuration hash and origin columns; retain all existing rows and leave legacy hashes unset until the first structurally valid run attempt (which may later be blocked by the data-quality gate).
- Hash the canonical full experiment configuration separately from its JSON and verify it, plus internal identity consistency, before every run. A first-run legacy backfill establishes a baseline only from that point forward and must be labelled as such.
- Persist SHA-256 hashes for the configuration, strategy specification and implementation, market-quality implementation, calendar input and effective session rules, dataset, and source files. Record the Git commit only for a clean worktree; explicitly identify unavailable or uncommitted revision state.
- A hash can detect changed content when the stored hash is unchanged; it cannot protect against a privileged actor changing both, nor reconstruct files, calendar inputs, or market data that have been lost.
- SMA uses a local Decimal precision of `max(28, integer_places + fractional_places + digits_in_input_count + 12)` significant digits and explicit `ROUND_HALF_EVEN`. Non-terminating averages are finite Decimal approximations at this precision; calculations do not quantize to tick size.
- Do not automatically fail a `RUNNING` record based only on age. Reconcile manually only after confirming its owning process has stopped; update only the selected run ID conditional on its status remaining `RUNNING`, and record the manual reconciliation in its error message.
- Changed strategy rules require a new version, written rationale, independent technical review, mathematical and regression testing, confirmed data-quality prerequisites, human approval, and a traceable link to the prior version before registration.

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
