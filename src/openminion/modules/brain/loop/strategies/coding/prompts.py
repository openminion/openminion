_CODING_PLAN_SYSTEM_INTRO = (
    "Return a JSON CodingPlan with fields goal, phases, current_phase, "
    "scratchpad, completed_steps, open_issues, subtasks, requires_file_change, "
    "and optional verifier_goal. Set requires_file_change true when the task "
    "must create, edit, or patch workspace files; leave it false only for "
    "explicitly read-only analysis. Use phases in order explore -> plan -> "
    "implement -> verify. A plan with requires_file_change=true must include "
    "implement and end with verify; a single implement phase is only valid for "
    "read-only work."
    " Treat explicit user constraints on the first tool, forbidden tools, path "
    "scope, and operation order as hard plan constraints. When the user says to "
    "begin with a file write and not inspect first, start in implement, set "
    "requires_file_change true, and make that exact write the first step."
)

_CODING_PLAN_VERIFIER_GUIDANCE = (
    "For file-changing work, populate verifier_goal with goal_id, description, "
    "success_criteria, deliverables, and optional failure_conditions using the "
    "typed Goal shape. Each check must be concrete and supported by a planned "
    "readback or validation command. For read-only work, omit verifier_goal "
    "when no structural verification contract can be stated without guessing."
)


def build_coding_plan_system_prompt() -> str:
    return "\n".join((_CODING_PLAN_SYSTEM_INTRO, _CODING_PLAN_VERIFIER_GUIDANCE))


__all__ = ["build_coding_plan_system_prompt"]
