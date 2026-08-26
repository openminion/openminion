from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
)
from openminion.modules.brain.execution.dispatch import invoke_decision_direct
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.llm.schemas import Message

from .prompts import build_coding_subtask_prompt


def _dispatch_subtasks_if_needed(runner: Any, ctx: ExecutionContext) -> None:
    if runner._coding_plan is None or runner._coding_plan.current_phase != "implement":
        return
    pending_indices = [
        index
        for index, subtask in enumerate(runner._coding_plan.subtasks)
        if subtask.status == "pending"
    ]
    if not pending_indices:
        return
    local_runner = getattr(getattr(ctx, "_services", None), "runner", None)
    if local_runner is None:
        return

    batches = _subtask_batches(runner, pending_indices)
    total_subtasks = max(1, len(pending_indices))
    child_budget = _child_budget_payload(runner, ctx, total_subtasks=total_subtasks)
    outcomes: dict[int, ExecutionResult] = {}
    for batch in batches:
        if _parent_subtask_budget_exhausted(runner, ctx):
            break
        if len(batch) == 1:
            index = batch[0]
            result, child_state = _invoke_coding_subtask(
                runner,
                ctx,
                runner_obj=local_runner,
                subtask_index=index,
                child_budget=child_budget,
            )
            outcomes[index] = result
            _debit_parent_budget_for_subtask(
                runner,
                ctx,
                child_budget=child_budget,
                child_state=child_state,
            )
            continue
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = {
                index: pool.submit(
                    _invoke_coding_subtask,
                    runner,
                    ctx,
                    runner_obj=local_runner,
                    subtask_index=index,
                    child_budget=child_budget,
                )
                for index in batch
            }
            for index in batch:
                result, child_state = futures[index].result()
                outcomes[index] = result
                _debit_parent_budget_for_subtask(
                    runner,
                    ctx,
                    child_budget=child_budget,
                    child_state=child_state,
                )
    _append_subtask_synthesis(runner, ctx, outcomes)
    runner._sync_coding_module_state(ctx)


def _subtask_batches(runner: Any, pending_indices: list[int]) -> list[list[int]]:
    if runner._coding_plan is None:
        return []
    conflicts = set(runner._coding_plan.conflicting_subtask_pairs())
    batches: list[list[int]] = []
    for index in pending_indices:
        placed = False
        for batch in batches:
            if any(
                (min(index, sibling), max(index, sibling)) in conflicts
                for sibling in batch
            ):
                continue
            batch.append(index)
            placed = True
            break
        if not placed:
            batches.append([index])
    return batches


