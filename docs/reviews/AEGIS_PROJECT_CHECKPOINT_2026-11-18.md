# Project Aegis — Development Checkpoint

**Date:** 18 November 2026
**Mode:** `RESEARCH_ONLY`
**Live broker orders:** Prohibited

## Executive status

Project Aegis is a local Python/SQLite research and paper-trading platform. The standalone deterministic synthetic paper pilot has passed at an initial capital of **₹10,000**. This is not evidence of real-market profitability, live market-data connectivity, or permission to place real orders.

## Verified synthetic paper pilot (P10-P3)

| Item | Observed result |
|---|---|
| Initial paper cash | ₹10,000.00 |
| Final cash | ₹10,000.15 |
| Final equity | ₹10,000.15 |
| Realized net P&L | ₹0.15 (synthetic only) |
| BUY_FILLED | 1 |
| SELL_FILLED | 1 |
| Reconciliation | RECONCILED |
| Restart verification | Passed |
| Data source | DETERMINISTIC_SYNTHETIC |
| Broker | NONE |
| Live orders | false |
| Forward-runtime regression | 75 passed |
| Ruff check on pilot | Passed |

The pilot uses synthetic price behavior and modeled assumptions; the gain must not be treated as an empirical performance claim.

## Portfolio qualification test status (18 November 2026)

The reported M2-focused run finished with **238 passed and 1 failed**. The failing test was:

`tests/test_portfolio_qualification.py::test_latest_schema_version_is_recorded_deterministically_and_readonly`

The test expected migrations `1..15`, while the actual database included migration **16**. This indicates an outdated hard-coded schema expectation **if schema v16 is confirmed to be an intentional, valid migration**. The migration and current `SCHEMA_VERSION` must be inspected before updating the test. Do not mark the M2 suite fully passing until the test is corrected and rerun. Because the validation script stopped at this failure, the subsequent Upstox-universe, Ruff, and MyPy checks from that script have not yet run.

## Scope and milestone status

| Milestone | Status | Boundary |
|---|---|---|
| M1B qualified one-instrument execution | Previously accepted | Bounded execution scope only |
| M2-B qualification | Previously accepted | Does not automatically establish M2-C acceptance |
| M2-C execution | Acceptance not confirmed in this checkpoint | Verify independent audit and current tests |
| Offline Upstox/provider universe development | Present in worktree | Not proof of live data integration |
| P10-P3 synthetic paper pilot | Passed | One-off deterministic synthetic run |
| P10-P4 SMA-driven decisions | Pending | No completed strategy-driven forward pilot established |
| P10-P5 continuous synthetic trading | Pending | Extended autonomous sessions not yet established |
| Live market-data paper trading | Pending | No authenticated live Upstox feed established |
| Real orders | Prohibited | No authorization to enable |

## Synthetic pilot risk configuration

- Initial capital: ₹10,000
- Maximum order value: ₹2,000
- Maximum instrument exposure: ₹3,000
- Maximum portfolio exposure: ₹8,000
- Maximum daily loss: ₹200
- Maximum position quantity: 10

These are pilot configuration limits, not production trading recommendations. Recheck values against source before treating them as general defaults.

## Architecture and safety boundaries

1. Keep `RESEARCH_ONLY` as a hard invariant; no live broker order calls or credentials.
2. Use isolated temporary SQLite databases for synthetic tests and pilots.
3. Strategy-generated `ProposalContract` objects are not execution authorizations.
4. Bind forward-runtime `DecisionProposal` objects to genuine scheduler events, evidence and persisted candles.
5. Route simulated orders through the existing risk-checked Phase 8 paper-execution boundary.
6. Preserve calendar, provenance, quality, freshness, one-use authorization, idempotency, reconciliation and restart protections.
7. Use completed candles only; prevent look-ahead and unfinished-candle decisions.
8. No paid services without explicit approval.

## Git checkpoint status at inspection

The reported `main` branch was **11 commits ahead of `origin/main`**, with **13 modified tracked files**, **35 untracked files**, and no staged files. This was a snapshot, not a live Git status. Preserve unrelated work and use file-specific staging. Do not commit temporary backups or archives without review. Do not push until commits and relevant tests are reviewed.

## Next development and acceptance steps

1. Confirm schema v16 in `src/aegis/database.py`; update the stale M2 test expectation only if v16 is intentional.
2. Rerun focused M2 tests; then run Upstox universe tests, Ruff and MyPy separately.
3. Review and commit code in cohesive, validated groups, with separate documentation updates.
4. Implement P10-P4: deterministic SMA-generated BUY/SELL/HOLD using completed synthetic candles, authentic event/proposal binding, risk checks and an isolated ₹10,000 paper account.
5. Implement P10-P5 continuous synthetic sessions only after P10-P4 acceptance.

**Acceptance discipline:** A passing pilot or focused test run is not full system acceptance. Keep unresolved milestones explicitly pending.
