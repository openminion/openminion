from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.bootstrap.recovery import (
    _recover_simple_tool_parity_decision,
)
from openminion.modules.brain.schemas import RespondDecision
from openminion.modules.llm.schemas import ToolCall
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.services.agent import AgentService


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _retired_symbol(*parts: str) -> str:
    return "".join(parts)


def _runner() -> BrainRunner:
    profile = SimpleNamespace(
        agent_id="guard-agent",
        llm_profiles=SimpleNamespace(decide_model="test-model"),
        defaults=SimpleNamespace(
            auto_save_lessons=False, auto_stage_policy_candidates=False
        ),
        budgets=SimpleNamespace(
            max_ticks_per_user_turn=10,
            max_tool_calls=5,
            max_a2a_calls=3,
            max_total_llm_tokens=1000,
            max_elapsed_ms=60000,
        ),
    )
    return BrainRunner(
        profile=profile,
        session_api=MagicMock(),
        tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
    )


def _state() -> WorkingState:
    return WorkingState(
        session_id="guard-session",
        agent_id="guard-agent",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=3,
            tokens=1000,
            time_ms=60000,
        ),
    )


def test_intent_router_module_removed_entirely() -> None:
    try:
        from openminion.services import intent_router  # noqa: F401

        assert False, "intent_router module should be removed"
    except ImportError:
        pass  # Expected - module removed


def test_orchestrator_capability_resolution_direct_passthrough() -> None:
    source = (
        _REPO_ROOT / "src/openminion/services/lifecycle/request_orchestrator.py"
    ).read_text(encoding="utf-8")
    assert "def _resolve_capability_category(" in source
    assert "return _resolve_capability_category_impl(" in source
    assert "explicit_category=explicit_category" in source


def test_runner_nl_tool_parser_surface_removed() -> None:
    runner = _runner()
    assert not hasattr(runner, "_parse_natural_language_tool")


def test_agent_weather_typo_extraction_no_longer_routes_args() -> None:
    assert not hasattr(AgentService, "_extract_weather_location")


def test_runtime_weather_clarification_prompt_present_in_executor() -> None:
    from openminion.services.agent.execution import executor as turn_executor

    source = inspect.getsource(turn_executor)
    assert "Which location should I check weather for?" not in source


def test_execution_controller_does_not_call_intent_classifier_for_conversation() -> (
    None
):
    from openminion.services.agent.execution import controller

    source = inspect.getsource(controller)
    assert "classify_intent(" not in source


def test_conversational_text_does_not_route_via_heuristic_tool_fallback() -> None:
    runner = _runner()
    decision = runner._decide(
        state=_state(),
        user_input="what's weather today in san francisco?",
        logger=MagicMock(),
    )
    assert decision.mode == "respond"
    assert decision.reason_code == "llm_unavailable"
    assert not list(getattr(decision, "_seeded_commands", []) or [])


def test_decide_phase_no_longer_contains_browser_keyword_classifier() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_REPO_ROOT / "src/openminion/modules/brain").rglob("*.py")
    )
    retired_debug_field = _retired_symbol("browser", "_intent", "_detected")
    retired_keyword_list = _retired_symbol("browser", "_keywords")
    assert retired_debug_field not in source
    assert retired_keyword_list not in source


def test_runtime_no_longer_exports_freshness_keyword_gate_symbols() -> None:
    decision_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_REPO_ROOT / "src/openminion/modules/brain").rglob("*.py")
    )
    constants_source = (
        _REPO_ROOT / "src/openminion/modules/brain/constants.py"
    ).read_text(encoding="utf-8")
    retired_reason_code = _retired_symbol(
        "freshness", "_required", "_no_tool", "_called"
    )
    retired_constant = _retired_symbol("BRAIN", "_FRESHNESS", "_KEYWORDS")
    assert retired_reason_code not in decision_source
    assert retired_constant not in constants_source


def test_explicit_commands_still_work_without_llm_context() -> None:
    runner = _runner()
    decision = runner._decide(
        state=_state(),
        user_input='tool weather {"location":"san francisco"}',
        logger=MagicMock(),
    )
    assert decision.mode == "act"
    assert decision.reason_code == "explicit_tool_command"
    seeded_commands = list(getattr(decision, "_seeded_commands", []) or [])
    assert len(seeded_commands) == 1
    assert seeded_commands[0].tool_name == "weather"


def test_structured_tool_call_response_can_seed_tool_decision_without_nl_parse() -> (
    None
):
    runner = _runner()
    state = _state()
    response = SimpleNamespace(
        tool_calls=[
            ToolCall(
                name="file.read",
                arguments={"path": "/tmp/demo.txt"},
            )
        ]
    )
    recovered = _recover_simple_tool_parity_decision(
        runner=runner,
        state=state,
        user_input="use file.read on /tmp/demo.txt and summarize it",
        capability_category=None,
        decision=RespondDecision(
            confidence=0.5,
            reason_code="entry_text_response",
            respond_kind="answer",
            answer="I cannot do that.",
        ),
        response=response,
        logger=MagicMock(),
        llm_call_id="llm-call-1",
    )
    assert recovered is not None
    assert recovered.mode == "act"
    seeded_commands = list(getattr(recovered, "_seeded_commands", []) or [])
    assert len(seeded_commands) == 1
    assert seeded_commands[0].tool_name == "file.read"
    assert seeded_commands[0].args == {"path": "/tmp/demo.txt"}


def test_explicit_backticked_tool_sequence_recovers_act_decision() -> None:
    runner = _runner()
    state = _state()
    recovered = _recover_simple_tool_parity_decision(
        runner=runner,
        state=state,
        user_input=(
            "Use exactly one `web.search`, then `web.fetch`, then `web.fetch` "
            "and finish in the same turn."
        ),
        capability_category=None,
        decision=RespondDecision(
            confidence=0.5,
            reason_code="entry_text_response",
            respond_kind="answer",
            answer="Here is the comparison.",
        ),
        response=SimpleNamespace(tool_calls=[]),
        logger=MagicMock(),
        llm_call_id="llm-call-2",
    )
    assert recovered is not None
    assert recovered.mode == "act"
    assert recovered.reason_code == "explicit_tool_sequence"
    assert not list(getattr(recovered, "_seeded_commands", []) or [])


def test_explicit_plain_tool_sequence_recovers_act_decision() -> None:
    runner = _runner()
    state = _state()
    recovered = _recover_simple_tool_parity_decision(
        runner=runner,
        state=state,
        user_input=(
            "Use exactly one web.search, then web.fetch, then web.fetch "
            "with official URLs."
        ),
        capability_category=None,
        decision=RespondDecision(
            confidence=0.5,
            reason_code="entry_text_response",
            respond_kind="answer",
            answer="Here is the comparison.",
        ),
        response=SimpleNamespace(tool_calls=[]),
        logger=MagicMock(),
        llm_call_id="llm-call-3",
    )
    assert recovered is not None
    assert recovered.mode == "act"
    assert recovered.reason_code == "explicit_tool_sequence"
