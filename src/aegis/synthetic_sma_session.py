"""Bounded, research-only SMA session driver over the existing forward runtime.

This is orchestration, not another execution or risk engine.  A runtime/account
must already be initialized using Aegis's authenticated setup functions.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal

from aegis.database import get_readonly_connection
from aegis.decision_journal import reconcile_journal
from aegis.forward_runtime import ForwardRuntimeService, Quote, SmaWarmupRequired
from aegis.paper_trading import reconcile_paper_account


@dataclass(frozen=True)
class SMASessionConfig:
    """Validated SMA parameters and a bound on delivered quotes, including duplicates."""

    fast_window: int = 2
    slow_window: int = 3
    buy_quantity: int = 1
    max_quotes: int = 120
    stop_loss: Decimal | None = None
    target_price: Decimal | None = None

    def __post_init__(self) -> None:
        for key in ("fast_window", "slow_window", "buy_quantity", "max_quotes"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if self.fast_window < 2 or self.slow_window <= self.fast_window:
            raise ValueError("Require 2 <= fast_window < slow_window")
        if self.max_quotes > 10_000:
            raise ValueError("max_quotes exceeds bounded pilot limit")
        for key in ("stop_loss", "target_price"):
            price = getattr(self, key)
            if price is not None and (
                not isinstance(price, Decimal) or not price.is_finite() or price <= 0
            ):
                raise ValueError(f"{key} must be a finite positive Decimal")


@dataclass(frozen=True)
class SMAEventOutcome:
    """One routed decision or explicitly recorded NO_DECISION warm-up."""

    event_id: str
    interval: str
    requested_action: str
    outcome_status: str
    order_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class SMASessionResult:
    """Batch observations; authoritative balances remain in the Phase 8 ledger."""

    quotes_accepted: int
    scheduled_events: int
    decisions: tuple[SMAEventOutcome, ...]
    protective_exits: int
    account_reconciliation: str
    journal_reconciliation: str = "UNRESOLVED"


def _event_interval(runtime: ForwardRuntimeService, event_id: str) -> str:
    """Read the persisted interval; never guess a timeframe from the quote."""
    with closing(get_readonly_connection(runtime.database_path)) as connection:
        row = connection.execute(
            """SELECT interval FROM forward_scheduler_events
               WHERE event_id = ? AND runtime_id = ?""",
            (event_id, runtime.runtime_id),
        ).fetchone()
    if row is None:
        raise RuntimeError("Scheduled event identity was not persisted")
    interval = str(row["interval"])
    if interval not in {"1m", "5m", "15m"}:
        raise RuntimeError("Unsupported scheduler interval")
    return interval


def _require_journal_reconciliation(runtime: ForwardRuntimeService) -> str:
    """Refuse continuation or success when terminal audit evidence is incomplete."""
    journal = reconcile_journal(runtime.database_path, runtime.runtime_id)
    status = str(journal["status"])
    if status != "RECONCILED" or not journal["audit_complete"]:
        raise RuntimeError(f"Decision journal reconciliation failed: {status}")
    return status


def run_existing_sma_runtime(
    runtime: ForwardRuntimeService,
    quotes: Iterable[Quote],
    config: SMASessionConfig,
) -> SMASessionResult:
    """Consume a bounded synthetic quote stream through existing trusted boundaries.

    Existing runtime readiness/recovery and Phase 8 checks remain authoritative.
    The caller is responsible for isolated synthetic account initialization and
    supplying synthetic quotes; this function never connects to a broker.
    Unresolved pending events block continuation, including after restart.
    Warm-up uses the runtime's opt-in NO_DECISION recording; its scheduler
    projection is REJECTED because the existing schema has no NO_DECISION status.
    Terminal rejections are reported truthfully; unresolved work and incomplete
    journal audits fail closed. Stops and targets apply only to new SMA BUYs.
    """
    if not isinstance(runtime, ForwardRuntimeService):
        raise TypeError("An initialized ForwardRuntimeService is required")
    if not isinstance(config, SMASessionConfig):
        raise TypeError("SMASessionConfig is required")

    accepted = 0
    scheduled = 0
    protective_exits = 0
    decisions: list[SMAEventOutcome] = []
    runtime.ensure_quote_progression_ready()
    _require_journal_reconciliation(runtime)
    for index, quote in enumerate(quotes):
        if index >= config.max_quotes:
            raise ValueError("Synthetic quote stream exceeded max_quotes")
        runtime.ensure_quote_progression_ready()
        outcome = runtime.process_quote(quote)
        if not outcome.accepted:
            raise RuntimeError(f"Synthetic quote rejected: {outcome.reason}")
        if outcome.duplicate:
            continue
        accepted += 1
        if outcome.exit_result is not None:
            if outcome.exit_result.status != "FILLED":
                raise RuntimeError(f"Protective exit blocked: {outcome.exit_result.reason}")
            protective_exits += 1
        for event_id in outcome.scheduled_event_ids:
            scheduled += 1
            interval = _event_interval(runtime, event_id)
            # This is the *existing* authenticated strategy builder. Do not
            # replace a warm-up or qualification error with a fabricated HOLD.
            try:
                proposal = runtime.build_sma_proposal(
                    event_id,
                    fast_window=config.fast_window,
                    slow_window=config.slow_window,
                    buy_quantity=config.buy_quantity,
                    record_warmup=True,
                    stop_loss=config.stop_loss,
                    target_price=config.target_price,
                )
            except SmaWarmupRequired:
                decisions.append(
                    SMAEventOutcome(event_id, interval, "NO_DECISION", "NO_DECISION", None)
                )
                continue
            snapshot = runtime.execution_snapshot(quote.instrument_id, quote, interval=interval)
            result = runtime.route_event(event_id, proposal, snapshot)
            action = proposal.action.value
            decisions.append(
                SMAEventOutcome(
                    event_id=event_id,
                    interval=interval,
                    requested_action=action,
                    outcome_status=str(result.status),
                    order_id=result.order_id,
                    reason=result.reason,
                )
            )
            if result.status not in {"HOLD", "FILLED", "REJECTED"}:
                raise RuntimeError(
                    f"Forward decision blocked: {event_id}: {result.status} {result.reason}"
                )
            if result.status == "REJECTED":
                # A journal write failure can also be returned as REJECTED.
                # Only an audited terminal rejection permits continuation.
                _require_journal_reconciliation(runtime)

    runtime.ensure_quote_progression_ready()
    reconciliation = reconcile_paper_account(runtime.database_path, runtime.account_id)
    status = str(reconciliation["status"])
    if status != "RECONCILED":
        raise RuntimeError(f"Financial reconciliation failed: {status}")
    journal_status = _require_journal_reconciliation(runtime)
    return SMASessionResult(
        quotes_accepted=accepted,
        scheduled_events=scheduled,
        decisions=tuple(decisions),
        protective_exits=protective_exits,
        account_reconciliation=status,
        journal_reconciliation=journal_status,
    )
