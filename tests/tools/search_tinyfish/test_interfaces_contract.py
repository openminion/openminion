from __future__ import annotations

from openminion.modules.tool import PLUGIN_CONTRACT_VERSION
from openminion.tools.search.providers import SearchProvider
from openminion.tools.search.providers.tinyfish import (
    REGISTRAR,
    TinyFishSearchProvider,
    TinyFishSearchProviderConfig,
)
from openminion.tools.search.providers.tinyfish.interfaces import (
    CONTRACT_VERSION,
    is_compatible,
)


def test_tinyfish_provider_satisfies_search_provider_protocol() -> None:
    provider = TinyFishSearchProvider()

    assert isinstance(provider, SearchProvider)
    assert provider.provider_id == "tinyfish"
    assert provider.display_name == "TinyFish Search"


def test_package_exports_expected_public_surface_and_contract_version() -> None:
    config = TinyFishSearchProviderConfig()

    assert REGISTRAR.module_id == "search.tinyfish"
    assert REGISTRAR.is_provider_only is True
    assert config.endpoint == ""
    assert config.timeout_s == 0.0
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert is_compatible(CONTRACT_VERSION, PLUGIN_CONTRACT_VERSION) is True
