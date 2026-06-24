from unittest.mock import Mock

import pytest

from openminion.modules.brain.runner import BrainRunner, RunnerOptions
from openminion.modules.brain.schemas import RunSubstate
from openminion.modules.brain.config import ClarifyConfig
from openminion.modules.brain.interfaces import (
    SessionAPI,
)


def _build_runner() -> BrainRunner:
    from openminion.modules.brain.schemas import AgentDefaults

    session_api = Mock(spec=SessionAPI)
    profile = Mock()
    profile.agent_id = "test-agent"
    profile.budgets.max_ticks_per_user_turn = 10
    profile.budgets.max_tool_calls = 5
    profile.budgets.max_a2a_calls = 5
    profile.budgets.max_total_llm_tokens = 1000
    profile.budgets.max_elapsed_ms = 10000
    profile.defaults = AgentDefaults(
        risk_tolerance="low",
        auto_save_lessons=True,
        auto_stage_policy_candidates=True,
    )

    options = RunnerOptions()
    options.clarify_config = ClarifyConfig()
    return BrainRunner(profile=profile, session_api=session_api, options=options)


def test_clarify_config_integration() -> None:
    runner = _build_runner()
    assert runner.options.clarify_config is not None
    assert runner.options.clarify_config.default_mode == "guided"
    assert runner.options.clarify_config.default_policy == "ask_if_ambiguous"
    assert runner.options.clarify_config.max_questions_per_turn == 5


def test_run_substate_includes_clarify() -> None:
    values = getattr(RunSubstate, "__args__", ())
    assert "CLARIFY" in (values or str(RunSubstate))


@pytest.mark.parametrize(
    ("enum_name", "values"),
    [
        ("BrainMode", ("command", "guided", "autonomous", "batch")),
        (
            "ClarifyPolicy",
            (
                "always_ask",
                "ask_if_ambiguous",
                "ask_if_risky",
                "assume_defaults",
                "smart_assume",
            ),
        ),
    ],
)
def test_brain_mode_and_clarify_policy_types_available(
    enum_name: str, values: tuple[str, ...]
) -> None:
    from openminion.modules.brain.schemas import BrainMode
    from openminion.modules.brain.schemas import ClarifyPolicy

    enums = {"BrainMode": BrainMode, "ClarifyPolicy": ClarifyPolicy}
    for value in values:
        assert enums[enum_name](value).value == value
