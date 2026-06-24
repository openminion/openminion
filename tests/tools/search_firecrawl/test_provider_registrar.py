from __future__ import annotations

from types import SimpleNamespace

from openminion.base.config.env import resolve_environment_config_with_explicit_env
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.search import plugin as search_plugin
from openminion.tools.search.providers.firecrawl.provider import FirecrawlSearchProvider
from openminion.tools.search.providers.firecrawl.registrar import (
    SearchFirecrawlRegistrar,
)


def setup_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def teardown_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def test_registrar_is_provider_only_with_empty_manifest() -> None:
    registrar = SearchFirecrawlRegistrar()

    manifest = registrar.get_manifest(
        ToolRegisterContext(module_id="search.firecrawl", config=None)
    )

    assert registrar.is_provider_only is True
    assert manifest.module_id == "search.firecrawl"
    assert manifest.model_tools == ()
    assert manifest.runtime_bindings == ()


def test_registrar_registers_firecrawl_provider_into_shared_search_map() -> None:
    registrar = SearchFirecrawlRegistrar()
    registry = ToolRegistry()

    registrar.register(registry)

    assert search_plugin.list_provider_ids() == ("firecrawl",)


def test_healthcheck_is_key_based_only() -> None:
    provider = FirecrawlSearchProvider()

    assert (
        provider.healthcheck(
            ctx=SimpleNamespace(
                env=resolve_environment_config_with_explicit_env(
                    {"FIRECRAWL_API_KEY": "fc-key"}
                )
            )
        )
        is True
    )
    assert (
        provider.healthcheck(
            ctx=SimpleNamespace(
                env=resolve_environment_config_with_explicit_env(
                    {"FIRECRAWL_API_KEY": ""}
                )
            )
        )
        is False
    )
