"""Production bridge for typed delegation policy facts."""

from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import (
    STATE_KEY_MODULE_STATE,
    STATE_KEY_TASK_BACKED_RESUME,
)
from openminion.modules.brain.execution.child_tasks import SubtaskResult
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.schemas import WorkingState
from openminion.modules.brain.runtime.delegation import (
    BudgetShare,
    ChildMargin,
    ChildResultRecord,
    ChildStateSnapshot,
    DelegationFlow,
    ParentBudget,
    ParentDeadline,
    ParentStateSnapshot,
    aggregate_delegation_results,
    build_depth_decision,
    build_depth_event,
    evaluate_cancellation_cascade,
    flow_defaults,
    project_child_budget,
    project_child_deadline,
)

_MODULE_STATE_KEY = "delegation_policy"


def _bucket(ctx: ExecutionContext) -> dict[str, Any]:
    module_state = getattr(ctx.state, STATE_KEY_MODULE_STATE, None)
    if not isinstance(module_state, dict):
        module_state = {}
        setattr(ctx.state, STATE_KEY_MODULE_STATE, module_state)
    bucket = module_state.get(_MODULE_STATE_KEY)
    if not isinstance(bucket, dict):
        bucket = {"version": 1, "projections": [], "aggregations": []}
        module_state[_MODULE_STATE_KEY] = bucket
    bucket.setdefault("version", 1)
    bucket.setdefault("projections", [])
    bucket.setdefault("aggregations", [])
    return bucket


def _parent_budget(ctx: ExecutionContext) -> ParentBudget:
    budget = ctx.state.budgets_remaining
    return ParentBudget(
        ticks=int(getattr(budget, "ticks", 0) or 0),
        tool_calls=int(getattr(budget, "tool_calls", 0) or 0),
        a2a_calls=int(getattr(budget, "a2a_calls", 0) or 0),
        tokens=int(getattr(budget, "tokens", 0) or 0),
        time_ms=int(getattr(budget, "time_ms", 0) or 0),
    )


def _parent_deadline(ctx: ExecutionContext) -> ParentDeadline:
    for attr in ("deadline_iso", "deadline", "task_deadline_iso"):
        value = str(getattr(ctx.state, attr, "") or "").strip()
        if value:
            return ParentDeadline(deadline_iso=value)
    resume_state = getattr(ctx.state, STATE_KEY_TASK_BACKED_RESUME, {}) or {}
    if isinstance(resume_state, dict):
        value = str(
            resume_state.get("deadline_iso")
            or resume_state.get("deadline")
            or ""
        ).strip()
        if value:
            return ParentDeadline(deadline_iso=value)
    return ParentDeadline()


