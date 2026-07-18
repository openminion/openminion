from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from openminion.modules.brain.execution import available_tool_names
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
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from pydantic import BaseModel


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=5,
        max_tool_calls=5,
        max_a2a_calls=1,
        max_total_llm_tokens=2000,
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


class _WebSearchArgs(BaseModel):
    query: str


class _TimeArgs(BaseModel):
    pass


def _web_search_spec() -> ToolSpec:
    def _handler(args, ctx):
        return {"ok": True, "results": [{"title": "news", "url": "http://example.com"}]}

    return ToolSpec(
        name="web.search",
        args_model=_WebSearchArgs,
        min_scope="READ_ONLY",
        handler=_handler,
    )


def _time_spec() -> ToolSpec:
    def _handler(args, ctx):
        return {"ok": True, "utc": "2025-01-01T00:00:00Z"}

    return ToolSpec(
        name="time",
        args_model=_TimeArgs,
        min_scope="READ_ONLY",
        handler=_handler,
    )


def _runner_with_tools(tmp_path: Path, specs: list[ToolSpec]) -> BrainRunner:
    session = LocalSessionStore(tmp_path / "sessions")
    registry = ToolRegistry([])
    for spec in specs:
        registry.add(spec)
    tool_adapter = LocalToolAdapter()
    tool_adapter.registry = registry
    return BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LocalLLMAdapter(),
        tool_api=tool_adapter,
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )




def test_available_tool_names_returns_registered_tools(tmp_path: Path) -> None:
    runner = _runner_with_tools(tmp_path, [_web_search_spec(), _time_spec()])
    names = available_tool_names(runner)
    assert "web.search" in names
    assert "time" in names


def test_available_tool_names_single_tool_visible(tmp_path: Path) -> None:
    runner = _runner_with_tools(tmp_path, [_web_search_spec()])
    names = available_tool_names(runner)
    assert "web.search" in names
    assert "time" not in names


def test_available_tool_names_consistent_across_calls(tmp_path: Path) -> None:
    runner = _runner_with_tools(tmp_path, [_web_search_spec(), _time_spec()])
    first_call = available_tool_names(runner)
    second_call = available_tool_names(runner)
    assert first_call == second_call


def test_available_tool_names_with_list_tools_method(tmp_path: Path) -> None:
    runner = _runner_with_tools(tmp_path, [])
    runner.tool_api.list_tools = lambda: [  # type: ignore[assignment]
        {"name": "web.search"},
        {"name": "time"},
    ]
    names = available_tool_names(runner)
    assert "web.search" in names
    assert "time" in names




def test_forced_tool_visible_single_path_also_resolves_in_multi_lane_context(
    tmp_path: Path,
) -> None:
    runner = _runner_with_tools(tmp_path, [_web_search_spec(), _time_spec()])
    with patch.object(
        runner,
        "_collect_runtime_tool_schemas",
        return_value=[{"name": "web.search"}, {"name": "time"}],
    ):
        tool_name, status = runner._resolve_forced_tool_name(
            forced_tools=["web.search"],
            capability_category=None,
        )
    assert status is None, f"Expected no error but got: {status}"
    assert tool_name == "web.search"


def test_both_tools_resolve_separately_simulating_multi_lane(tmp_path: Path) -> None:
    runner = _runner_with_tools(tmp_path, [_web_search_spec(), _time_spec()])
    with patch.object(
        runner,
        "_collect_runtime_tool_schemas",
        return_value=[{"name": "web.search"}, {"name": "time"}],
    ):
        search_name, search_status = runner._resolve_forced_tool_name(
            forced_tools=["web.search"],
            capability_category=None,
        )
        time_name, time_status = runner._resolve_forced_tool_name(
            forced_tools=["time"],
            capability_category=None,
        )
    assert search_status is None, f"web.search should resolve, got: {search_status}"
    assert time_status is None, f"time should resolve, got: {time_status}"
    assert search_name == "web.search"
    assert time_name == "time"


def test_tool_scope_consistent_regardless_of_single_or_multi_context(
    tmp_path: Path,
) -> None:
    runner_single = _runner_with_tools(tmp_path / "single", [_web_search_spec()])
    with patch.object(
        runner_single,
        "_collect_runtime_tool_schemas",
        return_value=[{"name": "web.search"}],
    ):
        single_name, single_status = runner_single._resolve_forced_tool_name(
            forced_tools=["web.search"],
            capability_category=None,
        )

    runner_multi = _runner_with_tools(
        tmp_path / "multi", [_web_search_spec(), _time_spec()]
    )
    with patch.object(
        runner_multi,
        "_collect_runtime_tool_schemas",
        return_value=[{"name": "web.search"}, {"name": "time"}],
    ):
        multi_name, multi_status = runner_multi._resolve_forced_tool_name(
            forced_tools=["web.search"],
            capability_category=None,
        )

    assert single_status == multi_status, (
        f"Tool scope inconsistency: single-tool status={single_status!r} "
        f"vs multi-tool status={multi_status!r}"
    )
    assert single_name == multi_name, (
        f"Tool scope inconsistency: single-tool name={single_name!r} "
        f"vs multi-tool name={multi_name!r}"
    )




def test_truly_unavailable_tool_fails_clearly_in_any_context(
    tmp_path: Path,
) -> None:
    runner = _runner_with_tools(tmp_path, [_web_search_spec(), _time_spec()])
    with patch.object(
        runner,
        "_collect_runtime_tool_schemas",
        return_value=[{"name": "web.search"}, {"name": "time"}],
    ):
        tool_name, status = runner._resolve_forced_tool_name(
            forced_tools=["nonexistent.tool.xyz"],
            capability_category=None,
        )
    assert status == "forced_tool_unavailable", (
        f"Expected 'forced_tool_unavailable' but got: {status!r}"
    )
    assert tool_name is None


def test_truly_unavailable_capability_fails_clearly(tmp_path: Path) -> None:
    runner = _runner_with_tools(tmp_path, [_web_search_spec()])
    with patch.object(
        runner,
        "_collect_runtime_tool_schemas",
        return_value=[{"name": "web.search"}],
    ):
        tool_name, status = runner._resolve_forced_tool_name(
            forced_tools=None,
            capability_category="weather",
        )
    assert status == "capability_tool_unavailable", (
        f"Expected 'capability_tool_unavailable' but got: {status!r}"
    )
    assert tool_name is None


def test_empty_registry_returns_all_unavailable(tmp_path: Path) -> None:
    runner = _runner_with_tools(tmp_path, [])
    with patch.object(
        runner,
        "_collect_runtime_tool_schemas",
        return_value=[],
    ):
        tool_name, status = runner._resolve_forced_tool_name(
            forced_tools=["web.search"],
            capability_category=None,
        )
    assert status == "forced_tool_unavailable"
    assert tool_name is None
