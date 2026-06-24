from __future__ import annotations

from openminion.tools.fetch.providers.firecrawl import (
    REGISTRAR,
    FirecrawlFetchProvider,
    provider,
)
from openminion.tools.fetch.providers.firecrawl.interfaces import (
    FETCH_FIRECRAWL_PLUGIN_INTERFACE_VERSION,
)


def test_firecrawl_fetch_provider_identity_and_contract() -> None:
    instance = FirecrawlFetchProvider()

    assert instance.name == "firecrawl"
    assert "markdown" in instance.capabilities["formats"]
    assert REGISTRAR.module_id == "fetch_firecrawl"
    assert REGISTRAR.is_provider_only is True
    assert FETCH_FIRECRAWL_PLUGIN_INTERFACE_VERSION == "v1"


def test_module_default_provider_singleton_matches_class() -> None:
    assert provider.name == "firecrawl"
    assert isinstance(provider, FirecrawlFetchProvider)


def test_capabilities_describes_dom_render() -> None:
    instance = FirecrawlFetchProvider()
    assert instance.capabilities["render"] == ["dom"]
