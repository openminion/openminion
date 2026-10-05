from __future__ import annotations

from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.tools.search import plugin as search_plugin
from openminion.tools.search.providers.duckduckgo import REGISTRAR
from openminion.tools.search.providers.duckduckgo.provider import (
    DuckDuckGoSearchProvider,
)


def setup_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def teardown_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def test_registrar_is_provider_only_with_empty_manifest() -> None:
    manifest = REGISTRAR.get_manifest(
        ToolRegisterContext(module_id="search.duckduckgo", config=None)
    )

    assert REGISTRAR.is_provider_only is True
    assert REGISTRAR.module_id == "search.duckduckgo"
    assert manifest.module_id == "search.duckduckgo"
    assert manifest.model_tools == ()
    assert manifest.runtime_bindings == ()


def test_registrar_registers_provider_into_shared_search_map() -> None:
    REGISTRAR.register(ToolRegistry())

    assert search_plugin.list_provider_ids() == ("duckduckgo",)


def test_healthcheck_has_no_local_credential_dependency() -> None:
    assert DuckDuckGoSearchProvider().healthcheck() is True
