from __future__ import annotations

from typing import TYPE_CHECKING

from openminion.base.config.env import resolve_environment_config

from ..constants import (
    BRAIN_ACTIVE_STATES,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_WAITING_USER,
)
from ..config import RunnerOptions
from ..diagnostics.events import CanonicalEventLogger
from ..meta.evaluator import MetaRulesEngine
from ..execution.mission import mission_is_active, set_mission_status
from ..schemas import MetaResult

RUN_TRIGGER_USER_INPUT = "user_input"
RUN_TRIGGER_PLAN_CONTINUATION = "plan_continuation"
RUN_TRIGGER_IDLE_TICK = "idle_tick"
_SUPPORTED_RUN_TRIGGERS = frozenset(
    {
        RUN_TRIGGER_USER_INPUT,
        RUN_TRIGGER_PLAN_CONTINUATION,
        RUN_TRIGGER_IDLE_TICK,
    }
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .coordinator import BrainRunner
    from ..schemas import StepOutput


def configure_runtime_controls(
    runner: "BrainRunner",
    *,
    meta_api,
    options: RunnerOptions | None,
) -> None:
    runner.options = options or RunnerOptions(
        reflection_enabled=runner.profile.defaults.auto_save_lessons
        or runner.profile.defaults.auto_stage_policy_candidates
    )

    runner._prompt_tool_schemas_enabled = str(
        resolve_environment_config().get("OPENMINION_PROMPT_TOOL_SCHEMAS", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    meta_cfg = runner.options.metactl_config if runner.options.metactl_enabled else None
    runner.meta_engine = (
        MetaRulesEngine(meta_cfg)
        if runner.options.metactl_enabled and meta_api is None
        else None
    )
    runner._meta_overrides: dict[str, MetaResult | None] | None = None
    runner._last_meta_application = None


def run_until_idle(
    runner: "BrainRunner",
    *,
    session_id: str,
    user_input: str | None,
    trace_id: str | None,
    forced_tools: list[str] | None,
    capability_category: str | None,
    trigger: str = RUN_TRIGGER_USER_INPUT,
) -> "StepOutput":
    trigger_mode = str(trigger or RUN_TRIGGER_USER_INPUT).strip()
    if trigger_mode not in _SUPPORTED_RUN_TRIGGERS:
        trigger_mode = RUN_TRIGGER_USER_INPUT
    if trigger_mode == RUN_TRIGGER_PLAN_CONTINUATION:
        user_input = None
        _emit_plan_continuation_started(
            runner=runner,
            session_id=session_id,
            trace_id=trace_id,
        )
    elif trigger_mode == RUN_TRIGGER_IDLE_TICK:
        user_input = None
        _emit_idle_tick_started(
            runner=runner,
            session_id=session_id,
            trace_id=trace_id,
        )

    max_iterations = max(
        1, int(getattr(runner.options, "plan_max_iterations", 64) or 64)
    )
    iterations = 0
    next_input = user_input
    first_trace = trace_id
    runner._pending_run_trigger = trigger_mode
    last = runner.step(
        session_id=session_id,
        user_input=next_input,
        trace_id=first_trace,
        forced_tools=forced_tools,
        capability_category=capability_category,
    )
    iterations += 1
    next_input = None
    first_trace = None

    while last.status in BRAIN_ACTIVE_STATES:
        if iterations >= max_iterations:
            return _terminate_loop(
                runner,
                last=last,
                event="brain.loop_safety.hard_cap",
                message=(
                    "Paused autonomous execution after reaching the iteration safety cap. "
                    "Continue in a new turn."
                ),
                details={
                    "iterations": iterations,
                    "max_iterations": max_iterations,
                },
            )
        if last.working_state.budgets_remaining.ticks <= 0:
            return _terminate_loop(
                runner,
                last=last,
                event="brain.loop_safety.budget_exhausted",
                message=(
                    "Paused autonomous execution because tick budget is exhausted. "
                    "Continue in a new turn."
                ),
                details={
                    "iterations": iterations,
                    "ticks_remaining": last.working_state.budgets_remaining.ticks,
                },
            )
        if last.status == BRAIN_STATE_JOB_PENDING and not getattr(
            last.working_state, "pending_jobs", []
        ):
            if (
                mission_is_active(last.working_state)
                and last.working_state.mission is not None
            ):
                set_mission_status(
                    mission=last.working_state.mission,
                    status="paused",
                    reason="mission paused because async state had no pending jobs to poll",
                    route_action=str(
                        getattr(last.working_state.mission, "latest_route_action", "")
                        or ""
                    ),
                )
                CanonicalEventLogger(
                    session_api=runner.session_api,
                    session_id=session_id,
                    agent_id=runner.profile.agent_id,
                ).emit(
                    "brain.mission.paused",
                    {
                        "mission_id": last.working_state.mission.mission_id,
                        "objective": last.working_state.mission.objective,
                        "reason": (
                            "mission paused because async state had no pending jobs "
                            "to poll"
                        ),
                        "route_action": str(
                            getattr(
                                last.working_state.mission,
                                "latest_route_action",
                                "",
                            )
                            or ""
                        ),
                    },
                    trace_id=last.working_state.trace_id,
                )
                return _terminate_loop(
                    runner,
                    last=last,
                    event="brain.mission.async_empty_pending",
                    message=(
                        "Mission paused because async work lost its pending handle. "
                        "Continue, revise, or fork a new turn."
                    ),
                    details={"iterations": iterations},
                )
            break
        previous_status = str(last.status)
        last = runner.step(
            session_id=session_id,
            user_input=next_input,
            trace_id=first_trace,
        )
        iterations += 1
        if (
            previous_status == BRAIN_STATE_JOB_PENDING
            and last.status == BRAIN_STATE_JOB_PENDING
        ):
            if (
                mission_is_active(last.working_state)
                and last.working_state.mission is not None
            ):
                set_mission_status(
                    mission=last.working_state.mission,
                    status="awaiting_async",
                    reason="mission is still waiting on async work",
                    route_action=str(
                        getattr(last.working_state.mission, "latest_route_action", "")
                        or ""
                    ),
                )
            break
    return last


def _emit_plan_continuation_started(
    *,
    runner: "BrainRunner",
    session_id: str,
    trace_id: str | None,
) -> None:
    """CTGP-02: telemetry marker for plan-continuation entry."""
    try:
        logger = CanonicalEventLogger(
            session_api=runner.session_api,
            session_id=session_id,
            agent_id=runner.profile.agent_id,
        )
        logger.emit(
            "brain.autonomous_continuation.started",
            {"trigger": RUN_TRIGGER_PLAN_CONTINUATION},
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        return


def _emit_idle_tick_started(
    *,
    runner: "BrainRunner",
    session_id: str,
    trace_id: str | None,
) -> None:
    """PAE-03: telemetry marker for idle-tick entry."""
    try:
        logger = CanonicalEventLogger(
            session_api=runner.session_api,
            session_id=session_id,
            agent_id=runner.profile.agent_id,
        )
        logger.emit(
            "brain.idle_tick.started",
            {"trigger": RUN_TRIGGER_IDLE_TICK},
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        return


def _terminate_loop(
    runner: "BrainRunner",
    *,
    last: "StepOutput",
    event: str,
    message: str,
    details: dict[str, int] | None = None,
) -> "StepOutput":
    state = last.working_state
    logger = CanonicalEventLogger(
        session_api=runner.session_api,
        session_id=state.session_id,
        agent_id=runner.profile.agent_id,
    )
    payload: dict[str, int | str] = {
        "status_before": str(last.status),
        "cursor": int(getattr(state, "cursor", 0) or 0),
        "ticks_remaining": int(getattr(state.budgets_remaining, "ticks", 0) or 0),
    }
    if details:
        payload.update(details)
    logger.emit(event, payload, trace_id=state.trace_id)
    return runner._respond_with_meta(
        state=state,
        logger=logger,
        message=message,
        status=BRAIN_STATE_WAITING_USER,
        action_result=last.action_result,
    )
