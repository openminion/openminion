from __future__ import annotations

import typing
from types import MappingProxyType

import pytest

from openminion.modules.brain.runtime.delegation import (
    AggregatedResult,
    BudgetShare,
    CancellationCascadePolicy,
    CascadePlan,
    ChildBudget,
    ChildDeadline,
    ChildMargin,
    ChildResultRecord,
    ChildStateSnapshot,
    ConflictResolutionPolicy,
    DelegationBudgetPropagationPolicy,
    DelegationDeadlinePolicy,
    DelegationDepthDecision,
    DelegationDepthEvent,
    DelegationFlow,
    DelegationResultAggregation,
    FLOW_DEFAULTS,
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


@pytest.mark.parametrize(
    ("literal_type", "expected"),
    [
        (
            DelegationBudgetPropagationPolicy,
            {"share_pool", "split_fixed", "split_proportional", "fresh_child"},
        ),
        (
            DelegationDeadlinePolicy,
            {"inherit", "shrink_by_margin", "fresh_child", "none"},
        ),
        (
            CancellationCascadePolicy,
            {"cascade_all", "cascade_async_only", "isolate_children"},
        ),
        (
            ConflictResolutionPolicy,
            {"serialize_conflicts", "skip_conflicts", "fail_on_conflict"},
        ),
        (
            DelegationResultAggregation,
            {"all_required", "first_success", "best_effort", "structural_merge"},
        ),
        (
            DelegationFlow,
            {
                "a2a_sync",
                "a2a_async",
                "orchestrate_inline",
                "orchestrate_promoted",
                "coding_subtask",
                "mission_turn",
            },
        ),
    ],
)
def test_literal_closed_sets(
    literal_type: object,
    expected: set[str],
) -> None:
    assert set(typing.get_args(literal_type)) == expected


def test_flow_defaults_is_mappingproxy() -> None:
    assert isinstance(FLOW_DEFAULTS, MappingProxyType)


def test_flow_defaults_cannot_be_mutated() -> None:
    with pytest.raises(TypeError):
        FLOW_DEFAULTS["a2a_sync"] = None  # type: ignore[index]


def test_flow_defaults_covers_every_flow() -> None:
    for flow in typing.get_args(DelegationFlow):
        bundle = flow_defaults(flow)
        assert bundle.budget_policy in typing.get_args(
            DelegationBudgetPropagationPolicy
        )
        assert bundle.deadline_policy in typing.get_args(DelegationDeadlinePolicy)
        assert bundle.cancel_policy in typing.get_args(CancellationCascadePolicy)
        assert bundle.conflict_policy in typing.get_args(ConflictResolutionPolicy)
        assert bundle.aggregation_policy in typing.get_args(DelegationResultAggregation)


def test_flow_defaults_unknown_flow_raises() -> None:
    with pytest.raises(ValueError):
        flow_defaults("not_a_flow")  # type: ignore[arg-type]


_PARENT_BUDGET = ParentBudget(
    ticks=20, tool_calls=10, a2a_calls=8, tokens=4000, time_ms=60_000
)


def test_project_child_budget_share_pool_preserves_counters() -> None:
    child = project_child_budget(_PARENT_BUDGET, "share_pool")
    assert child == ChildBudget(
        ticks=20,
        tool_calls=10,
        a2a_calls=8,
        tokens=4000,
        time_ms=60_000,
        source_policy="share_pool",
    )


def test_project_child_budget_split_fixed_divides_by_denominator() -> None:
    child = project_child_budget(
        _PARENT_BUDGET, "split_fixed", share=BudgetShare(denominator=4)
    )
    assert child.ticks == 5
    assert child.tool_calls == 2
    assert child.tokens == 1000
    assert child.time_ms == 15_000
    assert child.source_policy == "split_fixed"


def test_project_child_budget_split_proportional_scales_by_fraction() -> None:
    child = project_child_budget(
        _PARENT_BUDGET, "split_proportional", share=BudgetShare(fraction=0.25)
    )
    assert child.ticks == 5
    assert child.tool_calls == 2
    assert child.tokens == 1000
    assert child.source_policy == "split_proportional"


def test_project_child_budget_fresh_child_zeros_counters() -> None:
    child = project_child_budget(_PARENT_BUDGET, "fresh_child")
    assert child == ChildBudget(source_policy="fresh_child")


def test_project_child_budget_is_deterministic() -> None:
    first = project_child_budget(
        _PARENT_BUDGET, "split_proportional", share=BudgetShare(fraction=0.5)
    )
    second = project_child_budget(
        _PARENT_BUDGET, "split_proportional", share=BudgetShare(fraction=0.5)
    )
    assert first == second


def test_project_child_budget_does_not_mutate_parent() -> None:
    snapshot = _PARENT_BUDGET.model_dump()
    project_child_budget(
        _PARENT_BUDGET, "split_fixed", share=BudgetShare(denominator=2)
    )
    assert _PARENT_BUDGET.model_dump() == snapshot


def test_project_child_budget_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError):
        project_child_budget(_PARENT_BUDGET, "bogus")  # type: ignore[arg-type]


