from __future__ import annotations

from unittest.mock import MagicMock


from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.execution.dispatch import (
    prepare_decision_direct,
    validate_decision_direct,
)
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    BudgetCounters,
    ToolCommand,
    WorkingState,
)
from tests.brain.runner_test_support import _profile, build_seeded_act_decision


def _runner(tmp_path):
    session = LocalSessionStore(tmp_path / "sessions")
    return BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-validation-characterization",
        agent_id="router-agent",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=5,
            tokens=5000,
            time_ms=120000,
        ),
    )


def test_tick_validation_characterization_seeded_act_bypasses_plan_style_validation(
    tmp_path,
) -> None:
    runner = _runner(tmp_path)
    state = _state()
    state.decision_success_criteria = {"url": "required"}
    decision = build_seeded_act_decision(
        confidence=0.9,
        reason_code="lookup",
        act_profile="general",
        execution_target={"kind": "local"},
        sub_intents=[],
        command=ToolCommand(
            title="Get weather",
            tool_name="weather",
            args={"location": "Tokyo"},
            success_criteria={"status": "success"},
        ),
    )
    preparation = prepare_decision_direct(
        runner,
        state=state,
        decision=decision,
        user_input="weather in Tokyo",
        logger=MagicMock(),
    )

    result = validate_decision_direct(
        runner,
        state=state,
        decision=decision,
        user_input="weather in Tokyo",
        logger=MagicMock(),
        preparation=preparation,
    )

    assert result is None
