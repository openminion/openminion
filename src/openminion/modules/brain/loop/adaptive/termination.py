from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_STATE_WAITING_USER,
)
from openminion.modules.brain.config import ADAPTIVE_BUDGET_HARD_CAP
from openminion.modules.brain.schemas import AdaptiveBudgetConfig
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
)
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    new_uuid,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopOutcome,
)

from ..services import runner_from_context

_FAILURE_MEMORY_TERMINATION_REASONS = frozenset(
    {
        ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ADAPTIVE_TERM_ITERATION_CAP,
        ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_CIRCULAR_PATTERN,
    }
)


def _extract_failure_memories_for_outcome(
    ctx: ExecutionContext,
    *,
    outcome: AdaptiveToolLoopOutcome,
) -> None:
    if outcome.termination_reason not in _FAILURE_MEMORY_TERMINATION_REASONS:
        return
    runner = runner_from_context(ctx)
    if runner is None:
        return
    from openminion.modules.brain.execution.memory import extract_failure_memories

    extract_failure_memories(
        runner,
        state=ctx.state,
        action_result=outcome.action_result,
        termination_reason=outcome.termination_reason,
        logger=ctx.logger,
        outcome_snapshot={
            "tool_results": list(
                outcome.state.scratchpad.get("adaptive.tool_results", []) or []
            ),
            "correction_history": list(
                outcome.state.scratchpad.get("correction_history", []) or []
            ),
            "loop_iteration": outcome.state.iteration,
        },
    )


def effective_soft_cap(
    decision: Any,
    config: AdaptiveBudgetConfig,
) -> int:
    """AIB-05: compute the per-turn soft cap from typed Decision fields."""
    cap = int(config.soft_cap)
    if decision is None:
        return min(cap, ADAPTIVE_BUDGET_HARD_CAP)

    max_steps_hint = getattr(decision, "max_steps_hint", None)
    if max_steps_hint is not None:
        try:
            hint = int(max_steps_hint)
        except (TypeError, ValueError):
            hint = 0
        if hint > 0:
            cap = max(cap, hint + 6)

    sub_intents = getattr(decision, "sub_intents", None) or []
    try:
        sub_intent_count = len(list(sub_intents))
    except TypeError:
        sub_intent_count = 0
    if sub_intent_count > 1:
        cap = max(cap, int(config.soft_cap) * sub_intent_count)

    return min(cap, ADAPTIVE_BUDGET_HARD_CAP)


def _append_partial_success(
    *,
    message: str,
    summary: str | None,
) -> str:
    detail = str(summary or "").strip()
    if not detail:
        return message
    base = str(message or "").strip()
    if not base:
        return detail
    return f"{base}\n\n{detail}"


def _waiting_without_plan_can_close(
    *, ctx: ExecutionContext, remaining_ids: list[str]
) -> bool:
    post_action_message = str(
        getattr(ctx.state, "post_action_user_message", "") or ""
    ).strip()
    return (
        ctx.state.plan is None
        and not remaining_ids
        and str(getattr(ctx.state, "status", "") or "").strip()
        == BRAIN_STATE_WAITING_USER
        and post_action_message.startswith(
            "I no longer have an active plan for that result"
        )
    )


def _build_error_result(summary: str, code: str) -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status="failed",
        summary=summary,
        error=ActionError(code=code, message=summary),
    )


def _build_blocked_result(summary: str, code: str) -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status="blocked",
        summary=summary,
        error=ActionError(code=code, message=summary, details={"reason_code": code}),
    )


def _single_failed_tool_result_action(
    outcome: AdaptiveToolLoopOutcome,
) -> ActionResult | None:
    scratchpad = dict(getattr(getattr(outcome, "state", None), "scratchpad", {}) or {})
    tool_results = [
        item
        for item in list(scratchpad.get("adaptive.tool_results", []) or [])
        if isinstance(item, dict)
    ]
    if len(tool_results) != 1:
        return None
    tool_result = tool_results[0]
    if bool(tool_result.get("ok")):
        return None
    message = (
        str(tool_result.get("error", "") or "").strip()
        or str(tool_result.get("content", "") or "").strip()
        or str(getattr(outcome, "error_message", "") or "").strip()
    )
    if not message:
        return None
    error_code = (
        str(tool_result.get("error_code", "") or "").strip()
        or "act_adaptive_tool_failure"
    )
    error_details = dict(tool_result.get("data", {}) or {})
    tool_name = str(tool_result.get("tool_name", "") or "").strip()
    if tool_name:
        error_details.setdefault("tool_name", tool_name)
    return ActionResult(
        command_id=new_uuid(),
        status="failed",
        summary=message,
        error=ActionError(
            code=error_code,
            message=message,
            details=error_details or None,
        ),
    )
