from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from openminion.modules.telemetry.trace.phase_timing import active_chat_phase

from ...constants import BRAIN_STATE_WAITING_USER
from ...execution.entry import build_execution_entry_request, dispatch as dispatch_entry
from ...diagnostics.events import CanonicalEventLogger
from ...execution.mission import mission_enabled, resolve_mission_input_route
from ...runner.transitions import guard_waiting_state
from ...runtime.mrdd.hook import maybe_run_mrdd_pre_dispatch_hook
from ...schemas import new_uuid
from . import (
    confirmation,
    input_processing,
    job_resume,
    mission_routing,
)
from .context import (
    _budget_exhaustion_message,
    _runner_delegate,
    build_tick_run_context,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core import BrainRunner
    from ...schemas import StepOutput


def _stamp_pending_run_context(runner: "BrainRunner", state) -> None:
    pending_trigger = getattr(runner, "_pending_run_trigger", None)
    if pending_trigger:
        try:
            state.run_trigger = str(pending_trigger)
        except Exception:  # noqa: BLE001
            pass
        runner._pending_run_trigger = None

    pending_gateway_context = str(
        getattr(runner, "_pending_gateway_system_context", "") or ""
    ).strip()
    if pending_gateway_context:
        try:
            state.gateway_system_context = pending_gateway_context
        except Exception:  # noqa: BLE001
            pass
        runner._pending_gateway_system_context = None


def _mission_route_for_tick(runner: "BrainRunner", state, user_input: str | None):
    if not mission_enabled(runner):
        return SimpleNamespace(action="ordinary", objective="", ordinary_input="")
    return resolve_mission_input_route(state=state, user_input=user_input)


def _capture_new_user_input(
    runner: "BrainRunner", state, *, user_input: str | None, trace_id: str | None
) -> None:
    state.trace_id = trace_id or new_uuid()
    if getattr(state, "pending_confirmation_command", None) is not None:
        reply = confirmation._parse_confirmation_response(runner, user_input)  # noqa: SLF001
        if reply in {"affirm", "deny"}:
            return
    state.last_user_input = str(user_input or "").strip()


def _run_pre_dispatch_checks(
    runner: "BrainRunner",
    *,
    state,
    logger: CanonicalEventLogger,
    session_id: str,
    tick_ctx,
):
    resumed = job_resume.try_resume(
        runner=runner,
        state=state,
        user_input=tick_ctx.user_input,
        trace_id=tick_ctx.trace_id,
        logger=logger,
        session_id=session_id,
    )
    if resumed is not None:
        return resumed

    replay_result = input_processing.handle_pending_replay(
        runner=runner,
        state=state,
        logger=logger,
        tick_ctx=tick_ctx,
    )
    if replay_result is not None:
        return replay_result

    if tick_ctx.has_new_user_input:
        mission_result = mission_routing.handle(
            runner=runner,
            state=state,
            logger=logger,
            tick_ctx=tick_ctx,
        )
        if mission_result is not None:
            return mission_result

    input_result = input_processing.process_user_input(
        runner=runner,
        state=state,
        logger=logger,
        tick_ctx=tick_ctx,
    )
    if input_result is not None:
        return input_result

    return guard_waiting_state(state=state, user_input=tick_ctx.user_input)


def _handle_budget_exhaustion(
    runner: "BrainRunner",
    *,
    state,
    logger: CanonicalEventLogger,
    budget_stop_reason,
):
    if budget_stop_reason is None:
        return None
    message = _budget_exhaustion_message(budget_stop_reason)
    logger.emit(
        "brain.turn_budget.exhausted",
        {
            "reason": str(budget_stop_reason.value),
            "ticks_remaining": int(getattr(state.budgets_remaining, "ticks", 0) or 0),
            "time_ms_remaining": int(
                getattr(state.budgets_remaining, "time_ms", 0) or 0
            ),
        },
        trace_id=state.trace_id,
    )
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=message,
        status=BRAIN_STATE_WAITING_USER,
    )


