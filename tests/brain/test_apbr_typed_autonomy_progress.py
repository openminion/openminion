from __future__ import annotations

import pytest

from openminion.modules.brain.schemas.autonomy.progress import (
    BudgetExtensionTrigger,
    MissionProgressCheckpoint,
    NoProgressWatchdogConfig,
    NoProgressWatchdogCounters,
    NoProgressWatchdogTrigger,
    ProgressSignal,
    SbspBudgetCeilings,
    SbspBudgetUsage,
    compose_progress_signal,
    compute_budget_extension_trigger,
    evaluate_no_progress_watchdog,
    should_emit_checkpoint,
)


class TestProgressSignal:
    def test_zero_counters_implies_no_forward_motion(self) -> None:
        signal = compose_progress_signal(turn_index=0)
        assert signal.forward_motion is False
        assert signal.turn_index == 0

    def test_any_positive_counter_implies_forward_motion(self) -> None:
        for kwargs in [
            {"new_typed_record_delta": 1},
            {"new_artifact_ref_delta": 1},
            {"tool_call_delta": 1},
            {"verifier_result_delta": 1},
            {"session_event_delta": 1},
        ]:
            signal = compose_progress_signal(turn_index=5, **kwargs)
            assert signal.forward_motion is True, kwargs

    def test_negative_counter_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            compose_progress_signal(turn_index=0, new_typed_record_delta=-1)

    def test_run_id_pointer_is_advisory_and_optional(self) -> None:
        s_without = compose_progress_signal(turn_index=1, tool_call_delta=2)
        s_with = compose_progress_signal(
            turn_index=1, tool_call_delta=2, run_id="run-abc"
        )
        assert s_without.run_id is None
        assert s_with.run_id == "run-abc"
        assert s_without.forward_motion == s_with.forward_motion

    def test_extra_fields_forbidden_by_typed_contract(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
            ProgressSignal(
                turn_index=0,
                forward_motion=False,
                bogus_prose_field="model claimed progress",
            )


class TestBudgetExtensionTrigger:
    def _progress(self, *, forward: bool) -> ProgressSignal:
        return compose_progress_signal(
            turn_index=1,
            new_typed_record_delta=1 if forward else 0,
        )

    def test_no_forward_motion_blocks_extension(self) -> None:
        trigger = compute_budget_extension_trigger(
            progress=self._progress(forward=False),
            ceilings=SbspBudgetCeilings(max_iterations=100),
            usage=SbspBudgetUsage(iterations_used=5),
        )
        assert trigger.may_extend is False

    def test_forward_motion_and_unbounded_ceilings_allows_extension(self) -> None:
        trigger = compute_budget_extension_trigger(
            progress=self._progress(forward=True),
            ceilings=SbspBudgetCeilings(),
            usage=SbspBudgetUsage(iterations_used=999),
        )
        assert trigger.may_extend is True
        assert trigger.blocked_axes == ()

    def test_iteration_ceiling_blocks_extension(self) -> None:
        trigger = compute_budget_extension_trigger(
            progress=self._progress(forward=True),
            ceilings=SbspBudgetCeilings(max_iterations=10),
            usage=SbspBudgetUsage(iterations_used=10),
        )
        assert trigger.may_extend is False
        assert "iterations" in trigger.blocked_axes

    def test_wall_clock_ceiling_blocks_extension(self) -> None:
        trigger = compute_budget_extension_trigger(
            progress=self._progress(forward=True),
            ceilings=SbspBudgetCeilings(max_wall_clock_seconds=60),
            usage=SbspBudgetUsage(wall_clock_seconds_used=60),
        )
        assert trigger.may_extend is False
        assert "wall_clock_seconds" in trigger.blocked_axes

    def test_dollar_cost_ceiling_blocks_extension(self) -> None:
        trigger = compute_budget_extension_trigger(
            progress=self._progress(forward=True),
            ceilings=SbspBudgetCeilings(max_dollar_cost_cents=500),
            usage=SbspBudgetUsage(dollar_cost_cents_used=500),
        )
        assert trigger.may_extend is False
        assert "dollar_cost_cents" in trigger.blocked_axes

    def test_multiple_axes_blocked_reported_together(self) -> None:
        trigger = compute_budget_extension_trigger(
            progress=self._progress(forward=True),
            ceilings=SbspBudgetCeilings(
                max_iterations=10, max_tool_calls=5, max_wall_clock_seconds=60
            ),
            usage=SbspBudgetUsage(
                iterations_used=10, tool_calls_used=5, wall_clock_seconds_used=60
            ),
        )
        assert trigger.may_extend is False
        assert set(trigger.blocked_axes) == {
            "iterations",
            "tool_calls",
            "wall_clock_seconds",
        }

    def test_trigger_record_is_typed_and_exposes_inputs(self) -> None:
        progress = self._progress(forward=True)
        ceilings = SbspBudgetCeilings(max_iterations=10)
        usage = SbspBudgetUsage(iterations_used=2)
        trigger = compute_budget_extension_trigger(
            progress=progress, ceilings=ceilings, usage=usage
        )
        assert isinstance(trigger, BudgetExtensionTrigger)
        assert trigger.progress is progress
        assert trigger.ceilings is ceilings
        assert trigger.usage is usage


class TestMissionProgressCheckpoint:
    def test_zero_deltas_does_not_emit(self) -> None:
        progress = compose_progress_signal(turn_index=1)
        assert should_emit_checkpoint(progress) is False

    def test_typed_record_delta_emits(self) -> None:
        progress = compose_progress_signal(turn_index=1, new_typed_record_delta=1)
        assert should_emit_checkpoint(progress) is True

    def test_artifact_ref_delta_emits(self) -> None:
        progress = compose_progress_signal(turn_index=1, new_artifact_ref_delta=1)
        assert should_emit_checkpoint(progress) is True

    def test_verifier_result_delta_emits(self) -> None:
        progress = compose_progress_signal(turn_index=1, verifier_result_delta=1)
        assert should_emit_checkpoint(progress) is True

    def test_tool_call_delta_alone_does_not_emit(self) -> None:
        progress = compose_progress_signal(turn_index=1, tool_call_delta=5)
        assert should_emit_checkpoint(progress) is False

    def test_session_event_delta_alone_does_not_emit(self) -> None:
        progress = compose_progress_signal(turn_index=1, session_event_delta=3)
        assert should_emit_checkpoint(progress) is False

    def test_checkpoint_payload_is_typed_refs_only(self) -> None:
        progress = compose_progress_signal(turn_index=2, new_artifact_ref_delta=1)
        cp = MissionProgressCheckpoint(
            checkpoint_id="session-x:turn-2",
            turn_index=2,
            progress=progress,
            artifact_refs=("artifact-1",),
            verifier_result_refs=(),
        )
        assert cp.artifact_refs == ("artifact-1",)
        with pytest.raises(Exception):  # noqa: B017
            MissionProgressCheckpoint(
                checkpoint_id="x",
                turn_index=0,
                progress=progress,
                notes="model says we made progress",
            )


class TestNoProgressWatchdog:
    def test_below_thresholds_does_not_fire(self) -> None:
        result = evaluate_no_progress_watchdog(
            counters=NoProgressWatchdogCounters(),
            config=NoProgressWatchdogConfig(),
        )
        assert result.fired is False
        assert result.kinds == ()

    def test_circular_repeat_preserves_existing_seed(self) -> None:
        result = evaluate_no_progress_watchdog(
            counters=NoProgressWatchdogCounters(circular_repeat_count=3),
            config=NoProgressWatchdogConfig(),
        )
        assert result.fired is True
        assert "circular_repeat" in result.kinds

    def test_each_taxonomy_member_fires_independently(self) -> None:
        config = NoProgressWatchdogConfig()
        cases = [
            (
                NoProgressWatchdogCounters(repeated_identical_shape_failure_count=3),
                "repeated_identical_shape_failure",
            ),
            (
                NoProgressWatchdogCounters(no_new_record_or_artifact_count=2),
                "no_new_record_or_artifact",
            ),
            (
                NoProgressWatchdogCounters(
                    research_returning_identical_evidence_count=2
                ),
                "research_returning_identical_evidence",
            ),
        ]
        for counters, expected_kind in cases:
            result = evaluate_no_progress_watchdog(counters=counters, config=config)
            assert result.fired is True
            assert expected_kind in result.kinds, expected_kind

    def test_multiple_axes_fire_together(self) -> None:
        result = evaluate_no_progress_watchdog(
            counters=NoProgressWatchdogCounters(
                circular_repeat_count=3,
                no_new_record_or_artifact_count=2,
            ),
            config=NoProgressWatchdogConfig(),
        )
        assert result.fired is True
        assert set(result.kinds) == {
            "circular_repeat",
            "no_new_record_or_artifact",
        }

    def test_threshold_zero_disables_axis(self) -> None:
        # APBR-Q5: a threshold of 0 disables the axis.
        result = evaluate_no_progress_watchdog(
            counters=NoProgressWatchdogCounters(circular_repeat_count=99),
            config=NoProgressWatchdogConfig(circular_repeat_threshold=0),
        )
        # circular_repeat must NOT fire when its threshold is 0.
        assert "circular_repeat" not in result.kinds

    def test_kinds_are_closed_literal_taxonomy(self) -> None:
        # §3.1.6: free-form watchdog reasons are forbidden. Pydantic
        # rejects arbitrary kind strings.
        with pytest.raises(Exception):  # noqa: B017
            NoProgressWatchdogTrigger(
                fired=True,
                kinds=("model_seems_stuck",),  # type: ignore[arg-type]
                counters=NoProgressWatchdogCounters(),
                config=NoProgressWatchdogConfig(),
            )
