import pytest

from openminion.modules.tool import PLUGIN_CONTRACT_VERSION
from openminion.tools.reaction.interfaces import (
    CONTRACT_VERSION,
    ReactionsErrorEnvelope,
    ReactionsRequestEnvelope,
    ReactionsResultEnvelope,
    validate_contract_version,
)
from openminion.tools.reaction.plugin import ReactionsPlugin


def test_reactions_contract_baseline() -> None:
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert validate_contract_version(CONTRACT_VERSION) is True


def test_reactions_plugin_declares_contract_version() -> None:
    plugin = ReactionsPlugin()
    assert plugin.contract_version == CONTRACT_VERSION


def test_reactions_envelopes_validate_contract_version() -> None:
    req = ReactionsRequestEnvelope(method="reactions.list", args={})
    out = ReactionsResultEnvelope(status="ok", data={"reactions": []})
    err = ReactionsErrorEnvelope(error_code="POLICY_DENIED", error_message="blocked")
    assert req.contract_version == CONTRACT_VERSION
    assert out.contract_version == CONTRACT_VERSION
    assert err.contract_version == CONTRACT_VERSION

    with pytest.raises(ValueError):
        ReactionsRequestEnvelope(
            method="reactions.list",
            args={},
            contract_version="invalid",
        )