def _child_budget_payload(
    runner: Any,
    ctx: ExecutionContext,
    *,
    total_subtasks: int,
) -> dict[str, int]:
    del runner
    budget = ctx.state.budgets_remaining
    budget_parts = max(1, total_subtasks) + 1
    return {
        "ticks": max(1, budget.ticks // budget_parts),
        "tool_calls": max(1, budget.tool_calls // budget_parts),
        "a2a_calls": max(0, budget.a2a_calls // budget_parts),
        "tokens": max(1, budget.tokens // budget_parts),
        "time_ms": max(1, budget.time_ms // budget_parts),
    }


def _parent_subtask_budget_exhausted(runner: Any, ctx: ExecutionContext) -> bool:
    del runner
    budget = ctx.state.budgets_remaining
    return budget.ticks <= 0 or budget.tokens <= 0


def _debit_parent_budget_for_subtask(
    runner: Any,
    ctx: ExecutionContext,
    *,
    child_budget: dict[str, int],
    child_state: Any,
) -> None:
    del runner
    budget = ctx.state.budgets_remaining
    remaining = child_state.budgets_remaining
    budget.ticks = max(
        0,
        budget.ticks - max(0, child_budget["ticks"] - remaining.ticks),
    )
    budget.tool_calls = max(
        0,
        budget.tool_calls
        - max(0, child_budget["tool_calls"] - remaining.tool_calls),
    )
    budget.a2a_calls = max(
        0,
        budget.a2a_calls
        - max(0, child_budget["a2a_calls"] - remaining.a2a_calls),
    )
    budget.tokens = max(
        0,
        budget.tokens - max(0, child_budget["tokens"] - remaining.tokens),
    )
    budget.time_ms = max(
        0,
        budget.time_ms - max(0, child_budget["time_ms"] - remaining.time_ms),
    )
    ctx.state.llm_calls_used = min(
        ctx.state.llm_calls_max,
        ctx.state.llm_calls_used + child_state.llm_calls_used,
    )


def _invoke_coding_subtask(
    runner: Any,
    ctx: ExecutionContext,
    *,
    runner_obj: Any,
    subtask_index: int,
    child_budget: dict[str, int],
) -> tuple[ExecutionResult, Any]:
    assert runner._coding_plan is not None
    subtask = runner._coding_plan.subtasks[subtask_index]
    runner._coding_plan.subtasks[subtask_index] = subtask.model_copy(
        update={"status": "running"}
    )
    child_state = ctx.state.model_copy(deep=True)
    child_state.session_id = f"{ctx.state.session_id}::coding:{subtask_index + 1}"
    child_state.goal = subtask.goal
    child_state.module_state = {}
    child_state.task_backed_task_id = None
    child_state.task_backed_checkpoint_id = None
    child_state.task_backed_resume_state = {}
    child_state.child_tasks = {}
    child_state.child_task_order = []
    child_state.budgets_remaining.ticks = child_budget["ticks"]
    child_state.budgets_remaining.tool_calls = child_budget["tool_calls"]
    child_state.budgets_remaining.a2a_calls = child_budget["a2a_calls"]
    child_state.budgets_remaining.tokens = child_budget["tokens"]
    child_state.budgets_remaining.time_ms = child_budget["time_ms"]
    child_state.llm_calls_used = 0
    child_state.last_result = None
    child_state.pending_jobs = []
    decision = SimpleNamespace(
        route=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
        act_profile=BRAIN_ACT_PROFILE_GENERAL,
        reason_code="coding_subtask",
        confidence=1.0,
        objective=subtask.goal,
        sub_intents=[],
        rationale="",
        question=None,
        answer=None,
        success_criteria={"target_files": list(subtask.target_files)},
    )
    child_prompt = build_coding_subtask_prompt(
        goal=subtask.goal,
        target_files=subtask.target_files,
        success_criteria=subtask.success_criteria,
    )
    result = invoke_decision_direct(
        runner_obj,
        state=child_state,
        decision=decision,
        user_input=child_prompt,
        logger=ctx.logger,
        depth=1,
    )
    runner._coding_plan.subtasks[subtask_index] = subtask.model_copy(
        update={"status": "done" if result.status == "done" else "failed"}
    )
    return result, child_state


def _append_subtask_synthesis(
    runner: Any,
    ctx: ExecutionContext,
    outcomes: dict[int, ExecutionResult],
) -> None:
    del ctx
    if runner._coding_plan is None or not outcomes:
        return
    lines = ["Subtask synthesis:"]
    for index in sorted(outcomes):
        subtask = runner._coding_plan.subtasks[index]
        result = outcomes[index]
        summary = (
            str(result.message or "").strip()
            or str(getattr(result.action_result, "summary", "") or "").strip()
        )
        lines.append(f"- {subtask.goal} [{subtask.status}]: {summary or 'no summary'}")
        action_result = result.action_result
        if action_result is not None and action_result.outputs.get("diff"):
            lines.append(str(action_result.outputs.get("diff") or ""))
        if subtask.status == "failed":
            runner._coding_plan.record_open_issue(
                f"{subtask.goal}: {summary or 'subtask failed'}"
            )
    runner._loop_state.messages.append(Message(role="user", content="\n".join(lines)))
    runner._sync_plan_telemetry()
