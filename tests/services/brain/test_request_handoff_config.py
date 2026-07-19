from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.modules.brain.cli import _build_runner
from openminion.modules.brain.config import BrainConfig, RuntimeConfig
from openminion.modules.brain.schemas import AgentBudgets, LLMProfiles
from openminion.services.brain.metadata import resolve_runner_options


def _budgets() -> AgentBudgets:
    return AgentBudgets(
        max_ticks_per_user_turn=4,
        max_tool_calls=2,
        max_a2a_calls=0,
        max_total_llm_tokens=2000,
        max_elapsed_ms=10000,
    )


def _brain_config(*, enabled: bool) -> BrainConfig:
    return BrainConfig(
        agent_id="agent",
        role="general",
        thinking="minimal",
        llm_profiles=LLMProfiles(
            decide_model="decide",
            plan_model="plan",
            act_model=None,
            reflect_model="reflect",
            summarize_model="summarize",
        ),
        budgets=_budgets(),
        request_handoff={"enabled": enabled},
    )


def test_service_runner_options_map_request_handoff_enabled() -> None:
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            brain=SimpleNamespace(tool_schema_shortlisting_enabled=None),
            complex_request_plan_policy="balanced",
            session_context_token_budget=100000,
        )
    )

    options = resolve_runner_options(
        config,
        brain_config=_brain_config(enabled=True),
        override_value=lambda _name: "",
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )

    assert options.request_handoff_enabled is True


def test_standalone_runner_options_map_request_handoff_enabled(tmp_path: Path) -> None:
    runner, _session = _build_runner(
        config=RuntimeConfig(brain=_brain_config(enabled=True)),
        root=tmp_path,
    )

    assert runner.options.request_handoff_enabled is True

