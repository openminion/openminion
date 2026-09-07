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
    budgets = loop_ctx.state.budgets_remaining
    return (
        budgets.tokens <= 0
        or loop_ctx.state.llm_calls_used >= loop_ctx.state.llm_calls_max
        or (budgets.tool_calls <= 0 and state.total_tool_calls > 0)
    )


def _tool_call_budget_exhausted(
    loop_ctx: AdaptiveToolLoopContext, state: AdaptiveToolLoopState
) -> bool:
    return (
        loop_ctx.state.budgets_remaining.tool_calls <= 0 and state.total_tool_calls > 0
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
    state.llm_calls_used = min(state.llm_calls_used + 1, state.llm_calls_max)
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if input_tokens or output_tokens:
        state.budgets_remaining.tokens = max(
            0, state.budgets_remaining.tokens - input_tokens - output_tokens
        )


def _debit_tool_budget(loop_ctx: AdaptiveToolLoopContext) -> None:
    budgets = loop_ctx.state.budgets_remaining
    budgets.tool_calls = max(0, budgets.tool_calls - 1)


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


def _effective_cap(
    profile: AdaptiveToolLoopProfile, state: AdaptiveToolLoopState
) -> int:
    dynamic = int(getattr(state, "effective_max_iterations", 0) or 0)
    return dynamic if dynamic > 0 else int(profile.max_iterations)


def _ensure_effective_cap_initialized(
    *, profile: AdaptiveToolLoopProfile, state: AdaptiveToolLoopState
) -> None:
    if int(getattr(state, "effective_max_iterations", 0) or 0) <= 0:
        state.effective_max_iterations = int(profile.max_iterations)
