from __future__ import annotations

from unittest.mock import MagicMock

from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    BudgetCounters,
    LLMProfiles,
    WorkingState,
)


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=5,
        max_tool_calls=3,
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


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-parse",
        agent_id="test-agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=3,
            a2a_calls=1,
            tokens=1000,
            time_ms=10000,
        ),
        trace_id="t-parse",
    )


def test_parse_tool_command_with_json_args() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    command = runner._parse_tool_command(
        state=_state(),
        text='tool echo {"msg":"hi"}',
    )
    assert command is not None
    assert command.kind == "tool"
    assert command.tool_name == "echo"
    assert command.args.get("msg") == "hi"


def test_parse_tool_command_with_raw_payload() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    command = runner._parse_tool_command(
        state=_state(),
        text="tool echo not-json",
    )
    assert command is not None
    assert command.args.get("raw") == "not-json"


def test_parse_agent_command_with_json_params() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    command = runner._parse_agent_command(
        state=_state(),
        text='agent helper summarize {"topic":"x"}',
    )
    assert command is not None
    assert command.kind == "agent"
    assert command.target_agent_id == "helper"
    assert command.method == "summarize"
    assert command.params.get("topic") == "x"


def test_parse_tool_command_non_tool_returns_none() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    command = runner._parse_tool_command(state=_state(), text="hello world")
    assert command is None


def test_normalize_command_payload_title_only_missing_kind_is_invalid() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    normalized = runner._normalize_command_payload({"title": "Tool call: weather"})
    assert normalized["kind"] == "invalid"
    assert normalized["error"]["code"] == "MISSING_COMMAND_KIND"


def test_normalize_command_payload_title_only_runtime_candidate_is_invalid() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    normalized = runner._normalize_command_payload(
        {"title": "Tool call: weather.openmeteo.current"}
    )
    assert normalized["kind"] == "invalid"
    assert normalized["error"]["code"] == "MISSING_COMMAND_KIND"


def test_normalize_command_payload_missing_tool_name_marks_invalid() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    normalized = runner._normalize_command_payload({"kind": "tool"})
    assert normalized["kind"] == "invalid"
    assert normalized["error"]["code"] == "MISSING_TOOL_NAME"


def test_normalize_command_payload_uses_inputs_alias_for_tool_args() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    normalized = runner._normalize_command_payload(
        {
            "kind": "tool",
            "tool_name": "location",
            "inputs": {"max_privacy": "city"},
        }
    )
    assert normalized["kind"] == "tool"
    assert normalized["tool_name"] == "location"
    assert normalized["args"] == {"max_privacy": "city"}


def test_normalize_command_payload_rewrites_system_agent_tool_method_to_tool() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    normalized = runner._normalize_command_payload(
        {
            "kind": "agent",
            "title": "Cancel scheduled task",
            "target_agent_id": "system",
            "method": "task.cancel",
            "params": {"task_id": "job-123"},
        }
    )
    assert normalized["kind"] == "tool"
    assert normalized["tool_name"] == "task.cancel"
    assert normalized["args"] == {"task_id": "job-123"}
    assert "target_agent_id" not in normalized
    assert "method" not in normalized


def test_normalize_command_payload_keeps_non_system_agent_command() -> None:
    runner = BrainRunner(profile=_profile(), session_api=MagicMock())
    normalized = runner._normalize_command_payload(
        {
            "kind": "agent",
            "title": "Ask helper",
            "target_agent_id": "helper-agent",
            "method": "summarize",
            "params": {"topic": "x"},
        }
    )
    assert normalized["kind"] == "agent"
    assert normalized["target_agent_id"] == "helper-agent"
    assert normalized["method"] == "summarize"
    assert normalized["params"] == {"topic": "x"}
