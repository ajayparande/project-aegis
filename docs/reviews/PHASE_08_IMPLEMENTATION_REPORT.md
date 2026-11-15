# Phase 8 — Paper Execution Foundation Implementation Report

**Status: implementation foundation complete; application remains `RESEARCH_ONLY`.**

This report records the local deterministic paper ledger and its validation. It does not
claim forward-paper readiness, predictive performance, official fee accuracy, actual
execution quality, or authorization to change application mode.

## Scope and implementation

`src/aegis/paper_trading.py` provides an isolated service API for explicit paper accounts,
order submission, simulated fills, reconciliation, and read-only account reports. There
is no broker adapter, credential access, network market-data connection, strategy-to-order
routing, live order path, or interactive order-submission control. The dashboard only
displays simulated account reports. The application remains `RESEARCH_ONLY`.

Each request is a long-only BUY or SELL market order. It executes synchronously, all-or-none,
against a caller-supplied quote and quantity observed at execution time. Prior completed
candles provide quality and source evidence; the quality report must end strictly before
the execution time. The current or future candle's OHLCV is not used to determine the fill.
Because this foundation has no quote feed, quote authenticity and the caller's assertion
that the quote was available at execution are outside the service's verification.

The service requires an active registered supported instrument, an explicitly
`VERIFIED_PERMITTED` completed import, matching complete/fresh quality evidence and full
confirmed calendar coverage. It requires explicit capital, risk limits and cost assumptions.
Cash, positions, fees, execution costs, realized P&L and reports use deterministic Decimal
accounting. Orders, fills, cash entries, projections and lifecycle events are persisted
atomically in schema version 8. Stable IDs and idempotency keys prevent duplicate fills;
conflicting key reuse is rejected. Reconciliation replays persisted records, halts on
disagreement, and does not repair records automatically. Exposure projection replaces
the existing instrument's marked value once with the full post-order position at the
execution quote. A daily-loss halt blocks new BUY exposure; valid evidence-backed SELLs
can reduce a position during risk or data halts, but cannot clear the halt. A
reconciliation-failure halt blocks both sides.

## Corrective milestone findings and changes

| Review finding | Verification/root cause | Correction and regression evidence |
|---|---|---|
| Additional BUY daily-loss projection understated the position | The old projection added `(requested quantity - prior quantity) * reference price` to gross exposure, even though gross exposure already held the prior position. For an existing 10-share position and a 5-share add-on, this subtracts 5 shares instead of adding 5. | The projection now removes the old instrument's marked value once and adds the entire new quantity at the execution quote; post-fill cash reflects fill price and fees. `test_additional_buy_counts_existing_position_once_in_exposure_and_cash` and `test_daily_loss_projection_for_additional_buy_uses_full_new_position` verify accepted add-ons, quantity, average price, cash, gross exposure, and the actual daily equity change. The latter would be rejected by the old undercounting calculation. |
| Add-on exposure needed explicit limit evidence | Existing position and incremental quantity must be composed into one post-order instrument value; total portfolio value must replace that instrument's old mark once while retaining other positions. | Instrument and total projected exposure now use full post-order quantity at the snapshot quote. `test_additional_buy_obeys_instrument_and_portfolio_exposure_limits` independently configures each limit and checks rejection leaves the existing position and cash unchanged. |
| Ledger replay did not prove the fill belonged to the order on its ledger row | Separate foreign keys only prove that the referenced fill and order exist. The replay joined both records without comparing `fill.order_id` to `ledger.order_id`, and did not enforce exact per-order financial record counts or all matching fields. | `_financial_relationship_errors` validates order/fill/ledger ownership, exact counts, status, direction, account, instrument, quantity, fill price and execution timestamp before replay. `test_reconciliation_rejects_ledger_entry_linked_to_another_orders_fill`, `test_reconciliation_rejects_order_fill_quantity_mismatch_without_repair`, and `test_reconciliation_rejects_missing_ledger_entry_without_repair` corrupt isolated databases and assert failure, halt, and preserved audit rows. No schema migration was needed; SQLite foreign keys remain enabled. |
| Halted SELL behavior was implicit and daily-loss/stale-position gating could trap a position | The prior outer gate allowed SELLs for non-reconciliation halts, but the later daily-loss check applied to SELLs too. The evidence validator also rejected a SELL based on stale marks for existing holdings, even when the SELL had its own fresh, valid quote. | A valid SELL bypasses the daily-loss entry gate and the separate freshness requirement for existing portfolio marks; its own source/quality/freshness evidence, instrument, all-or-none capacity, order-value and held-quantity checks remain mandatory. `test_risk_halt_allows_only_risk_reducing_sell_and_stays_halted`, `test_daily_loss_halt_allows_valid_exit_but_blocks_new_buy`, and `test_stale_data_halt_allows_sell_only_with_new_fresh_evidence` verify exits and persistent halts. The stale test's SELL quote is later than the stale mark. `test_risk_halt_rejects_sell_larger_than_held_position` verifies no short can result. `test_reconciliation_halt_blocks_buy_and_sell_without_repair` verifies integrity failures block both sides without repairing the corrupted position. |

