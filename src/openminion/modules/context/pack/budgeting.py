import hashlib
from typing import Any

from ..constants import (
    CONTEXT_BUDGET_TIER_FULL as _CONTEXT_BUDGET_TIER_FULL,
    CONTEXT_BUDGET_TIER_MEDIUM as _CONTEXT_BUDGET_TIER_MEDIUM,
    CONTEXT_BUDGET_TIER_SHORT as _CONTEXT_BUDGET_TIER_SHORT,
)
from ..mode_ranking import normalize_mode_name as _normalize_mode_name
from ..schemas import ContextBudgets


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _fit_to_budget(text: str, cap_tokens: int) -> tuple[str, bool]:
    cap = max(0, int(cap_tokens))
    if cap == 0:
        return "", bool(text.strip())
    max_chars = cap * 4
    compact = text.strip()
    if len(compact) <= max_chars:
        return compact, False
    return compact[:max_chars].rstrip() + "\n...[truncated]", True


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _apply_mode_budget_bias(
    budgets: ContextBudgets,
    *,
    mode_name: str | None = None,
) -> ContextBudgets:
    normalized_mode = _normalize_mode_name(mode_name)
    if normalized_mode is None:
        return budgets

    data = budgets.model_dump()
    if normalized_mode == "respond":
        data["recent_turn_tokens"] = max(160, int(data["recent_turn_tokens"]) // 2)
        data["memory_tokens"] = max(120, int(data["memory_tokens"]) // 2)
        data["facts_tokens"] = max(int(data["facts_tokens"]), 180)
    elif normalized_mode == "plan":
        data["skills_tokens"] = max(int(data["skills_tokens"]), 320)
        data["instructions_tokens"] = max(int(data["instructions_tokens"]), 240)
        data["memory_tokens"] = max(int(data["memory_tokens"]), 320)
    elif normalized_mode == "act":
        data["recent_turn_tokens"] = max(220, int(data["recent_turn_tokens"]) - 120)
        data["skills_tokens"] = max(int(data["skills_tokens"]), 240)

    return ContextBudgets(**data)


def _normalize_context_budget_tier(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {
        _CONTEXT_BUDGET_TIER_SHORT,
        _CONTEXT_BUDGET_TIER_MEDIUM,
        _CONTEXT_BUDGET_TIER_FULL,
    }:
        return normalized
    return None


def _apply_context_budget_tier_bias(
    budgets: ContextBudgets,
    *,
    tier: str | None = None,
) -> ContextBudgets:
    normalized_tier = _normalize_context_budget_tier(tier)
    if normalized_tier is None or normalized_tier == _CONTEXT_BUDGET_TIER_MEDIUM:
        return budgets

    data = budgets.model_dump()
    if normalized_tier == _CONTEXT_BUDGET_TIER_SHORT:
        data["recent_turn_tokens"] = max(160, int(data["recent_turn_tokens"] * 0.5))
        data["memory_tokens"] = max(120, int(data["memory_tokens"] * 0.5))
        data["artifact_tokens"] = max(50, int(data["artifact_tokens"] * 0.75))
    elif normalized_tier == _CONTEXT_BUDGET_TIER_FULL:
        data["recent_turn_tokens"] = int(data["recent_turn_tokens"] * 1.5)
        data["memory_tokens"] = int(data["memory_tokens"] * 1.5)
        data["artifact_tokens"] = int(data["artifact_tokens"] * 1.25)

    return ContextBudgets(**data)