def _parent_id(ctx: ExecutionContext, fallback: str) -> str:
    for value in (
        getattr(ctx.state, "task_backed_task_id", None),
        getattr(ctx.state, "trace_id", None),
        getattr(ctx.state, "session_id", None),
        fallback,
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return "delegation-parent"


def _cancel_requested(ctx: ExecutionContext) -> bool:
    return bool(
        getattr(ctx.options, "decompose_cancel_requested", False)
        or getattr(ctx.options, "delegation_cancel_requested", False)
    )


def record_child_policy_projection(
    ctx: ExecutionContext,
    *,
    flow: DelegationFlow,
    child_id: str,
    seam_id: str,
    child_count: int = 1,
    child_mode: str = "sync",
    parent_id: str = "",
) -> dict[str, Any]:
    """Record structural budget/deadline/cancel facts for one child call."""

    defaults = flow_defaults(flow)
    denominator = max(1, int(child_count or 1))
    projected_budget = project_child_budget(
        _parent_budget(ctx),
        defaults.budget_policy,
        share=BudgetShare(denominator=denominator, fraction=1 / denominator),
    )
    projected_deadline = project_child_deadline(
        _parent_deadline(ctx),
        defaults.deadline_policy,
        margin=ChildMargin(margin_ms=1000),
    )
    normalized_parent_id = _parent_id(ctx, parent_id or "delegation-parent")
    normalized_child_id = str(child_id or "delegation-child").strip()
    decision = build_depth_decision(
        decision_id=f"{normalized_parent_id}:{normalized_child_id}:{flow}",
        parent_id=normalized_parent_id,
        child_id=normalized_child_id,
        flow=flow,
        projected_budget=projected_budget,
        projected_deadline=projected_deadline,
    )
    mode = "async" if child_mode == "async" else "sync"
    cascade = evaluate_cancellation_cascade(
        ParentStateSnapshot(
            parent_id=normalized_parent_id,
            cancel_requested=_cancel_requested(ctx),
        ),
        [
            ChildStateSnapshot(
                child_id=normalized_child_id,
                mode=mode,
                is_terminal=False,
            )
        ],
        defaults.cancel_policy,
    )
    events = [
        build_depth_event(
            event_id=f"{decision.decision_id}:budget",
            decision=decision,
            seam_id=seam_id,
            event_kind="budget_projected",
        ),
        build_depth_event(
            event_id=f"{decision.decision_id}:deadline",
            decision=decision,
            seam_id=seam_id,
            event_kind="deadline_projected",
        ),
        build_depth_event(
            event_id=f"{decision.decision_id}:cancel",
            decision=decision,
            seam_id=seam_id,
            event_kind="cancellation_evaluated",
        ),
    ]
    entry = {
        "flow": flow,
        "child_mode": mode,
        "decision": decision.model_dump(mode="python"),
        "cascade": cascade.model_dump(mode="python"),
        "events": [event.model_dump(mode="python") for event in events],
    }
    _bucket(ctx)["projections"].append(entry)
    return entry


def _record_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "success", "done"}:
        return "success"
    if normalized in {"cancelled", "canceled", "stopped"}:
        return "canceled"
    if normalized == "skipped":
        return "skipped"
    return "failure"


def record_result_aggregation(
    ctx: ExecutionContext,
    *,
    flow: DelegationFlow,
    parent_id: str,
    seam_id: str,
    results: list[SubtaskResult],
) -> dict[str, Any]:
    """Record structural child-result aggregation facts."""

    defaults = flow_defaults(flow)
    records = [
        ChildResultRecord(
            child_id=result.subtask_id,
            status=_record_status(result.status),
            required=True,
            payload={
                "goal": result.goal,
                "mode_used": result.mode_used,
                "tokens_used": result.tokens_used,
            },
        )
        for result in results
    ]
    aggregate = aggregate_delegation_results(records, defaults.aggregation_policy)
    entry = {
        "flow": flow,
        "parent_id": _parent_id(ctx, parent_id),
        "seam_id": seam_id,
        "aggregation": aggregate.model_dump(mode="python"),
    }
    _bucket(ctx)["aggregations"].append(entry)
    return entry


def merge_child_policy_facts(ctx: ExecutionContext, *, child_state: WorkingState) -> None:
    """Merge child-recorded structural delegation facts into the parent state."""

    child_module_state = getattr(child_state, STATE_KEY_MODULE_STATE, {}) or {}
    if not isinstance(child_module_state, dict):
        return
    child_bucket = child_module_state.get(_MODULE_STATE_KEY)
    if not isinstance(child_bucket, dict):
        return
    parent_bucket = _bucket(ctx)
    parent_bucket["projections"].extend(list(child_bucket.get("projections", []) or []))
    parent_bucket["aggregations"].extend(
        list(child_bucket.get("aggregations", []) or [])
    )


def clear_policy_facts(state: WorkingState) -> None:
    """Remove inherited delegation-policy facts from a child state copy."""

    module_state = getattr(state, STATE_KEY_MODULE_STATE, None)
    if isinstance(module_state, dict):
        module_state.pop(_MODULE_STATE_KEY, None)


__all__ = [
    "clear_policy_facts",
    "merge_child_policy_facts",
    "record_child_policy_projection",
    "record_result_aggregation",
]
