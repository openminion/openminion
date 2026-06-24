from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.brain.bootstrap.resolve import (
    build_internal_decision,
    build_internal_dispatch,
)
from openminion.modules.brain.loop.adaptive import ActLoopMode
from openminion.modules.brain.schemas import (
    ActDecision,
    BudgetCounters,
    ExecutionTargetPayload,
    ToolCommand,
    WorkingState,
)


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-act",
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


def test_act_mode_routes_general_profile_to_shared_loop_handler() -> None:
    decision = ActDecision(
        confidence=0.9,
        reason_code="single_tool",
        act_profile="general",
        execution_target=_local_target(),
    )
    decision._seeded_commands = [
        ToolCommand(
            title="get time",
            tool_name="time.get",
            args={},
            success_criteria={"status": "success"},
        )
    ]
    ctx = SimpleNamespace(
        state=_state(), decision=decision, user_input="what time is it?"
    )

    dispatch = build_internal_dispatch(ctx)
    handler, internal = dispatch.handler, dispatch.decision

    assert isinstance(handler, ActLoopMode)
    assert not hasattr(internal, "mode")
    assert len(getattr(internal, "_seeded_commands")) == 1
    assert getattr(internal, "_seeded_commands")[0].tool_name == "time.get"


def test_act_mode_routes_general_profile_without_seed_commands() -> None:
    decision = ActDecision(
        confidence=0.9,
        reason_code="adaptive_loop",
        act_profile="general",
        execution_target=_local_target(),
    )
    ctx = SimpleNamespace(
        state=_state(), decision=decision, user_input="check time and weather"
    )

    dispatch = build_internal_dispatch(ctx)
    handler, internal = dispatch.handler, dispatch.decision

    assert isinstance(handler, ActLoopMode)
    assert not hasattr(internal, "mode")
    assert getattr(internal, "act_profile") == "general"
    assert not hasattr(internal, "_seeded_commands")


def test_act_mode_rejects_unsupported_profile() -> None:
    decision = SimpleNamespace(
        confidence=0.9,
        reason_code="unknown_profile",
        act_profile="unsupported",
        execution_target=_local_target(),
    )
    ctx = SimpleNamespace(state=_state(), decision=decision, user_input="do work")

    with pytest.raises(ValueError, match="act_profile"):
        build_internal_decision(ctx)


def test_act_mode_routes_general_profile_to_internal_loop_handler() -> None:
    decision = ActDecision(
        confidence=0.9,
        reason_code="adaptive",
        act_profile="general",
        execution_target=_local_target(),
    )
    ctx = SimpleNamespace(
        state=_state(), decision=decision, user_input="inspect then decide"
    )

    dispatch = build_internal_dispatch(ctx)
    handler, internal = dispatch.handler, dispatch.decision

    assert isinstance(handler, ActLoopMode)
    assert not hasattr(internal, "mode")
    assert getattr(internal, "act_profile") == "general"
