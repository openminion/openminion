from __future__ import annotations

from unittest.mock import Mock
from types import SimpleNamespace

from openminion.base.config import (
    CapabilityBinding,
    ToolSelectionConfig,
    OpenMinionConfig,
)
from openminion.modules.llm.providers.base import ProviderResponse, ProviderToolCall
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.registry import ToolRegistry
from openminion.services.tool.selection import ToolSelectionService
from openminion.services.agent import AgentService
from openminion.services.agent.fallbacks import AgentToolFallbacksMixin
from tests._csc_fixtures import _csc_install_default_agent


class TestCapabilityBindingSchema:
    def test_capability_binding_creation(self):
        binding = CapabilityBinding(
            primary="web_search_tool",
            fallback_tools=["web_alt_tool", "fallback_search"],
        )

        assert binding.primary == "web_search_tool"
        assert binding.fallback_tools == ["web_alt_tool", "fallback_search"]

    def test_tool_selection_config_capabilities_field(self):
        caps_config = ToolSelectionConfig(
            mode="deterministic",
            capabilities={
                "web.search": CapabilityBinding(
                    primary="tavily_search", fallback_tools=["bing_search"]
                ),
                "weather": CapabilityBinding(primary="weather_api", fallback_tools=[]),
            },
        )

        assert "web.search" in caps_config.capabilities
        assert caps_config.capabilities["web.search"].primary == "tavily_search"
        assert caps_config.capabilities["web.search"].fallback_tools == ["bing_search"]
        assert caps_config.capabilities["weather"].primary == "weather_api"
        assert caps_config.capabilities["weather"].fallback_tools == []


class TestDeterministicSelectionWithCapabilities:
    def setup_mock_registry(self):
        registry = Mock(spec=ToolRegistry)
        registry.provider_spec_for_name = Mock(return_value=None)

        class MockTool:
            def __init__(self, name):
                self.name = name

        registry._tools = {
            "weather_service": MockTool("weather_service"),
            "alt_weather": MockTool("alt_weather"),
            "dummy": MockTool("dummy"),
        }

        def mock_provider_specs():
            specs = []
            for name, tool in registry._tools.items():
                spec = SimpleNamespace(
                    name=tool.name,
                    description=f"Description for {name}",
                    parameters={
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                )
                specs.append(spec)
            return specs

        registry.provider_specs = Mock(return_value=mock_provider_specs())

        def mock_tools_by_category(category):
            if "weather" in category:
                return ["weather_service", "alt_weather"]
            return ["dummy"]

        registry.tools_by_category = Mock(side_effect=mock_tools_by_category)

        return registry

    def test_deterministic_selection_uses_capabilities(self):
        registry = self.setup_mock_registry()

        config = ToolSelectionConfig(
            mode="deterministic",
            capabilities={
                "weather": CapabilityBinding(
                    primary="weather_service", fallback_tools=["alt_weather"]
                )
            },
        )

        service = ToolSelectionService(config, registry)

        result = service._deterministic_selection("What is the weather?", "weather")

        assert result.shortlist == ["weather_service"]
        assert result.binding_source == "capability_primary"
        assert result.category == "weather"

    def test_deterministic_fallback_chain_from_capabilities(self):
        registry = self.setup_mock_registry()

        config = ToolSelectionConfig(
            mode="deterministic",
            capabilities={
                "web.search": CapabilityBinding(
                    primary="main_search",
                    fallback_tools=["alt_search", "backup_search"],
                )
            },
        )

        service = ToolSelectionService(config, registry)

        plan = service.create_shortlist_plan(
            query="latest news on ai", intent_categories=["web.search"]
        )

        assert "web.search" in plan.selected_categories
        assert "main_search" in plan.selected_tools


class MockAgentProvider:
    def __init__(self):
        self.name = "test_provider"

    async def generate(self, request):
        return ProviderResponse(
            text="Hello from test",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            tool_calls=[
                ProviderToolCall(
                    name="primary_weather_tool",
                    arguments={"location": "NYC"},
                    source="model_call",
                )
            ]
            if request.tool_choice == "required"
            else [],
        )


class TestFallbackEligibilityPolicy:
    def test_should_retry_with_fallback_logic(self):
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]

        logger = Mock()

        agent = AgentService(
            config=config, plugins=Mock(), provider=MockAgentProvider(), logger=logger
        )

        success_result = ToolExecutionResult(
            tool_name="weather_api",
            ok=True,
            verified=True,
            content="Weather data",
            error=None,
        )
        assert not agent._should_retry_with_fallback(success_result)

        network_error = ToolExecutionResult(
            tool_name="web.search",
            ok=False,
            verified=False,
            content="",
            error="connection timeout: server not responding",
            data={},
        )
        assert not agent._should_retry_with_fallback(network_error)

        structured_network_error = ToolExecutionResult(
            tool_name="web.search",
            ok=False,
            verified=False,
            content="",
            error="connection timeout: server not responding",
            data={"error_code": "transient_network_error"},
        )
        assert agent._should_retry_with_fallback(structured_network_error)

        policy_error = ToolExecutionResult(
            tool_name="restricted_tool",
            ok=False,
            verified=False,
            content="",
            error="security_policy_denied: user_not_authorized",
            data={"policy_decision": "DENIED"},
        )
        assert not agent._should_retry_with_fallback(policy_error)

        approval_error = ToolExecutionResult(
            tool_name="privileged_tool",
            ok=False,
            verified=False,
            content="",
            error="tool_requires_user_approval",
            data={"policy_decision": "require_approval"},
        )
        assert not agent._should_retry_with_fallback(approval_error)

        trans_error = ToolExecutionResult(
            tool_name="api_client",
            ok=False,
            verified=False,
            content="",
            error="transient_network_error",
            data={"error_code": "transient_network_error"},
        )
        assert agent._should_retry_with_fallback(trans_error)


