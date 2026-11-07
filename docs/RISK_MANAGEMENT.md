# Risk Management and Safety

## Initial mode
RESEARCH_ONLY. No live order placement. Paper trading only after the simulator passes tests.

## Hard safety requirements
- No broker order-placement methods in the initial application.
- No API keys, passwords, access tokens or personal account data in Git.
- LLM agents cannot access secrets or change risk configuration.
- LLM-generated code runs in isolated branches/workspaces and must pass tests/review.
- Stale, missing, malformed or inconsistent data halts the affected workflow.
- Unexpected state, failed reconciliation or unhandled exceptions stop simulated entries.
- All simulated orders and state transitions are logged.
- Dashboard clearly distinguishes actual account information (if ever read-only connected) from simulated values.

## Paper-trading limits
Before paper trading, define and test maximum position size, maximum portfolio exposure, maximum daily simulated loss, maximum open positions and instrument allowlist. These limits are configuration-controlled and require human approval to change.

## Future live-trading gate
Live trading is not part of this plan's initial implementation. Any proposal requires:
1. Independent evidence review of out-of-sample and paper results.
2. Current SEBI, exchange and broker compliance review.
3. Zerodha API terms, static IP, authentication and rate-limit verification.
4. Security review and secret-management design.
5. Order idempotency, duplicate prevention, reconciliation, kill switch and incident procedures.
6. Explicit user approval after plain-language risk disclosure.
7. A separately reviewed implementation with live execution disabled by default.

No performance result guarantees future returns. No borrowed or essential household funds should be used.
