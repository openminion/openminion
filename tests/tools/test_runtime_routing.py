from __future__ import annotations

from types import SimpleNamespace

from openminion.base.config import ToolFamilyRuntimeConfig, ToolRuntimeConfig
from openminion.modules.tool.runtime.routing import (
    build_runtime_tool_routing_metadata,
    resolve_runtime_provider_chain,
    resolve_runtime_tool_config,
    resolve_runtime_tool_family_config,
)


def test_build_runtime_tool_routing_metadata_serializes_config() -> None:
    payload = build_runtime_tool_routing_metadata(
        ToolRuntimeConfig(
            search=ToolFamilyRuntimeConfig(
                enabled_providers=["tavily", "brave"],
                default_provider="tavily",
                provider_order=["tavily", "brave"],
                allow_fallback=True,
            )
        )
    )

    assert payload == {
        "runtime_tools": {
            "search": {
                "enabled_providers": ["tavily", "brave"],
                "default_provider": "tavily",
                "provider_order": ["tavily", "brave"],
                "allow_fallback": True,
            }
        }
    }


def test_resolve_runtime_tool_family_config_reads_policy_context_metadata() -> None:
    context = SimpleNamespace(
        policy=SimpleNamespace(
            raw={
                "context_metadata": {
                    "runtime_tools": {
                        "fetch": {
                            "enabled_providers": ["core-http", "scrapling"],
                            "default_provider": "core-http",
                            "provider_order": ["core-http", "scrapling"],
                            "allow_fallback": True,
                        }
                    }
                }
            }
        )
    )

    fetch = resolve_runtime_tool_family_config(context, family_name="fetch")

    assert fetch is not None
    assert fetch.enabled_providers == ["core-http", "scrapling"]
    assert fetch.default_provider == "core-http"
    assert fetch.provider_order == ["core-http", "scrapling"]
    assert fetch.allow_fallback is True


def test_resolve_runtime_tool_config_defaults_when_metadata_missing() -> None:
    config = resolve_runtime_tool_config(
        SimpleNamespace(policy=SimpleNamespace(raw={}))
    )
    assert config.configured_families() == {}


def test_resolve_runtime_provider_chain_prioritizes_runtime_config_over_hints() -> None:
    ordered = resolve_runtime_provider_chain(
        available=("tavily", "brave"),
        family_config=ToolFamilyRuntimeConfig(
            enabled_providers=["brave", "tavily"],
            default_provider="brave",
            provider_order=["brave", "tavily"],
            allow_fallback=True,
        ),
        hinted_order=("tavily",),
    )

    assert ordered == ["brave", "tavily"]


def test_resolve_runtime_provider_chain_can_disable_fallback() -> None:
    ordered = resolve_runtime_provider_chain(
        available=("core-http", "scrapling"),
        family_config=ToolFamilyRuntimeConfig(
            enabled_providers=["core-http", "scrapling"],
            default_provider="scrapling",
            provider_order=[],
            allow_fallback=False,
        ),
        hinted_order=("core-http",),
    )

    assert ordered == ["scrapling"]
