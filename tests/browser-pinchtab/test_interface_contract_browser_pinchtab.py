import pytest

from openminion.modules.tool import PLUGIN_CONTRACT_VERSION
from openminion.tools.browser.providers.pinchtab.interfaces import (
    CONTRACT_VERSION,
    PinchTabErrorEnvelope,
    PinchTabRequestEnvelope,
    PinchTabResultEnvelope,
    validate_contract_version,
)
from openminion.tools.browser.providers.pinchtab.plugin import PinchTabPlugin


def test_pinchtab_contract_baseline() -> None:
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert validate_contract_version(CONTRACT_VERSION) is True


def test_pinchtab_plugin_declares_contract_version() -> None:
    plugin = PinchTabPlugin()
    assert plugin.contract_version == CONTRACT_VERSION


def test_pinchtab_envelopes_validate_contract_version() -> None:
    req = PinchTabRequestEnvelope(method="browser.pinchtab.health", args={})
    out = PinchTabResultEnvelope(status="ok", data={"ok": True})
    err = PinchTabErrorEnvelope(error_code="EXEC_ERROR", error_message="failed")
    assert req.contract_version == CONTRACT_VERSION
    assert out.contract_version == CONTRACT_VERSION
    assert err.contract_version == CONTRACT_VERSION

    with pytest.raises(ValueError):
        PinchTabResultEnvelope(status="ok", data={}, contract_version="broken-version")
