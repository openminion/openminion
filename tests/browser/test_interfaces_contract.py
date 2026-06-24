from __future__ import annotations

from openminion.tools.browser.models import BrowserOp, SUPPORTED_OPS


def test_browser_contract_includes_instance_lifecycle_ops() -> None:
    assert BrowserOp.INSTANCE_START.value in SUPPORTED_OPS
    assert BrowserOp.INSTANCE_LIST.value in SUPPORTED_OPS
    assert BrowserOp.INSTANCE_STOP.value in SUPPORTED_OPS
    assert BrowserOp.INSTANCE_KILL.value in SUPPORTED_OPS
