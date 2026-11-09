# Validation and Acceptance Standard

## Software quality
- Unit tests for calculations, data validation, portfolio accounting and order state.
- Integration tests using fixtures/mocks; never place real broker orders in tests.
- Static typing/linting/formatting and dependency vulnerability checks.
- Reproducible environment and documented setup.
- CI must pass before merge.

## Phase 5 research-library acceptance
- Keep candlestick detection read-only and separate from strategy generation, backtesting, brokers, and execution. Outputs are descriptive research artifacts, never BUY/SELL instructions.
- Reject incomplete bars, non-finite/non-positive OHLC values, OHLC values outside the candle range, malformed or timezone-naive timestamps, duplicate UTC instants, invalid volume, and invalid configuration.
- Compare timestamps by UTC instant while preserving source timestamp text. Sort only through the explicit normalization option.
- Run all arithmetic in a documented local Decimal context and compare complete serialized result collections under multiple caller contexts.
- Confirm pivots only after the configured right-side window. Build HH/HL/LH/LL and trend summaries only from confirmed swings; report event and confirmation timestamps separately.
- Form support/resistance only from multiple confirmed swing touches. Breakout, retest, failure, and double-top/bottom outputs must use previously confirmed levels and completed closes.
- Require the actual Mother Bar → Inside Bar → false break → close-back-inside sequence for Fakey. Require complete, session-scoped expected bars for opening-range results; do not infer exchange sessions or holidays.
- Version every detector definition and include the exact detection configuration and any supplied session context in JSON-safe result metadata.
- Require positive examples for every approved detector ID, with negative and inclusive/exclusive boundary fixtures for shared predicate families and confirmed multi-candle events.
- Coverage interpretation: the positive-example requirement applies to each approved detector ID. Negative and inclusive/exclusive boundary examples apply to shared predicate families and confirmed multi-candle events; this criterion does not require a separate negative and boundary test for every detector or every mathematical input edge. Document remaining test-depth limitations without treating them as implementation defects when the required shared-family and event evidence is present.
- Define consolidation using both a relative width ceiling and bounded first-to-last close drift so a narrow percentage band alone cannot label a monotonic trend as consolidation.
- Bound recurring scans and test deterministic behavior, prefix/truncation invariance, positive and negative formations, invalid inputs, session boundaries, and measured large inputs.
- Label continuous-market candle variants, swing-level touch counts, pullbacks, and OHLCV supply/demand as approximations where they do not prove canonical market context or order-book liquidity.
- Do not mark the milestone accepted until its implementation report enumerates each requirement, maps it to code and an exact test, reports executed results, and identifies all deferred or approximated scope.

## Phase 6 technical-indicator acceptance
- Use the Phase 6 task's explicitly approved v1 subset as the implementation scope. Other rows in its preserved candidate catalog, including anchored VWAP and higher-timeframe integration, are deferred proposals and are not v1 acceptance requirements.
- For every approved indicator, document formula, input, parameters, minimum history, warm-up, initialization/smoothing, missing and zero-denominator behavior, units/range, numeric precision/rounding, and an independent reference convention.
- Test each approved indicator with positive, negative, boundary, and multi-point golden-value fixtures, plus temporal prefix consistency as candles arrive.
- Require deterministic calculations independent of ambient Decimal context, no fabricated warm-up, no future-data leakage, and no SQLite REAL storage for derived financial values.
- Validate 1m, 5m, 15m, and daily timestamp sequences; document session-aware VWAP reset and session-start metadata; and version result serialization/API exports. Higher-timeframe aggregation and anchored VWAP require separate approval.
- Measure performance and memory on reproducible large sequences. Keep calculations separate from dashboard/database layers and do not add strategy, BUY/SELL, ML, LLM, broker, or live-execution behavior.

