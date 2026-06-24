"""Typed delegation-depth policy helpers."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.base.time import utc_now_iso

DelegationBudgetPropagationPolicy = Literal[
    "share_pool",
    "split_fixed",
    "split_proportional",
    "fresh_child",
]

DelegationDeadlinePolicy = Literal[
    "inherit",
    "shrink_by_margin",
    "fresh_child",
    "none",
]

CancellationCascadePolicy = Literal[
    "cascade_all",
    "cascade_async_only",
    "isolate_children",
]

ConflictResolutionPolicy = Literal[
    "serialize_conflicts",
    "skip_conflicts",
    "fail_on_conflict",
]

DelegationResultAggregation = Literal[
    "all_required",
    "first_success",
    "best_effort",
    "structural_merge",
]

DelegationFlow = Literal[
    "a2a_sync",
    "a2a_async",
    "orchestrate_inline",
    "orchestrate_promoted",
    "coding_subtask",
    "mission_turn",
]

DelegationDepthEventKind = Literal[
    "budget_projected",
    "deadline_projected",
    "cancellation_evaluated",
    "results_aggregated",
]

ChildCancelDirective = Literal["cancel", "isolate"]
ChildExecutionMode = Literal["sync", "async"]
ChildResultStatus = Literal["success", "failure", "skipped", "canceled"]


_VALID_BUDGET_POLICIES: tuple[DelegationBudgetPropagationPolicy, ...] = (
    "share_pool",
    "split_fixed",
    "split_proportional",
    "fresh_child",
)

_VALID_DEADLINE_POLICIES: tuple[DelegationDeadlinePolicy, ...] = (
    "inherit",
    "shrink_by_margin",
    "fresh_child",
    "none",
)

_VALID_CANCEL_POLICIES: tuple[CancellationCascadePolicy, ...] = (
    "cascade_all",
    "cascade_async_only",
    "isolate_children",
)

_VALID_CONFLICT_POLICIES: tuple[ConflictResolutionPolicy, ...] = (
    "serialize_conflicts",
    "skip_conflicts",
    "fail_on_conflict",
)

_VALID_AGGREGATION_POLICIES: tuple[DelegationResultAggregation, ...] = (
    "all_required",
    "first_success",
    "best_effort",
    "structural_merge",
)

_VALID_FLOWS: tuple[DelegationFlow, ...] = (
    "a2a_sync",
    "a2a_async",
    "orchestrate_inline",
    "orchestrate_promoted",
    "coding_subtask",
    "mission_turn",
)


class ParentBudget(BaseModel):
    """Typed parent-side delegation budget snapshot."""

    model_config = ConfigDict(extra="forbid")

    ticks: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    a2a_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    time_ms: int = Field(default=0, ge=0)


class ChildBudget(BaseModel):
    """Typed child-side projected delegation budget.

    Returned by ``project_child_budget``. Never mutates the parent.
    """

    model_config = ConfigDict(extra="forbid")

    ticks: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    a2a_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    time_ms: int = Field(default=0, ge=0)
    source_policy: DelegationBudgetPropagationPolicy


class ParentDeadline(BaseModel):
    """Typed parent-side deadline snapshot."""

    model_config = ConfigDict(extra="forbid")

    deadline_iso: str = ""


class ChildDeadline(BaseModel):
    """Typed child-side projected deadline."""

    model_config = ConfigDict(extra="forbid")

    deadline_iso: str = ""
    source_policy: DelegationDeadlinePolicy


class ChildMargin(BaseModel):
    """Typed shrink-by-margin margin used by ``project_child_deadline``."""

    model_config = ConfigDict(extra="forbid")

    margin_ms: int = Field(default=0, ge=0)


class BudgetShare(BaseModel):
    """Typed budget share specification for ``split_*`` policies."""

    model_config = ConfigDict(extra="forbid")

    denominator: int = Field(default=1, ge=1)
    fraction: float = Field(default=1.0, ge=0.0, le=1.0)


class ChildStateSnapshot(BaseModel):
    """Typed child task snapshot consumed by ``evaluate_cancellation_cascade``.

    The evaluator inspects only structural fields here; no payload prose.
    """

    model_config = ConfigDict(extra="forbid")

    child_id: str
    mode: ChildExecutionMode
    is_terminal: bool = False


class ParentStateSnapshot(BaseModel):
    """Typed parent-cancel snapshot consumed by ``evaluate_cancellation_cascade``."""

    model_config = ConfigDict(extra="forbid")

    parent_id: str
    cancel_requested: bool = False


class ChildCascadeStep(BaseModel):
    """One typed step of the cascade plan."""

    model_config = ConfigDict(extra="forbid")

    child_id: str
    directive: ChildCancelDirective
    mode: ChildExecutionMode
    source_policy: CancellationCascadePolicy


class CascadePlan(BaseModel):
    """Typed cascade plan returned by ``evaluate_cancellation_cascade``.

    The plan is replayable: same inputs produce the same plan. The plan
    never fires cancellations itself.
    """

    model_config = ConfigDict(extra="forbid")

    parent_id: str
    steps: list[ChildCascadeStep] = Field(default_factory=list)
    source_policy: CancellationCascadePolicy


class ChildResultRecord(BaseModel):
    """Typed child-result record consumed by ``aggregate_delegation_results``."""

    model_config = ConfigDict(extra="forbid")

    child_id: str
    status: ChildResultStatus
    required: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class AggregatedResult(BaseModel):
    """Typed aggregated parent result."""

    model_config = ConfigDict(extra="forbid")

    total_children: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    canceled_count: int = Field(default=0, ge=0)
    completed_required: bool = False
    selected_child_id: str = ""
    merged_payload: dict[str, Any] = Field(default_factory=dict)
    child_ids: list[str] = Field(default_factory=list)
    source_policy: DelegationResultAggregation


class DelegationDepthDecision(BaseModel):
    """Typed canonical delegation-depth decision."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    parent_id: str
    child_id: str
    budget_policy: DelegationBudgetPropagationPolicy
    deadline_policy: DelegationDeadlinePolicy
    cancel_policy: CancellationCascadePolicy
    conflict_policy: ConflictResolutionPolicy
    aggregation_policy: DelegationResultAggregation
    projected_budget: ChildBudget
    projected_deadline: ChildDeadline
    decided_at: str = Field(default="")


