"""Continuation-budget helpers for brain runtime closure gates."""

from typing import Any


def has_continuation_budget(state: Any) -> bool:
    budgets = getattr(state, "budgets_remaining", None)
    if budgets is None:
        return False
    return (
        int(budgets.tool_calls) > 0
        and int(budgets.tokens) > 0
        and int(budgets.time_ms) > 0
    )


__all__ = ["has_continuation_budget"]
