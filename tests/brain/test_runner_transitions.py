from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
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


def _build_runner(tmp_path: Path) -> BrainRunner:
    session = LocalSessionStore(tmp_path / "sessions")
    return BrainRunner(
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


def test_decide_transitions_from_input_variants(tmp_path) -> None:
    runner = _build_runner(tmp_path)
    state = runner._load_or_init_state("s-trans")
    logger = MagicMock()

    decision = runner._decide(state=state, user_input="hello", logger=logger)
    assert decision.mode == "respond"

    decision = runner._decide(state=state, user_input="plan a task", logger=logger)
    assert decision.mode == "respond"

    decision = runner._decide(
        state=state, user_input='tool echo {"msg":"hi"}', logger=logger
    )
    assert decision.mode in {"act", "respond"}