Reconciliation does not auto-repair or delete any financial rows. SQLite constraints already
prevent several orphan/duplicate forms in normal connections; the application-level checks
also fail closed if persisted structures are cross-linked or inconsistent. Test corruption
is performed only in isolated temporary databases.

## Phase 8 acceptance mapping

| Task acceptance criterion | Implementation and evidence |
|---|---|
| No broker order-placement calls in the runtime path | `paper_trading.py` accepts local explicit inputs and uses SQLite only; no broker adapter or credentials. Dashboard safety assertion: `test_dashboard_displays_research_only_warning`; read-only paper view assertion: `test_dashboard_shows_simulated_reports_without_execution_controls`. |
| Deterministic ledger reconciliation | Fill/sell/restart test `test_buy_sell_accounting_idempotency_and_restart_reconciliation`; tamper detection `test_reconciliation_detects_tampered_position_and_halts`; public report reconciliation exercised by report calls in these tests. |
| Fail-closed behavior | `test_malformed_persisted_risk_configuration_cannot_submit`, `test_risk_limit_rejects_and_halts_new_entries`, `test_position_and_exposure_limits_reject_and_halt`, `test_daily_loss_from_cost_assumptions_halts_entry`, `test_unverified_source_fails_closed`, `test_stale_snapshot_fails_closed`, `test_quality_failure_halts_and_does_not_store_an_untrusted_mark`, and `test_unknown_instrument_is_persisted_as_auditable_rejection`. |
| Clearly simulated values | Report/dashboard labels and assumptions are checked by `test_dashboard_shows_simulated_reports_without_execution_controls`; account output is created and inspected in `test_buy_sell_accounting_idempotency_and_restart_reconciliation` and `test_spread_slippage_tick_rounding_and_fees_are_recorded`. |
| No unavailable-after-signal fill price | Explicit quote and strictly prior quality evidence are enforced by `test_execution_snapshot_cannot_precede_order_submission`, `test_stale_snapshot_fails_closed`, and `test_quality_failure_halts_and_does_not_store_an_untrusted_mark`. Current candle OHLCV is not used for the quote fill. |
| Decimal accounting and persisted fee/cost assumptions | `test_buy_sell_accounting_idempotency_and_restart_reconciliation`, `test_spread_slippage_tick_rounding_and_fees_are_recorded`, and `test_public_accounting_is_independent_of_ambient_decimal_context`. |
| Duplicate requests do not duplicate orders/fills | `test_buy_sell_accounting_idempotency_and_restart_reconciliation`; key conflict `test_idempotency_key_conflict_does_not_create_another_order`. |
| Restart reconstruction and reconciliation | Persisted state is reopened and checked in `test_buy_sell_accounting_idempotency_and_restart_reconciliation`; database upgrade preservation is checked in `test_version_7_migration_adds_paper_tables_and_preserves_records`. |
| Explicit, valid risk configuration | `test_paper_account_requires_explicit_safe_risk_configuration` and `test_malformed_persisted_risk_configuration_cannot_submit`. |
| Remain `RESEARCH_ONLY` pending separate approval | `test_dashboard_displays_research_only_warning` and `test_dashboard_shows_simulated_reports_without_execution_controls`; the config continues to expose only `RESEARCH_ONLY`. |

