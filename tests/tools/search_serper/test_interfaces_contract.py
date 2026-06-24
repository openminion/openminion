from __future__ import annotations

from openminion.modules.tool import PLUGIN_CONTRACT_VERSION
from openminion.tools.search.providers import SearchProvider
from openminion.tools.search.providers.serper import (
    REGISTRAR,
    SerperSearchProvider,
    SerperSearchProviderConfig,
)
from openminion.tools.search.providers.serper.interfaces import (
    CONTRACT_VERSION,
    is_compatible,
)


def test_serper_provider_satisfies_search_provider_protocol() -> None:
    provider = SerperSearchProvider()

    assert isinstance(provider, SearchProvider)
    assert provider.provider_id == "serper"
    assert provider.display_name == "Serper Search"


def test_package_exports_expected_public_surface_and_contract_version() -> None:
    config = SerperSearchProviderConfig()

    assert REGISTRAR.module_id == "search.serper"
    assert REGISTRAR.is_provider_only is True
    assert config.endpoint == ""
    assert config.timeout_s == 0.0
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert is_compatible(CONTRACT_VERSION, PLUGIN_CONTRACT_VERSION) is True
