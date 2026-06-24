from __future__ import annotations

from openminion.modules.brain.runtime.drift import ActionTrajectoryRecord
from openminion.modules.brain.runtime.mrdd.tick import (
    MRDDTickInputs,
    resolve_policy_from_metadata,
    run_mrdd_tick,
)
from openminion.modules.brain.runtime.regrounding import RegroundingPolicy
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
        ],
        deliverables=[Deliverable(deliverable_id="d1", description="slice")],
    )


def test_default_policy_outcome_has_no_inject_or_signal():

    out = run_mrdd_tick(
        MRDDTickInputs(
            goal=_make_goal(),
            policy=RegroundingPolicy(),
            cadence_counter=0,
            just_compacted=False,
        )
    )
    assert out.inject is None
    assert out.signal is None


def test_enabled_policy_emits_inject_at_cadence():

    out = run_mrdd_tick(
        MRDDTickInputs(
            goal=_make_goal(),
            policy=RegroundingPolicy(enabled=True, cadence_turns=2),
            cadence_counter=2,
            just_compacted=False,
        )
    )
    assert out.inject is not None
    assert out.next_counter == 0


def test_coordinator_emits_signal_when_trajectory_provided():

    out = run_mrdd_tick(
        MRDDTickInputs(
            goal=_make_goal(),
            policy=RegroundingPolicy(enabled=True, cadence_turns=10),
            cadence_counter=1,
            just_compacted=False,
            trajectory=ActionTrajectoryRecord(
                recent_action_tokens=("foo", "bar", "baz", "quux")
            ),
            detected_at="2026-05-26",
            signal_id="sig-1",
        )
    )
    assert out.signal is not None
    assert out.signal.kind == "actions_diverge_from_criteria"


def test_coordinator_skips_detector_without_trajectory():

    out = run_mrdd_tick(
        MRDDTickInputs(
            goal=_make_goal(),
            policy=RegroundingPolicy(enabled=True, cadence_turns=10),
            cadence_counter=1,
            just_compacted=False,
            trajectory=None,
        )
    )
    assert out.signal is None


def test_resolve_policy_from_metadata_defaults_disabled():

    p = resolve_policy_from_metadata({})
    assert p.enabled is False
    assert p.cadence_turns == 10
    assert p.inject_after_compaction is True


def test_resolve_policy_from_metadata_honors_opt_in():

    p = resolve_policy_from_metadata(
        {
            "mrdd_regrounding_enabled": "true",
            "mrdd_regrounding_cadence_turns": "5",
            "mrdd_regrounding_inject_after_compaction": "false",
        }
    )
    assert p.enabled is True
    assert p.cadence_turns == 5
    assert p.inject_after_compaction is False


def test_resolve_policy_from_metadata_falls_back_on_invalid_input():

    p = resolve_policy_from_metadata({"mrdd_regrounding_cadence_turns": "not_a_number"})
    assert p.cadence_turns == 10
