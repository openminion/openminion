from __future__ import annotations

from typing import Any
from typing import TYPE_CHECKING

from ...constants import (
    BRAIN_COMMAND_KIND_ASK_USER,
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITION_REPLAN,
    BRAIN_DISPOSITIONS_RETRYING,
    BRAIN_MISSION_JUDGMENT_COMPLETE,
    BRAIN_MISSION_JUDGMENT_HALT,
    BRAIN_MISSION_STATUS_ACTIVE,
    BRAIN_MISSION_STATUS_COMPLETED,
    BRAIN_MISSION_STATUS_HALTED,
    BRAIN_STATE_DONE,
)
from ...diagnostics.transitions import transition
from ...diagnostics.events import CanonicalEventLogger
from ..mission import (
    mission_is_active,
    set_mission_status,
    update_mission_task,
)
from ...schemas import ActionResult, WorkingState
from ...schemas.closure import ClosureJudgment
from ...runtime.verification.policy import (
    build_freshness_failure_message,
    verify_freshness_answer,
)
from ...retry import call_structured_with_retry as _call_structured_with_retry
from ..runtime.closure.evaluator import (
    evaluate_turn_closure as _evaluate_turn_closure_impl,
)
from ..memory import write_post_completion_critique_memory

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


call_structured_with_retry = _call_structured_with_retry


def final_close_message(
    *,
    state: WorkingState | None = None,
    judgment: ClosureJudgment | None,
    action_result: ActionResult | None,
    fallback_message: str,
) -> str:
    final_answer = str(getattr(judgment, "final_answer", "") or "").strip()
    summary = str(getattr(action_result, "summary", "") or "").strip()
    candidate = final_answer or summary or fallback_message
    contract = getattr(state, "freshness_contract", None)
    obligations = getattr(state, "freshness_obligations", None)
    reasons = verify_freshness_answer(
        contract=contract,
        obligations=obligations,
        answer=candidate,
        action_result=action_result,
    )
    if not reasons:
        return candidate
    if state is not None and state.freshness_diagnostics is not None:
        state.freshness_diagnostics.verifier_notes.extend(reasons)
    return build_freshness_failure_message(contract=contract, reasons=reasons)


def evaluate_turn_closure(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    completion_reason: str,
) -> ClosureJudgment:
    return _evaluate_turn_closure_impl(
        runner,
        state=state,
        action_result=action_result,
        logger=logger,
        completion_reason=completion_reason,
    )


def _emit_mission_lifecycle(
    *,
    closure_logger: CanonicalEventLogger,
    state: WorkingState,
    mission: Any,
    event_type: str,
    reason: str,
) -> None:
    closure_logger.emit(
        event_type,
        {
            "mission_id": mission.mission_id,
            "objective": mission.objective,
            "reason": reason,
            "route_action": str(getattr(mission, "latest_route_action", "") or ""),
        },
        trace_id=state.trace_id,
    )


def _closure_transition_event(
    *, state: WorkingState, done_event: str, active_event: str
) -> str:
    return done_event if state.status == BRAIN_STATE_DONE else active_event


def _apply_mission_closure_judgment(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    mission: Any,
    mission_judgment: Any,
    judgment: ClosureJudgment,
    closure_logger: CanonicalEventLogger,
) -> str | None:
    if mission_judgment.outcome == BRAIN_MISSION_JUDGMENT_COMPLETE:
        reason = judgment.reason or "mission_completed"
        set_mission_status(
            mission=mission,
            status=BRAIN_MISSION_STATUS_COMPLETED,
            reason=reason,
            route_action=str(getattr(mission, "latest_route_action", "") or ""),
        )
        update_mission_task(runner=runner, mission=mission, to_state="done")
        _emit_mission_lifecycle(
            closure_logger=closure_logger,
            state=state,
            mission=mission,
            event_type="brain.mission.completed",
            reason=reason,
        )
        return None
    if mission_judgment.outcome == BRAIN_MISSION_JUDGMENT_HALT:
        reason = mission_judgment.reason or "mission_halted"
        set_mission_status(
            mission=mission,
            status=BRAIN_MISSION_STATUS_HALTED,
            reason=mission_judgment.reason,
            route_action=str(getattr(mission, "latest_route_action", "") or ""),
        )
        update_mission_task(runner=runner, mission=mission, to_state="failed")
        _emit_mission_lifecycle(
            closure_logger=closure_logger,
            state=state,
            mission=mission,
            event_type="brain.mission.halted",
            reason=reason,
        )
        transition(
            state,
            _closure_transition_event(
                state=state,
                done_event="closure_stopped",
                active_event="execution_stopped",
            ),
        )
        return BRAIN_COMMAND_KIND_ASK_USER
    set_mission_status(
        mission=mission,
        status=BRAIN_MISSION_STATUS_ACTIVE,
        reason=mission_judgment.reason,
        route_action=str(getattr(mission, "latest_route_action", "") or ""),
    )
    update_mission_task(runner=runner, mission=mission)
    transition(
        state,
        _closure_transition_event(
            state=state,
            done_event="closure_needs_user",
            active_event="judgment_ask_user",
        ),
    )
    return BRAIN_COMMAND_KIND_ASK_USER


def apply_closure_judgment(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    judgment: ClosureJudgment,
) -> str:
    closure_logger = CanonicalEventLogger(
        session_api=runner.session_api,
        session_id=state.session_id,
        agent_id=runner.profile.agent_id,
    )
    write_post_completion_critique_memory(
        runner,
        state=state,
        judgment=judgment,
        logger=closure_logger,
    )

    mission = getattr(state, "mission", None)
    mission_judgment = (
        getattr(mission, "latest_judgment", None) if mission is not None else None
    )
    if judgment.satisfied and judgment.next_action == BRAIN_DISPOSITION_CLOSE:
        if (
            mission is not None
            and mission_judgment is not None
            and mission_is_active(state)
        ):
            mission_disposition = _apply_mission_closure_judgment(
                runner=runner,
                state=state,
                mission=mission,
                mission_judgment=mission_judgment,
                judgment=judgment,
                closure_logger=closure_logger,
            )
            if mission_disposition is not None:
                return mission_disposition
        transition(state, "task_completed")
        return BRAIN_DISPOSITION_CLOSE
    if (
        judgment.next_action == BRAIN_DISPOSITION_REPLAN
        and state.replans_used < runner.options.max_replans
    ):
        state.replans_used += 1
        state.plan = None
        state.cursor = 0
        transition(
            state,
            _closure_transition_event(
                state=state,
                done_event="closure_replan",
                active_event="step_advanced",
            ),
        )
        return BRAIN_DISPOSITION_REPLAN
    if judgment.next_action in BRAIN_DISPOSITIONS_RETRYING:
        transition(
            state,
            _closure_transition_event(
                state=state,
                done_event="closure_retry",
                active_event="step_retrying",
            ),
        )
        return judgment.next_action
    transition(
        state,
        _closure_transition_event(
            state=state,
            done_event="closure_needs_user",
            active_event="judgment_ask_user",
        ),
    )
    return BRAIN_COMMAND_KIND_ASK_USER