class DelegationDepthEvent(BaseModel):
    """Typed canonical delegation-depth audit event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    decision_id: str
    parent_id: str
    child_id: str
    seam_id: str
    event_kind: DelegationDepthEventKind
    recorded_at: str = Field(default="")


class _FlowDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    budget_policy: DelegationBudgetPropagationPolicy
    deadline_policy: DelegationDeadlinePolicy
    cancel_policy: CancellationCascadePolicy
    conflict_policy: ConflictResolutionPolicy
    aggregation_policy: DelegationResultAggregation


_FLOW_DEFAULTS_RAW: dict[DelegationFlow, _FlowDefaults] = {
    "a2a_sync": _FlowDefaults(
        budget_policy="share_pool",
        deadline_policy="inherit",
        cancel_policy="cascade_all",
        conflict_policy="serialize_conflicts",
        aggregation_policy="all_required",
    ),
    "a2a_async": _FlowDefaults(
        budget_policy="split_proportional",
        deadline_policy="inherit",
        cancel_policy="cascade_async_only",
        conflict_policy="serialize_conflicts",
        aggregation_policy="best_effort",
    ),
    "orchestrate_inline": _FlowDefaults(
        budget_policy="split_fixed",
        deadline_policy="inherit",
        cancel_policy="cascade_all",
        conflict_policy="serialize_conflicts",
        aggregation_policy="structural_merge",
    ),
    "orchestrate_promoted": _FlowDefaults(
        budget_policy="split_proportional",
        deadline_policy="shrink_by_margin",
        cancel_policy="cascade_all",
        conflict_policy="serialize_conflicts",
        aggregation_policy="all_required",
    ),
    "coding_subtask": _FlowDefaults(
        budget_policy="split_fixed",
        deadline_policy="inherit",
        cancel_policy="cascade_all",
        conflict_policy="serialize_conflicts",
        aggregation_policy="all_required",
    ),
    "mission_turn": _FlowDefaults(
        budget_policy="share_pool",
        deadline_policy="none",
        cancel_policy="isolate_children",
        conflict_policy="serialize_conflicts",
        aggregation_policy="structural_merge",
    ),
}

FLOW_DEFAULTS: Mapping[DelegationFlow, _FlowDefaults] = MappingProxyType(
    _FLOW_DEFAULTS_RAW
)


def flow_defaults(flow: DelegationFlow) -> _FlowDefaults:
    """Return the frozen per-flow default policy bundle."""

    if flow not in _VALID_FLOWS:
        raise ValueError(f"unknown delegation flow: {flow!r}")
    return FLOW_DEFAULTS[flow]


def _require_policy(policy: str, *, valid: tuple[str, ...], kind: str) -> None:
    if policy not in valid:
        raise ValueError(f"unknown {kind} policy: {policy!r}")


def project_child_budget(
    parent_budget: ParentBudget | Mapping[str, Any],
    policy: DelegationBudgetPropagationPolicy,
    *,
    share: BudgetShare | Mapping[str, Any] | None = None,
) -> ChildBudget:
    """Apply the typed budget propagation policy to a parent budget."""

    _require_policy(policy, valid=_VALID_BUDGET_POLICIES, kind="budget")
    parent_obj = (
        parent_budget
        if isinstance(parent_budget, ParentBudget)
        else ParentBudget.model_validate(parent_budget)
    )
    share_obj: BudgetShare | None
    if share is None:
        share_obj = None
    elif isinstance(share, BudgetShare):
        share_obj = share
    else:
        share_obj = BudgetShare.model_validate(share)

    if policy == "share_pool":
        return ChildBudget(
            ticks=parent_obj.ticks,
            tool_calls=parent_obj.tool_calls,
            a2a_calls=parent_obj.a2a_calls,
            tokens=parent_obj.tokens,
            time_ms=parent_obj.time_ms,
            source_policy=policy,
        )
    if policy == "split_fixed":
        denominator = share_obj.denominator if share_obj is not None else 1
        return ChildBudget(
            ticks=parent_obj.ticks // denominator,
            tool_calls=parent_obj.tool_calls // denominator,
            a2a_calls=parent_obj.a2a_calls // denominator,
            tokens=parent_obj.tokens // denominator,
            time_ms=parent_obj.time_ms // denominator,
            source_policy=policy,
        )
    if policy == "split_proportional":
        fraction = share_obj.fraction if share_obj is not None else 1.0
        return ChildBudget(
            ticks=int(parent_obj.ticks * fraction),
            tool_calls=int(parent_obj.tool_calls * fraction),
            a2a_calls=int(parent_obj.a2a_calls * fraction),
            tokens=int(parent_obj.tokens * fraction),
            time_ms=int(parent_obj.time_ms * fraction),
            source_policy=policy,
        )
    return ChildBudget(source_policy=policy)


def project_child_deadline(
    parent_deadline: ParentDeadline | Mapping[str, Any] | None,
    policy: DelegationDeadlinePolicy,
    *,
    margin: ChildMargin | Mapping[str, Any] | None = None,
) -> ChildDeadline:
    """Apply the typed deadline policy to a parent deadline."""

    _require_policy(policy, valid=_VALID_DEADLINE_POLICIES, kind="deadline")
    parent_obj: ParentDeadline | None
    if parent_deadline is None:
        parent_obj = None
    elif isinstance(parent_deadline, ParentDeadline):
        parent_obj = parent_deadline
    else:
        parent_obj = ParentDeadline.model_validate(parent_deadline)

    parent_iso = (parent_obj.deadline_iso if parent_obj is not None else "").strip()

    if policy == "none":
        return ChildDeadline(deadline_iso="", source_policy=policy)
    if policy == "fresh_child":
        return ChildDeadline(deadline_iso="", source_policy=policy)
    if policy == "inherit":
        return ChildDeadline(deadline_iso=parent_iso, source_policy=policy)
    if not parent_iso:
        return ChildDeadline(deadline_iso="", source_policy=policy)
    margin_obj: ChildMargin
    if margin is None:
        margin_obj = ChildMargin()
    elif isinstance(margin, ChildMargin):
        margin_obj = margin
    else:
        margin_obj = ChildMargin.model_validate(margin)
    if margin_obj.margin_ms <= 0:
        return ChildDeadline(deadline_iso=parent_iso, source_policy=policy)
    return ChildDeadline(
        deadline_iso=f"{parent_iso}|margin_ms={margin_obj.margin_ms}",
        source_policy=policy,
    )


def evaluate_cancellation_cascade(
    parent_state: ParentStateSnapshot | Mapping[str, Any],
    children: list[ChildStateSnapshot] | list[Mapping[str, Any]],
    policy: CancellationCascadePolicy,
) -> CascadePlan:
    """Evaluate the typed cancellation-cascade plan."""

    _require_policy(policy, valid=_VALID_CANCEL_POLICIES, kind="cancellation")
    parent_obj = (
        parent_state
        if isinstance(parent_state, ParentStateSnapshot)
        else ParentStateSnapshot.model_validate(parent_state)
    )
    child_objs: list[ChildStateSnapshot] = []
    for child in children:
        child_objs.append(
            child
            if isinstance(child, ChildStateSnapshot)
            else ChildStateSnapshot.model_validate(child)
        )

    if not parent_obj.cancel_requested:
        return CascadePlan(
            parent_id=parent_obj.parent_id,
            steps=[],
            source_policy=policy,
        )

    steps: list[ChildCascadeStep] = []
    for child in child_objs:
        if child.is_terminal:
            continue
        if policy == "isolate_children":
            directive: ChildCancelDirective = "isolate"
        elif policy == "cascade_async_only":
            directive = "cancel" if child.mode == "async" else "isolate"
        else:  # policy == "cascade_all"
            directive = "cancel"
        steps.append(
            ChildCascadeStep(
                child_id=child.child_id,
                directive=directive,
                mode=child.mode,
                source_policy=policy,
            )
        )
    steps.sort(key=lambda step: step.child_id)
    return CascadePlan(
        parent_id=parent_obj.parent_id,
        steps=steps,
        source_policy=policy,
    )


def aggregate_delegation_results(
    child_results: list[ChildResultRecord] | list[Mapping[str, Any]],
    policy: DelegationResultAggregation,
) -> AggregatedResult:
    """Compose typed child results under the closed-set aggregation policy.

    Structural composition only. No prose-derived "verdict", no LLM
    judgment, no content-type-based routing.
    """

    _require_policy(policy, valid=_VALID_AGGREGATION_POLICIES, kind="aggregation")
    records: list[ChildResultRecord] = []
    for record in child_results:
        records.append(
            record
            if isinstance(record, ChildResultRecord)
            else ChildResultRecord.model_validate(record)
        )

    success_count = sum(1 for r in records if r.status == "success")
    failure_count = sum(1 for r in records if r.status == "failure")
    skipped_count = sum(1 for r in records if r.status == "skipped")
    canceled_count = sum(1 for r in records if r.status == "canceled")
    child_ids = [r.child_id for r in records]

    if policy == "all_required":
        required_records = [r for r in records if r.required]
        completed_required = bool(required_records) and all(
            r.status == "success" for r in required_records
        )
        merged: dict[str, Any] = {}
        for r in records:
            if r.status == "success":
                merged[r.child_id] = dict(r.payload)
        return AggregatedResult(
            total_children=len(records),
            success_count=success_count,
            failure_count=failure_count,
            skipped_count=skipped_count,
            canceled_count=canceled_count,
            completed_required=completed_required,
            selected_child_id="",
            merged_payload=merged,
            child_ids=list(child_ids),
            source_policy=policy,
        )
    if policy == "first_success":
        selected = ""
        merged = {}
        for r in records:
            if r.status == "success":
                selected = r.child_id
                merged = dict(r.payload)
                break
        return AggregatedResult(
            total_children=len(records),
            success_count=success_count,
            failure_count=failure_count,
            skipped_count=skipped_count,
            canceled_count=canceled_count,
            completed_required=bool(selected),
            selected_child_id=selected,
            merged_payload=merged,
            child_ids=list(child_ids),
            source_policy=policy,
        )
    if policy == "best_effort":
        merged = {}
        for r in records:
            if r.status == "success":
                merged[r.child_id] = dict(r.payload)
        return AggregatedResult(
            total_children=len(records),
            success_count=success_count,
            failure_count=failure_count,
            skipped_count=skipped_count,
            canceled_count=canceled_count,
            completed_required=success_count > 0,
            selected_child_id="",
            merged_payload=merged,
            child_ids=list(child_ids),
            source_policy=policy,
        )
    merged = {}
    for r in records:
        merged[r.child_id] = {
            "status": r.status,
            "required": r.required,
            "payload": dict(r.payload),
        }
    required_records = [r for r in records if r.required]
    completed_required = bool(required_records) and all(
        r.status == "success" for r in required_records
    )
    return AggregatedResult(
        total_children=len(records),
        success_count=success_count,
        failure_count=failure_count,
        skipped_count=skipped_count,
        canceled_count=canceled_count,
        completed_required=completed_required,
        selected_child_id="",
        merged_payload=merged,
        child_ids=list(child_ids),
        source_policy=policy,
    )


def build_depth_decision(
    *,
    decision_id: str,
    parent_id: str,
    child_id: str,
    flow: DelegationFlow,
    projected_budget: ChildBudget,
    projected_deadline: ChildDeadline,
    budget_policy: DelegationBudgetPropagationPolicy | None = None,
    deadline_policy: DelegationDeadlinePolicy | None = None,
    cancel_policy: CancellationCascadePolicy | None = None,
    conflict_policy: ConflictResolutionPolicy | None = None,
    aggregation_policy: DelegationResultAggregation | None = None,
) -> DelegationDepthDecision:
    """Build a typed ``DelegationDepthDecision``."""

    defaults = flow_defaults(flow)
    return DelegationDepthDecision(
        decision_id=decision_id,
        parent_id=parent_id,
        child_id=child_id,
        budget_policy=budget_policy or defaults.budget_policy,
        deadline_policy=deadline_policy or defaults.deadline_policy,
        cancel_policy=cancel_policy or defaults.cancel_policy,
        conflict_policy=conflict_policy or defaults.conflict_policy,
        aggregation_policy=aggregation_policy or defaults.aggregation_policy,
        projected_budget=projected_budget,
        projected_deadline=projected_deadline,
        decided_at=utc_now_iso(),
    )


def build_depth_event(
    *,
    event_id: str,
    decision: DelegationDepthDecision,
    seam_id: str,
    event_kind: DelegationDepthEventKind,
) -> DelegationDepthEvent:
    """Build a typed ``DelegationDepthEvent`` for the canonical-events stream.

    ``seam_id`` must be a caller-declared constant. This function does
    NOT synthesize a seam_id from any runtime payload.
    """

    if not isinstance(seam_id, str) or not seam_id.strip():
        raise ValueError("seam_id must be a non-empty constant string")
    return DelegationDepthEvent(
        event_id=event_id,
        decision_id=decision.decision_id,
        parent_id=decision.parent_id,
        child_id=decision.child_id,
        seam_id=seam_id,
        event_kind=event_kind,
        recorded_at=utc_now_iso(),
    )


__all__ = [
    "AggregatedResult",
    "BudgetShare",
    "CancellationCascadePolicy",
    "CascadePlan",
    "ChildBudget",
    "ChildCancelDirective",
    "ChildCascadeStep",
    "ChildDeadline",
    "ChildExecutionMode",
    "ChildMargin",
    "ChildResultRecord",
    "ChildResultStatus",
    "ChildStateSnapshot",
    "ConflictResolutionPolicy",
    "DelegationBudgetPropagationPolicy",
    "DelegationDeadlinePolicy",
    "DelegationDepthDecision",
    "DelegationDepthEvent",
    "DelegationDepthEventKind",
    "DelegationFlow",
    "DelegationResultAggregation",
    "FLOW_DEFAULTS",
    "ParentBudget",
    "ParentDeadline",
    "ParentStateSnapshot",
    "aggregate_delegation_results",
    "build_depth_decision",
    "build_depth_event",
    "evaluate_cancellation_cascade",
    "flow_defaults",
    "project_child_budget",
    "project_child_deadline",
]
