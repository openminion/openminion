from __future__ import annotations

from openminion.tools.search.providers import SearchProvider
from openminion.tools.search.providers.serpapi import (
    REGISTRAR,
    SerpApiSearchProvider,
    SerpApiSearchProviderConfig,
)


def test_serpapi_provider_satisfies_search_provider_protocol() -> None:
    provider = SerpApiSearchProvider()

    assert isinstance(provider, SearchProvider)
    assert provider.provider_id == "serpapi"
    assert provider.display_name == "SerpApi Search"


def test_package_exports_expected_public_surface() -> None:
    config = SerpApiSearchProviderConfig()

    assert REGISTRAR.module_id == "search.serpapi"
    assert config.endpoint == ""
    assert config.timeout_s == 0.0
