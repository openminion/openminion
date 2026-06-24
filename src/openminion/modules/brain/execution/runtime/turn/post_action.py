"""Turn post-action runtime judgment implementation."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ....diagnostics.events import CanonicalEventLogger
from ....retry import call_structured_with_retry
from ....schemas import (
    ActionResult,
    Command,
    PostActionJudgment,
    WorkingState,
    new_uuid,
)
from ...judgment_context import build_live_state_overlay, intent_execution_payload
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


def evaluate_post_action_judgment(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    fact_kind: str,
    action_result: ActionResult | None = None,
    current_command: Command | None = None,
    current_step_index: int | None = None,
    total_steps: int | None = None,
    runtime_facts: dict[str, Any] | None = None,
) -> PostActionJudgment | None:
    if runner.llm_api is None or runner.context_api is None:
        return None
    llm_call_id = new_uuid()
    model = runner.profile.llm_profiles.reflect_model
    hint_facts = _post_action_hint_facts(
        runner=runner,
        state=state,
        fact_kind=fact_kind,
        action_result=action_result,
        current_command=current_command,
        current_step_index=current_step_index,
        total_steps=total_steps,
        runtime_facts=runtime_facts,
    )
    hints = _post_action_hints(
        state=state,
        current_command=current_command,
        llm_call_id=llm_call_id,
        hint_facts=hint_facts,
    )
    _emit_post_action_started(
        logger=logger,
        state=state,
        llm_call_id=llm_call_id,
        model=model,
        fact_kind=hint_facts["fact_kind"],
    )
    _runner_delegate("_track_call_started", runner, llm_call_id, "judge", model)
    context = _runner_delegate(
        "_build_context",
        runner,
        state=state,
        purpose="judge",
        budget={"max_tokens": min(1200, int(state.budgets_remaining.tokens or 0))},
        hints=hints,
        logger=logger,
    )
    if _token_budget_exceeded(
        runner=runner,
        state=state,
        logger=logger,
        llm_call_id=llm_call_id,
        model=model,
        context=context,
    ):
        return None
    raw = _run_post_action_judge(
        runner=runner,
        state=state,
        logger=logger,
        llm_call_id=llm_call_id,
        model=model,
        fact_kind=hint_facts["fact_kind"],
        context=context,
    )
    if raw is None:
        return None
    return _validate_post_action_judgment(
        state=state,
        logger=logger,
        llm_call_id=llm_call_id,
        fact_kind=hint_facts["fact_kind"],
        raw=raw,
    )


def _post_action_hint_facts(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    fact_kind: str,
    action_result: ActionResult | None,
    current_command: Command | None,
    current_step_index: int | None,
    total_steps: int | None,
    runtime_facts: dict[str, Any] | None,
) -> dict[str, Any]:
    step_key = (
        str(getattr(current_command, "command_id", "") or "").strip()
        or str(getattr(action_result, "command_id", "") or "").strip()
    )
    hint_facts: dict[str, Any] = {
        "fact_kind": str(fact_kind or "").strip() or "action_result",
        "current_step_index": int(current_step_index or 0),
        "total_steps": int(total_steps or 0),
        "current_retry_count": int(state.retries_for_step.get(step_key, 0) or 0)
        if step_key
        else 0,
        "max_retries_per_step": int(
            getattr(runner.options, "max_retries_per_step", 0) or 0
        ),
        "replans_used": int(getattr(state, "replans_used", 0) or 0),
        "max_replans": int(getattr(runner.options, "max_replans", 0) or 0),
        "configured_failure_strategy": str(
            getattr(runner.options, "failure_strategy", "") or ""
        ).strip(),
        "command": _model_payload(current_command),
        "action_result": _model_payload(action_result),
        "next_step_preview": _next_step_preview(state=state, total_steps=total_steps),
    }
    if isinstance(runtime_facts, Mapping):
        hint_facts.update(dict(runtime_facts))
    return hint_facts


def _post_action_hints(
    *,
    state: WorkingState,
    current_command: Command | None,
    llm_call_id: str,
    hint_facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "_llm_call_id": llm_call_id,
        "current_datetime": "",
        "user_input": str(
            getattr(state, "goal", "") or getattr(state, "last_user_input", "") or ""
        ).strip(),
        "post_action_fact_kind": hint_facts["fact_kind"],
        "post_action_runtime_facts": hint_facts,
        "post_action_intent_outcomes": intent_execution_payload(state),
        "post_action_success_criteria": dict(
            getattr(current_command, "success_criteria", {}) or {}
        ),
        "live_state_overlay": build_live_state_overlay(
            state=state,
            extra_fields={
                "cursor",
                "plan",
                "replans_used",
                "retries_for_step",
                "constraints",
            },
        ),
        "style_overrides": {
            "post_action_judgment_contract": (
                "You are the post-action semantic judge. Runtime has already executed a "
                "tool, step, or confirmation-handling action and is giving you raw facts. "
                "Return a structured PostActionJudgment with outcome, reason, "
                "user_message, and optional confidence. Valid outcomes are: "
                "advance, retry, replan, ask_user, halt, skip. "
                "'advance' means the current step is resolved and the workflow may move "
                "past it. 'skip' means explicitly skip the current step and move on. "
                "'retry' means keep the same step active for another attempt. "
                "'replan' means the current remaining workflow should be discarded and "
                "replanned. 'ask_user' means user guidance is required before proceeding. "
                "'halt' means stop safely without claiming completion. Tool success does "
                "not automatically mean the task is done. Tool failure does not "
                "automatically mean the task is impossible."
            )
        },
    }


def _next_step_preview(
    *, state: WorkingState, total_steps: int | None
) -> dict[str, Any] | None:
    if state.plan is None or total_steps is None or state.cursor + 1 >= total_steps:
        return None
    return _model_payload(state.plan.steps[state.cursor + 1])


def _emit_post_action_started(
    *,
    logger: CanonicalEventLogger,
    state: WorkingState,
    llm_call_id: str,
    model: str,
    fact_kind: str,
) -> None:
    logger.emit(
        "brain.post_action_judge.started",
        {"llm_call_id": llm_call_id, "model": model, "fact_kind": fact_kind},
        trace_id=state.trace_id,
    )


def _token_budget_exceeded(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    llm_call_id: str,
    model: str,
    context: Any,
) -> bool:
    estimate = _runner_delegate(
        "_estimate_tokens", runner, model=model, context=context
    )
    available_tokens = int(getattr(state.budgets_remaining, "tokens", 0) or 0)
    if estimate <= available_tokens:
        return False
    logger.emit(
        "brain.post_action_judge.skipped",
        {
            "llm_call_id": llm_call_id,
            "reason": "token_budget_exceeded",
            "estimated_tokens": estimate,
            "available_tokens": available_tokens,
        },
        trace_id=state.trace_id,
        status="warning",
    )
    _runner_delegate("_track_call_completed", runner, llm_call_id)
    return True


def _run_post_action_judge(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    llm_call_id: str,
    model: str,
    fact_kind: str,
    context: Any,
) -> Any | None:
    try:
        raw = call_structured_with_retry(
            runner.llm_api,
            model=model,
            purpose="judge",
            context=context,
            schema=PostActionJudgment,
        )
        state.llm_calls_used += 1
        if isinstance(raw, dict):
            _runner_delegate("_debit_tokens", runner, state, raw, logger)
        _runner_delegate("_track_call_completed", runner, llm_call_id)
        return raw
    except Exception as exc:  # noqa: BLE001
        logger.emit(
            "brain.post_action_judge.failed",
            {
                "llm_call_id": llm_call_id,
                "fact_kind": fact_kind,
                "error": str(exc),
            },
            trace_id=state.trace_id,
            status="error",
            error={"code": "POST_ACTION_JUDGE_FAILED", "message": str(exc)},
        )
        _runner_delegate("_track_call_completed", runner, llm_call_id)
        return None


def _validate_post_action_judgment(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    llm_call_id: str,
    fact_kind: str,
    raw: Any,
) -> PostActionJudgment | None:
    try:
        judgment = PostActionJudgment.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        logger.emit(
            "brain.post_action_judge.invalid_output",
            {"llm_call_id": llm_call_id, "fact_kind": fact_kind, "error": str(exc)},
            trace_id=state.trace_id,
            status="warning",
        )
        return None
    logger.emit(
        "brain.post_action_judge.completed",
        {
            "llm_call_id": llm_call_id,
            "fact_kind": fact_kind,
            "outcome": judgment.outcome,
            "reason": judgment.reason,
            "user_message_present": bool(str(judgment.user_message or "").strip()),
        },
        trace_id=state.trace_id,
    )
    return judgment


def _model_payload(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        return value.model_dump(mode="json")
    except Exception:
        return None
