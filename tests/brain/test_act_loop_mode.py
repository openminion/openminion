from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.brain.bootstrap.resolve import (
    build_internal_decision,
    build_internal_dispatch,
)
from openminion.modules.brain.config import ModeProfileConfig, ReflectionConfig
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


@pytest.mark.parametrize("policy", ["never", "always", "anomaly"])
def test_legacy_internal_reflection_policy_remains_mode_local(policy: str) -> None:
    mode = ActLoopMode()

    mode.apply_mode_config(
        config={"adaptive_reflection_policy": policy},
        runner=SimpleNamespace(options=SimpleNamespace()),
        profile=None,
    )

    assert mode._reflection_policy == policy


def test_public_mode_profile_does_not_invent_reflection_policy() -> None:
    assert "adaptive_reflection_policy" not in ModeProfileConfig().model_dump()
    with pytest.raises(ValidationError):
        ModeProfileConfig.model_validate({"adaptive_reflection_policy": "always"})


def test_brain_reflection_defaults_remain_unchanged() -> None:
    config = ReflectionConfig()

    assert config.enabled is True
    assert config.reserved_llm_calls == 1
