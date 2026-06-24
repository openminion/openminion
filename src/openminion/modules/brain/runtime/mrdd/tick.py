from dataclasses import dataclass
from typing import Mapping

from ..drift import (
    ActionTrajectoryRecord,
    DriftDetectionThresholds,
    detect_goal_drift,
)
from ..regrounding import (
    RegroundingDecision,
    RegroundingInject,
    RegroundingPolicy,
    RegroundingTickResult,
    evaluate_regrounding_tick,
)
from openminion.modules.brain.schemas.goals import Goal, GoalDriftSignal


@dataclass(frozen=True)
class MRDDTickInputs:
    """Typed per-tick inputs for the MRDD coordinator."""

    goal: Goal
    policy: RegroundingPolicy
    cadence_counter: int
    just_compacted: bool
    trajectory: ActionTrajectoryRecord | None = None
    thresholds: DriftDetectionThresholds | None = None
    detected_at: str = ""
    signal_id: str = ""
    forced_regrounding: bool = False


@dataclass(frozen=True)
class MRDDTickOutcome:
    """Typed MRDD tick result."""

    inject: RegroundingInject | None
    signal: GoalDriftSignal | None
    next_counter: int
    decision: RegroundingDecision


def run_mrdd_tick(inputs: MRDDTickInputs) -> MRDDTickOutcome:
    """Run one MRDD coordination tick."""

    regrounding: RegroundingTickResult = evaluate_regrounding_tick(
        goal=inputs.goal,
        policy=inputs.policy,
        cadence_counter=inputs.cadence_counter,
        just_compacted=inputs.just_compacted,
        forced=inputs.forced_regrounding,
    )

    signal: GoalDriftSignal | None = None
    if inputs.trajectory is not None and inputs.signal_id and inputs.detected_at:
        signal = detect_goal_drift(
            goal=inputs.goal,
            trajectory=inputs.trajectory,
            thresholds=inputs.thresholds,
            detected_at=inputs.detected_at,
            signal_id=inputs.signal_id,
        )

    return MRDDTickOutcome(
        inject=regrounding.inject,
        signal=signal,
        next_counter=regrounding.next_counter,
        decision=regrounding.decision,
    )


def resolve_policy_from_metadata(
    metadata: Mapping[str, object] | None,
) -> RegroundingPolicy:
    """Resolve ``RegroundingPolicy`` from operator metadata."""

    raw = dict(metadata or {})
    enabled_raw = raw.get("mrdd_regrounding_enabled", False)
    enabled = str(enabled_raw).strip().lower() in {"1", "true", "yes", "on"}
    cadence_raw = raw.get("mrdd_regrounding_cadence_turns", 10)
    try:
        cadence = int(cadence_raw)
    except (TypeError, ValueError):
        cadence = 10
    if cadence < 1:
        cadence = 10
    inject_after_compaction_raw = raw.get(
        "mrdd_regrounding_inject_after_compaction", True
    )
    inject_after_compaction = str(inject_after_compaction_raw).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    return RegroundingPolicy(
        cadence_turns=cadence,
        enabled=enabled,
        inject_after_compaction=inject_after_compaction,
    )


__all__ = [
    "MRDDTickInputs",
    "MRDDTickOutcome",
    "resolve_policy_from_metadata",
    "run_mrdd_tick",
]
