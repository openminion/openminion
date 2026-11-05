"""Execution inputs from the current resolved task and bounded context owner."""

from typing import Any

from openminion.modules.brain.adapters.llm.request import _messages_from_context
from openminion.modules.brain.loop.context.task_context import (
    input_digest,
    task_context_message,
    message_token_estimate,
    TaskContextBudgetError,
)
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.loop.tools.transcript import replay_tool_messages
from openminion.modules.llm.schemas import Message


def execution_task_text(ctx: Any) -> str:
    state = getattr(ctx, "state", None)
    latest = str(getattr(ctx, "user_input", "") or "").strip()
    task = getattr(state, "resolved_task_context", None)
    if task is not None and (not latest or task.consumed_input == input_digest(latest)):
        snapshot = str(task_context_message(task).content)
        return (
            snapshot + "\nLATEST USER INPUT (verbatim):\n" + latest
            if latest
            else snapshot
        )
    # Do not infer task provenance from last_user_input or permanently prefer goal.
    task_text = latest or str(getattr(state, "goal", "") or "").strip()
    inherited = getattr(state, "inherited_task_conditions", {})
    if inherited:
        import json

        task_text += (
            "\nINHERITED USER CONDITIONS (applicable to this subtask):\n"
            + json.dumps(inherited, ensure_ascii=False)
        )
    return task_text


def execution_messages(ctx: Any, *, autonomous: bool = False) -> list[Message]:
    runner = runner_from_context(ctx)
    task = "" if autonomous else execution_task_text(ctx)
    builder = getattr(runner, "_build_context", None)
    messages: list[Message] = []
    if (
        not autonomous
        and callable(builder)
        and getattr(runner, "context_api", None) is not None
    ):
        context = builder(
            state=ctx.state,
            purpose="act",
            budget={"max_tokens": ctx.state.budgets_remaining.tokens},
            hints={"user_input": task, "canonical_tool_replay": True},
            logger=ctx.logger,
        )
        if context:
            messages = list(_messages_from_context(context))
    task_message = Message(role="user", content=task) if task else None
    if task and messages and messages[-1].role == "user":
        current = messages[-1]
        if current.content == task or current.meta.get("block_kind") == "turn_input":
            task_message = messages.pop()
    messages.extend(
        replay_tool_messages(
            getattr(runner, "session_api", None), str(ctx.state.session_id or "")
        )
    )
    if task_message is not None:
        messages.append(task_message)
    if getattr(ctx.state, "resolved_task_context", None) is not None:
        if task and not any(task in message.content for message in messages):
            raise TaskContextBudgetError("The required task context was truncated")
        if (
            message_token_estimate(runner, messages)
            > ctx.state.budgets_remaining.tokens
        ):
            raise TaskContextBudgetError(
                "Required task context exceeds the remaining token budget"
            )
    return messages


def execution_input(ctx: Any, *, autonomous: bool = False) -> tuple[list[Message], Any]:
    from .loop_contracts import ExecutionResult

    try:
        return execution_messages(ctx, autonomous=autonomous), None
    except TaskContextBudgetError:
        return [], ExecutionResult(
            status="waiting_user",
            working_state=ctx.state,
            message="The required task conditions do not fit the remaining context budget. Please split the task or increase the budget.",
        )
