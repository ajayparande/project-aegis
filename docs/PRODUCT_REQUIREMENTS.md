# Product Requirements

## Functional requirements
### FR-01 Research library
Store curated educational references, source, date accessed, topic, and summary. AI-generated summaries must link back to source material.

### FR-02 Market data
Ingest permitted historical and live data for selected NSE instruments. Store source, timestamp, timezone, interval, retrieval time and data-quality status. Do not assume free sources are licensed or complete.

### FR-03 Instrument universe
Start with a small, explicitly defined universe of liquid cash-market instruments or a broad-market index. Record universe membership by date where possible to reduce survivorship bias.

### FR-04 Strategy specification
Every strategy must have a versioned, machine-readable specification containing:
- Rationale and hypothesis.
- Instrument universe and timeframe.
- Entry and exit rules.
- Position sizing.
- Stop/exit logic.
- Trading frequency assumptions.
- Costs and slippage assumptions.
- Known limitations.

### FR-05 Backtesting
Simulate chronological execution without future data leakage. Account for signal timing, next-bar execution assumptions, corporate actions where relevant, transaction costs, slippage, liquidity and rejected/unfilled orders where supported.

### FR-06 Experiment registry
Record experiment ID, strategy version, code commit, dataset/version, date range, parameters, costs, metrics, logs and result status.

### FR-07 Validation
Separate training, validation and final untouched test periods chronologically. Include walk-forward testing and robustness/sensitivity checks. The final test period must not be repeatedly used for tuning.

### FR-08 Paper trading
Use live or appropriately delayed data from a permitted source to simulate orders. Clearly label simulated fills and never submit orders to the broker.

### FR-09 Dashboard
Show data health, research queue, experiment history, net simulated results, drawdown, exposure, costs, paper positions and system errors.

### FR-10 AI assistance
AI may propose experiments and explain results. It must not fabricate data, metrics, citations or fills. All numerical claims must be generated from deterministic code and linked to experiment records.

## Non-functional requirements
- Reproducibility: same inputs and version should produce same deterministic results.
- Security: secrets in environment variables or a local secret store; `.env` excluded from Git.
- Auditability: structured logs and immutable experiment records.
- Reliability: fail closed on stale/missing data and exceptions.
- Cost control: track LLM calls/tokens and paid services.
- Maintainability: typed interfaces, documentation, tests and small modules.
- Accessibility: dashboard labels and explanations understandable to a non-trader.

## Acceptance principle
A feature is complete only when its acceptance criteria and automated tests pass. AI review alone is not sufficient.
