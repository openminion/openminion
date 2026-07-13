from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

import openminion.services.tool.selection as tool_selection_module
from openminion.base.config import ToolSelectionConfig
from openminion.modules.tool.base import (
    Tool,
    ToolCategoryInfo,
    ToolExecutionContext,
    ToolExecutionResult,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.services.tool.selection import (
    SelectionResult,
    ToolStub,
    ValidationRetryManager,
    create_tool_selection_service,
    create_validation_error,
    stub_to_provider_spec,
)


def test_service_tool_selection_surface_is_canonical_module_owner() -> None:
    from openminion.modules.tool.selection import ToolSelectionService as canonical
    from openminion.services.tool.selection import (
        ToolSelectionService as compatibility,
    )

    assert compatibility is canonical


class MockTool(Tool):
    def __init__(
        self,
        name: str,
        description: str = "Mock tool",
        primary_category: str = "uncategorized",
        secondary_categories: tuple = (),
        parameters: dict = None,
    ) -> None:
        self.name = name
        self.description = description
        self.categories = ToolCategoryInfo(
            primary_category=primary_category,
            secondary_categories=secondary_categories,
        )
        self.parameters = parameters or {}

    def execute(
        self,
        arguments,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content="mock result",
        )


@pytest.fixture
def basic_registry() -> ToolRegistry:
    tools = [
        MockTool(
            name="search.tavily.search",
            description="Search the web for current information",
            primary_category="web.search",
            secondary_categories=("search.news", "search.web"),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        MockTool(
            name="weather.openmeteo.current",
            description="Get current weather data",
            primary_category="weather",
            parameters={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        ),
        MockTool(
            name="read_file",
            description="Read file contents",
            primary_category="filesystem.read",
        ),
        MockTool(
            name="write_file",
            description="Write file contents",
            primary_category="filesystem.write",
        ),
        MockTool(
            name="utility.calculate_expression",
            description="Calculate mathematical expressions",
            primary_category="math.compute",
        ),
    ]
    return ToolRegistry(tools)


@pytest.fixture
def hybrid_config() -> ToolSelectionConfig:
    return ToolSelectionConfig(
        mode="typed",
        max_tools_per_turn=6,
        tool_prompt_token_budget=600,
        bindings={
            "web.search": "search.tavily.search",
            "weather": "weather.openmeteo.current",
        },
        bindings_fallback={
            "web.search": ["web.search"],
        },
    )


class TestCategoryRouting:
    def test_tools_by_category(self, basic_registry: ToolRegistry) -> None:
        tools = basic_registry.tools_by_category("web.search")
        assert "search.tavily.search" in tools

    def test_category_for_tool(self, basic_registry: ToolRegistry) -> None:
        entry = basic_registry.category_for_tool("search.tavily.search")
        assert entry.primary_category == "web.search"
        assert "search.web" in entry.secondary_categories

    def test_missing_category_returns_general_assistance(
        self, basic_registry: ToolRegistry
    ) -> None:
        entry = basic_registry.category_for_tool("nonexistent_tool")
        assert entry.primary_category == "general_assistance"

    def test_deterministic_binding_precedence(
        self, basic_registry: ToolRegistry, hybrid_config: ToolSelectionConfig
    ) -> None:
        service = create_tool_selection_service(hybrid_config, basic_registry)
        result = service.select_tools(
            query="what's latest news on iran?",
            intent_categories=["web.search"],
        )
        assert result.mode == "deterministic"
        assert result.shortlist == ["web.search"]
        assert result.category == "web.search"
        assert result.binding_source in ("capability_primary", "explicit_model_tool")

    def test_fallback_binding(self, basic_registry: ToolRegistry) -> None:
        config = ToolSelectionConfig(
            mode="deterministic",
            bindings={},
            bindings_fallback={"web.search": ["web.search"]},
        )
        service = create_tool_selection_service(config, basic_registry)
        result = service.select_tools(
            query="news about iran",
            intent_categories=["web.search"],
        )
        assert result.mode == "deterministic"
        assert result.binding_source in ("category_index", "explicit_model_tool")

    def test_category_index_binding(self, basic_registry: ToolRegistry) -> None:
        config = ToolSelectionConfig(
            mode="deterministic",
            bindings={},
            bindings_fallback={},
        )
        service = create_tool_selection_service(config, basic_registry)
        result = service.select_tools(
            query="news about iran",
            intent_categories=["web.search"],
        )
        assert result.mode == "deterministic"
        assert result.binding_source in ("category_index", "explicit_model_tool")

    def test_browser_category_index_prefers_pinchtab_navigate_tool(self) -> None:
        registry = ToolRegistry(
            [
                MockTool(
                    name="test.browser.pinchtab.action",
                    primary_category="browser",
                ),
                MockTool(
                    name="test.browser.pinchtab.navigate",
                    primary_category="browser",
                ),
                MockTool(
                    name="test.browser.playwright.navigate",
                    primary_category="browser",
                ),
            ]
        )
        config = ToolSelectionConfig(
            mode="deterministic",
            bindings={},
            bindings_fallback={},
        )
        service = create_tool_selection_service(config, registry)
        result = service.select_tools(
            query="open browser and go to google",
            intent_categories=["browser"],
        )
        assert result.mode == "deterministic"
        assert result.binding_source == "category_index"
        assert result.shortlist == ["test.browser.pinchtab.action"]

    def test_deterministic_selection_returns_empty_when_category_unavailable(
        self,
    ) -> None:
        registry = ToolRegistry(
            [
                MockTool(name="run_command", primary_category="exec.run"),
                MockTool(name="read_file", primary_category="file.read"),
            ]
        )
        config = ToolSelectionConfig(
            mode="deterministic",
            bindings={},
            bindings_fallback={},
        )
        service = create_tool_selection_service(config, registry)
        result = service.select_tools(
            query="open browser and go to google",
            intent_categories=["browser"],
        )
        assert result.mode == "deterministic"
        assert result.shortlist == []
        assert "no_category_tool_match" in result.reason_codes

    def test_identity_allowed_mode_does_not_enforce_allowlist(
        self, basic_registry: ToolRegistry
    ) -> None:
        config = ToolSelectionConfig(mode="typed")
        service = create_tool_selection_service(config, basic_registry)
        filtered = service._apply_identity_tool_filter(
            basic_registry.provider_specs(),
            {
                "tool_use": "allowed",
                "allowed_tools": ["read_file"],
            },
        )
        names = {spec.name for spec in filtered.specs}
        assert "search.tavily.search" in names
        assert "weather.openmeteo.current" in names

    def test_identity_restricted_mode_enforces_allowlist(
        self, basic_registry: ToolRegistry
    ) -> None:
        config = ToolSelectionConfig(mode="typed")
        service = create_tool_selection_service(config, basic_registry)
        filtered = service._apply_identity_tool_filter(
            basic_registry.provider_specs(),
            {
                "tool_use": "restricted",
                "allowed_tools": ["read_file"],
            },
        )
        names = {spec.name for spec in filtered.specs}
        assert names == {"read_file"}

    def test_identity_blocked_patterns_filters_matching_tools(
        self, basic_registry: ToolRegistry
    ) -> None:
        service = create_tool_selection_service(
            ToolSelectionConfig(mode="typed"), basic_registry
        )
        filtered = service._apply_identity_tool_filter(
            basic_registry.provider_specs(),
            {
                "tool_use": "allowed",
                "blocked_patterns": ["*weather*"],
            },
        )
        names = {spec.name for spec in filtered.specs}
        assert "weather.openmeteo.current" not in names
        assert "search.tavily.search" in names

    def test_identity_filter_unresolved_reason_code_plumbing(
        self, basic_registry: ToolRegistry
    ) -> None:
        config = ToolSelectionConfig(mode="typed")
        service = create_tool_selection_service(config, basic_registry)

        def _fake_filter(_tools, _identity_filter):
            return tool_selection_module._FilterOutcome(
                specs=basic_registry.provider_specs(),
                unresolved_category_count=3,
            )

        service._apply_identity_tool_filter = _fake_filter  # type: ignore[method-assign]
        result = service.select_tools(
            query="weather in sf",
            intent_categories=["weather"],
            identity_tool_filter={"tool_use": "read_only"},
        )
        assert "read_only:unresolved_categories:3" in result.reason_codes

    def test_concurrent_filtered_selection_isolation(self) -> None:
        registry = ToolRegistry(
            [
                MockTool(
                    name="weather.openmeteo.current",
                    description="Get current weather",
                    primary_category="weather",
                ),
                MockTool(
                    name="search.tavily.search",
                    description="Search the web",
                    primary_category="web.search",
                ),
            ]
        )
        service = create_tool_selection_service(
            ToolSelectionConfig(
                mode="typed",
                bindings={},
                bindings_fallback={},
            ),
            registry,
        )
        baseline_registry_id = id(service._registry)

        def _select_weather() -> SelectionResult:
            return service.select_tools(
                query="weather in sf",
                intent_categories=["weather"],
                identity_tool_filter={
                    "tool_use": "restricted",
                    "allowed_tools": ["weather.openmeteo.current"],
                },
            )

        def _select_web() -> SelectionResult:
            return service.select_tools(
                query="latest tech news",
                intent_categories=["web.search"],
                identity_tool_filter={
                    "tool_use": "restricted",
                    "allowed_tools": ["search.tavily.search"],
                },
            )

        for _ in range(50):
            with ThreadPoolExecutor(max_workers=2) as pool:
                weather_result = pool.submit(_select_weather).result()
                web_result = pool.submit(_select_web).result()
            assert weather_result.shortlist == ["weather.openmeteo.current"]
            assert web_result.shortlist == ["web.search"]
            assert id(service._registry) == baseline_registry_id

    def test_read_only_filter_blocks_configured_write_exec_categories(self) -> None:
        registry = ToolRegistry(
            [
                MockTool(name="read_file", primary_category="file.read"),
                MockTool(name="write_file", primary_category="file.write"),
                MockTool(name="run_command", primary_category="exec.run"),
            ]
        )
        service = create_tool_selection_service(
            ToolSelectionConfig(mode="typed"), registry
        )
        outcome = service._apply_identity_tool_filter(
            registry.provider_specs(),
            {"tool_use": "read_only"},
        )
        names = {spec.name for spec in outcome.specs}
        assert "read_file" in names
        assert "write_file" not in names
        assert "run_command" not in names

    def test_read_only_filter_unresolved_categories_fail_open_and_log(
        self, basic_registry: ToolRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        service = create_tool_selection_service(
            ToolSelectionConfig(mode="typed"), basic_registry
        )
        specs = basic_registry.provider_specs()
        service._is_write_exec_tool = lambda _tool_name: None  # type: ignore[method-assign]
        with caplog.at_level("DEBUG", logger="openminion.services.tool.selection"):
            outcome = service._apply_identity_tool_filter(
                specs,
                {"tool_use": "read_only"},
            )
        assert len(outcome.specs) == len(specs)
        assert outcome.unresolved_category_count == len(specs)
        assert "read_only unresolved category lookup" in caplog.text

    def test_select_tools_emits_read_only_unresolved_reason_code(
        self, basic_registry: ToolRegistry
    ) -> None:
        service = create_tool_selection_service(
            ToolSelectionConfig(mode="typed"), basic_registry
        )
        expected_count = len(service._registry_specs())
        service._is_write_exec_tool = lambda _tool_name: None  # type: ignore[method-assign]
        result = service.select_tools(
            query="weather in sf",
            intent_categories=["weather"],
            identity_tool_filter={"tool_use": "read_only"},
        )
        assert (
            f"read_only:unresolved_categories:{expected_count}" in result.reason_codes
        )

    def test_identity_filter_reuses_structural_filter_result(
        self, basic_registry: ToolRegistry
    ) -> None:
        service = create_tool_selection_service(
            ToolSelectionConfig(mode="typed"), basic_registry
        )

        first = service.select_tools(
            query="weather in sf",
            intent_categories=None,
            identity_tool_filter={"tool_use": "read_only"},
        )
        second = service.select_tools(
            query="weather in sf",
            intent_categories=None,
            identity_tool_filter={"tool_use": "read_only"},
        )

        assert "identity_filter_cache_miss" in first.reason_codes
        assert "identity_filter_cache_hit" in second.reason_codes
        assert first.shortlist == second.shortlist

    def test_identity_filter_cache_key_includes_filter_payload(
        self, basic_registry: ToolRegistry
    ) -> None:
        service = create_tool_selection_service(
            ToolSelectionConfig(mode="typed"), basic_registry
        )

        read_only = service.select_tools(
            query="weather in sf",
            intent_categories=None,
            identity_tool_filter={"tool_use": "read_only"},
        )
        allowed_tool = read_only.shortlist[0]
        restricted = service.select_tools(
            query="weather in sf",
            intent_categories=None,
            identity_tool_filter={
                "tool_use": "restricted",
                "allowed_tools": [allowed_tool],
            },
        )

        assert "identity_filter_cache_miss" in read_only.reason_codes
        assert "identity_filter_cache_miss" in restricted.reason_codes
        assert restricted.shortlist == [allowed_tool]


class TestRegistrySpecsObservability:
    def test_registry_specs_logs_probe_failures_and_warning(
        self, basic_registry: ToolRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _BrokenRegistry:
            def model_provider_specs(self):
                raise RuntimeError("model probe failed")

        service = create_tool_selection_service(
            ToolSelectionConfig(mode="typed"), basic_registry
        )
        service._registry = _BrokenRegistry()  # type: ignore[assignment]

        with (
            caplog.at_level("WARNING", logger="openminion.services.tool.exposure"),
            caplog.at_level("WARNING", logger="openminion.services.tool.selection"),
        ):
            specs = service._registry_specs()

        assert specs == []
        assert "model_provider_specs lookup failed" in caplog.text
        assert "registry_specs_unavailable" in caplog.text


class TestStubGenerator:
    def test_stub_generation(self, basic_registry: ToolRegistry) -> None:
        config = ToolSelectionConfig(mode="typed")
        service = create_tool_selection_service(config, basic_registry)
        stub = service._generate_stub("search.tavily.search")
        assert stub is not None
        assert stub.name == "search.tavily.search"
        assert "query" in stub.required_args

    def test_stub_description_truncation(self, basic_registry: ToolRegistry) -> None:
        config = ToolSelectionConfig(mode="typed")
        service = create_tool_selection_service(config, basic_registry)
        stub = service._generate_stub("search.tavily.search")
        assert stub is not None
        assert len(stub.description_short) <= 120

    def test_token_estimation(self, basic_registry: ToolRegistry) -> None:
        config = ToolSelectionConfig(mode="typed")
        service = create_tool_selection_service(config, basic_registry)
        stub = service._generate_stub("search.tavily.search")
        assert stub is not None
        tokens = service._estimate_stub_tokens(stub)
        assert tokens > 0

    def test_budget_enforcement(self, basic_registry: ToolRegistry) -> None:
        config = ToolSelectionConfig(
            mode="typed",
            max_tools_per_turn=2,
            tool_prompt_token_budget=50,
        )
        service = create_tool_selection_service(config, basic_registry)
        result = service.select_tools("search for information about python")
        assert len(result.shortlist) <= 2

    def test_stub_to_provider_spec(self, basic_registry: ToolRegistry) -> None:
        stub = ToolStub(
            name="test_tool",
            description_short="Test tool",
            required_args=["arg1", "arg2"],
            example_minimal={"arg1": "<arg1>"},
        )
        spec = stub_to_provider_spec(stub)
        assert spec.name == "test_tool"
        assert "arg1" in spec.parameters.get("required", [])


class TestValidationRetry:
    def test_create_validation_error(self) -> None:
        error = create_validation_error(
            tool_name="search.tavily.search",
            missing_required=["query"],
            wrong_type=[],
        )
        assert error.code == "TOOL_ARG_VALIDATION_FAILED"
        assert error.tool_name == "search.tavily.search"
        assert error.retry_mode == "full_schema_once"

    def test_should_retry_allowed(
        self, basic_registry: ToolRegistry, hybrid_config: ToolSelectionConfig
    ) -> None:
        service = create_tool_selection_service(hybrid_config, basic_registry)
        manager = ValidationRetryManager(hybrid_config, service)
        error = create_validation_error("test", ["query"], [])
        assert manager.should_retry("search.tavily.search", error)

    def test_should_retry_max_reached(
        self, basic_registry: ToolRegistry, hybrid_config: ToolSelectionConfig
    ) -> None:
        service = create_tool_selection_service(hybrid_config, basic_registry)
        manager = ValidationRetryManager(hybrid_config, service)
        manager.record_retry("search.tavily.search")
        error = create_validation_error("test", ["query"], [])
        assert not manager.should_retry("search.tavily.search", error)

    def test_get_expanded_schema(
        self, basic_registry: ToolRegistry, hybrid_config: ToolSelectionConfig
    ) -> None:
        service = create_tool_selection_service(hybrid_config, basic_registry)
        manager = ValidationRetryManager(hybrid_config, service)
        spec = manager.get_expanded_schema("search.tavily.search")
        assert spec is not None
        assert spec.name == "search.tavily.search"

    def test_retry_disabled(self, basic_registry: ToolRegistry) -> None:
        config = ToolSelectionConfig(validation_retry_max=0)
        service = create_tool_selection_service(config, basic_registry)
        manager = ValidationRetryManager(config, service)
        error = create_validation_error("test", ["query"], [])
        assert not manager.should_retry("search.tavily.search", error)


class TestShortlistPlan:
    def test_create_shortlist_plan(
        self, basic_registry: ToolRegistry, hybrid_config: ToolSelectionConfig
    ) -> None:
        service = create_tool_selection_service(hybrid_config, basic_registry)
        plan = service.create_shortlist_plan(
            query="what's latest news on iran?",
            intent_categories=["web.search"],
        )
        assert plan.mode == "deterministic"
        assert plan.selected_tools == ["web.search"]
        assert "web.search" in plan.selected_categories


class TestModeNormalization:
    def test_unknown_mode_normalizes_to_typed(
        self, basic_registry: ToolRegistry
    ) -> None:
        config = ToolSelectionConfig(mode="unsupported-mode")
        service = create_tool_selection_service(config, basic_registry)
        result = service.select_tools("weather in sf", intent_categories=["weather"])
        assert result.mode == "deterministic"


class TestSelectionMode:
    def test_typed_mode_deterministic_for_intent(
        self, basic_registry: ToolRegistry, hybrid_config: ToolSelectionConfig
    ) -> None:
        service = create_tool_selection_service(hybrid_config, basic_registry)
        result = service.select_tools(
            query="weather in san francisco",
            intent_categories=["weather"],
        )
        assert result.mode == "deterministic"
        assert result.category == "weather"

    def test_typed_mode_full_catalog_for_general(
        self, basic_registry: ToolRegistry, hybrid_config: ToolSelectionConfig
    ) -> None:
        service = create_tool_selection_service(hybrid_config, basic_registry)
        result = service.select_tools("calculate 2 + 2")
        assert result.mode == "typed"
        assert "full_catalog" in result.reason_codes
