from __future__ import annotations

from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.modules.tool.registry import ToolSpec
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.exposure import (
    get_allowed_model_tool_names,
    get_model_exposure_specs,
    get_visible_tool_specs_and_dispatch_map,
)


def _spec(name: str) -> ProviderToolSpec:
    return ProviderToolSpec(name=name, description=name, parameters={})


def test_get_model_exposure_specs_uses_canonical_manager_path() -> None:
    registry = build_default_tool_registry()
    names = {spec.name for spec in get_model_exposure_specs(registry)}
    assert "web.fetch" in names
    assert "weather" in names
    assert "time" in names
    assert "search.tavily.search" not in names
    assert "web_search" not in names


def test_browser_and_web_fetch_descriptions_preserve_tool_boundary() -> None:
    registry = build_default_tool_registry()
    by_name = {spec.name: spec for spec in get_model_exposure_specs(registry)}

    browser_description = by_name["browser"].description.lower()
    web_fetch_description = by_name["web.fetch"].description.lower()

    assert "interactive" in browser_description
    assert "visual" in browser_description
    assert "use web.fetch" in browser_description
    assert "static url content" in web_fetch_description
    assert "prefer this over browser" in web_fetch_description


def test_get_model_exposure_specs_does_not_fallback_to_provider_specs() -> None:
    class _Manager:
        def model_provider_specs(self, _available):
            return []

    class _Registry:
        _tools = {"web_search": object()}

        def _binding_manager(self):
            return _Manager()

        def provider_specs(self):
            return [_spec("web_search")]

    assert get_model_exposure_specs(_Registry()) == []


def test_get_model_exposure_specs_filters_non_canonical_stub_names() -> None:
    class _StubRegistry:
        def model_provider_specs(self):
            return [_spec("web.search"), _spec("web_search"), _spec("weather")]

    names = [spec.name for spec in get_model_exposure_specs(_StubRegistry())]
    assert names == ["weather", "web.search"]


def test_get_allowed_model_tool_names_returns_canonical_set() -> None:
    class _StubRegistry:
        def model_provider_specs(self):
            return [
                _spec("web.search"),
                _spec("search.tavily.search"),
                _spec("weather"),
            ]

    assert get_allowed_model_tool_names(_StubRegistry()) == {"web.search", "weather"}


def test_get_visible_tool_specs_and_dispatch_map_merges_prompt_visible_runtime_tools() -> (
    None
):
    prompt_visible = ToolSpec(
        name="mcp.fixture.echo_text",
        args_model=dict,
        min_scope="READ_ONLY",
        handler=lambda _args, _ctx: {"ok": True},
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        prompt_visible_runtime_name=True,
        runtime_binding_id="runtime.mcp.fixture.echo_text",
    )

    class _Registry:
        _tools = {"mcp.fixture.echo_text": prompt_visible}

        def _binding_manager(self):
            class _Manager:
                def model_provider_specs(self, _available):
                    return [_spec("weather")]

            return _Manager()

        def provider_spec_for_name(self, name: str):
            if name == "mcp.fixture.echo_text":
                return ProviderToolSpec(
                    name=name,
                    description="echo",
                    parameters=dict(prompt_visible.parameters_schema or {}),
                )
            return None

        def model_runtime_dispatch_map(self):
            return {"weather": {"runtime_binding_id": "runtime.weather.current"}}

    specs, dispatch_map = get_visible_tool_specs_and_dispatch_map(_Registry())

    assert [spec.name for spec in specs] == ["mcp.fixture.echo_text", "weather"]
    assert dispatch_map["weather"]["runtime_binding_id"] == "runtime.weather.current"
    assert dispatch_map["mcp.fixture.echo_text"] == {
        "runtime_binding_id": "runtime.mcp.fixture.echo_text",
        "runtime_tool_name": "mcp.fixture.echo_text",
    }
