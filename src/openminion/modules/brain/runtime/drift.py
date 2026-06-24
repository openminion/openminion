"""Detect structural goal-drift signals from recent action history."""

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from openminion.modules.brain.schemas.goals import Goal, GoalDriftSignal


@dataclass(frozen=True)
class ActionTrajectoryRecord:
    """Recent action-history snapshot for drift detection."""

    recent_action_tokens: tuple[str, ...] = field(default_factory=tuple)
    unsatisfied_criterion_ids: tuple[str, ...] = field(default_factory=tuple)
    observed_mission_type: str = ""
    expected_mission_type: str = ""
    actions_since_progress: int = 0


@dataclass(frozen=True)
class DriftDetectionThresholds:
    """Thresholds for structural goal-drift detection."""

    min_recent_actions: int = 3
    divergence_ratio: float = 0.66
    inaction_action_count: int = 5
    objective_substitution_token: str = ""

    def __post_init__(self) -> None:  # pragma: no cover - simple guards
        if self.min_recent_actions < 1:
            raise ValueError("DriftDetectionThresholds.min_recent_actions must be >= 1")
        if not 0.0 <= self.divergence_ratio <= 1.0:
            raise ValueError(
                "DriftDetectionThresholds.divergence_ratio must be in [0, 1]"
            )
        if self.inaction_action_count < 1:
            raise ValueError(
                "DriftDetectionThresholds.inaction_action_count must be >= 1"
            )


def _structural_check_set(goal: Goal) -> frozenset[str]:
    return frozenset(
        criterion.structural_check
        for criterion in goal.success_criteria
        if criterion.structural_check
    )


def _build_evidence(
    *,
    trajectory: ActionTrajectoryRecord,
    matched_tokens: Sequence[str],
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "recent_action_count": len(trajectory.recent_action_tokens),
        "matched_token_count": len(matched_tokens),
        "unsatisfied_criterion_ids": list(trajectory.unsatisfied_criterion_ids),
    }
    if trajectory.observed_mission_type:
        evidence["observed_mission_type"] = trajectory.observed_mission_type
    if trajectory.expected_mission_type:
        evidence["expected_mission_type"] = trajectory.expected_mission_type
    if extra:
        evidence.update(dict(extra))
    return evidence


def detect_goal_drift(
    *,
    goal: Goal,
    trajectory: ActionTrajectoryRecord,
    thresholds: DriftDetectionThresholds | None = None,
    detected_at: str,
    signal_id: str,
) -> GoalDriftSignal | None:
    """Detect structural goal drift from the goal and recent trajectory."""

    if thresholds is None:
        thresholds = DriftDetectionThresholds()

    recent_tokens = tuple(trajectory.recent_action_tokens)
    structural_checks = _structural_check_set(goal)

    if (
        trajectory.expected_mission_type
        and trajectory.observed_mission_type
        and trajectory.observed_mission_type != trajectory.expected_mission_type
    ):
        return GoalDriftSignal(
            signal_id=signal_id,
            goal_id=goal.goal_id,
            kind="mission_type_drift",
            description=(
                f"Observed mission-type mix {trajectory.observed_mission_type!r} "
                f"differs from expected {trajectory.expected_mission_type!r}"
            ),
            detected_at=detected_at,
            evidence=_build_evidence(
                trajectory=trajectory,
                matched_tokens=(),
            ),
        )

    # Bail early if we don't have enough action history to score.
    if len(recent_tokens) < thresholds.min_recent_actions:
        return None

    matched_tokens = tuple(
        token for token in recent_tokens if token in structural_checks
    )

    sub_token = thresholds.objective_substitution_token.strip()
    if sub_token and sub_token in recent_tokens and len(matched_tokens) == 0:
        return GoalDriftSignal(
            signal_id=signal_id,
            goal_id=goal.goal_id,
            kind="objective_substitution",
            description=(
                f"Recent actions include substitution token {sub_token!r} "
                f"with zero progress on success_criteria"
            ),
            detected_at=detected_at,
            evidence=_build_evidence(
                trajectory=trajectory,
                matched_tokens=matched_tokens,
                extra={"substitution_token": sub_token},
            ),
        )

    divergent_count = len(recent_tokens) - len(matched_tokens)
    if recent_tokens:
        divergence_observed = divergent_count / len(recent_tokens)
    else:
        divergence_observed = 0.0
    if structural_checks and divergence_observed >= thresholds.divergence_ratio:
        return GoalDriftSignal(
            signal_id=signal_id,
            goal_id=goal.goal_id,
            kind="actions_diverge_from_criteria",
            description=(
                f"{divergent_count} of {len(recent_tokens)} recent actions did "
                f"not match any structural success criterion"
            ),
            detected_at=detected_at,
            evidence=_build_evidence(
                trajectory=trajectory,
                matched_tokens=matched_tokens,
                extra={"divergence_ratio_observed": round(divergence_observed, 4)},
            ),
        )

    if (
        trajectory.unsatisfied_criterion_ids
        and trajectory.actions_since_progress >= thresholds.inaction_action_count
    ):
        return GoalDriftSignal(
            signal_id=signal_id,
            goal_id=goal.goal_id,
            kind="inaction_against_criteria",
            description=(
                f"No progress on {len(trajectory.unsatisfied_criterion_ids)} "
                f"unsatisfied criteria across "
                f"{trajectory.actions_since_progress} actions"
            ),
            detected_at=detected_at,
            evidence=_build_evidence(
                trajectory=trajectory,
                matched_tokens=matched_tokens,
                extra={
                    "actions_since_progress": trajectory.actions_since_progress,
                },
            ),
        )

    return None


__all__ = [
    "ActionTrajectoryRecord",
    "DriftDetectionThresholds",
    "detect_goal_drift",
]
