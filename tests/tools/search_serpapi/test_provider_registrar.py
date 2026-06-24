from __future__ import annotations

from openminion.tools.search import plugin as search_plugin
from openminion.tools.search.providers.serpapi import REGISTRAR


def setup_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def teardown_function() -> None:
    search_plugin._PROVIDERS.clear()
    search_plugin._PROVIDER_ORDER.clear()


def test_registrar_is_provider_only_with_empty_manifest() -> None:
    manifest = REGISTRAR.get_manifest(None)

    assert REGISTRAR.is_provider_only is True
    assert manifest.module_id == "search.serpapi"
    assert manifest.model_tools == ()
    assert manifest.runtime_bindings == ()


def test_register_adds_serpapi_provider_to_shared_search_family() -> None:
    REGISTRAR.register(registry=None)

    assert search_plugin.list_provider_ids() == ("serpapi",)
    assert "serpapi" in search_plugin._PROVIDERS
