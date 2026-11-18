"""Offline-only simulation of multi-instrument collection and restart."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WindowIdentity = tuple[str, str, str]


@dataclass
class SyntheticCollectionState:
    completed: set[WindowIdentity] = field(default_factory=set)
    corrupted: set[WindowIdentity] = field(default_factory=set)


@dataclass(frozen=True)
class SimulationProgress:
    planned: int
    reused: int
    simulated: int
    pending: int
    interrupted: bool


def _identities(plan: dict[str, Any]) -> tuple[WindowIdentity, ...]:
    if (
        plan.get("mode") != "RESEARCH_ONLY"
        or plan.get("network_enabled") is not False
        or plan.get("storage_enabled") is not False
        or plan.get("ml_training_approved") is not False
        or plan.get("source_use_status") != "UNVERIFIED"
    ):
        raise ValueError("unsafe simulation plan")

    identities = tuple(
        (
            instrument["instrument_key"],
            window["from_date"],
            window["to_date"],
        )
        for instrument in plan["instruments"]
        for window in instrument["windows"]
    )

    if (
        len(identities) != plan["planned_requests"]
        or len(set(identities)) != len(identities)
        or not 1 <= len(identities) <= 1000
    ):
        raise ValueError("invalid simulation window identities")

    return identities


def simulate_collection(
    *,
    plan: dict[str, Any],
    state: SyntheticCollectionState,
    max_new_windows: int,
) -> SimulationProgress:
    """Simulate bounded progress; never call a provider or filesystem.

    Caller must supply a plan already validated by validate_collection_plan.
    The state is synthetic and is not evidence of downloaded candles.
    """
    if type(max_new_windows) is not int or not 0 <= max_new_windows <= 1000:
        raise ValueError("invalid simulation budget")
    if not isinstance(state, SyntheticCollectionState):
        raise TypeError("invalid synthetic collection state")

    identities = _identities(plan)
    expected = set(identities)

    if not state.completed <= expected or not state.corrupted <= expected:
        raise ValueError("unknown synthetic window identity")

    # Preflight every instrument before simulating any additional completion.
    if state.corrupted:
        raise ValueError("corrupted synthetic window; collection blocked")

    prior = len(state.completed)
    simulated = 0

    for identity in identities:
        if identity in state.completed:
            continue
        if simulated >= max_new_windows:
            break
        state.completed.add(identity)
        simulated += 1

    pending = len(identities) - len(state.completed)

    return SimulationProgress(
        planned=len(identities),
        reused=prior,
        simulated=simulated,
        pending=pending,
        interrupted=pending > 0,
    )
