from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.brain.execution.child_tasks import (
    BudgetAllocator,
    CancellationPolicy,
    ChildContext,
    ContextInheritancePolicy,
    DecomposeControlPayload,
    DecomposePayload,
    ExecutionStrategy,
    FailureAction,
    FailurePolicy,
    ProgressMonitor,
    ResultSynthesizer,
    SubtaskModeResolver,
    SubtaskResult,
    SubtaskSpec,
)
from openminion.modules.brain.execution.orchestrate.strategies import (
    AbortOnNewMessagePolicy,
    AcceptOrPlanResolver,
    CompletionRatioMonitor,
    EqualSplitAllocator,
    FailFastPolicy,
    LLMSynthesizer,
    SequentialStrategy,
    SummaryInheritancePolicy,
    merge_delegation_context,
)
from openminion.modules.brain.constants import DELEGATION_TEXT_MAX_CHARS
from openminion.modules.brain.schemas import (
    BudgetCounters,
    DelegationContext,
    WorkingState,
)


def test_decompose_contract_types_are_runtime_checkable() -> None:
    assert isinstance(SequentialStrategy(), ExecutionStrategy)
    assert isinstance(EqualSplitAllocator(), BudgetAllocator)
    assert isinstance(AcceptOrPlanResolver(), SubtaskModeResolver)
    assert isinstance(LLMSynthesizer(), ResultSynthesizer)
    assert isinstance(FailFastPolicy(), FailurePolicy)
    assert isinstance(SummaryInheritancePolicy(), ContextInheritancePolicy)
    assert isinstance(CompletionRatioMonitor(), ProgressMonitor)
    assert isinstance(AbortOnNewMessagePolicy(), CancellationPolicy)


def test_synthesis_fails_closed_without_llm_service() -> None:
    state = WorkingState(
        session_id="s-decompose",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=0, tool_calls=0, a2a_calls=0, tokens=0, time_ms=0
        ),
    )
    ctx = SimpleNamespace(
        state=state,
        user_input="summarize",
        _services=SimpleNamespace(runner=None),
    )

    with pytest.raises(RuntimeError, match="requires an LLM service"):
        LLMSynthesizer().synthesize(ctx=ctx, results=[])


def test_decompose_payload_rejects_fewer_than_two_subtasks() -> None:
    with pytest.raises(ValidationError):
        DecomposePayload(subtasks=[SubtaskSpec(goal="only one")])


def test_subtask_dependencies_are_canonical_and_bounded() -> None:
    subtask = SubtaskSpec(
        subtask_id="child",
        goal="Child",
        depends_on=[" parent ", "parent", "peer"],
    )

    assert subtask.depends_on == ["parent", "peer"]
    with pytest.raises(ValidationError):
        SubtaskSpec(goal="Child", depends_on=["x" * 65])
    with pytest.raises(ValidationError):
        DecomposeControlPayload.model_validate(
            {
                "subtasks": [
                    {
                        "id": "child",
                        "description": "Child",
                        "depends_on": [" "],
                    }
                ]
            }
        )


def test_decompose_control_payload_allows_empty_decline() -> None:
    payload = DecomposeControlPayload(subtasks=[])

    assert payload.subtasks == []


def test_decompose_control_payload_requires_typed_subtask_fields() -> None:
    with pytest.raises(ValidationError):
        DecomposeControlPayload(subtasks=[{"id": "research"}])
    with pytest.raises(ValidationError):
        DecomposeControlPayload(subtasks=[{"description": "Research current docs"}])


def test_decompose_control_payload_rejects_runtime_rationale_field() -> None:
    with pytest.raises(ValidationError):
        DecomposeControlPayload(
            subtasks=[
                {
                    "id": "research",
                    "description": "Research current docs",
                    "decompose_rationale": "This task seems complex.",
                }
            ]
        )


def test_failure_action_enum_values_are_stable() -> None:
    assert FailureAction.ABORT.value == "abort"
    assert FailureAction.CONTINUE.value == "continue"


def test_equal_split_allocator_preserves_total_budget() -> None:
    allocator = EqualSplitAllocator()
    parent = BudgetCounters(
        ticks=10,
        tool_calls=7,
        a2a_calls=4,
        tokens=101,
        time_ms=1000,
    )

    budgets = allocator.allocate(budget=parent, subtask_count=3)

    assert len(budgets) == 3
    assert sum(item.ticks for item in budgets) == parent.ticks
    assert sum(item.tool_calls for item in budgets) == parent.tool_calls
    assert sum(item.a2a_calls for item in budgets) == parent.a2a_calls
    assert sum(item.tokens for item in budgets) == parent.tokens
    assert sum(item.time_ms for item in budgets) == parent.time_ms


def test_accept_or_plan_resolver_accepts_registered_modes_and_blocks_decompose() -> (
    None
):
    resolver = AcceptOrPlanResolver()
    available = ["respond", "act"]

    assert (
        resolver.resolve(
            subtask=SubtaskSpec(goal="weather", suggested_mode="act"),
            available_routes=available,
        )
        == "act"
    )
    assert (
        resolver.resolve(
            subtask=SubtaskSpec(goal="nest", suggested_mode="decompose"),
            available_routes=available,
        )
        == "act"
    )
    assert (
        resolver.resolve(
            subtask=SubtaskSpec(goal="unknown", suggested_mode="made_up"),
            available_routes=available,
        )
        == "act"
    )


