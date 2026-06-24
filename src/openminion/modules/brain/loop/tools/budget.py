from __future__ import annotations

from typing import Any

from .contracts import (
    AdaptiveToolLoopContext,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
)


def _token_budget_exhausted(
    loop_ctx: AdaptiveToolLoopContext, state: AdaptiveToolLoopState
) -> bool:
    budgets = getattr(loop_ctx.state, "budgets_remaining", None)
    if budgets is None:
        return False
    if int(getattr(budgets, "tokens", 1) or 0) <= 0:
        return True
    if int(getattr(loop_ctx.state, "llm_calls_used", 0) or 0) >= int(
        getattr(loop_ctx.state, "llm_calls_max", 0) or 0
    ):
        return True
    if int(getattr(budgets, "tool_calls", 1) or 0) <= 0 and state.total_tool_calls > 0:
        return True
    return False


def _tool_call_budget_exhausted(
    loop_ctx: AdaptiveToolLoopContext, state: AdaptiveToolLoopState
) -> bool:
    budgets = getattr(loop_ctx.state, "budgets_remaining", None)
    if budgets is None:
        return False
    return (
        int(getattr(budgets, "tool_calls", 1) or 0) <= 0
        and int(getattr(state, "total_tool_calls", 0) or 0) > 0
    )


def _profile_budget_exhausted(
    *,
    profile: AdaptiveToolLoopProfile,
    state: AdaptiveToolLoopState,
) -> bool:
    if profile.max_llm_calls_per_loop is not None and state.llm_calls >= int(
        profile.max_llm_calls_per_loop
    ):
        return True
    if profile.max_tool_calls_per_loop is not None and state.total_tool_calls >= int(
        profile.max_tool_calls_per_loop
    ):
        return True
    return False


def _debit_llm_usage(loop_ctx: AdaptiveToolLoopContext, response: Any) -> None:
    state = loop_ctx.state
    state.llm_calls_used = min(
        int(getattr(state, "llm_calls_used", 0) or 0) + 1,
        int(getattr(state, "llm_calls_max", 0) or 0),
    )
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if input_tokens or output_tokens:
        budgets = getattr(state, "budgets_remaining", None)
        if budgets is not None:
            budgets.tokens = max(
                0,
                int(getattr(budgets, "tokens", 0) or 0) - input_tokens - output_tokens,
            )


def _debit_tool_budget(loop_ctx: AdaptiveToolLoopContext) -> None:
    budgets = getattr(loop_ctx.state, "budgets_remaining", None)
    if budgets is None:
        return
    budgets.tool_calls = max(0, int(getattr(budgets, "tool_calls", 0) or 0) - 1)


def _remaining_budget_fraction(
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    state: AdaptiveToolLoopState,
) -> float:
    max_llm = profile.max_llm_calls_per_loop
    if max_llm is None or int(max_llm) <= 0:
        return 1.0
    used = int(state.llm_calls or 0)
    return max(0.0, 1.0 - used / int(max_llm))
