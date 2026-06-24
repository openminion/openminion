from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.bootstrap.resolve import build_internal_dispatch
from openminion.modules.brain.execution.loop_contracts import ExecutionResult
from openminion.modules.brain.loop.strategies.research import ResearchMode
from openminion.modules.brain.loop.adaptive import ActLoopMode
from openminion.modules.brain.schemas import (
    ActDecision,
    BudgetCounters,
    ExecutionTargetPayload,
    WorkingState,
)


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-act-profile",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )


def _local_target() -> ExecutionTargetPayload:
    return ExecutionTargetPayload(kind="local")


def test_act_mode_routes_coding_profile_to_internal_coding_handler() -> None:
    decision = ActDecision(
        confidence=0.9,
        reason_code="coding",
        act_profile="coding",
        execution_target=_local_target(),
    )
    ctx = SimpleNamespace(
        state=_state(), decision=decision, user_input="fix the failing tests"
    )

    dispatch = build_internal_dispatch(ctx)
    handler, internal = dispatch.handler, dispatch.decision

    assert isinstance(handler, ActLoopMode)
    assert getattr(internal, "act_profile") == "coding"
    assert getattr(internal, "objective") == "fix the failing tests"


def test_coding_profile_executes_through_adaptive_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = ActDecision(
        confidence=0.9,
        reason_code="coding",
        act_profile="coding",
        execution_target=_local_target(),
    )
    ctx = SimpleNamespace(
        state=_state(), decision=decision, user_input="fix the failing tests"
    )
    dispatch = build_internal_dispatch(ctx)
    handler, internal = dispatch.handler, dispatch.decision
    captured: dict[str, object] = {}

    def _fake_execute(inner_ctx):
        captured["decision"] = inner_ctx.decision
        return ExecutionResult(
            status="done",
            working_state=inner_ctx.state,
            message="coding delegated through adaptive",
        )

    monkeypatch.setattr(
        "openminion.modules.brain.loop.adaptive.execute_coding_profile",
        _fake_execute,
    )

    result = handler.execute(
        SimpleNamespace(
            state=ctx.state,
            decision=internal,
            user_input=ctx.user_input,
            llm_adapter=None,
            command_executor=None,
        )
    )

    assert isinstance(handler, ActLoopMode)
    assert result.message == "coding delegated through adaptive"
    assert getattr(captured["decision"], "act_profile") == "coding"


def test_act_mode_routes_research_profile_to_internal_research_handler() -> None:
    decision = ActDecision(
        confidence=0.9,
        reason_code="research",
        act_profile="research",
        execution_target=_local_target(),
    )
    ctx = SimpleNamespace(
        state=_state(), decision=decision, user_input="research latest Seattle events"
    )

    dispatch = build_internal_dispatch(ctx)
    handler, internal = dispatch.handler, dispatch.decision

    assert isinstance(handler, ResearchMode)
    assert not hasattr(internal, "mode")
    assert getattr(internal, "research_query") == "research latest Seattle events"
