from __future__ import annotations

import pytest

from openminion.modules.brain.runtime.regrounding import (
    RegroundingPolicy,
    RegroundingTrigger,
    build_regrounding_inject,
    build_regrounding_inject_text,
    compose_regrounding_section,
    evaluate_regrounding_tick,
    should_inject_regrounding,
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
                description="lands clean",
                structural_check="regrounding.module_exists",
            ),
        ],
        deliverables=[Deliverable(deliverable_id="d1", description="slice")],
    )


def test_default_policy_is_production_safe_disabled():

    policy = RegroundingPolicy()
    assert policy.enabled is False
    assert policy.cadence_turns >= 1
    assert policy.inject_after_compaction is True


def test_disabled_policy_never_injects_even_at_high_counter():

    decision = should_inject_regrounding(
        policy=RegroundingPolicy(enabled=False, cadence_turns=1),
        cadence_counter=999,
        just_compacted=True,
    )
    assert decision.should_inject is False
    assert decision.trigger is None


def test_enabled_cadence_threshold_triggers_inject():

    decision = should_inject_regrounding(
        policy=RegroundingPolicy(enabled=True, cadence_turns=3),
        cadence_counter=3,
        just_compacted=False,
    )
    assert decision.should_inject is True
    assert decision.trigger is not None
    assert decision.trigger.kind == "cadence"


def test_enabled_post_compaction_triggers_inject():

    decision = should_inject_regrounding(
        policy=RegroundingPolicy(
            enabled=True, cadence_turns=10, inject_after_compaction=True
        ),
        cadence_counter=0,
        just_compacted=True,
    )
    assert decision.should_inject is True
    assert decision.trigger.kind == "post_compaction"


def test_forced_bypasses_disabled_policy():

    decision = should_inject_regrounding(
        policy=RegroundingPolicy(enabled=False),
        cadence_counter=0,
        just_compacted=False,
        forced=True,
    )
    assert decision.should_inject is True
    assert decision.trigger.kind == "forced"


def test_inject_text_is_deterministic():

    goal = _make_goal()
    a = build_regrounding_inject_text(goal)
    b = build_regrounding_inject_text(goal)
    assert a == b
    assert "[mrdd:regrounding]" in a
    assert "goal_id=g-1" in a
    assert "structural_check=regrounding.module_exists" in a


def test_build_regrounding_inject_rejects_unknown_trigger_kind():

    goal = _make_goal()
    bad = RegroundingTrigger(kind="nonsense")
    with pytest.raises(ValueError):
        build_regrounding_inject(goal=goal, trigger=bad)


def test_evaluate_regrounding_tick_resets_counter_on_inject():

    goal = _make_goal()
    policy = RegroundingPolicy(enabled=True, cadence_turns=3)
    # no inject — counter advances
    r = evaluate_regrounding_tick(
        goal=goal, policy=policy, cadence_counter=1, just_compacted=False
    )
    assert r.inject is None
    assert r.next_counter == 2
    # cadence hits — counter resets
    r = evaluate_regrounding_tick(
        goal=goal, policy=policy, cadence_counter=3, just_compacted=False
    )
    assert r.inject is not None
    assert r.next_counter == 0


def test_compose_regrounding_section_is_structural_dict():

    goal = _make_goal()
    r = evaluate_regrounding_tick(
        goal=goal,
        policy=RegroundingPolicy(enabled=True, cadence_turns=1),
        cadence_counter=1,
        just_compacted=False,
    )
    assert r.inject is not None
    section = compose_regrounding_section(r.inject)
    assert section["section"] == "regrounding"
    assert section["goal_id"] == "g-1"
    assert section["trigger_kind"] == "cadence"
    assert "[mrdd:regrounding]" in section["body"]


def test_invalid_cadence_turns_raises():

    with pytest.raises(ValueError):
        RegroundingPolicy(cadence_turns=0)
