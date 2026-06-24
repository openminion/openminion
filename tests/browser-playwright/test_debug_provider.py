from __future__ import annotations

import builtins

import pytest

from openminion.tools.browser.providers.playwright.debug_provider import (
    FAULT_CLASS_DEPENDENCY_MISSING,
    FAULT_CLASS_POLICY_DENIED,
    FAULT_CLASS_ROUTING_MISCONFIG,
    PlaywrightDebugProvider,
    get_browser_playwright_debug_info,
)


def test_debug_available_when_provider_registered() -> None:
    info = get_browser_playwright_debug_info(
        {"registered_providers": ["pinchtab", "playwright"]}
    )
    assert info["status"] == "available"
    assert info["fault_class"] == ""
    assert info["hint"] == ""
    assert info["playwright_installed"] is True
    assert info["provider_available"] is True


def test_debug_routing_misconfig_when_provider_missing_from_registry() -> None:
    info = get_browser_playwright_debug_info({"registered_providers": ["pinchtab"]})
    assert info["status"] == "unavailable"
    assert info["fault_class"] == FAULT_CLASS_ROUTING_MISCONFIG
    assert "BrowserProviderRegistry" in info["hint"]
    # Importability remains true — this is a wiring problem, not a dep one.
    assert info["playwright_installed"] is True
    assert info["provider_available"] is True


def test_debug_policy_denied_takes_priority_over_available() -> None:
    info = get_browser_playwright_debug_info(
        {
            "registered_providers": ["playwright", "pinchtab"],
            "policy_denied": True,
            "policy_reason": "browser tool disabled by profile policy",
        }
    )
    assert info["status"] == "unavailable"
    assert info["fault_class"] == FAULT_CLASS_POLICY_DENIED
    assert info["policy_reason"] == "browser tool disabled by profile policy"
    assert "policy" in info["hint"]


def test_debug_routing_misconfig_precedes_policy_denied() -> None:
    info = get_browser_playwright_debug_info(
        {"registered_providers": ["pinchtab"], "policy_denied": True}
    )
    assert info["fault_class"] == FAULT_CLASS_ROUTING_MISCONFIG


def test_debug_dependency_missing_when_playwright_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    info = PlaywrightDebugProvider().get_debug_info()
    assert info["status"] == "unavailable"
    assert info["fault_class"] == FAULT_CLASS_DEPENDENCY_MISSING
    assert info["playwright_installed"] is False
    assert info["provider_available"] is False
    assert "pip install playwright" in info["hint"]
