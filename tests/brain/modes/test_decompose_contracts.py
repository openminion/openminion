from __future__ import annotations

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
)
from openminion.modules.brain.schemas import BudgetCounters, WorkingState


def test_decompose_contract_types_are_runtime_checkable() -> None:
    assert isinstance(SequentialStrategy(), ExecutionStrategy)
    assert isinstance(EqualSplitAllocator(), BudgetAllocator)
    assert isinstance(AcceptOrPlanResolver(), SubtaskModeResolver)
    assert isinstance(LLMSynthesizer(), ResultSynthesizer)
    assert isinstance(FailFastPolicy(), FailurePolicy)
    assert isinstance(SummaryInheritancePolicy(), ContextInheritancePolicy)
    assert isinstance(CompletionRatioMonitor(), ProgressMonitor)
    assert isinstance(AbortOnNewMessagePolicy(), CancellationPolicy)


def test_decompose_payload_rejects_fewer_than_two_subtasks() -> None:
    with pytest.raises(ValidationError):
        DecomposePayload(subtasks=[SubtaskSpec(goal="only one")])


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
