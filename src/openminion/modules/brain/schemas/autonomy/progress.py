from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NoProgressWatchdogKind = Literal[
    "circular_repeat",
    "repeated_identical_shape_failure",
    "no_new_record_or_artifact",
    "research_returning_identical_evidence",
]

SbspBudgetAxis = Literal[
    "iterations",
    "tool_calls",
    "wall_clock_seconds",
    "dollar_cost_cents",
]


class ProgressSignal(BaseModel):
    """Typed structural per-turn progress record. APBR-01 owner surface."""

    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0)
    new_typed_record_delta: int = Field(default=0, ge=0)
    new_artifact_ref_delta: int = Field(default=0, ge=0)
    tool_call_delta: int = Field(default=0, ge=0)
    verifier_result_delta: int = Field(default=0, ge=0)
    session_event_delta: int = Field(default=0, ge=0)
    forward_motion: bool = False
    run_id: str | None = None


def compose_progress_signal(
    *,
    turn_index: int,
    new_typed_record_delta: int = 0,
    new_artifact_ref_delta: int = 0,
    tool_call_delta: int = 0,
    verifier_result_delta: int = 0,
    session_event_delta: int = 0,
    run_id: str | None = None,
) -> ProgressSignal:
    """Pure composer for ``ProgressSignal``."""

    counters = (
        int(new_typed_record_delta),
        int(new_artifact_ref_delta),
        int(tool_call_delta),
        int(verifier_result_delta),
        int(session_event_delta),
    )
    for value in counters:
        if value < 0:
            raise ValueError("ProgressSignal counters must be non-negative")
    forward_motion = any(value > 0 for value in counters)
    return ProgressSignal(
        turn_index=int(turn_index),
        new_typed_record_delta=counters[0],
        new_artifact_ref_delta=counters[1],
        tool_call_delta=counters[2],
        verifier_result_delta=counters[3],
        session_event_delta=counters[4],
        forward_motion=forward_motion,
        run_id=run_id,
    )


class SbspBudgetCeilings(BaseModel):
    """Operator-config ceiling vector for SBSP precedence ladder."""

    model_config = ConfigDict(extra="forbid")

    max_iterations: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_wall_clock_seconds: int | None = Field(default=None, ge=1)
    max_dollar_cost_cents: int | None = Field(default=None, ge=1)


class SbspBudgetUsage(BaseModel):
    """Typed structural per-axis usage vector."""

    model_config = ConfigDict(extra="forbid")

    iterations_used: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)
    wall_clock_seconds_used: int = Field(default=0, ge=0)
    dollar_cost_cents_used: int = Field(default=0, ge=0)


class BudgetExtensionTrigger(BaseModel):
    """Typed extension recommendation. APBR-02 owner surface."""

    model_config = ConfigDict(extra="forbid")

    may_extend: bool
    blocked_axes: tuple[SbspBudgetAxis, ...] = Field(default_factory=tuple)
    progress: ProgressSignal
    ceilings: SbspBudgetCeilings
    usage: SbspBudgetUsage


def compute_budget_extension_trigger(
    *,
    progress: ProgressSignal,
    ceilings: SbspBudgetCeilings,
    usage: SbspBudgetUsage,
) -> BudgetExtensionTrigger:
    """Pure composer for ``BudgetExtensionTrigger``."""

    blocked: list[SbspBudgetAxis] = []
    if ceilings.max_iterations is not None and (
        usage.iterations_used >= ceilings.max_iterations
    ):
        blocked.append("iterations")
    if ceilings.max_tool_calls is not None and (
        usage.tool_calls_used >= ceilings.max_tool_calls
    ):
        blocked.append("tool_calls")
    if ceilings.max_wall_clock_seconds is not None and (
        usage.wall_clock_seconds_used >= ceilings.max_wall_clock_seconds
    ):
        blocked.append("wall_clock_seconds")
    if ceilings.max_dollar_cost_cents is not None and (
        usage.dollar_cost_cents_used >= ceilings.max_dollar_cost_cents
    ):
        blocked.append("dollar_cost_cents")

    may_extend = bool(progress.forward_motion) and not blocked
    return BudgetExtensionTrigger(
        may_extend=may_extend,
        blocked_axes=tuple(blocked),
        progress=progress,
        ceilings=ceilings,
        usage=usage,
    )


