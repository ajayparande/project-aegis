# Project Aegis
## AI-assisted quantitative trading research and paper-trading platform

Project Aegis is a controlled research platform for Indian-market strategy research. It uses AI agents to assist with learning, hypothesis generation, experiment analysis, and software development. Deterministic Python components handle market-data processing, backtesting, risk checks, and simulated execution.

**Initial objective:** build a reproducible research and paper-trading system within an initial total budget ceiling of ₹10,000.

**Important:** Project Aegis starts in research-only mode. It must not place live orders. No strategy is assumed profitable. Live trading is out of scope until separate validation, regulatory/broker checks, and explicit human approval.

## Non-negotiable principles
1. No live orders in the initial release.
2. No claim of profitability without reproducible out-of-sample evidence.
3. Include brokerage, taxes/charges where applicable, slippage, liquidity and execution assumptions in tests.
4. Never expose broker/API credentials in source code, logs, notebooks, prompts, or Git history.
5. AI agents cannot change risk limits, approve their own work, or enable live execution.
6. All experiments, datasets, code versions, assumptions and results must be recorded.
7. Fail closed: stale data, missing data, API errors, unexpected state or failed checks stop the relevant workflow.
8. Preserve the ₹10,000 budget ceiling; no paid service is activated without a documented need and user approval.

## Default technical decisions
- Language: Python.
- Development: local computer first.
- Source control: Git and GitHub.
- Data/analysis: pandas, NumPy, scikit-learn.
- Storage: SQLite initially; PostgreSQL only if justified later.
- Dashboard: Streamlit.
- Agent orchestration: begin with simple Python workflows; evaluate LangGraph only when workflows need it.
- LLM: one low-cost API model with hard usage limits; model-provider adapter keeps it replaceable.
- Broker: Zerodha Kite Connect may be integrated for data after current pricing, API access, data-use and regulatory requirements are verified.
- Initial execution: custom paper-trading simulator; no live order endpoint.

## Milestone sequence
1. Requirements, repository and secure development foundation.
2. Market-data ingestion and data-quality controls.
3. Strategy research and experiment registry.
4. Event-aware backtesting with costs.
5. Out-of-sample validation and robustness checks.
6. Paper trading with live market data.
7. Monitoring, reporting and operational review.
8. Separate live-readiness assessment (not automatic activation).

See `docs/DEVELOPMENT_ROADMAP.md` and `tasks/` for acceptance criteria.

## Start here
Read these in order:
1. `docs/PROJECT_VISION.md`
2. `docs/PRODUCT_REQUIREMENTS.md`
3. `docs/SYSTEM_ARCHITECTURE.md`
4. `docs/BUDGET_AND_COST_CONTROLS.md`
5. `docs/RISK_MANAGEMENT.md`
6. `docs/VALIDATION_AND_ACCEPTANCE.md`
7. `docs/DEVELOPMENT_ROADMAP.md`

## Current status
Planning scaffold only. No broker connection, market-data ingestion, trading strategy, or order execution is implemented.
