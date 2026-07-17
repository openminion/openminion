from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    LLMProfiles,
)


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=3,
        max_tool_calls=2,
        max_a2a_calls=1,
        max_total_llm_tokens=1000,
        max_elapsed_ms=10000,
    )
    llm_profiles = LLMProfiles(
        decide_model="decide-default",
        plan_model="plan-default",
        act_model=None,
        reflect_model="reflect-default",
        summarize_model="summarize-default",
    )
    return AgentProfile(
        agent_id="test-agent",
        role="general",
        llm_profiles=llm_profiles,
        budgets=budgets,
        defaults=AgentDefaults(),
    )


def _build_runner(tmp_path: Path) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LocalLLMAdapter(),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def _index_of(types: list[str], name: str) -> int:
    return types.index(name)


def test_golden_no_tool_turn_event_order(tmp_path: Path) -> None:
    runner, session = _build_runner(tmp_path)
    output = runner.step(session_id="s-golden", user_input="hello", trace_id="t1")

    turns = session.list_turns("s-golden")
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert output.message == "I'm here. What can I help you with?"

    events = session.list_events("s-golden")
    types = [event["type"] for event in events]

    assert "brain.interpret" in types
    assert "llm.call.started" in types
    assert "llm.call.completed" in types
    assert "brain.entry" in types
    assert "summary.updated" in types

    completed = next(event for event in events if event["type"] == "llm.call.completed")
    assert completed["payload"]["provider"] == "local"

    assert _index_of(types, "brain.interpret") < _index_of(types, "llm.call.started")
    assert _index_of(types, "llm.call.started") < _index_of(types, "llm.call.completed")
    assert _index_of(types, "llm.call.completed") < _index_of(types, "brain.entry")
