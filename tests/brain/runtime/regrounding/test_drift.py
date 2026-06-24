from __future__ import annotations

from openminion.modules.brain.runtime.drift import (
    ActionTrajectoryRecord,
    DriftDetectionThresholds,
    detect_goal_drift,
)
from openminion.modules.brain.schemas.goals import (
    Deliverable,
    Goal,
    SuccessCriterion,
)


def _make_goal():
    return Goal(
        goal_id="g-1",
        description="Ship MRDD",
        success_criteria=[
            SuccessCriterion(
                criterion_id="c1",
                description="lands",
                structural_check="regrounding.module_exists",
            ),
            SuccessCriterion(
                criterion_id="c2",
                description="tests pass",
                structural_check="regrounding.tests_pass",
            ),
        ],
        deliverables=[Deliverable(deliverable_id="d1", description="slice")],
    )


def _detect(traj, *, thresholds=None):
    return detect_goal_drift(
        goal=_make_goal(),
        trajectory=traj,
        thresholds=thresholds,
        detected_at="2026-05-26",
        signal_id="sig-test",
    )


def test_mission_type_drift_fires_when_observed_differs_from_expected():

    traj = ActionTrajectoryRecord(
        recent_action_tokens=("a", "b", "c", "d"),
        observed_mission_type="research",
        expected_mission_type="coding",
    )
    sig = _detect(traj)
    assert sig is not None
    assert sig.kind == "mission_type_drift"


def test_actions_diverge_when_no_recent_tokens_match_structural_checks():

    traj = ActionTrajectoryRecord(recent_action_tokens=("foo", "bar", "baz", "quux"))
    sig = _detect(traj)
    assert sig is not None
    assert sig.kind == "actions_diverge_from_criteria"
    assert sig.evidence["divergence_ratio_observed"] == 1.0


def test_no_drift_when_actions_match_structural_checks():

    traj = ActionTrajectoryRecord(
        recent_action_tokens=(
            "regrounding.module_exists",
            "regrounding.tests_pass",
            "regrounding.module_exists",
        )
    )
    assert _detect(traj) is None


def test_inaction_against_criteria_fires_after_threshold():

    # All match criteria so divergence check is skipped; but no
    # progress on c2 across 10 actions triggers inaction.
    traj = ActionTrajectoryRecord(
        recent_action_tokens=(
            "regrounding.module_exists",
            "regrounding.module_exists",
            "regrounding.module_exists",
        ),
        unsatisfied_criterion_ids=("c2",),
        actions_since_progress=10,
    )
    sig = _detect(traj)
    assert sig is not None
    assert sig.kind == "inaction_against_criteria"
    assert sig.evidence["actions_since_progress"] == 10


def test_objective_substitution_fires_with_threshold_opt_in():

    traj = ActionTrajectoryRecord(
        recent_action_tokens=("sub", "sub", "sub"),
    )
    sig = _detect(
        traj, thresholds=DriftDetectionThresholds(objective_substitution_token="sub")
    )
    assert sig is not None
    assert sig.kind == "objective_substitution"


def test_below_min_recent_actions_returns_none():

    traj = ActionTrajectoryRecord(recent_action_tokens=("x", "y"))
    assert _detect(traj) is None


def test_mission_type_drift_takes_priority_over_divergence():

    traj = ActionTrajectoryRecord(
        recent_action_tokens=("foo", "bar", "baz"),  # 100% divergent
        observed_mission_type="research",
        expected_mission_type="coding",
    )
    sig = _detect(traj)
    assert sig is not None
    assert sig.kind == "mission_type_drift"


def test_partial_match_below_divergence_threshold_returns_none():

    traj = ActionTrajectoryRecord(
        recent_action_tokens=(
            "regrounding.module_exists",
            "regrounding.module_exists",
            "regrounding.tests_pass",
            "foo",
            "bar",
        )
    )
    # 2 of 5 = 40% divergent; below 66% default; no signal
    assert _detect(traj) is None


def test_signal_evidence_contains_unsatisfied_criterion_ids():

    traj = ActionTrajectoryRecord(
        recent_action_tokens=("foo", "bar", "baz"),
        unsatisfied_criterion_ids=("c1", "c2"),
    )
    sig = _detect(traj)
    assert sig is not None
    assert sig.evidence["unsatisfied_criterion_ids"] == ["c1", "c2"]
