"""Mission-completion judgment helpers for the closure gate."""

from typing import TYPE_CHECKING, Any

from ....constants import (
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_MISSION_JUDGMENT_ASK_USER,
    BRAIN_MISSION_JUDGMENT_COMPLETE,
    BRAIN_MISSION_JUDGMENT_CONTINUE,
    BRAIN_MISSION_ROUTE_FINISH,
)
from ....diagnostics.events import CanonicalEventLogger
from ...mission import continue_message, mission_is_active
from ....schemas import ActionResult, MissionJudgment, WorkingState, new_uuid
from ....schemas.closure import ClosureJudgment
from ... import closure as closure_api
from ...judgment_context import build_live_state_overlay as _build_live_state_overlay
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


def apply_mission_completion_gate(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    completion_reason: str,
    context: Any,
    judgment: ClosureJudgment,
) -> ClosureJudgment:
    if not (
        context.mission is not None
        and mission_is_active(state)
        and judgment.satisfied
        and judgment.next_action == BRAIN_DISPOSITION_CLOSE
    ):
        return judgment
    if context.finish_requested != BRAIN_MISSION_ROUTE_FINISH:
        context.mission.latest_judgment = MissionJudgment(
            outcome=BRAIN_MISSION_JUDGMENT_CONTINUE,
            reason=continue_message(context.mission),
        )
        logger.emit(
            "brain.mission_judge.completed",
            {
                "mission_id": context.mission.mission_id,
                "outcome": BRAIN_MISSION_JUDGMENT_CONTINUE,
                "finish_requested": False,
            },
            trace_id=state.trace_id,
        )
        judgment.reason = context.mission.latest_judgment.reason
        return judgment
    mission_judgment = _run_mission_completion_judge(
        runner,
        state=state,
        action_result=action_result,
        logger=logger,
        completion_reason=completion_reason,
        context=context,
    )
    context.mission.latest_judgment = mission_judgment
    context.mission.completion_confidence = mission_judgment.confidence
    logger.emit(
        "brain.mission_judge.completed",
        {
            "mission_id": context.mission.mission_id,
            "outcome": mission_judgment.outcome,
            "finish_requested": True,
            "confidence": mission_judgment.confidence,
        },
        trace_id=state.trace_id,
    )
    if mission_judgment.outcome == BRAIN_MISSION_JUDGMENT_COMPLETE:
        judgment.reason = (
            mission_judgment.reason.strip() or judgment.reason or "mission_completed"
        )
        if mission_judgment.final_answer:
            judgment.final_answer = mission_judgment.final_answer
        return judgment
    judgment.reason = mission_judgment.reason.strip() or continue_message(
        context.mission
    )
    return judgment


def _run_mission_completion_judge(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    completion_reason: str,
    context: Any,
) -> MissionJudgment:
    mission_call_id = new_uuid()
    logger.emit(
        "brain.mission_judge.started",
        {
            "llm_call_id": mission_call_id,
            "mission_id": context.mission.mission_id,
            "completion_reason": completion_reason,
        },
        trace_id=state.trace_id,
    )
    try:
        mission_raw = closure_api.call_structured_with_retry(
            runner.llm_api,
            model=context.model,
            purpose="judge",
            context=_runner_delegate(
                "_build_context",
                runner,
                state=state,
                purpose="judge",
                budget={"max_tokens": min(900, state.budgets_remaining.tokens)},
                hints={
                    "_llm_call_id": mission_call_id,
                    "user_input": context.mission.objective,
                    "mission_objective": context.mission.objective,
                    "mission_status": context.mission.status,
                    "mission_last_turn_summary": str(
                        getattr(action_result, "summary", "") or ""
                    ).strip(),
                    "closure_candidate_reason": completion_reason,
                    "live_state_overlay": _build_live_state_overlay(state=state),
                    "style_overrides": {
                        "mission_completion_contract": (
                            "You are the mission completion judge. The turn-closure gate "
                            "already decided the current turn can close. Decide whether the "
                            "overall mission is complete. Return MissionJudgment with outcome "
                            "(complete|continue|ask_user|halt), reason, final_answer, and "
                            "confidence. Never mark complete unless the overall mission objective "
                            "is satisfied."
                        )
                    },
                },
                logger=logger,
            ),
            schema=MissionJudgment,
        )
        state.llm_calls_used += 1
        if isinstance(mission_raw, dict):
            _runner_delegate("_debit_tokens", runner, state, mission_raw, logger)
        return MissionJudgment.model_validate(mission_raw)
    except Exception as exc:  # noqa: BLE001
        logger.emit(
            "brain.mission_judge.failed",
            {
                "llm_call_id": mission_call_id,
                "mission_id": context.mission.mission_id,
                "error": str(exc),
            },
            trace_id=state.trace_id,
            status="warning",
        )
        return MissionJudgment(
            outcome=BRAIN_MISSION_JUDGMENT_ASK_USER,
            reason=(
                "I could not safely confirm that the mission is complete yet. "
                "Please review the result or continue the mission."
            ),
        )
