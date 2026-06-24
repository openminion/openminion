"""Continuation-budget helpers for brain runtime closure gates."""

from typing import Any


def has_continuation_budget(state: Any) -> bool:
    budgets = getattr(state, "budgets_remaining", None)
    if budgets is None:
        return False
    try:
        return (
            int(getattr(budgets, "tool_calls", 0) or 0) > 0
            and int(getattr(budgets, "tokens", 0) or 0) > 0
            and int(getattr(budgets, "time_ms", 0) or 0) > 0
        )
    except (TypeError, ValueError):
        return False


__all__ = ["has_continuation_budget"]