def _maybe_run_autonomous_turn(
    runner: "BrainRunner",
    *,
    state,
    logger: CanonicalEventLogger,
    user_input: str | None,
):
    state_mode = getattr(state, "mode", "")
    if hasattr(state_mode, "value"):
        state_mode = getattr(state_mode, "value")
    if not (
        str(state_mode).strip().lower() == "autonomous" and runner.rlm_api is not None
    ):
        return None

    if _runner_delegate("_autonomous_requires_confirmation", runner, state=state):
        logger.emit(
            "brain.recursive_turn.confirmation_required",
            {
                "reason": "autonomous_high_risk_confirmation",
                "query": user_input or state.goal,
            },
            trace_id=state.trace_id,
        )
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=(
                "This autonomous request appears high risk. Please explicitly "
                "confirm before I proceed."
            ),
            status=BRAIN_STATE_WAITING_USER,
        )
    return _runner_delegate(
        "_run_recursive_turn",
        runner,
        state=state,
        user_input=user_input,
        logger=logger,
    )


def run_step(
    runner: "BrainRunner",
    *,
    session_id: str,
    user_input: str | None = None,
    trace_id: str | None = None,
    forced_tools: list[str] | None = None,
    capability_category: str | None = None,
) -> "StepOutput":
    started = _runner_delegate("_now_ms", runner)
    with active_chat_phase("brain_state_load"):
        state = _runner_delegate("_load_or_init_state", runner, session_id)
    _stamp_pending_run_context(runner, state)
    logger = CanonicalEventLogger(
        session_api=runner.session_api,
        session_id=session_id,
        agent_id=runner.profile.agent_id,
    )
    tick_ctx = build_tick_run_context(
        session_id=session_id,
        user_input=user_input,
        trace_id=trace_id,
        forced_tools=forced_tools,
        capability_category=capability_category,
    )

    try:
        tick_ctx.mission_route = _mission_route_for_tick(
            runner, state, tick_ctx.user_input
        )
        if tick_ctx.has_new_user_input:
            _capture_new_user_input(
                runner,
                state,
                user_input=tick_ctx.user_input,
                trace_id=trace_id,
            )

        with active_chat_phase("brain_pre_dispatch"):
            maybe_run_mrdd_pre_dispatch_hook(runner=runner, state=state, logger=logger)

            pre_dispatch_result = _run_pre_dispatch_checks(
                runner,
                state=state,
                logger=logger,
                session_id=session_id,
                tick_ctx=tick_ctx,
            )
        if pre_dispatch_result is not None:
            return pre_dispatch_result

        with active_chat_phase("brain_budget_check"):
            budget_stop_reason = _runner_delegate("_consume_tick", runner, state)
            budget_result = _handle_budget_exhaustion(
                runner,
                state=state,
                logger=logger,
                budget_stop_reason=budget_stop_reason,
            )
        if budget_result is not None:
            return budget_result

        autonomous_result = _maybe_run_autonomous_turn(
            runner,
            state=state,
            logger=logger,
            user_input=tick_ctx.user_input,
        )
        if autonomous_result is not None:
            return autonomous_result

        with active_chat_phase("brain_confirmation"):
            confirmation_result = confirmation.process(
                runner=runner,
                state=state,
                logger=logger,
                tick_ctx=tick_ctx,
            )
        if confirmation_result is not None:
            return confirmation_result

        with active_chat_phase("brain_dispatch"):
            return dispatch_entry(
                runner=runner,
                state=state,
                logger=logger,
                request=build_execution_entry_request(
                    user_input=tick_ctx.user_input,
                    forced_tools=tick_ctx.forced_tools,
                    capability_category=tick_ctx.capability_category,
                    skip_decide=tick_ctx.skip_decide,
                    decision=tick_ctx.decision,
                    mask_pending_confirmation_in_output=tick_ctx.mask_pending_confirmation_in_output,
                    masked_resume_cursor=tick_ctx.masked_resume_cursor,
                    consume_user_input_for_command=tick_ctx.consume_user_input_for_command,
                ),
            )
    finally:
        elapsed = max(0, _runner_delegate("_now_ms", runner) - started)
        state.budgets_remaining.time_ms = max(
            0, state.budgets_remaining.time_ms - elapsed
        )
        _runner_delegate("_save_state", runner, state)
