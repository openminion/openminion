from typing import Any

from openminion.modules.context.schemas import default_budgets_for


def resolve_context_total_token_budget(
    *,
    purpose: str,
    runtime_token_budget: Any = None,
    requested_token_budget: Any = None,
) -> int:
    default_cap = int(default_budgets_for(_normalize_purpose(purpose)).total_max_tokens)
    runtime_cap = _normalize_positive_int(runtime_token_budget)
    requested_cap = _normalize_positive_int(requested_token_budget)

    if runtime_cap is not None and requested_cap is not None:
        return max(1, min(runtime_cap, requested_cap))
    if requested_cap is not None:
        return max(1, requested_cap)
    if runtime_cap is not None:
        return max(1, runtime_cap)
    return max(1, default_cap)


def _normalize_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if normalized <= 0:
        return None
    return normalized


def _normalize_purpose(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {
        "decide",
        "plan",
        "act",
        "reflect",
        "summarize",
        "judge",
        "validate",
        "chat",
    }:
        return normalized
    return "plan"


__all__ = ["resolve_context_total_token_budget"]
