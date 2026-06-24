from __future__ import annotations

from openminion.tools.search.providers import SearchProvider
from openminion.tools.search.providers.firecrawl import (
    FirecrawlSearchProvider,
    FirecrawlSearchProviderConfig,
    REGISTRAR,
)


def test_module_exports_provider_contract_and_registrar() -> None:
    provider = FirecrawlSearchProvider(FirecrawlSearchProviderConfig())

    assert isinstance(provider, SearchProvider)
    assert provider.provider_id == "firecrawl"
    assert provider.display_name == "Firecrawl Search"
    assert REGISTRAR.module_id == "search.firecrawl"
    assert REGISTRAR.is_provider_only is True
