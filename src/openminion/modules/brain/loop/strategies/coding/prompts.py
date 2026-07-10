"""Coding-strategy prompt renderers."""

_CODING_PLAN_SYSTEM_INTRO = (
    "Return a JSON CodingPlan with fields goal, phases, current_phase, "
    "scratchpad, completed_steps, open_issues, subtasks, and optional "
    "verifier_goal. Use phases in order explore -> plan -> implement -> "
    "verify, or return a single implement phase."
)

_CODING_PLAN_VERIFIER_GUIDANCE = (
    "When you can state structural verification facts without guessing, "
    "populate verifier_goal with goal_id, description, success_criteria, "
    "deliverables, and optional failure_conditions using the typed Goal "
    "shape. Omit verifier_goal instead of inventing one."
)


def build_coding_plan_system_prompt(*, repo_index: str = "", repo_map: str = "") -> str:
    """Render the coding planner system prompt with optional repo context."""

    parts = [_CODING_PLAN_SYSTEM_INTRO, _CODING_PLAN_VERIFIER_GUIDANCE]
    index = str(repo_index or "").strip()
    fallback_map = str(repo_map or "").strip()
    if index:
        parts.extend(("", "[REPO INDEX]", index))
    elif fallback_map:
        parts.extend(("", "[REPO MAP - FALLBACK]", fallback_map))
    return "\n".join(parts).strip()


__all__ = ["build_coding_plan_system_prompt"]
