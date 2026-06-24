from __future__ import annotations

from openminion.tools.fetch.providers.tinyfish import REGISTRAR, TinyFishFetchProvider
from openminion.tools.fetch.providers.tinyfish.interfaces import (
    FETCH_TINYFISH_PLUGIN_INTERFACE_VERSION,
)


def test_tinyfish_fetch_provider_identity_and_contract() -> None:
    provider = TinyFishFetchProvider()

    assert provider.name == "tinyfish"
    assert "markdown" in provider.capabilities["formats"]
    assert REGISTRAR.module_id == "fetch_tinyfish"
    assert REGISTRAR.is_provider_only is True
    assert FETCH_TINYFISH_PLUGIN_INTERFACE_VERSION == "v1"