## Additional test coverage

Paper service tests (`tests/test_paper_trading.py`) cover: explicit account configuration;
persisted config validation; BUY/SELL accounting; restart reconciliation; spread, slippage,
tick rounding and fees; idempotent repeat and key conflict; order/position/exposure/daily-loss
limits; verified-source, freshness, quality and instrument gates; long-only constraint;
signal/submission/execution ordering; execution capacity; tamper detection; atomic rollback;
Decimal-context independence; additional BUY exposure and daily-loss calculations; corrupted
order/fill/ledger links; and SELL/BUY behavior during risk, daily-loss, stale-data, quality,
and reconciliation halts.

Schema tests (`tests/test_database.py`) cover fresh schema initialization, additive v7-to-v8
migration with preservation of prior records, and rollback when the new migration fails.
Dashboard tests use isolated temporary database and log paths. The focused paper/data/dashboard
test run from the initial implementation passed 188 tests. The corrective milestone adds
dedicated coverage documented above; the final focused regression selection passed 19 tests,
and the full paper module passed 32 tests.

## Known limitations and operational gates

- Execution is a synchronous all-or-none quote simulation. Pending orders, cancellation,
  partial fills, reservations, and open-order restart recovery are not implemented.
- There is no quote feed or independent verification of caller-supplied execution quotes.
  This API is a deterministic simulation boundary, not a market connection.
- There is no interactive account/order entry, automated strategy routing, forward-test
  scheduler, short selling, leverage, BSE instrument support, or derivatives support.
- Simulated costs are assumptions reused from the Phase 4 cost model, not official charges.
- Synthetic tests establish behavior under supplied inputs; they do not establish market
  realism, profitability, or actual fill quality.
- Phase 4 acceptance remains pending. The checked-in exchange calendar has no claimed
  official holiday coverage, and source-use permissions and trustworthy market-data
  availability remain separate operational gates. These prerequisites block forward
  operation but do not activate or authorize it through this Phase 8 foundation.

## Validation

Corrective validation was run on 2026-11-10 using the existing virtual environment, in this
order:

1. `python -m pytest -q -p no:cacheprovider tests/test_paper_trading.py -k "additional_buy or reconciliation_rejects or halt"` — **19 passed, 13 deselected**.
2. `python -m pytest -q -p no:cacheprovider tests/test_paper_trading.py` — **32 passed**.
3. `python -m pytest -q -p no:cacheprovider tests/test_database.py` — **24 passed**.
4. `python -m pytest -q -p no:cacheprovider` — **444 passed**.
5. `ruff check .` — passed.
6. `ruff format --check .` — passed, **58 files already formatted**.
7. `mypy src` — passed, no issues in **13 source files** (with informational notes for untyped function bodies).
8. `git diff --check` — passed; Git printed only LF-to-CRLF normalization notices for four tracked files.

The explicit trailing-whitespace scan of the newly added paper service, its test module,
and this report returned no matches. All tests use temporary database fixtures; the normal
database and logs were not used as test targets. Phase 8's local simulation acceptance
criteria are met for the implemented synchronous design. This is not operational readiness:
no external quote feed, forward testing, or authorization to change `RESEARCH_ONLY` is
claimed. No commit or push was made.
