from __future__ import annotations

import asyncio
import json
import logging

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.tool.base import (
    Tool,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolExecutionResult,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.services.agent import AgentService
from openminion.services.runtime.plugins import PluginRegistry
from openminion.services.security.policy import SecurityPolicyEngine, ToolBudgetPolicy
from tests.services.agent._agent_service_support import (
    FakeChangingToolCallProvider,
    FakeToolCallProvider,
)
from tests._csc_fixtures import _csc_install_default_agent


def _run(coro):
    return asyncio.run(coro)


class _RequiredWeatherTool(Tool):
    name = "weather.openmeteo.current"
    description = "Lookup weather by location"
    parameters = {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        location = str(arguments.get("location", "") or "").strip() or "unknown"
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            verified=True,
            content=f"{location} weather ok",
            data={"location": location},
        )


class _BudgetWeatherTool(Tool):
    name = "weather.openmeteo.current"
    description = "Lookup weather by city"
    policy = ToolExecutionPolicy(
        required_scopes_all=("tool.execute", "tool.weather.read"),
        risk="medium",
        budget_cost=2,
    )
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        city = str(arguments.get("city", "") or "").strip() or "unknown"
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            verified=True,
            content=f"{city} weather ok",
            data={"city": city},
        )


class _NoToolProvider(LLMProvider):
    name = "no-tool-provider"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(text="", model="test-model", finish_reason="stop")


class _MissingRequiredArgsProvider(LLMProvider):
    name = "missing-required-args"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        self.calls += 1
        return ProviderResponse(
            text="",
            model="test-model",
            tool_calls=[
                ProviderToolCall(
                    name="weather.openmeteo.current",
                    arguments={},
                    source="fallback",
                )
            ],
            finish_reason="tool_calls",
        )


def test_executor_characterization_duplicate_tool_calls_metadata() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.agent_loop_max_steps = 4
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=FakeToolCallProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_BudgetWeatherTool()]),
    )

    response = _run(
        service.run_turn(Message(channel="console", target="me", body="weather"))
    )

    assert response.metadata["tool_loop_termination_reason"] == "duplicate_tool_calls"
    assert response.metadata["tool_calls_count"] == "1"
    assert response.metadata["tool_execution_count"] == "1"
    assert "weather.openmeteo.current" in response.metadata["tool_results"]


def test_executor_characterization_budget_denial_metadata() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.agent_loop_max_steps = 4
    policy = SecurityPolicyEngine(
        tool_budget_policy=ToolBudgetPolicy(
            max_calls_per_run=4,
            max_calls_per_tool=4,
            max_budget_cost_per_run=3,
        )
    )
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=FakeChangingToolCallProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_BudgetWeatherTool()]),
        security_policy=policy,
    )

    response = _run(
        service.run_turn(Message(channel="console", target="me", body="weather both"))
    )

    assert response.metadata["tool_loop_termination_reason"] == "tool_no_success"
    assert "security_events" in response.metadata
    assert "tool_budget_cost_exceeded" in response.metadata["security_events"]
    assert "tool_budget" in response.metadata
    budget = json.loads(response.metadata["tool_budget"])
    assert budget["tool_calls_total"] >= 1


def test_executor_characterization_required_tool_missing_metadata() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.allow_runtime_direct_fallback = False
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_RequiredWeatherTool()]),
    )

    response = _run(
        service.run_turn(
            Message(channel="console", target="cli", body="what's weather in sf?"),
            forced_tools=["weather.openmeteo.current"],
        )
    )

    assert response.text == "Required tool call missing"
    assert (
        response.metadata["tool_loop_termination_reason"]
        == "required_tool_call_missing"
    )
    assert response.metadata["tool_execution_count"] == "0"


def test_executor_characterization_invalid_argument_exhaustion_metadata() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.allow_runtime_direct_fallback = False
    provider = _MissingRequiredArgsProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_RequiredWeatherTool()]),
    )

    response = _run(
        service.run_turn(
            Message(channel="console", target="cli", body="what's weather in sf?"),
            forced_tools=["weather.openmeteo.current"],
        )
    )

    assert provider.calls == 2
    assert response.text == "Invalid tool arguments"
    assert response.metadata["tool_loop_termination_reason"] == "tool_arg_exhausted"
    assert response.metadata["tool_arg_exhausted"] == "weather.openmeteo.current"
    assert "location" in response.metadata["tool_arg_exhausted_missing"]
