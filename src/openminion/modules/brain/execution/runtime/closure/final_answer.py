"""Final-answer repair helpers for the closure gate."""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ....diagnostics.events import CanonicalEventLogger
from ....schemas import ActionResult, WorkingState, new_uuid
from ....schemas.closure import ClosureJudgment
from ... import closure as closure_api
from ...closure.checks import _closure_action_outputs
from ...judgment_context import (
    build_live_state_overlay as _build_live_state_overlay,
    intent_execution_payload as _intent_execution_payload,
)
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


class _ClosureFinalAnswerRepair(BaseModel):
    model_config = ConfigDict(extra="ignore")

    final_answer: str


def repair_missing_final_answer_if_needed(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    completion_reason: str,
    closure_goal: str,
    judgment: ClosureJudgment,
) -> None:
    if not (
        judgment.satisfied
        and judgment.next_action == "close"
        and not judgment.final_answer
    ):
        return
    try:
        repaired_answer = _repair_missing_final_answer(
            runner,
            state=state,
            action_result=action_result,
            logger=logger,
            closure_goal=closure_goal,
            completion_reason=completion_reason,
        )
    except Exception as repair_exc:  # noqa: BLE001
        logger.emit(
            "brain.closure_gate.final_answer_repair.failed",
            {
                "error": str(repair_exc),
                "completion_reason": completion_reason,
            },
            trace_id=state.trace_id,
            status="warning",
        )
        repaired_answer = None
    if repaired_answer:
        judgment.final_answer = repaired_answer
        return
    judgment.satisfied = False
    judgment.next_action = "continue"
    judgment.reason = (
        f"{judgment.reason}; closure_missing_final_answer"
        if judgment.reason
        else "closure_missing_final_answer"
    )


def _repair_missing_final_answer(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    logger: CanonicalEventLogger,
    closure_goal: str,
    completion_reason: str,
) -> str | None:
    repair_call_id = new_uuid()
    logger.emit(
        "brain.closure_gate.final_answer_repair.started",
        {
            "llm_call_id": repair_call_id,
            "completion_reason": completion_reason,
        },
        trace_id=state.trace_id,
    )
    raw = closure_api.call_structured_with_retry(
        runner.llm_api,
        model=runner.profile.llm_profiles.reflect_model,
        purpose="judge",
        context=_runner_delegate(
            "_build_context",
            runner,
            state=state,
            purpose="judge",
            budget={
                "max_tokens": min(400, max(1, int(state.budgets_remaining.tokens)))
            },
            hints={
                "_llm_call_id": repair_call_id,
                "current_datetime": state.last_result.created_at
                if getattr(state, "last_result", None) is not None
                and getattr(state.last_result, "created_at", None)
                else "",
                "user_input": closure_goal,
                "closure_candidate_reason": completion_reason,
                "closure_action_summary": str(
                    getattr(action_result, "summary", "") or ""
                ).strip(),
                "closure_action_outputs": _closure_action_outputs(action_result),
                "closure_intent_outcomes": _intent_execution_payload(state),
                "live_state_overlay": _build_live_state_overlay(state=state),
                "style_overrides": {
                    "closure_final_answer_repair_contract": (
                        "The turn-closure gate already determined the goal is satisfied, "
                        "but it failed to provide the required final_answer. Return only "
                        "a concise user-facing final_answer grounded in the execution "
                        "facts. Do not emit tool JSON, internal reasoning, or null."
                    )
                },
            },
            logger=logger,
        ),
        schema=_ClosureFinalAnswerRepair,
    )
    state.llm_calls_used += 1
    if isinstance(raw, dict):
        _runner_delegate("_debit_tokens", runner, state, raw, logger)
        final_answer = str(raw.get("final_answer", "") or "").strip()
    else:
        final_answer = str(getattr(raw, "final_answer", "") or "").strip()
    logger.emit(
        "brain.closure_gate.final_answer_repair.completed",
        {
            "llm_call_id": repair_call_id,
            "final_answer_present": bool(final_answer),
        },
        trace_id=state.trace_id,
        status="success" if final_answer else "warning",
    )
    return final_answer or None
