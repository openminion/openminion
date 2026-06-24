from __future__ import annotations

from typing import Any

from openminion.modules.tool.constants import TOOL_SOURCE_MODULE_FIRST
from openminion.services.diagnostics.debug import (
    DebugProvider,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
)


def _tool_runtime_failure_payload(
    module: str, exc: BaseException
) -> ModuleDebugPayload:
    return ModuleDebugPayload(
        module=module,
        status=DebugStatus.FAIL,
        mode="runtime",
        wiring_source=WiringSource.UNKNOWN,
        last_error=str(exc),
    )


def _tool_import_error_payload(
    module: str,
    exc: BaseException,
    *,
    fallback: str,
    details: dict[str, Any],
) -> ModuleDebugPayload:
    return ModuleDebugPayload(
        module=module,
        status=DebugStatus.WARN,
        mode="runtime",
        wiring_source=WiringSource.DISABLED,
        fallback=fallback,
        last_error=str(exc),
        details=details,
    )


class _ToolDebugProvider(DebugProvider):
    MODULE_NAME: str = ""

    def __init__(self) -> None:
        super().__init__(
            module_name=self.MODULE_NAME,
            probe_fn=self._probe,
            wiring_check_fn=None,
        )


def _module_first_registry_payload(
    *,
    module: str,
    source_value: str,
    source_key: str,
    canonical_tool_name: str,
    missing_fallback: str,
    legacy_disabled_fallback: str,
) -> ModuleDebugPayload:
    from openminion.modules.tool import build_default_tool_registry

    registry = build_default_tool_registry()
    has_module = canonical_tool_name in registry._tools
    details = {
        source_key: source_value,
        "canonical_registered": has_module,
    }
    if source_value == TOOL_SOURCE_MODULE_FIRST and has_module:
        status = DebugStatus.OK
        wiring = WiringSource.REAL
        fallback_reason = None
    elif source_value == TOOL_SOURCE_MODULE_FIRST:
        status = DebugStatus.WARN
        wiring = WiringSource.STUB
        fallback_reason = missing_fallback
    else:
        status = DebugStatus.FAIL
        wiring = WiringSource.UNKNOWN
        fallback_reason = legacy_disabled_fallback
    return ModuleDebugPayload(
        module=module,
        status=status,
        mode="runtime",
        wiring_source=wiring,
        fallback=fallback_reason,
        details=details,
    )


class OpenMinionWeatherDebugProvider(_ToolDebugProvider):
    MODULE_NAME = "openminion-tool-weather-openmeteo"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.tool import (
                _WEATHER_SOURCE,
            )

            return _module_first_registry_payload(
                module=self.MODULE_NAME,
                source_value=_WEATHER_SOURCE,
                source_key="weather_source",
                canonical_tool_name="weather.openmeteo.current",
                missing_fallback="Module-first enabled but openminion-tool-weather-openmeteo not installed",
                legacy_disabled_fallback="Legacy weather source is disabled",
            )
        except Exception as exc:
            return _tool_runtime_failure_payload(self.MODULE_NAME, exc)


class OpenMinionTavilyDebugProvider(_ToolDebugProvider):
    MODULE_NAME = "openminion-tool-search-tavily"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.tool import (
                _TAVILY_SOURCE,
            )

            return _module_first_registry_payload(
                module=self.MODULE_NAME,
                source_value=_TAVILY_SOURCE,
                source_key="tavily_source",
                canonical_tool_name="search.tavily.search",
                missing_fallback="Module-first enabled but openminion-tool-search-tavily not installed",
                legacy_disabled_fallback="Legacy Tavily source is disabled",
            )
        except Exception as exc:
            return _tool_runtime_failure_payload(self.MODULE_NAME, exc)


class OpenMinionReactionsDebugProvider(_ToolDebugProvider):
    MODULE_NAME = "openminion-tool-reactions"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.tools.reaction.plugin import TOOL_DESCRIPTOR

            plugin_installed = True
            tools = TOOL_DESCRIPTOR.get("methods", [])
            version = TOOL_DESCRIPTOR.get("version", "unknown")
            capabilities = TOOL_DESCRIPTOR.get("capabilities", [])

            return ModuleDebugPayload(
                module=self.MODULE_NAME,
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                details={
                    "import_ok": True,
                    "plugin_installed": plugin_installed,
                    "version": version,
                    "tools": tools,
                    "capabilities": capabilities,
                    "tool_count": len(tools),
                },
            )

        except ImportError as exc:
            return _tool_import_error_payload(
                self.MODULE_NAME,
                exc,
                fallback="openminion-tool-reactions module not installed",
                details={
                    "import_ok": False,
                    "plugin_installed": False,
                    "reason": "Module not installed or not in PYTHONPATH",
                },
            )
        except Exception as exc:
            return _tool_runtime_failure_payload(self.MODULE_NAME, exc)


def build_playwright_debug_provider() -> DebugProvider | None:
    try:
        from openminion.tools.browser.providers.playwright.debug_provider import (
            get_browser_playwright_debug_info,
        )
    except ImportError:
        return None

    class OpenMinionPlaywrightDebugProvider(_ToolDebugProvider):
        MODULE_NAME = "openminion.tools.browser.providers.playwright"

        def _probe(self) -> ModuleDebugPayload:
            try:
                info = get_browser_playwright_debug_info()
                status = (
                    DebugStatus.OK
                    if info.get("status") == "available"
                    else DebugStatus.WARN
                )
                return ModuleDebugPayload(
                    module=self.MODULE_NAME,
                    status=status,
                    mode="runtime",
                    wiring_source=WiringSource.REAL
                    if info.get("provider_available")
                    else WiringSource.DISABLED,
                    details=info,
                )
            except ImportError as exc:
                return _tool_import_error_payload(
                    self.MODULE_NAME,
                    exc,
                    fallback="openminion.tools.browser.providers.playwright module not installed",
                    details={"import_ok": False},
                )
            except Exception as exc:
                return _tool_runtime_failure_payload(self.MODULE_NAME, exc)

    return OpenMinionPlaywrightDebugProvider()
