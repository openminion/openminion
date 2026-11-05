"""Task-context preflight and commit at the existing entry owner."""

from typing import Any

from openminion.modules.brain.schemas import RespondDecision
from openminion.modules.llm.schemas import Message
from .task_context import (
    TASK_UPDATE_INSTRUCTIONS,
    message_token_estimate,
    task_context_message,
    resolve_entry_task_update,
)


def prepare_task_context_prompt(
    runner: Any, state: Any, *, model: str, has_new_input: bool, target: int
) -> tuple[list[Message], int, RespondDecision | None]:

    required_messages = (
        [Message(role="system", content=TASK_UPDATE_INSTRUCTIONS)]
        if has_new_input
        else []
    )
    if has_new_input:
        required_messages.append(
            task_context_message(state.resolved_task_context)
            if state.resolved_task_context is not None
            else Message(role="user", content="CURRENT TASK: absent (revision 0)")
        )
    if not required_messages:
        return [], target, None
    reserved_tokens = (
        message_token_estimate(runner, required_messages, model=model)
        if required_messages
        else 0
    )
    pack_budget = max(1, target - reserved_tokens)
    if reserved_tokens >= state.budgets_remaining.tokens:
        required_messages = task_context_recovery_messages(state)
        reserved_tokens = message_token_estimate(runner, required_messages, model=model)
        pack_budget = max(1, target - reserved_tokens)
    if reserved_tokens >= state.budgets_remaining.tokens:
        return (
            [],
            0,
            RespondDecision(
                confidence=1.0,
                reason_code="task_context_budget_exceeded",
                respond_kind="clarify",
                question="The active task conditions exceed the context budget. Increase the configured budget or split the request before continuing.",
            ),
        )
    return required_messages, pack_budget, None


def append_required_task_messages(
    runner: Any,
    state: Any,
    base_messages: list[Message],
    required_messages: list[Message],
    *,
    model: str,
    user_input: str | None = None,
) -> RespondDecision | None:
    if required_messages:
        move_current_input_last(base_messages, user_input)
        base_messages.insert(0, required_messages[0])
        if len(required_messages) > 1:
            base_messages.insert(max(0, len(base_messages) - 1), required_messages[1])
        if (
            message_token_estimate(runner, base_messages, model=model)
            > state.budgets_remaining.tokens
        ):
            recovery = task_context_recovery_messages(state)
            base_messages[0] = recovery[0]
            base_messages[-2] = recovery[1]
        if (
            message_token_estimate(runner, base_messages, model=model)
            > state.budgets_remaining.tokens
        ):
            return RespondDecision(
                confidence=1.0,
                reason_code="task_context_budget_exceeded",
                respond_kind="clarify",
                question="The task and required context do not fit the current budget. Please split the request or increase its context budget.",
            )
    return None


def apply_entry_task_context(
    state: Any,
    response: Any,
    *,
    user_input: str | None,
    clarify: bool,
    logger: Any,
    recovery_only: bool = False,
) -> RespondDecision | None:
    if (
        user_input
        and state.resolved_task_context is not None
        and not response.tool_calls
        and not clarify
    ):
        # Text can answer without committing a new executable task revision.
        state.task_context_needs_update = True
        return None
    replacing = any(
        isinstance(call.arguments.get("task_update"), dict)
        and call.arguments["task_update"].get("operation") in {"replace", "cancel"}
        for call in response.tool_calls
    )
    cancelled = any(
        isinstance(call.arguments.get("task_update"), dict)
        and call.arguments["task_update"].get("operation") == "cancel"
        for call in response.tool_calls
    )
    try:
        task_context = resolve_entry_task_update(
            state,
            response,
            user_input=user_input,
            clarify=clarify,
            recovery_only=recovery_only,
        )
    except ValueError:
        state.task_context_needs_update = True
        logger.emit(
            "brain.task_context.rejected",
            {"reason": "invalid_or_missing_update"},
            trace_id=state.trace_id,
            status="warning",
        )
        return RespondDecision(
            confidence=1.0,
            reason_code="invalid_task_context_update",
            respond_kind="clarify",
            question="I could not safely update the task conditions. Please restate the current task and its required conditions.",
        )
    if replacing:
        detach_replaced_task_execution(state)
    state.task_context_needs_update = False
    state.resolved_task_context = task_context
    if task_context is not None:
        state.goal = task_context_message(task_context).content

    elif cancelled:
        state.goal = None
        state.plan = None
        state.cursor = 0
        return RespondDecision(
            confidence=1.0,
            reason_code="entry_task_cancelled",
            respond_kind="answer",
            answer=str(
                response.tool_calls[0].arguments.get("answer") or "Task cancelled."
            ),
        )
    return None


