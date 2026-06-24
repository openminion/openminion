"""Playwright provider diagnostics."""

import importlib
from typing import Any


FAULT_CLASS_DEPENDENCY_MISSING = "dependency_missing"
FAULT_CLASS_ROUTING_MISCONFIG = "routing_misconfig"
FAULT_CLASS_POLICY_DENIED = "policy_denied"

_HINT_DEPENDENCY_MISSING = (
    "playwright not installed - run: pip install playwright && playwright install"
)
_HINT_PROVIDER_IMPORT_FAILED = (
    "playwright python package importable but provider module failed to load - "
    "verify openminion.tools.browser.providers.playwright is on PYTHONPATH"
)
_HINT_ROUTING_MISCONFIG = (
    "playwright provider not present in BrowserProviderRegistry - "
    "verify discovery ran and `provider_order`/`default_provider` is reachable"
)
_HINT_POLICY_DENIED = (
    "playwright provider is registered but disabled by policy - "
    "review tool policy/grants for the browser family"
)


class PlaywrightDebugProvider:
    def get_debug_info(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = dict(config or {})
        info: dict[str, Any] = {
            "module": "openminion.tools.browser.providers.playwright",
            "status": "available",
            "fault_class": "",
            "hint": "",
        }

        try:
            import playwright

            info["playwright_installed"] = True
            info["playwright_version"] = getattr(playwright, "__version__", "unknown")
        except ImportError as exc:
            info["playwright_installed"] = False
            info["playwright_version"] = None
            info["provider_available"] = False
            info["status"] = "unavailable"
            info["fault_class"] = FAULT_CLASS_DEPENDENCY_MISSING
            info["hint"] = _HINT_DEPENDENCY_MISSING
            info["error"] = str(exc) or _HINT_DEPENDENCY_MISSING
            return info

        try:
            importlib.import_module(
                "openminion.tools.browser.providers.playwright.provider"
            )
            info["provider_available"] = True
        except ImportError as exc:
            info["provider_available"] = False
            info["status"] = "unavailable"
            info["fault_class"] = FAULT_CLASS_DEPENDENCY_MISSING
            info["hint"] = _HINT_PROVIDER_IMPORT_FAILED
            info["error"] = str(exc) or _HINT_PROVIDER_IMPORT_FAILED
            return info

        registered_providers = config.get("registered_providers")
        if registered_providers is not None:
            registered = tuple(str(p).strip() for p in registered_providers)
            info["registered_providers"] = list(registered)
            if "playwright" not in registered:
                info["status"] = "unavailable"
                info["fault_class"] = FAULT_CLASS_ROUTING_MISCONFIG
                info["hint"] = _HINT_ROUTING_MISCONFIG
                return info

        if bool(config.get("policy_denied")):
            info["status"] = "unavailable"
            info["fault_class"] = FAULT_CLASS_POLICY_DENIED
            info["hint"] = _HINT_POLICY_DENIED
            reason = str(config.get("policy_reason") or "").strip()
            if reason:
                info["policy_reason"] = reason
            return info

        return info


def get_browser_playwright_debug_info(
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = PlaywrightDebugProvider()
    return provider.get_debug_info(config)
