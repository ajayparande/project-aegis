# Phase 8 — Paper Execution Foundation

## Goal
Implement a deterministic simulated broker and portfolio ledger. This phase establishes the paper-execution machinery; the later forward-testing phase runs it against live market data for an extended observation period.

## Tasks
- [x] Implement a local paper simulation interface; no live adapter or real order-placement path exists.
- [x] Simulate synchronous all-or-none market-order lifecycle, fills, fee details, spread, slippage and tick-rounding assumptions.
- [x] Reconcile virtual cash, long-only positions, fills and ledger entries from persisted history before orders and during reports.
- [x] Require explicit per-account exposure, position-size, order-value, daily-loss and data-age limits.
- [x] Halt new entries on stale/invalid source-quality evidence, reconciliation failure and hard-risk breaches; persist rejection/halt reasons.
- [x] Add daily simulated reports and a read-only dashboard view with orders, fills, positions and assumptions.
- [x] Exercise injected transaction failure and use the Phase 4 fill-price and fee calculations to keep assumptions aligned.
- [x] Add deterministic order/fill/ledger/event identifiers and conflicting idempotency-key rejection.
- [x] Persist orders, fills, cash entries, positions, realized P&L, marks, state events and daily starting equity in schema version 8.
- [x] Persist signal, submission, simulated execution and fill timestamps and reference/fill prices separately.
- [x] Test process-independent recovery/reconciliation, rollback, duplicates and malformed projections using isolated temporary databases.

## Acceptance criteria
- Runtime path contains no broker order-placement calls.
- Paper ledger reconciles deterministically.
- Fail-closed behaviour is tested.
- Simulated values are clearly labelled and never presented as actual profit.
- The simulator does not assume fills at a price that was unavailable after the signal decision.
- Use deterministic Decimal accounting for cash, positions, fees, and realized/unrealized P&L.
- Reject duplicate order requests without creating duplicate simulated orders or fills.
- Reconcile positions, cash, open orders, and fills after restart.
- Require explicit risk configuration and fail closed when risk settings are missing or invalid.
- Keep the application in RESEARCH_ONLY until paper execution is explicitly implemented, tested, and separately approved.

## Implementation interpretation and current status

The engine executes a submitted order immediately against a caller-supplied, validated
execution-time quote, within the same SQLite transaction as its order, fill,
cash-entry, position and audit records. It supports terminal `FILLED` and `REJECTED`
orders, with `SUBMITTED` and terminal events. Because no order remains pending, this
version does not implement cancellation, partial fills, reservations, or open-order
recovery; interrupted transactions roll back atomically and repeat submissions resolve
through persisted idempotency records.

The read-only dashboard cannot create accounts or submit orders. The application remains
`RESEARCH_ONLY`. The implementation does not provide a data feed, strategy routing,
short selling, leverage, BSE/non-cash products, or forward-testing automation. Current
market-data/calendar prerequisites and Phase 4 acceptance remain external operational
gates. See [the Phase 8 implementation report](../docs/reviews/PHASE_08_IMPLEMENTATION_REPORT.md)
for the exact requirement/test map and remaining limits.

During risk-limit, daily-loss, stale-data, or quality halts, a SELL may proceed only when
fresh valid evidence is supplied and the order reduces an existing long position. Order
value, execution capacity, instrument, and held-quantity checks still apply. A SELL does
not clear the halt. Reconciliation-failure halts block both BUY and SELL until a separately
supported recovery mechanism exists; no such automatic recovery is implemented.

Execution is all-or-none: an automatic stop/target exit does not simulate a partial fill.
If an automatic exit is rejected, the forward runtime releases its exit claim and keeps
the remaining position's protection active so the exit can be retried on later accepted
quotes. Manual partial SELLs likewise leave protection active while a long position remains.
