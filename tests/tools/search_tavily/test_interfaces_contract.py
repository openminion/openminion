import pytest

from openminion.modules.tool import PLUGIN_CONTRACT_VERSION
from openminion.tools.search.providers.tavily.interfaces import (
    CONTRACT_VERSION,
    TavilyErrorEnvelope,
    TavilyRequestEnvelope,
    TavilyResultEnvelope,
    validate_contract_version,
)
from openminion.tools.search.providers.tavily.plugin import TavilySearchPlugin


def test_tavily_contract_baseline() -> None:
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert validate_contract_version(CONTRACT_VERSION) is True


def test_tavily_plugin_declares_contract_version() -> None:
    plugin = TavilySearchPlugin()
    assert plugin.contract_version == CONTRACT_VERSION


def test_tavily_envelopes_validate_contract_version() -> None:
    req = TavilyRequestEnvelope(
        method="search.tavily.search", args={"query": "openminion"}
    )
    out = TavilyResultEnvelope(status="ok", data={"result_count": 1})
    err = TavilyErrorEnvelope(error_code="UPSTREAM_ERROR", error_message="failed")
    assert req.contract_version == CONTRACT_VERSION
    assert out.contract_version == CONTRACT_VERSION
    assert err.contract_version == CONTRACT_VERSION

    with pytest.raises(ValueError):
        TavilyResultEnvelope(status="ok", data={}, contract_version="bad")
