from __future__ import annotations

from openminion.modules.brain.schemas.autonomy.progress import (
    MissionProgressCheckpoint,
    NoProgressWatchdogConfig,
    NoProgressWatchdogCounters,
    SbspBudgetCeilings,
    SbspBudgetUsage,
    compose_progress_signal,
    compute_budget_extension_trigger,
    evaluate_no_progress_watchdog,
    should_emit_checkpoint,
)


def test_apbr_end_to_end_turn_simulation() -> None:

    ceilings = SbspBudgetCeilings(
        max_iterations=50,
        max_tool_calls=200,
        max_wall_clock_seconds=300,
        max_dollar_cost_cents=1000,
    )
    watchdog_config = NoProgressWatchdogConfig()  # defaults

    checkpoints: list[MissionProgressCheckpoint] = []
    extension_decisions: list[bool] = []
    watchdog_fires: list[tuple[str, ...]] = []

    # ----- Turn 0: tool calls, no typed records -----
    progress_0 = compose_progress_signal(turn_index=0, tool_call_delta=3)
    assert should_emit_checkpoint(progress_0) is False
    trigger_0 = compute_budget_extension_trigger(
        progress=progress_0,
        ceilings=ceilings,
        usage=SbspBudgetUsage(
            iterations_used=1, tool_calls_used=3, wall_clock_seconds_used=10
        ),
    )
    extension_decisions.append(trigger_0.may_extend)

    # ----- Turn 1: new typed record + artifact -----
    progress_1 = compose_progress_signal(
        turn_index=1,
        new_typed_record_delta=1,
        new_artifact_ref_delta=1,
        tool_call_delta=2,
    )
    assert progress_1.forward_motion is True
    assert should_emit_checkpoint(progress_1) is True
    checkpoints.append(
        MissionProgressCheckpoint(
            checkpoint_id="session-x:turn-1",
            turn_index=1,
            progress=progress_1,
            artifact_refs=("artifact-1",),
        )
    )
    trigger_1 = compute_budget_extension_trigger(
        progress=progress_1,
        ceilings=ceilings,
        usage=SbspBudgetUsage(
            iterations_used=2, tool_calls_used=5, wall_clock_seconds_used=25
        ),
    )
    extension_decisions.append(trigger_1.may_extend)

    # ----- Turn 2: no progress -----
    progress_2 = compose_progress_signal(turn_index=2)
    assert progress_2.forward_motion is False
    assert should_emit_checkpoint(progress_2) is False
    trigger_2 = compute_budget_extension_trigger(
        progress=progress_2,
        ceilings=ceilings,
        usage=SbspBudgetUsage(
            iterations_used=3, tool_calls_used=5, wall_clock_seconds_used=40
        ),
    )
    extension_decisions.append(trigger_2.may_extend)

    # ----- Turn 3+4: circular pattern accumulating -----
    counters_circular = NoProgressWatchdogCounters(circular_repeat_count=2)
    wd_t3 = evaluate_no_progress_watchdog(
        counters=counters_circular, config=watchdog_config
    )
    watchdog_fires.append(wd_t3.kinds)
    assert wd_t3.fired is False  # threshold 3 not yet reached

    counters_circular_fire = NoProgressWatchdogCounters(circular_repeat_count=3)
    wd_t4 = evaluate_no_progress_watchdog(
        counters=counters_circular_fire, config=watchdog_config
    )
    watchdog_fires.append(wd_t4.kinds)
    assert wd_t4.fired is True
    assert "circular_repeat" in wd_t4.kinds

    # ----- Turn 5: wall-clock ceiling reached, would-be-progress blocked -----
    progress_5 = compose_progress_signal(turn_index=5, new_typed_record_delta=1)
    trigger_5 = compute_budget_extension_trigger(
        progress=progress_5,
        ceilings=ceilings,
        usage=SbspBudgetUsage(
            iterations_used=5,
            tool_calls_used=12,
            wall_clock_seconds_used=300,  # at ceiling
            dollar_cost_cents_used=200,
        ),
    )
    extension_decisions.append(trigger_5.may_extend)
    assert trigger_5.may_extend is False
    assert "wall_clock_seconds" in trigger_5.blocked_axes

    assert extension_decisions == [True, True, False, False]
    # Exactly one checkpoint emitted across the simulation (turn 1).
    assert len(checkpoints) == 1
    assert checkpoints[0].turn_index == 1
    # Watchdog fired exactly once (turn 4).
    fired = [k for k in watchdog_fires if k]
    assert len(fired) == 1
    assert "circular_repeat" in fired[0]


def test_apbr_all_four_watchdog_paths_fail_closed_at_thresholds() -> None:

    config = NoProgressWatchdogConfig(
        circular_repeat_threshold=3,
        repeated_identical_shape_failure_threshold=3,
        no_new_record_or_artifact_threshold=2,
        research_returning_identical_evidence_threshold=2,
    )

    # Each axis at its threshold fires exactly the expected kind.
    axis_to_counter = {
        "circular_repeat": NoProgressWatchdogCounters(circular_repeat_count=3),
        "repeated_identical_shape_failure": NoProgressWatchdogCounters(
            repeated_identical_shape_failure_count=3
        ),
        "no_new_record_or_artifact": NoProgressWatchdogCounters(
            no_new_record_or_artifact_count=2
        ),
        "research_returning_identical_evidence": NoProgressWatchdogCounters(
            research_returning_identical_evidence_count=2
        ),
    }
    for axis, counters in axis_to_counter.items():
        result = evaluate_no_progress_watchdog(counters=counters, config=config)
        assert result.fired is True, axis
        assert axis in result.kinds, axis


def test_apbr_budget_extension_respects_all_four_axes() -> None:

    progress = compose_progress_signal(turn_index=1, new_typed_record_delta=1)
    axis_scenarios = [
        (
            SbspBudgetCeilings(max_iterations=10),
            SbspBudgetUsage(iterations_used=10),
            "iterations",
        ),
        (
            SbspBudgetCeilings(max_tool_calls=20),
            SbspBudgetUsage(tool_calls_used=20),
            "tool_calls",
        ),
        (
            SbspBudgetCeilings(max_wall_clock_seconds=60),
            SbspBudgetUsage(wall_clock_seconds_used=60),
            "wall_clock_seconds",
        ),
        (
            SbspBudgetCeilings(max_dollar_cost_cents=500),
            SbspBudgetUsage(dollar_cost_cents_used=500),
            "dollar_cost_cents",
        ),
    ]
    for ceilings, usage, expected_blocked in axis_scenarios:
        trigger = compute_budget_extension_trigger(
            progress=progress, ceilings=ceilings, usage=usage
        )
        assert trigger.may_extend is False, expected_blocked
        assert expected_blocked in trigger.blocked_axes, expected_blocked