def test_project_child_deadline_none_policy_yields_empty() -> None:
    out = project_child_deadline(
        ParentDeadline(deadline_iso="2026-05-14T00:00:00Z"), "none"
    )
    assert out == ChildDeadline(deadline_iso="", source_policy="none")


def test_project_child_deadline_inherit_copies_parent_iso() -> None:
    out = project_child_deadline(
        ParentDeadline(deadline_iso="2026-05-14T00:00:00Z"), "inherit"
    )
    assert out.deadline_iso == "2026-05-14T00:00:00Z"
    assert out.source_policy == "inherit"


def test_project_child_deadline_inherit_with_missing_parent_yields_empty() -> None:
    out = project_child_deadline(None, "inherit")
    assert out.deadline_iso == ""
    assert out.source_policy == "inherit"


def test_project_child_deadline_fresh_child_drops_parent_iso() -> None:
    out = project_child_deadline(
        ParentDeadline(deadline_iso="2026-05-14T00:00:00Z"), "fresh_child"
    )
    assert out.deadline_iso == ""
    assert out.source_policy == "fresh_child"


def test_project_child_deadline_shrink_by_margin_preserves_parent_when_margin_zero() -> (
    None
):
    out = project_child_deadline(
        ParentDeadline(deadline_iso="2026-05-14T00:00:00Z"),
        "shrink_by_margin",
        margin=ChildMargin(margin_ms=0),
    )
    assert out.deadline_iso == "2026-05-14T00:00:00Z"


def test_project_child_deadline_shrink_by_margin_encodes_margin_structurally() -> None:
    out = project_child_deadline(
        ParentDeadline(deadline_iso="2026-05-14T00:00:00Z"),
        "shrink_by_margin",
        margin=ChildMargin(margin_ms=500),
    )
    assert out.deadline_iso == "2026-05-14T00:00:00Z|margin_ms=500"
    assert out.source_policy == "shrink_by_margin"


def test_project_child_deadline_shrink_by_margin_with_no_parent_does_not_invent() -> (
    None
):
    out = project_child_deadline(
        None, "shrink_by_margin", margin=ChildMargin(margin_ms=500)
    )
    assert out.deadline_iso == ""
    assert out.source_policy == "shrink_by_margin"


def test_project_child_deadline_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError):
        project_child_deadline(None, "bogus")  # type: ignore[arg-type]


def _children() -> list[ChildStateSnapshot]:
    return [
        ChildStateSnapshot(child_id="c2", mode="async", is_terminal=False),
        ChildStateSnapshot(child_id="c1", mode="sync", is_terminal=False),
        ChildStateSnapshot(child_id="c3", mode="async", is_terminal=True),
    ]


def test_evaluate_cancellation_cascade_returns_empty_when_not_requested() -> None:
    plan = evaluate_cancellation_cascade(
        ParentStateSnapshot(parent_id="p1", cancel_requested=False),
        _children(),
        "cascade_all",
    )
    assert plan == CascadePlan(parent_id="p1", steps=[], source_policy="cascade_all")


