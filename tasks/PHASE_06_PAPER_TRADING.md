# Phase 6 — Paper Execution Foundation

## Goal
Implement a deterministic simulated broker and portfolio ledger. This phase establishes the paper-execution machinery; the later forward-testing phase runs it against live market data for an extended observation period.

## Tasks
- [ ] Implement a paper broker interface with no live broker adapter or order-placement path.
- [ ] Simulate order lifecycle, fills, fees, spread, and slippage assumptions.
- [ ] Reconcile virtual cash, positions, and orders on every cycle.
- [ ] Add configurable exposure, position-size, and loss limits.
- [ ] Halt new entries on stale data, reconciliation errors, or breached limits.
- [ ] Add daily reports and dashboard views.
- [ ] Run failure drills and compare paper behaviour with backtest assumptions.
- [ ] Add duplicate-request protection and deterministic order identifiers.
- [ ] Clearly distinguish signal time, order-submission time, simulated fill time, and fill price.

## Acceptance criteria
- Runtime path contains no broker order-placement calls.
- Paper ledger reconciles deterministically.
- Fail-closed behaviour is tested.
- Simulated values are clearly labelled and never presented as actual profit.
- The simulator does not assume fills at a price that was unavailable after the signal decision.
