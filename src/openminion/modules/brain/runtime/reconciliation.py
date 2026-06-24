"""Pure evaluator for plan-reconciliation closure facts."""

from typing import Any

from ..constants import (
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITION_CONTINUE,
    PLAN_RECONCILIATION_INCOMPLETE_REASON,
    PLAN_RECONCILIATION_STEP_ID_DIAG_CAP,
    PLAN_RECONCILIATION_TERMINAL_STATUSES,
)
from ..schemas.closure import (
    ClosureJudgment,
    PlanReconciliationFact,
)
from .budget.continuation import has_continuation_budget


def evaluate_plan_reconciliation(
    active_plan: dict[str, Any] | None,
) -> PlanReconciliationFact:
    """Compute closure facts for non-terminal plan steps."""
    if not isinstance(active_plan, dict):
        return PlanReconciliationFact()
    steps = active_plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return PlanReconciliationFact()
    unreconciled_ids: list[str] = []
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            continue
        status = str(raw_step.get("status") or "").strip().lower()
        if status in PLAN_RECONCILIATION_TERMINAL_STATUSES:
            continue
        step_id = str(raw_step.get("step_id") or "").strip()
        unreconciled_ids.append(step_id or "<unknown>")
    if not unreconciled_ids:
        return PlanReconciliationFact()
    return PlanReconciliationFact(
        state="incomplete",
        unreconciled_items=len(unreconciled_ids),
        unreconciled_step_ids=tuple(
            unreconciled_ids[:PLAN_RECONCILIATION_STEP_ID_DIAG_CAP]
        ),
    )


def is_plan_reconciliation_incomplete(fact: PlanReconciliationFact | None) -> bool:
    """Return whether the plan reconciliation fact is incomplete."""
    return fact is not None and fact.state == "incomplete"


def apply_plan_reconciliation_to_judgment(
    judgment: ClosureJudgment,
    fact: PlanReconciliationFact,
    *,
    state: Any,
) -> ClosureJudgment:
    """Apply plan-reconciliation overrides to a closure judgment."""
    judgment.plan_reconciliation = fact
    if not is_plan_reconciliation_incomplete(fact):
        return judgment
    if not (judgment.satisfied and judgment.next_action == BRAIN_DISPOSITION_CLOSE):
        return judgment
    if has_continuation_budget(state):
        judgment.satisfied = False
        judgment.next_action = BRAIN_DISPOSITION_CONTINUE
        judgment.final_answer = None
    judgment.reason = (
        f"{judgment.reason}; {PLAN_RECONCILIATION_INCOMPLETE_REASON}"
        if judgment.reason
        else PLAN_RECONCILIATION_INCOMPLETE_REASON
    )
    return judgment


__all__ = [
    "PLAN_RECONCILIATION_INCOMPLETE_REASON",
    "PLAN_RECONCILIATION_STEP_ID_DIAG_CAP",
    "PLAN_RECONCILIATION_TERMINAL_STATUSES",
    "apply_plan_reconciliation_to_judgment",
    "evaluate_plan_reconciliation",
    "is_plan_reconciliation_incomplete",
]