def test_fail_fast_policy_aborts_on_any_failure() -> None:
    policy = FailFastPolicy()
    result = SubtaskResult(
        subtask_id="subtask-1",
        goal="broken",
        status="failed",
        mode_used="act",
        error="boom",
    )
    assert (
        policy.on_failure(subtask=SubtaskSpec(goal="broken"), result=result)
        == FailureAction.ABORT
    )


def test_summary_inheritance_policy_builds_child_context() -> None:
    policy = SummaryInheritancePolicy()
    parent_state = WorkingState(
        session_id="s-decompose",
        agent_id="agent",
        goal="Compare cloud providers",
        active_skill_id="skill-123",
        constraints=["keep it short"],
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=5,
            tokens=5000,
            time_ms=60000,
        ),
    )

    child = policy.build_child_context(
        parent_state=parent_state,
        subtask=SubtaskSpec(goal="Research AWS pricing", constraints="us-east only"),
    )

    assert isinstance(child, ChildContext)
    assert "Parent goal" in child.prompt
    assert "Subtask goal: Research AWS pricing" in child.prompt
    assert child.active_skill_id == "skill-123"
    assert child.constraints == ["keep it short", "us-east only"]


def test_summary_inheritance_bounds_multi_dependency_context() -> None:
    results = [
        SubtaskResult(
            subtask_id=subtask_id,
            goal=f"Goal {subtask_id}",
            status=status,
            mode_used="act",
            output=marker + ("x" * 700),
            child_artifact={"artifact": {"manifest_ref": f"artifact://{subtask_id}"}},
        )
        for subtask_id, status, marker in (
            ("research", "completed", "RESEARCH_ONLY_"),
            ("review", "failed", "REVIEW_ONLY_"),
        )
    ]

    child = SummaryInheritancePolicy().build_child_context(
        parent_state=WorkingState(
            session_id="s-dependencies",
            agent_id="agent",
            budgets_remaining=BudgetCounters(
                ticks=1,
                tool_calls=1,
                a2a_calls=1,
                tokens=1,
                time_ms=1,
            ),
        ),
        subtask=SubtaskSpec(goal="Verify", depends_on=["research", "review"]),
        dependency_results=results,
    )

    context = DelegationContext.model_validate(child.delegation_context)
    assert len(context.summary) <= DELEGATION_TEXT_MAX_CHARS
    payload = json.loads(context.summary.split("\n", 1)[1])
    assert [(item["subtask_id"], item["status"]) for item in payload] == [
        ("research", "completed"),
        ("review", "failed"),
    ]
    assert all(item["output"] for item in payload)
    assert context.artifacts == ["artifact://research", "artifact://review"]


def test_dependency_context_reserves_explicit_delegation_summary() -> None:
    results = [
        SubtaskResult(
            subtask_id=subtask_id,
            goal=subtask_id,
            status="completed",
            mode_used="act",
            output=marker + ("x" * 700),
        )
        for subtask_id, marker in (
            ("research", "EVIDENCE_ONLY_"),
            ("review", "REVIEW_ONLY_"),
        )
    ]
    child = SummaryInheritancePolicy().build_child_context(
        parent_state=WorkingState(
            session_id="s-explicit",
            agent_id="agent",
            budgets_remaining=BudgetCounters(
                ticks=0, tool_calls=0, a2a_calls=0, tokens=0, time_ms=0
            ),
        ),
        subtask=SubtaskSpec(goal="Verify", depends_on=["research", "review"]),
        dependency_results=results,
    )

    merged = DelegationContext.model_validate(
        merge_delegation_context(
            child.delegation_context,
            {"summary": "Use the strict rubric."},
        )
    )

    dependency_summary, explicit_summary = merged.summary.rsplit("\n", 1)
    payload = json.loads(dependency_summary.split("\n", 1)[1])
    assert len(merged.summary) <= DELEGATION_TEXT_MAX_CHARS
    assert explicit_summary == "Use the strict rubric."
    assert [item["subtask_id"] for item in payload] == ["research", "review"]
    assert all(item["output"] for item in payload)


def test_explicit_artifacts_survive_saturated_dependency_context() -> None:
    merged = DelegationContext.model_validate(
        merge_delegation_context(
            {
                "summary": "Inherited context.",
                "artifacts": [f"artifact://dependency-{index}" for index in range(8)],
            },
            {
                "summary": "Use the caller-selected artifact.",
                "artifacts": ["artifact://explicit"],
            },
        )
    )

    assert merged.artifacts[0] == "artifact://explicit"
    assert len(merged.artifacts) == 8


def test_completion_ratio_monitor_detects_stall() -> None:
    monitor = CompletionRatioMonitor()
    results = [
        SubtaskResult(
            subtask_id="subtask-1",
            goal="first",
            status="failed",
            mode_used="act",
            error="x",
        ),
        SubtaskResult(
            subtask_id="subtask-2",
            goal="second",
            status="failed",
            mode_used="act",
            error="y",
        ),
    ]
    assert monitor.is_stalled(results=results, attempts=2) is True


def test_abort_on_new_message_policy_uses_option_flag() -> None:
    policy = AbortOnNewMessagePolicy()
    ctx = SimpleNamespace(
        options=SimpleNamespace(decompose_cancel_requested=True),
        _services=SimpleNamespace(runner=None),
        state=SimpleNamespace(session_id="s-1", trace_id="t-1"),
    )
    assert policy.should_cancel(ctx=ctx, results=[], attempts=1) is True
