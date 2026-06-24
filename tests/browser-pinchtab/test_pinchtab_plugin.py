from __future__ import annotations

from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.browser import BrowserProviderRegistry
from openminion.tools.browser.providers.pinchtab.plugin import (
    PinchTabPlugin,
    register,
    register_browser_provider,
)


def test_register_browser_provider_registers_pinchtab():
    registry = BrowserProviderRegistry()
    register_browser_provider(registry)
    assert "pinchtab" in registry.list_provider_ids()


def test_compat_plugin_register_accepts_tool_registry():
    registry = ToolRegistry()
    PinchTabPlugin().register(registry)


def test_register_accepts_browser_provider_registry():
    registry = BrowserProviderRegistry()
    register(registry)
    assert "pinchtab" in registry.list_provider_ids()
