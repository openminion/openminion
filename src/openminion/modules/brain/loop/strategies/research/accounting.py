"""Settle one isolated research invocation before allocating its next sibling.

Only budget consumption and tool result facts cross this boundary. Public LLM
usage reconciliation remains with turn usage/events (#516); execution-budget
deltas are not provider billing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from copy import deepcopy
from typing import Any

from openminion.base.constants import STATE_KEY_WORKING
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.tool.diagnostics.events import dedupe_tool_results


def research_budget_exhausted(state: WorkingState) -> bool:
    budget = state.budgets_remaining
    return bool(
        budget.ticks <= 0
        or budget.tool_calls <= 0
        or budget.tokens <= 0
        or budget.time_ms <= 0
        or state.llm_calls_used >= state.llm_calls_max
    )


def settle_child(
    *,
    parent: WorkingState,
    child: WorkingState,
    starting_budget: BudgetCounters,
    starting_llm_calls: int,
    action_result: Any = None,
) -> None:
    """Called once, in the invocation's finally block, including cancellation.

    A resumed checkpoint contains findings/cursor, never a deferred debit. Each
    invocation pays only its own counter deltas; inherited parent calls are not
    consumption. Do not infer budget use from result count or provider events.
    """
    for name in BudgetCounters.model_fields:
        consumed = max(
            0,
            getattr(starting_budget, name) - getattr(child.budgets_remaining, name),
        )
        setattr(
            parent.budgets_remaining,
            name,
            max(0, getattr(parent.budgets_remaining, name) - consumed),
        )
    parent.llm_calls_used += max(0, child.llm_calls_used - starting_llm_calls)

    # The child starts without a parent loop snapshot. Unite result identities,
    # never sum overlapping output/snapshot counts. Snapshots survive exceptions.
    outputs = getattr(action_result or child.last_result, "outputs", {}) or {}
    results = list(child.module_state.get("adaptive_loop", {}).get("tool_results", []))
    for key in ("tool_results", "adaptive.tool_results"):
        if isinstance(outputs.get(key), list):
            results.extend(outputs[key])
    current = [
        item
        for item in parent.module_state.get("research", {}).get("tool_results", [])
        if isinstance(item, dict) and item.get("turn_scope_id") == parent.trace_id
    ]
    current.extend(
        deepcopy(item)
        for item in results
        if isinstance(item, dict) and item.get("turn_scope_id") == parent.trace_id
    )
    # Keep the original call IDs/scope for the existing turn collector to dedupe.
    parent.module_state.setdefault("research", {})["tool_results"] = (
        dedupe_tool_results(current)
    )


@dataclass
class ChildExecution:
    result: Any = None


@contextmanager
def account_child(
    parent: WorkingState, child: WorkingState
) -> Iterator[ChildExecution]:
    """One settlement site for normal return, exception and BaseException exits."""
    starting_budget = child.budgets_remaining.model_copy(deep=True)
    starting_llm_calls = child.llm_calls_used
    execution = ChildExecution()
    try:
        yield execution
    finally:
        result = execution.result
        returned_state = getattr(result, STATE_KEY_WORKING, None)
        settle_child(
            parent=parent,
            child=returned_state if isinstance(returned_state, WorkingState) else child,
            starting_budget=starting_budget,
            starting_llm_calls=starting_llm_calls,
            action_result=getattr(result, "action_result", None),
        )


def allocate_child_budget(
    state: WorkingState, *, remaining_iterations: int
) -> BudgetCounters:
    budgets = state.budgets_remaining

    def _split(value: int, *, minimum: int = 1) -> int:
        available = max(0, int(value))
        if available <= 0:
            return 0
        return min(available, max(minimum, int(available // remaining_iterations)))

    return BudgetCounters(
        ticks=_split(int(budgets.ticks), minimum=3),
        tool_calls=_split(int(budgets.tool_calls), minimum=3),
        a2a_calls=_split(int(budgets.a2a_calls)),
        tokens=_split(int(budgets.tokens)),
        time_ms=_split(int(budgets.time_ms)),
    )
