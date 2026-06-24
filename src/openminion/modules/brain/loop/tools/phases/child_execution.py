from __future__ import annotations

from typing import Any

from openminion.modules.brain.execution.dispatch import invoke_decision_direct
from openminion.modules.brain.loop.services import runner_from_context
from openminion.modules.brain.schemas import ActDecision, BudgetCounters, WorkingState
from openminion.modules.brain.diagnostics.transitions import set_status_unchecked


def normalized_text(value: Any) -> str:
    return str(value or "").strip()


def plan_objective_fallback(ctx: Any, *, child_goal: str, default: str) -> str:
    try:
        plan = ctx.plan(user_input=child_goal)
    except Exception:
        return default
    return normalized_text(getattr(plan, "objective", "") or "") or default


def split_budget_evenly(*, budgets: BudgetCounters, divisor: int) -> BudgetCounters:
    divisor = max(1, int(divisor))

    def _split(value: int) -> int:
        return max(1, int(value // divisor))

    return BudgetCounters(
        ticks=_split(int(budgets.ticks)),
        tool_calls=_split(int(budgets.tool_calls)),
        a2a_calls=_split(int(budgets.a2a_calls)),
        tokens=_split(int(budgets.tokens)),
        time_ms=_split(int(budgets.time_ms)),
    )


def build_child_state(
    *,
    parent_state: WorkingState,
    child_budget: BudgetCounters,
    goal: str,
) -> WorkingState:
    child_state = parent_state.model_copy(deep=True)
    child_state.goal = goal
    child_state.plan = None
    child_state.cursor = 0
    set_status_unchecked(child_state, "active", reason="bootstrap")
    child_state.budgets_remaining = child_budget.model_copy(deep=True)
    child_state.last_command_id = None
    child_state.last_result = None
    child_state.step_outputs = []
    child_state.pending_jobs = []
    child_state.memory_candidates = []
    child_state.idempotency_cache = {}
    child_state.child_tasks = {}
    child_state.child_task_order = []
    child_state.pending_clarify_items = []
    child_state.unresolved_clarify_items = []
    child_state.clarify_responses = {}
    child_state.task_backed_task_id = None
    child_state.task_backed_checkpoint_id = None
    child_state.task_backed_resume_state = {}
    return child_state


def execute_child_goal(
    ctx: Any,
    *,
    child_goal: str,
    child_state: WorkingState,
    blocked_mode_name: str,
    fallback_reason_code: str,
    depth: int = 1,
) -> str:
    runner = runner_from_context(ctx)
    if runner is None:
        return ""
    try:
        from openminion.modules.brain.loop.orchestration import (
            decide as decide_phase,
        )

        decision = decide_phase(
            runner,
            state=child_state,
            user_input=child_goal,
            logger=ctx.logger,
        )
        if (
            normalized_text(
                getattr(decision, "route", getattr(decision, "mode", "")) or ""
            )
            == blocked_mode_name
        ):
            decision = ActDecision(
                confidence=0.7,
                reason_code=fallback_reason_code,
                sub_intents=[child_goal],
                rationale=child_goal,
            )
        result = invoke_decision_direct(
            runner,
            state=child_state,
            decision=decision,
            user_input=child_goal,
            logger=ctx.logger,
            depth=depth,
        )
    except Exception:
        return ""
    return normalized_text(getattr(result, "message", "") or "")