## Historical CSV ingestion acceptance
- Migrate schema version 3 to 4 transactionally; preserve legacy instrument records and REAL tick_size values, populate tick_size_decimal without claiming recovery of the original pre-REAL decimal precision, and verify rollback on failure using temporary databases.
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
- Migrate schema version 6 to 7 additively by defaulting existing and new data imports to `UNVERIFIED` and adding declaration history; preserve all imports, candles, quality issues, and existing run results.
- Require an explicit actor and rationale for source-use status changes. Do not infer permission from a filename, source row, or metadata; block backtests unless every contributing import is explicitly `VERIFIED_PERMITTED`, and preserve the declaration used by completed runs.
- Require a strategy's declared minimum history before execution; do not generate SMA crossover signals from a dataset too short to compare consecutive slow-window averages.
- Keep generic Strategy Lab experiment listings distinct from typed backtest registrations without hiding malformed Strategy Lab configurations.
- Snapshot exchange, symbol, instrument type/segment, lot size, exact decimal tick size, and deterministic instrument hash in backtest provenance. Clearly label legacy runs lacking source or instrument snapshots as incomplete rather than reconstructing unknown historic values.
- Hash the canonical full experiment configuration separately from its JSON and verify it, plus internal identity consistency, before every run. A first-run legacy backfill establishes a baseline only from that point forward and must be labelled as such.
- Persist SHA-256 hashes for the configuration, strategy specification and implementation, market-quality implementation, calendar input and effective session rules, dataset, and source files. Record the Git commit only for a clean worktree; explicitly identify unavailable or uncommitted revision state.
- A hash can detect changed content when the stored hash is unchanged; it cannot protect against a privileged actor changing both, nor reconstruct files, calendar inputs, or market data that have been lost.
- SMA uses a local Decimal precision of `max(28, integer_places + fractional_places + digits_in_input_count + 12)` significant digits and explicit `ROUND_HALF_EVEN`. Non-terminating averages are finite Decimal approximations at this precision; calculations do not quantize to tick size.
- Do not automatically fail a `RUNNING` record based only on age. Reconcile manually only after confirming its owning process has stopped; update only the selected run ID conditional on its status remaining `RUNNING`, and record the manual reconciliation in its error message.
- Changed strategy rules require a new version, written rationale, independent technical review, mathematical and regression testing, confirmed data-quality prerequisites, human approval, and a traceable link to the prior version before registration.

## Backtesting safety and accounting acceptance
- Verify causal timing: completed candle signals use only prior/completed information and execute at the next eligible open; volume capacity uses only the prior completed candle's volume.
- Verify cash, open-position marks, realized/unrealized P&L, and estimated liquidation costs reconcile. Do not silently liquidate open positions at the end of the dataset.
- Verify per-order brokerage cap and fee rounding under multiple Decimal contexts; costs, spread, and slippage remain assumptions, not official schedules.
- Run the public backtest API under default and altered Decimal precision/rounding contexts; successful results must match and the caller context must remain unchanged, including blocked runs.
- Require malformed registered backtest JSON/schema/configuration, snapshot field types or relationships, and missing/malformed/mismatched canonical configuration hashes to produce experiment-specific diagnostics; preserve legacy typed backtests and unrelated null-strategy record handling.
- Verify benchmark behavior for valid, unaffordable, and flat-price inputs, and preserve explicit unavailable status when the benchmark cannot be computed.
- Verify the metrics contract for no closed trades, breakeven trades, profit-factor denominator, drawdown, and insufficient/zero-variance daily returns.
- Exercise migration rollback and preservation on temporary databases. Never run schema migration tests against the user's normal database.
- Keep the dashboard and all execution surfaces in `RESEARCH_ONLY`; no broker connection or live-order path is permitted.

## Phase 7 ML research acceptance

- Build features only from information available at the decision timestamp and completed candles.
- Generate labels strictly from future observations; purge overlapping target windows across chronological train, validation and test partitions.
- Keep the test partition untouched during feature selection, model selection, sensitivity analysis and threshold selection.
- Compare ML results against deterministic training-majority and SMA20/50 classification baselines.
- Use validation data for sensitivity analysis; report expanding walk-forward results where available.
- Record dataset and source fingerprints, declared provenance, feature definitions, library versions, model parameters and partition boundaries.
- Require independently verified permitted data and complete, fresh market-quality coverage before empirical acceptance.
- Require strategy-level evaluation after realistic transaction costs, slippage, turnover, liquidity and capacity assumptions before claiming research value.
- Synthetic tests establish implementation correctness only; they are not evidence of predictive performance.
- Do not persist or deploy models, connect them to execution, or change execution mode without separate approval.
- Phase 7 acceptance remains pending until the implementation report maps every criterion to evidence and demonstrates cost-aware, untouched out-of-sample results.

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
