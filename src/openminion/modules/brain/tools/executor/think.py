from typing import TYPE_CHECKING

from ...diagnostics.events import CanonicalEventLogger
from ...constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
)
from ...retry import call_structured_with_retry
from ...schemas import (
    ActionError,
    ActionResult,
    Command,
    JobHandle,
    ThinkResult,
    WorkingState,
    iso_now,
    new_uuid,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


def execute_think(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    logger: CanonicalEventLogger,
) -> tuple[ActionResult, JobHandle | None]:
    """Execute a ``ThinkCommand`` via a structured LLM call."""

    if state.llm_calls_used >= state.llm_calls_max:
        return (
            runner._budget_blocked_result(
                command_id=command.command_id, budget_name="llm_calls"
            ),
            None,
        )

    model = (
        str(getattr(command, "model", "") or "").strip()
        or runner.profile.llm_profiles.plan_model
    )
    llm_call_id = new_uuid()
    prompt = str(getattr(command, "prompt", "") or "").strip()
    output_key = str(getattr(command, "output_key", "") or "").strip()

    hints: dict = {"user_input": prompt, "current_datetime": iso_now()}
    if output_key:
        hints["output_key"] = output_key
    if state.last_result is not None and state.last_result.summary:
        hints["prior_step_result"] = state.last_result.summary
    if state.step_outputs:
        hints["step_history"] = [
            item.model_dump(mode="json") for item in state.step_outputs[-5:]
        ]

    logger.emit(
        "think.started",
        {
            "command_id": command.command_id,
            "llm_call_id": llm_call_id,
            "model": model,
            "output_key": output_key,
            "step_index": state.cursor,
        },
        trace_id=state.trace_id,
    )
    runner._track_call_started(llm_call_id, "think", model)

    context = runner._build_context(
        state=state,
        purpose="act",
        budget={"max_tokens": min(2000, state.budgets_remaining.tokens)},
        hints=hints,
        logger=logger,
    )
    try:
        raw = call_structured_with_retry(
            runner.llm_api,
            model=model,
            purpose="act",
            context=context,
            schema=ThinkResult,
        )
        state.llm_calls_used += 1
        runner._debit_tokens(state, raw, logger)
        runner._track_call_completed(llm_call_id)
        text = (
            str(raw.get("response") or "").strip()
            if isinstance(raw, dict)
            else str(raw or "").strip()
        )
        result = ActionResult(
            command_id=command.command_id,
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary=text or "(no output)",
        )
        logger.emit(
            "think.completed",
            {
                "command_id": command.command_id,
                "llm_call_id": llm_call_id,
                "output_chars": len(text),
                "output_key": output_key,
            },
            trace_id=state.trace_id,
        )
    except Exception as exc:
        state.llm_calls_used += 1
        runner._track_call_completed(llm_call_id)
        logger.emit(
            "think.failed",
            {
                "command_id": command.command_id,
                "llm_call_id": llm_call_id,
                "error": str(exc),
            },
            trace_id=state.trace_id,
            status="error",
        )
        result = ActionResult(
            command_id=command.command_id,
            status=BRAIN_ACTION_STATUS_FAILED,
            summary=f"Think step failed: {exc}",
            error=ActionError(
                code="THINK_FAILED",
                message=str(exc),
                details={"reason_code": "think_llm_call_failed"},
            ),
        )
    runner._remember_idempotency(state=state, command=command, result=result)
    return result, None


__all__ = ["execute_think"]