def test_evaluate_cancellation_cascade_cascade_all_orders_by_child_id() -> None:
    plan = evaluate_cancellation_cascade(
        ParentStateSnapshot(parent_id="p1", cancel_requested=True),
        _children(),
        "cascade_all",
    )
    assert [step.child_id for step in plan.steps] == ["c1", "c2"]
    assert all(step.directive == "cancel" for step in plan.steps)
    assert plan.source_policy == "cascade_all"


def test_evaluate_cancellation_cascade_async_only_skips_sync_cancel() -> None:
    plan = evaluate_cancellation_cascade(
        ParentStateSnapshot(parent_id="p1", cancel_requested=True),
        _children(),
        "cascade_async_only",
    )
    by_id = {step.child_id: step.directive for step in plan.steps}
    assert by_id == {"c1": "isolate", "c2": "cancel"}


def test_evaluate_cancellation_cascade_isolate_children_never_cancels() -> None:
    plan = evaluate_cancellation_cascade(
        ParentStateSnapshot(parent_id="p1", cancel_requested=True),
        _children(),
        "isolate_children",
    )
    assert all(step.directive == "isolate" for step in plan.steps)


def test_evaluate_cancellation_cascade_skips_terminal_children() -> None:
    plan = evaluate_cancellation_cascade(
        ParentStateSnapshot(parent_id="p1", cancel_requested=True),
        _children(),
        "cascade_all",
    )
    assert "c3" not in [step.child_id for step in plan.steps]


def test_evaluate_cancellation_cascade_is_replayable() -> None:
    parent = ParentStateSnapshot(parent_id="p1", cancel_requested=True)
    children = _children()
    first = evaluate_cancellation_cascade(parent, children, "cascade_all")
    second = evaluate_cancellation_cascade(parent, children, "cascade_all")
    assert first == second


def test_evaluate_cancellation_cascade_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError):
        evaluate_cancellation_cascade(
            ParentStateSnapshot(parent_id="p1", cancel_requested=True),
            _children(),
            "bogus",  # type: ignore[arg-type]
        )


def _records() -> list[ChildResultRecord]:
    return [
        ChildResultRecord(
            child_id="a",
            status="success",
            required=True,
            payload={"value": 1},
        ),
        ChildResultRecord(
            child_id="b",
            status="failure",
            required=True,
            payload={"value": 2},
        ),
        ChildResultRecord(
            child_id="c",
            status="success",
            required=False,
            payload={"value": 3},
        ),
    ]


def test_aggregate_all_required_marks_completed_false_on_failure() -> None:
    out = aggregate_delegation_results(_records(), "all_required")
    assert out.success_count == 2
    assert out.failure_count == 1
    assert out.completed_required is False
    assert out.source_policy == "all_required"


def test_aggregate_first_success_picks_first_success_in_order() -> None:
    out = aggregate_delegation_results(_records(), "first_success")
    assert out.selected_child_id == "a"
    assert out.merged_payload == {"value": 1}
    assert out.source_policy == "first_success"


def test_aggregate_best_effort_completes_required_when_any_success() -> None:
    out = aggregate_delegation_results(_records(), "best_effort")
    assert out.completed_required is True
    assert "a" in out.merged_payload
    assert "c" in out.merged_payload
    assert out.source_policy == "best_effort"


def test_aggregate_structural_merge_emits_status_for_every_child() -> None:
    out = aggregate_delegation_results(_records(), "structural_merge")
    assert set(out.merged_payload.keys()) == {"a", "b", "c"}
    for child_id, entry in out.merged_payload.items():
        assert entry["status"] in {"success", "failure", "skipped", "canceled"}
    assert out.source_policy == "structural_merge"


def test_aggregate_totality_every_policy_returns_typed_result() -> None:
    for policy in typing.get_args(DelegationResultAggregation):
        out = aggregate_delegation_results(_records(), policy)
        assert isinstance(out, AggregatedResult)
        assert out.total_children == 3
        assert out.source_policy == policy


def test_aggregate_is_deterministic() -> None:
    first = aggregate_delegation_results(_records(), "all_required")
    second = aggregate_delegation_results(_records(), "all_required")
    assert first == second


def test_aggregate_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError):
        aggregate_delegation_results(_records(), "bogus")  # type: ignore[arg-type]


