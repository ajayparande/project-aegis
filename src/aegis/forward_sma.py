"""Pure P10-P4 SMA decision adapter; no execution authority."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from aegis.strategy_lab import (
    StrategyCandle,
    generate_sma_crossover_signals,
)

SmaAction = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class SmaDecision:
    action: SmaAction
    candle_timestamp: str
    reference_price: Decimal
    rationale: str
    quantity: int = 0


def decide_completed_sma(
    candles: Sequence[StrategyCandle],
    *,
    fast_window: int,
    slow_window: int,
) -> SmaDecision:
    """Calculate a signal without authorizing or placing an order.

    The caller must independently establish that every candle
    is persisted, completed, qualified and belongs to the
    authorized scheduler event. This function does not
    establish any of those properties.
    """
    if (
        isinstance(fast_window, bool)
        or isinstance(slow_window, bool)
        or not isinstance(fast_window, int)
        or not isinstance(slow_window, int)
        or fast_window < 2
        or slow_window <= fast_window
    ):
        raise ValueError("Require 2 <= fast_window < slow_window.")

    if len(candles) < slow_window + 1:
        raise ValueError("Insufficient completed candles for crossover detection.")

    sequence = tuple(candles)

    # The existing Strategy Lab validator checks candle semantics
    # and chronological ordering.
    signals = generate_sma_crossover_signals(
        sequence,
        fast_window=fast_window,
        slow_window=slow_window,
    )

    latest = sequence[-1]
    latest_signal = signals[-1] if signals and signals[-1].timestamp == latest.timestamp else None

    if latest_signal is None:
        return SmaDecision(
            action="HOLD",
            candle_timestamp=latest.timestamp,
            reference_price=latest.close,
            rationale="No new SMA crossover on the latest candle.",
        )

    action: SmaAction = "BUY" if latest_signal.action == "ENTER_LONG" else "SELL"

    return SmaDecision(
        action=action,
        candle_timestamp=latest.timestamp,
        reference_price=latest.close,
        rationale=(
            f"SMA crossover {latest_signal.action}; "
            f"fast={latest_signal.fast_sma}; "
            f"slow={latest_signal.slow_sma}."
        ),
    )
