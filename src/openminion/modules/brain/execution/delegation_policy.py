"""Production bridge for typed delegation policy facts."""

from __future__ import annotations

from typing import Any

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
    module_state = ctx.state.module_state
    bucket = module_state.get(_MODULE_STATE_KEY)
    if bucket is None:
        bucket = {"version": 1, "projections": [], "aggregations": []}
        module_state[_MODULE_STATE_KEY] = bucket
    bucket.setdefault("version", 1)
    bucket.setdefault("projections", [])
    bucket.setdefault("aggregations", [])
    return bucket


def _parent_budget(ctx: ExecutionContext) -> ParentBudget:
    budget = ctx.state.budgets_remaining
    return ParentBudget(
        ticks=budget.ticks,
        tool_calls=budget.tool_calls,
        a2a_calls=budget.a2a_calls,
        tokens=budget.tokens,
        time_ms=budget.time_ms,
    )


def _parent_deadline(ctx: ExecutionContext) -> ParentDeadline:
    resume_state = ctx.state.task_backed_resume_state
    value = str(
        resume_state.get("deadline_iso") or resume_state.get("deadline") or ""
    ).strip()
    return ParentDeadline(deadline_iso=value)


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
    return {
        "completed": "success",
        "cancelled": "canceled",
        "skipped": "skipped",
    }.get(status, "failure")


def record_result_aggregation(
    ctx: ExecutionContext,
    *,
    flow: DelegationFlow,
    parent_id: str,
    seam_id: str,
    results: list[SubtaskResult],
) -> dict[str, Any]:
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


def merge_child_policy_facts(
    ctx: ExecutionContext, *, child_state: WorkingState
) -> None:
    child_bucket = child_state.module_state.get(_MODULE_STATE_KEY)
    if child_bucket is None:
        return
    parent_bucket = _bucket(ctx)
    parent_bucket["projections"].extend(list(child_bucket.get("projections", []) or []))
    parent_bucket["aggregations"].extend(
        list(child_bucket.get("aggregations", []) or [])
    )


def clear_policy_facts(state: WorkingState) -> None:
    state.module_state.pop(_MODULE_STATE_KEY, None)


__all__ = [
    "clear_policy_facts",
    "merge_child_policy_facts",
    "record_child_policy_projection",
    "record_result_aggregation",
]