def test_config_parsing_with_capabilities():
    from openminion.base.config import _parse_tool_selection_config

    raw_config = {
        "mode": "deterministic",
        "max_tools_per_turn": 6,
        "capabilities": {
            "web.search": {
                "primary": "tavily_search",
                "fallback_tools": ["google_search", "bing_search"],
            },
            "weather": {
                "primary": "weather_api",
                "fallback_tools": ["backup_weather"],
            },
        },
    }

    parsed = _parse_tool_selection_config(raw_config)

    assert "web.search" in parsed.capabilities
    assert parsed.capabilities["web.search"].primary == "tavily_search"
    assert parsed.capabilities["web.search"].fallback_tools == [
        "bing_search",
        "google_search",
    ]  # Should be sorted

    assert "weather" in parsed.capabilities
    assert parsed.capabilities["weather"].primary == "weather_api"
    assert parsed.capabilities["weather"].fallback_tools == ["backup_weather"]


def test_config_parsing_with_runtime_bindings_and_fallback_policy() -> None:
    from openminion.base.config import _parse_tool_selection_config

    parsed = _parse_tool_selection_config(
        {
            "mode": "typed",
            "runtime_bindings": {
                "runtime.web.search": {
                    "primary": "tavily.web.search",
                    "fallback_tools": ["search.dispatch"],
                },
                "runtime.weather.current": {
                    "primary": "weather.openmeteo.current",
                    "fallback_tools": ["lookup_weather"],
                },
            },
            "runtime_binding_selection_strategy": "ordered",
            "runtime_fallback_on": ["timeout", "provider_empty"],
            "runtime_no_fallback_on": ["policy_denied", "approval"],
        }
    )

    assert parsed.runtime_binding_selection_strategy == "ordered"
    assert parsed.runtime_fallback_on == ["timeout", "provider_empty"]
    assert parsed.runtime_no_fallback_on == ["policy_denied", "approval"]
    assert "runtime.web.search" in parsed.runtime_bindings
    assert parsed.runtime_bindings["runtime.web.search"].primary == "tavily.web.search"


def test_config_parsing_no_longer_normalizes_legacy_category_aliases() -> None:
    from openminion.base.config import _parse_tool_selection_config

    parsed = _parse_tool_selection_config(
        {
            "mode": "typed",
            "bindings": {"search.news": "tavily.web.search"},
        }
    )

    assert parsed.bindings["search.news"] == "tavily.web.search"
    assert "web.search" not in parsed.bindings
    assert not hasattr(parsed, "capability_aliases")


def test_legacy_binding_no_longer_drives_canonical_category_selection() -> None:
    from openminion.base.config import _parse_tool_selection_config
    from openminion.services.tool.selection import ToolSelectionService

    config = _parse_tool_selection_config(
        {
            "mode": "typed",
            "bindings": {"search.news": "search.tavily.search"},
        }
    )

    class _Tool:
        name = "search.tavily.search"
        description = "Search the web for current information"
        parameters = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    class _Registry:
        def __init__(self):
            self._tools = {"search.tavily.search": _Tool()}

        def provider_specs(self):
            return []

        def tools_by_category(self, _category: str):
            return []

    service = ToolSelectionService(config, _Registry())  # type: ignore[arg-type]

    assert service.get_primary_tool_for_category("web.search") is None
    result = service.select_tools(
        query="what's latest news on iran?",
        intent_categories=["web.search"],
        forced_category="web.search",
    )
    assert result.mode == "deterministic"
    assert result.shortlist == []
    assert "no_category_tool_match" in result.reason_codes


def test_agent_service_no_longer_inherits_tool_fallbacks_mixin() -> None:
    assert not issubclass(AgentService, AgentToolFallbacksMixin)