class MissionProgressCheckpoint(BaseModel):
    """Typed checkpoint record projected from the session-event substrate."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    turn_index: int = Field(ge=0)
    progress: ProgressSignal
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple)
    verifier_result_refs: tuple[str, ...] = Field(default_factory=tuple)


def should_emit_checkpoint(progress: ProgressSignal) -> bool:
    """Structural-delta cadence per APBR-Q4."""

    return bool(
        progress.new_typed_record_delta > 0
        or progress.new_artifact_ref_delta > 0
        or progress.verifier_result_delta > 0
    )


class NoProgressWatchdogConfig(BaseModel):
    """Operator-tunable per-axis thresholds for the watchdog taxonomy."""

    model_config = ConfigDict(extra="forbid")

    circular_repeat_threshold: int = Field(default=3, ge=0)
    repeated_identical_shape_failure_threshold: int = Field(default=3, ge=0)
    no_new_record_or_artifact_threshold: int = Field(default=2, ge=0)
    research_returning_identical_evidence_threshold: int = Field(default=2, ge=0)


class NoProgressWatchdogCounters(BaseModel):
    """Typed structural counter vector consumed by the watchdog evaluator."""

    model_config = ConfigDict(extra="forbid")

    circular_repeat_count: int = Field(default=0, ge=0)
    repeated_identical_shape_failure_count: int = Field(default=0, ge=0)
    no_new_record_or_artifact_count: int = Field(default=0, ge=0)
    research_returning_identical_evidence_count: int = Field(default=0, ge=0)


class NoProgressWatchdogTrigger(BaseModel):
    """Typed fire-or-pass record from the watchdog evaluator."""

    model_config = ConfigDict(extra="forbid")

    fired: bool
    kinds: tuple[NoProgressWatchdogKind, ...] = Field(default_factory=tuple)
    counters: NoProgressWatchdogCounters
    config: NoProgressWatchdogConfig


def evaluate_no_progress_watchdog(
    *,
    counters: NoProgressWatchdogCounters,
    config: NoProgressWatchdogConfig,
) -> NoProgressWatchdogTrigger:
    """Pure structural evaluator for the watchdog taxonomy."""

    fired: list[NoProgressWatchdogKind] = []
    if (
        config.circular_repeat_threshold > 0
        and counters.circular_repeat_count >= config.circular_repeat_threshold
    ):
        fired.append("circular_repeat")
    if (
        config.repeated_identical_shape_failure_threshold > 0
        and counters.repeated_identical_shape_failure_count
        >= config.repeated_identical_shape_failure_threshold
    ):
        fired.append("repeated_identical_shape_failure")
    if (
        config.no_new_record_or_artifact_threshold > 0
        and counters.no_new_record_or_artifact_count
        >= config.no_new_record_or_artifact_threshold
    ):
        fired.append("no_new_record_or_artifact")
    if (
        config.research_returning_identical_evidence_threshold > 0
        and counters.research_returning_identical_evidence_count
        >= config.research_returning_identical_evidence_threshold
    ):
        fired.append("research_returning_identical_evidence")
    return NoProgressWatchdogTrigger(
        fired=bool(fired),
        kinds=tuple(fired),
        counters=counters,
        config=config,
    )


__all__ = [
    "BudgetExtensionTrigger",
    "MissionProgressCheckpoint",
    "NoProgressWatchdogConfig",
    "NoProgressWatchdogCounters",
    "NoProgressWatchdogKind",
    "NoProgressWatchdogTrigger",
    "ProgressSignal",
    "SbspBudgetAxis",
    "SbspBudgetCeilings",
    "SbspBudgetUsage",
    "compose_progress_signal",
    "compute_budget_extension_trigger",
    "evaluate_no_progress_watchdog",
    "should_emit_checkpoint",
]
