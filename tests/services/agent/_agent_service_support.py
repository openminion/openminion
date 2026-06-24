import asyncio
import json
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.services.runtime.plugins import Plugin, PluginContext
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.services.agent import AgentService
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from openminion.services.security.policy import (
    SecurityPolicyEngine,
    SecurityPolicyRule,
    ToolBudgetPolicy,
)
from openminion.modules.tool.base import (
    Tool,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolExecutionResult,
)
from openminion.modules.tool.registry import ToolRegistry

_WEATHER_RUNTIME_TOOL = "weather.openmeteo.current"


class UppercaseInboundPlugin(Plugin):
    name = "upper"

    def on_message(self, message: Message, context: PluginContext) -> Message:
        return replace(message, body=message.body.upper())


class FakeProvider(LLMProvider):
    name = "fake"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse(
            text=f"reply:{request.user_message}", model="fake-model"
        )


class FakeToolCallProvider(LLMProvider):
    name = "fake-tools"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name=_WEATHER_RUNTIME_TOOL,
                    arguments={"city": "Tokyo"},
                    source="fallback",
                )
            ],
            finish_reason="tool_calls",
        )


class FakeTextToolCallProvider(LLMProvider):
    name = "fake-text-tools"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(
            text=(
                '{"tool_calls":[{"name":"weather.openmeteo.current",'
                '"arguments":{"city":"Tokyo"}}]}'
            ),
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name=_WEATHER_RUNTIME_TOOL,
                    arguments={"city": "Tokyo"},
                    source="fallback",
                )
            ],
            finish_reason="tool_calls",
        )


class FakeTextOnlyToolCallProvider(LLMProvider):
    name = "fake-text-only-tools"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(
            text='{"name":"weather.openmeteo.current","arguments":{"city":"Tokyo"}}',
            model="fake-model",
            tool_calls=[],
            finish_reason="stop",
        )


class FakeDoubleToolCallProvider(LLMProvider):
    name = "fake-double-tools"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name=_WEATHER_RUNTIME_TOOL,
                    arguments={"city": "San Francisco"},
                    source="fallback",
                ),
                ProviderToolCall(
                    name=_WEATHER_RUNTIME_TOOL,
                    arguments={"city": "Tokyo"},
                    source="fallback",
                ),
            ],
            finish_reason="tool_calls",
        )


class FakeTwoStepToolThenFinalProvider(LLMProvider):
    name = "fake-two-step"

    def __init__(self) -> None:
        self.call_count = 0
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        self.requests.append(request)
        if self.call_count == 1:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "Tokyo"},
                        source="fallback",
                    )
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text="Final answer after tool execution.",
            model="fake-model",
            finish_reason="stop",
        )


class FakeSubstantiveToolThenFinalizationProvider(LLMProvider):
    name = "fake-substantive-finalization"

    def __init__(self) -> None:
        self.call_count = 0
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        self.requests.append(request)
        if self.call_count == 1:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "Tokyo"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "Osaka"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "Kyoto"},
                        source="fallback",
                    ),
                ],
                finish_reason="tool_calls",
            )
        if self.call_count == 2:
            return ProviderResponse(
                text='Delivered the final comparison.\n<finalization_status>{"status":"final_answer","reasoning":"all requested sections delivered","remaining_work":"","blocking_reason":""}</finalization_status>',
                model="fake-model",
                finish_reason="stop",
            )
        return ProviderResponse(
            text="unexpected", model="fake-model", finish_reason="stop"
        )


class FakeSubstantiveToolMissingFinalizationProvider(LLMProvider):
    name = "fake-substantive-missing-finalization"

    def __init__(self) -> None:
        self.call_count = 0
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        self.requests.append(request)
        if self.call_count == 1:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "Tokyo"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "Osaka"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "Kyoto"},
                        source="fallback",
                    ),
                ],
                finish_reason="tool_calls",
            )
        if self.call_count == 2:
            return ProviderResponse(
                text="Delivered the final comparison.",
                model="fake-model",
                finish_reason="stop",
            )
        if self.call_count == 3:
            return ProviderResponse(
                text='Delivered the final comparison.\n<finalization_status>{"status":"final_answer","reasoning":"retry satisfied","remaining_work":"","blocking_reason":""}</finalization_status>',
                model="fake-model",
                finish_reason="stop",
            )
        return ProviderResponse(
            text="unexpected", model="fake-model", finish_reason="stop"
        )


