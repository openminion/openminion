from __future__ import annotations

from openminion.modules.tool import PLUGIN_CONTRACT_VERSION
from openminion.tools.search.providers import SearchProvider
from openminion.tools.search.providers.duckduckgo import (
    REGISTRAR,
    DuckDuckGoSearchProvider,
    DuckDuckGoSearchProviderConfig,
)
from openminion.tools.search.providers.duckduckgo.interfaces import (
    CONTRACT_VERSION,
    is_compatible,
)


def test_duckduckgo_provider_satisfies_search_provider_protocol() -> None:
    provider = DuckDuckGoSearchProvider()

    assert isinstance(provider, SearchProvider)
    assert provider.provider_id == "duckduckgo"
    assert provider.display_name == "DuckDuckGo Search"


def test_package_exports_expected_public_surface_and_contract_version() -> None:
    config = DuckDuckGoSearchProviderConfig()

    assert REGISTRAR.module_id == "search.duckduckgo"
    assert REGISTRAR.is_provider_only is True
    assert config.endpoint == ""
    assert config.timeout_s == 0.0
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert is_compatible(CONTRACT_VERSION, PLUGIN_CONTRACT_VERSION) is True
