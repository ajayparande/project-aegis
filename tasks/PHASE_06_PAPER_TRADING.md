# Phase 6 — Paper Trading

## Goal
Simulate decisions using current permitted market data without placing broker orders.

## Tasks
- [ ] Implement paper broker interface with no live broker adapter.
- [ ] Simulate order lifecycle, fills, fees and slippage assumptions.
- [ ] Reconcile cash, positions and orders on every cycle.
- [ ] Add configurable exposure and loss limits.
- [ ] Halt new entries on stale data, reconciliation errors or breached limits.
- [ ] Add daily reports and dashboard views.
- [ ] Run failure drills and compare paper behaviour with backtest assumptions.

## Acceptance criteria
- Runtime path contains no broker order-placement calls.
- Paper ledger reconciles deterministically.
- Fail-closed behaviour is tested.
- Simulated values are clearly labelled and never presented as actual profit.