def build_task_entry_context(
    runner: Any,
    state: Any,
    *,
    model: str,
    user_input: str | None,
    hints: dict[str, Any],
    logger: Any,
    target: int,
    tool_specs: list[Any],
) -> tuple[dict[str, Any], int, list[Message], RespondDecision | None]:
    from openminion.modules.brain.execution.delegation import _runner_delegate
    from openminion.modules.brain.adapters.llm.request import _messages_from_context
    from openminion.modules.brain.bootstrap.budget import trim_decide_context_to_budget

    required_messages, pack_budget, task_error = prepare_task_context_prompt(
        runner,
        state,
        model=model,
        has_new_input=bool(str(user_input or "").strip()),
        target=target,
    )
    if task_error is not None:
        return {}, 0, [], task_error
    schema_tokens = message_token_estimate(runner, [], model=model, tools=tool_specs)
    reserved_tokens = schema_tokens + (
        message_token_estimate(runner, required_messages, model=model)
        if required_messages
        else 0
    )
    pack_budget = max(1, pack_budget - schema_tokens)
    context = _runner_delegate(
        "_build_context",
        runner,
        state=state,
        purpose="decide",
        budget={"max_tokens": pack_budget},
        hints=hints,
        logger=logger,
    )
    if reserved_tokens >= state.budgets_remaining.tokens:
        return (
            {},
            0,
            [],
            RespondDecision(
                confidence=1.0,
                reason_code="task_context_budget_exceeded",
                respond_kind="clarify",
                question="The required task and selected tool schemas exceed the remaining token budget. Split the request or increase its budget.",
            ),
        )
    estimate = _runner_delegate(
        "_estimate_tokens", runner, model=model, context=context
    )
    context, _estimate, budget_decision = trim_decide_context_to_budget(
        runner=runner,
        state=state,
        logger=logger,
        model=model,
        budget_max_tokens=pack_budget,
        hints=hints,
        context=context,
        estimate=estimate,
        user_input=user_input,
        reserved_tokens=reserved_tokens,
    )
    if budget_decision is not None:
        return context, _estimate, [], budget_decision

    messages = list(_messages_from_context(context))
    error = append_required_task_messages(
        runner, state, messages, required_messages, model=model, user_input=user_input
    )
    if required_messages:
        _estimate = message_token_estimate(runner, messages, model=model)
    context["task_context_recovery_only"] = any(
        message.meta.get("block_kind") == "task_context_recovery"
        for message in messages
    )
    return context, _estimate, messages, error


def task_context_recovery_messages(state: Any) -> list[Message]:
    return [
        Message(
            role="system",
            content="The stored task does not fit the context budget. Its content is withheld and remains stored. Only task_update operation replace or cancel is allowed. Do not continue, summarize or modify the omitted task. Ask the user to restate a smaller replacement task or increase the budget if they want to retain it.",
        ),
        Message(
            role="user",
            content=f"CURRENT TASK revision: {state.resolved_task_context.revision if state.resolved_task_context else 0}. Content unavailable due to budget; replacement/cancellation only.",
            meta={"block_kind": "task_context_recovery"},
        ),
    ]


def entry_task_preflight(state: Any, *, has_new_input: bool) -> RespondDecision | None:
    if state.task_context_needs_update and not has_new_input:
        return RespondDecision(
            confidence=1.0,
            reason_code="task_context_update_required",
            respond_kind="clarify",
            question="Please restate the current task and its conditions before execution can continue.",
        )
    if has_new_input and state.resolved_task_context is not None:
        state.task_context_needs_update = True
    return None


def entry_task_tool_specs(
    specs: list[Any], state: Any, *, has_new_input: bool
) -> tuple[list[Any], RespondDecision | None]:
    from .task_context import with_task_update_schema

    if not has_new_input:
        return specs, None
    try:
        return [
            with_task_update_schema(
                spec, required=state.resolved_task_context is not None
            )
            for spec in specs
        ], None
    except ValueError:
        return [], RespondDecision(
            confidence=1.0,
            reason_code="task_context_schema_conflict",
            respond_kind="answer",
            answer="The selected tool uses the reserved entry argument task_update. Its schema must be adjusted before this request can run safely.",
        )


def detach_replaced_task_execution(state: Any) -> None:
    """Detach local workflow state; do not modify grants or external task records."""
    from openminion.modules.brain.constants import CODING_MODULE_STATE_KEY
    from openminion.modules.brain.execution.intent_state import clear_working_route

    clear_working_route(state=state)
    state.plan = None
    state.cursor = 0
    state.step_outputs = []
    state.child_tasks = {}
    state.child_task_order = []
    state.task_backed_task_id = None
    state.task_backed_checkpoint_id = None
    state.task_backed_resume_state = {}
    state.module_state = {
        key: value
        for key, value in state.module_state.items()
        if key != CODING_MODULE_STATE_KEY
    }


def task_budget_entry_decision(error: Exception) -> RespondDecision:
    return RespondDecision(
        confidence=1.0,
        reason_code="task_context_budget_exceeded",
        respond_kind="clarify",
        question=str(error),
    )


def emit_entry_identity_audit(
    logger: Any, state: Any, runner: Any, call_id: str
) -> None:
    logger.emit(
        "llm.identity_audit",
        {
            "llm_call_id": call_id,
            "purpose": "entry",
            "agent_id": state.agent_id,
            "profile_version": getattr(runner.profile, "profile_version", "unknown"),
            "trace_id": state.trace_id,
        },
        trace_id=state.trace_id,
    )


def debit_entry_response(
    runner: Any, state: Any, usage: dict[str, Any], logger: Any
) -> None:
    from openminion.modules.brain.execution.delegation import _runner_delegate

    state.llm_calls_used += 1
    _runner_delegate("_debit_tokens", runner, state, {"usage": usage}, logger)


def move_current_input_last(messages: list[Message], user_input: str | None) -> None:
    """Historical tool replay precedes this turn; never split a call/result batch."""
    candidates = [
        index
        for index, message in enumerate(messages)
        if message.role == "user"
        and (
            message.meta.get("block_kind") == "turn_input"
            or (
                user_input is not None and message.content.strip() == user_input.strip()
            )
        )
    ]
    if candidates:
        messages.append(messages.pop(candidates[-1]))
    elif user_input:
        messages.append(
            Message(role="user", content=user_input, meta={"block_kind": "turn_input"})
        )


def emit_entry_call_started(
    logger: Any, state: Any, call_id: str, model: str | None
) -> None:
    logger.emit(
        "llm.call.started",
        {"llm_call_id": call_id, "purpose": "entry", "model": model},
        trace_id=state.trace_id,
    )