class FakeChangingToolCallProvider(LLMProvider):
    name = "fake-changing-tools"

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        self.call_count += 1
        if self.call_count == 1:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name=_WEATHER_RUNTIME_TOOL,
                        arguments={"city": "San Francisco"},
                        source="fallback",
                    )
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name=_WEATHER_RUNTIME_TOOL,
                    arguments={"city": "Tokyo"},
                    source="fallback",
                )
            ],
            finish_reason="tool_calls",
        )


class _StubWeatherTool(Tool):
    name = "weather.openmeteo.current"
    description = "Lookup weather by city"
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        city = (
            str(
                arguments.get("city")
                or arguments.get("location")
                or arguments.get("query")
                or arguments.get("place")
                or ""
            ).strip()
            or "unknown"
        )
        city_display = (
            city
            if city == "unknown"
            else " ".join(part.capitalize() for part in city.split())
        )
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            verified=True,
            content=f"{city_display} weather now: 31C, humidity 74%, wind 9 km/h.",
            data={
                "city": city,
                "temperature_c": 31,
                "humidity_pct": 74,
                "wind_speed_kmh": 9,
            },
            source="test-stub",
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
        city = (
            str(
                arguments.get("city")
                or arguments.get("location")
                or arguments.get("query")
                or arguments.get("place")
                or ""
            ).strip()
            or "unknown"
        )
        city_display = (
            city
            if city == "unknown"
            else " ".join(part.capitalize() for part in city.split())
        )
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            verified=True,
            content=f"{city_display} weather now: 20C, humidity 60%, wind 10 km/h.",
            data={"city": city},
            source="budget-stub",
        )


class CapturingProvider(LLMProvider):
    name = "capture"

    def __init__(self) -> None:
        self.last_request: ProviderRequest | None = None

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.last_request = request
        return ProviderResponse(text="ok", model="fake-model")


class CapturingToolCallProvider(LLMProvider):
    name = "capture-tools"

    def __init__(self) -> None:
        self.last_request: ProviderRequest | None = None

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.last_request = request
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name=_WEATHER_RUNTIME_TOOL,
                    arguments={},
                    source="fallback",
                )
            ],
            finish_reason="tool_calls",
        )


class _FailingWeatherTool(Tool):
    name = "weather.openmeteo.current"
    description = "Lookup weather by city"
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(
            tool_name=self.name,
            ok=False,
            verified=False,
            content="",
            error="missing city argument",
            source="test-stub",
        )


class FakeNoToolCallProvider(LLMProvider):
    name = "fake-no-tools"

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        self.call_count += 1
        return ProviderResponse(text="text-only", model="fake-model")


class _StubSearchTool(Tool):
    name = "web.search"
    description = "Search the web"
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        query = str(arguments.get("query", "")).strip() or "unknown"
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            verified=True,
            content=f"search results for: {query}",
            data={"query": query, "result_count": 1},
            source="search-stub",
        )


class AgentServiceTestCase(unittest.TestCase):
    pass


__all__ = [
    "AgentService",
    "AgentServiceTestCase",
    "CapturingProvider",
    "CapturingToolCallProvider",
    "FakeChangingToolCallProvider",
    "FakeDoubleToolCallProvider",
    "FakeNoToolCallProvider",
    "FakeProvider",
    "FakeSubstantiveToolMissingFinalizationProvider",
    "FakeSubstantiveToolThenFinalizationProvider",
    "FakeTextOnlyToolCallProvider",
    "FakeTextToolCallProvider",
    "FakeToolCallProvider",
    "FakeTwoStepToolThenFinalProvider",
    "LLMProvider",
    "Message",
    "OpenMinionConfig",
    "Path",
    "Plugin",
    "PluginContext",
    "PluginRegistry",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderToolCall",
    "SecurityPolicyEngine",
    "SecurityPolicyRule",
    "SelfImprovementEngine",
    "Tool",
    "ToolBudgetPolicy",
    "ToolExecutionContext",
    "ToolExecutionPolicy",
    "ToolExecutionResult",
    "ToolRegistry",
    "UppercaseInboundPlugin",
    "_BudgetWeatherTool",
    "_FailingWeatherTool",
    "_StubSearchTool",
    "_StubWeatherTool",
    "asyncio",
    "json",
    "logging",
    "replace",
    "tempfile",
    "unittest",
]