def test_conflict_policy_outcome_is_deterministic_under_same_shape() -> None:
    budget = project_child_budget(_PARENT_BUDGET, "share_pool")
    deadline = project_child_deadline(None, "none")
    outcomes = {
        build_depth_decision(
            decision_id="d",
            parent_id="p",
            child_id="c",
            flow="coding_subtask",
            projected_budget=budget,
            projected_deadline=deadline,
            conflict_policy="serialize_conflicts",
        ).conflict_policy
        for _ in range(5)
    }
    assert outcomes == {"serialize_conflicts"}


def test_conflict_policy_default_per_flow_is_stable() -> None:
    for flow in typing.get_args(DelegationFlow):
        assert flow_defaults(flow).conflict_policy in typing.get_args(
            ConflictResolutionPolicy
        )


def test_build_depth_decision_uses_flow_defaults_when_unspecified() -> None:
    budget = project_child_budget(_PARENT_BUDGET, "share_pool")
    deadline = project_child_deadline(None, "none")
    decision = build_depth_decision(
        decision_id="d1",
        parent_id="p1",
        child_id="c1",
        flow="mission_turn",
        projected_budget=budget,
        projected_deadline=deadline,
    )
    expected = flow_defaults("mission_turn")
    assert decision.budget_policy == expected.budget_policy
    assert decision.deadline_policy == expected.deadline_policy
    assert decision.cancel_policy == expected.cancel_policy
    assert decision.conflict_policy == expected.conflict_policy
    assert decision.aggregation_policy == expected.aggregation_policy


def test_build_depth_event_requires_caller_declared_seam() -> None:
    budget = project_child_budget(_PARENT_BUDGET, "share_pool")
    deadline = project_child_deadline(None, "none")
    decision = build_depth_decision(
        decision_id="d1",
        parent_id="p1",
        child_id="c1",
        flow="a2a_sync",
        projected_budget=budget,
        projected_deadline=deadline,
    )
    with pytest.raises(ValueError):
        build_depth_event(
            event_id="e1",
            decision=decision,
            seam_id="",
            event_kind="budget_projected",
        )


def test_decision_event_parity_one_event_per_decision() -> None:
    budget = project_child_budget(_PARENT_BUDGET, "share_pool")
    deadline = project_child_deadline(None, "none")
    decision = build_depth_decision(
        decision_id="d1",
        parent_id="p1",
        child_id="c1",
        flow="a2a_async",
        projected_budget=budget,
        projected_deadline=deadline,
    )
    events: list[DelegationDepthEvent] = []
    for kind in (
        "budget_projected",
        "deadline_projected",
        "cancellation_evaluated",
        "results_aggregated",
    ):
        events.append(
            build_depth_event(
                event_id=f"e:{kind}",
                decision=decision,
                seam_id="modules.brain.adapters.a2a.runtime",
                event_kind=kind,  # type: ignore[arg-type]
            )
        )
    assert {e.decision_id for e in events} == {"d1"}
    assert {e.event_kind for e in events} == {
        "budget_projected",
        "deadline_projected",
        "cancellation_evaluated",
        "results_aggregated",
    }


_FORBIDDEN_FIELDS = {
    "verdict",
    "decision_reason_text",
    "judgment",
    "rationale_text",
    "reasoning_text",
    "summary_text",
}


@pytest.mark.parametrize(
    "model_cls",
    [
        ParentBudget,
        ChildBudget,
        ParentDeadline,
        ChildDeadline,
        ChildMargin,
        BudgetShare,
        ChildStateSnapshot,
        ParentStateSnapshot,
        ChildResultRecord,
        AggregatedResult,
        CascadePlan,
        DelegationDepthDecision,
        DelegationDepthEvent,
    ],
)
def test_no_prose_derived_fields_in_typed_records(model_cls: type) -> None:
    fields = set(model_cls.model_fields.keys())  # type: ignore[attr-defined]
    overlap = fields & _FORBIDDEN_FIELDS
    assert not overlap, f"{model_cls.__name__} has forbidden prose fields: {overlap}"


def test_aggregated_result_payload_is_structural_not_prose() -> None:
    out = aggregate_delegation_results(_records(), "all_required")
    for key, value in out.merged_payload.items():
        assert isinstance(key, str)
        assert isinstance(value, dict)
