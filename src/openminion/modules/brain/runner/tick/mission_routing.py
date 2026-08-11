from __future__ import annotations

from ...constants import (
    BRAIN_STATE_ACTIVE,
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
    MissionStatus,
)
from ...diagnostics.transitions import set_status_unchecked
from ...execution.mission import (
    allocate_mission_turn_budget,
    build_mission_state,
    mission_is_active,
    reset_policy_for,
    set_mission_status,
    update_mission_objective,
    update_mission_task,
)
from ...schemas import iso_now
from .context import TickRunContext, _runner_delegate


def _append_original_turn(*, runner, tick_ctx: TickRunContext) -> None:
    runner.session_api.append_turn(
        tick_ctx.session_id,
        "user",
        str(tick_ctx.original_user_input or ""),
        meta={"ts": iso_now()},
    )


def _set_reset_policy(*, state, tick_ctx: TickRunContext, route_action: str) -> None:
    policy_name = reset_policy_for(route_action=route_action).name
    tick_ctx.forced_reset_policy_name = policy_name
    state.mission.latest_reset_policy = policy_name


def handle(*, runner, state, logger, tick_ctx: TickRunContext):
    mission_route = tick_ctx.mission_route
    if mission_route is None:
        raise RuntimeError("mission route must be resolved before routing")
    if tick_ctx.has_new_user_input and mission_route.action == "start":
        state.mission = build_mission_state(
            runner=runner,
            state=state,
            objective=mission_route.objective,
        )
        allocate_mission_turn_budget(runner=runner, state=state)
        state.goal = state.mission.objective
        state.mission.latest_route_action = mission_route.action
        _set_reset_policy(
            state=state, tick_ctx=tick_ctx, route_action=mission_route.action
        )
        logger.emit(
            "brain.mission.started",
            {
                "mission_id": state.mission.mission_id,
                "objective": state.mission.objective,
                "reset_policy": tick_ctx.forced_reset_policy_name,
            },
            trace_id=state.trace_id,
        )
        logger.emit(
            "brain.mission.budget_allocated",
            {
                "mission_id": state.mission.mission_id,
                "ticks": state.budgets_remaining.ticks,
                "tool_calls": state.budgets_remaining.tool_calls,
                "a2a_calls": state.budgets_remaining.a2a_calls,
                "tokens": state.budgets_remaining.tokens,
                "time_ms": state.budgets_remaining.time_ms,
            },
            trace_id=state.trace_id,
        )
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        tick_ctx.skip_initial_append = True
        tick_ctx.user_input = state.mission.objective
    elif state.mission is not None and mission_route.action == "revise":
        update_mission_objective(
            mission=state.mission,
            objective=mission_route.objective,
        )
        allocate_mission_turn_budget(runner=runner, state=state)
        update_mission_task(runner=runner, mission=state.mission)
        _set_reset_policy(
            state=state, tick_ctx=tick_ctx, route_action=mission_route.action
        )
        logger.emit(
            "brain.mission.revised",
            {
                "mission_id": state.mission.mission_id,
                "objective": state.mission.objective,
                "reset_policy": tick_ctx.forced_reset_policy_name,
            },
            trace_id=state.trace_id,
        )
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        tick_ctx.skip_initial_append = True
        tick_ctx.user_input = state.mission.objective
    elif state.mission is not None and mission_route.action == "continue":
        allocate_mission_turn_budget(runner=runner, state=state)
        state.goal = state.mission.objective
        state.mission.latest_route_action = mission_route.action
        _set_reset_policy(
            state=state, tick_ctx=tick_ctx, route_action=mission_route.action
        )
        logger.emit(
            "brain.mission.continued",
            {
                "mission_id": state.mission.mission_id,
                "objective": state.mission.objective,
                "reset_policy": tick_ctx.forced_reset_policy_name,
            },
            trace_id=state.trace_id,
        )
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        _runner_delegate(
            "_interpret",
            runner,
            state=state,
            user_input=state.mission.objective,
            logger=logger,
            reset_policy_name=tick_ctx.forced_reset_policy_name,
        )
        set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="new_turn")
        tick_ctx.skip_initial_append = True
        tick_ctx.skip_initial_interpret = True
        tick_ctx.user_input = None
    elif state.mission is not None and mission_route.action == "finish":
        allocate_mission_turn_budget(runner=runner, state=state)
        state.goal = state.mission.objective
        state.mission.latest_route_action = mission_route.action
        state.mission.latest_reason = "mission finish requested"
        _set_reset_policy(
            state=state, tick_ctx=tick_ctx, route_action=mission_route.action
        )
        logger.emit(
            "brain.mission.finish_requested",
            {
                "mission_id": state.mission.mission_id,
                "objective": state.mission.objective,
            },
            trace_id=state.trace_id,
        )
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        _runner_delegate(
            "_interpret",
            runner,
            state=state,
            user_input=state.mission.objective,
            logger=logger,
            reset_policy_name=tick_ctx.forced_reset_policy_name,
        )
        set_status_unchecked(state, BRAIN_STATE_ACTIVE, reason="new_turn")
        tick_ctx.skip_initial_append = True
        tick_ctx.skip_initial_interpret = True
        tick_ctx.user_input = None
    elif state.mission is not None and mission_route.action == "pause":
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        set_mission_status(
            mission=state.mission,
            status=MissionStatus.PAUSED,
            reason="mission paused by user",
            route_action=mission_route.action,
        )
        update_mission_task(runner=runner, mission=state.mission, to_state="paused")
        logger.emit(
            "brain.mission.paused",
            {
                "mission_id": state.mission.mission_id,
                "objective": state.mission.objective,
                "reason": "mission paused by user",
                "route_action": mission_route.action,
            },
            trace_id=state.trace_id,
        )
        _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "Mission paused. Resume with `continue mission`, revise it, "
                "or fork a new turn."
            ),
            status=BRAIN_STATE_WAITING_USER,
        )
    elif state.mission is not None and mission_route.action == "cancel":
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        set_mission_status(
            mission=state.mission,
            status=MissionStatus.CANCELLED,
            reason="mission cancelled by user",
            route_action=mission_route.action,
        )
        update_mission_task(
            runner=runner,
            mission=state.mission,
            to_state="cancelled",
        )
        logger.emit(
            "brain.mission.cancelled",
            {
                "mission_id": state.mission.mission_id,
                "objective": state.mission.objective,
                "reason": "mission cancelled by user",
                "route_action": mission_route.action,
            },
            trace_id=state.trace_id,
        )
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message="Mission cancelled.",
            status=BRAIN_STATE_STOPPED,
        )
    elif state.mission is not None and mission_route.action == "fork":
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        if not str(mission_route.ordinary_input or "").strip():
            return _runner_delegate(
                "_respond_with_meta",
                runner,
                state=state,
                logger=logger,
                message="Provide a non-mission request after `fork:`.",
                status=BRAIN_STATE_WAITING_USER,
            )
        set_mission_status(
            mission=state.mission,
            status=MissionStatus.PAUSED,
            reason="mission paused by forked ordinary turn",
            route_action=mission_route.action,
        )
        update_mission_task(runner=runner, mission=state.mission, to_state="paused")
        logger.emit(
            "brain.mission.paused",
            {
                "mission_id": state.mission.mission_id,
                "objective": state.mission.objective,
                "reason": "mission paused by forked ordinary turn",
                "route_action": mission_route.action,
            },
            trace_id=state.trace_id,
        )
        _set_reset_policy(
            state=state, tick_ctx=tick_ctx, route_action=mission_route.action
        )
        tick_ctx.skip_initial_append = True
        tick_ctx.user_input = mission_route.ordinary_input

    if (
        tick_ctx.has_new_user_input
        and mission_is_active(state)
        and mission_route.action == "ordinary"
    ):
        _append_original_turn(runner=runner, tick_ctx=tick_ctx)
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "An active mission is already in progress. Use `continue mission`, "
                "`finish mission`, `revise mission: ...`, `pause mission`, "
                "`cancel mission`, or `fork: ...`."
            ),
            status=BRAIN_STATE_WAITING_USER,
        )
    return None
