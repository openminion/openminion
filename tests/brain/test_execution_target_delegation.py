from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.execution.targets import (
    build_delegated_decision,
    is_delegated_target,
)
from openminion.modules.brain.bootstrap.resolve import build_internal_dispatch
from openminion.modules.brain.execution.targets.delegated.handler import DelegateMode
from openminion.modules.brain.schemas import (
    ActDecision,
    AgentCommand,
    BudgetCounters,
    ExecutionTargetPayload,
    WorkingState,
)


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-delegate",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )


def test_execution_target_delegated_builds_internal_delegate_dispatch() -> None:
    target = ExecutionTargetPayload(
        kind="delegated",
        target_agent_id="coding-specialist",
        target_capability="coding",
        expect_async=True,
    )
    decision = ActDecision(
        confidence=0.9,
        reason_code="delegate_work",
        act_profile="general",
        execution_target=target,
    )
    ctx = SimpleNamespace(state=_state(), decision=decision, user_input="fix the build")

    assert is_delegated_target(target) is True
    dispatch = build_internal_dispatch(ctx)
    handler, internal = dispatch.handler, dispatch.decision

    assert isinstance(handler, DelegateMode)
    assert not hasattr(internal, "mode")
    assert getattr(internal, "target_agent_id") == "coding-specialist"
    assert getattr(internal, "goal") == "fix the build"


def test_build_delegated_decision_uses_structured_execution_target() -> None:
    target = ExecutionTargetPayload(
        kind="delegated",
        target_agent_id="research-specialist",
        target_capability="research",
        expect_async=False,
    )
    decision = SimpleNamespace(
        confidence=0.8,
        reason_code="handoff",
        sub_intents=["research"],
        rationale="",
        execution_target=target,
    )

    internal = build_delegated_decision(decision=decision, goal="research events")

    assert not hasattr(internal, "mode")
    assert getattr(internal, "target_capability") == "research"


def test_build_delegated_decision_preserves_raw_goal_without_structured_delegate_goal() -> (
    None
):
    target = ExecutionTargetPayload(
        kind="delegated",
        target_agent_id="alibaba-kimi-k2-5",
        expect_async=False,
    )
    decision = SimpleNamespace(
        confidence=0.8,
        reason_code="handoff",
        sub_intents=["time"],
        rationale="The user explicitly requested delegation.",
        execution_target=target,
    )

    internal = build_delegated_decision(
        decision=decision,
        goal="delegate to alibaba-kimi-k2-5 and tell me the current UTC time",
    )

    assert (
        getattr(internal, "goal")
        == "delegate to alibaba-kimi-k2-5 and tell me the current UTC time"
    )


def test_build_delegated_decision_falls_back_to_agent_command_goal() -> None:
    target = ExecutionTargetPayload(
        kind="delegated",
        target_agent_id="alibaba-kimi-k2-5",
        expect_async=False,
    )
    decision = SimpleNamespace(
        confidence=0.8,
        reason_code="handoff",
        sub_intents=["time"],
        rationale="The user explicitly requested delegation.",
        execution_target=target,
    )
    decision._seeded_commands = [
        AgentCommand(
            title="delegate time lookup",
            target_agent_id="alibaba-kimi-k2-5",
            method="act",
            params={"action": "get_utc_time"},
        )
    ]

    internal = build_delegated_decision(
        decision=decision,
        goal="delegate this to alibaba-kimi-k2-5",
    )

    assert getattr(internal, "goal") == "delegate this to alibaba-kimi-k2-5"
