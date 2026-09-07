from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .registrar import ToolModuleRegistrar


class PluginRegistrarDiscoveryError(RuntimeError):
    """An enabled legacy tool plugin could not supply its registrar."""

    def __init__(self, *, plugin_id: str, reason_code: str) -> None:
        super().__init__("Plugin registrar discovery failed.")
        self.plugin_id = plugin_id
        self.reason_code = reason_code


def _plugin_entry_points() -> list[EntryPoint]:
    try:
        eps = entry_points(group="openminion.modules.tool.runtime.plugins")
        return sorted(eps, key=lambda ep: ep.name)
    except TypeError:
        all_eps = entry_points()
        fallback_eps = all_eps.get("openminion.modules.tool.runtime.plugins", [])
        return sorted(fallback_eps, key=lambda ep: ep.name)


def discover_plugin_registrars(
    policy: Any,
) -> tuple[list[tuple[str, ToolModuleRegistrar]], list[dict[str, Any]]]:
    registrars: list[tuple[str, ToolModuleRegistrar]] = []
    statuses: list[dict[str, Any]] = []
    for ep in _plugin_entry_points():
        status: dict[str, Any] = {
            "name": ep.name,
            "module": ep.module,
            "installed": True,
            "enabled": policy.is_plugin_enabled(ep.name),
            "loaded": False,
            "healthy": None,
        }
        if not status["enabled"]:
            statuses.append(status)
            continue
        try:
            loaded = ep.load()
        except Exception as exc:
            raise PluginRegistrarDiscoveryError(
                plugin_id=ep.name,
                reason_code="registration_failed",
            ) from exc
        registrar = getattr(loaded, "REGISTRAR", loaded)
        if not isinstance(registrar, ToolModuleRegistrar):
            cause = TypeError(f"Plugin '{ep.name}' must expose a ToolModuleRegistrar")
            raise PluginRegistrarDiscoveryError(
                plugin_id=ep.name,
                reason_code="registrar_invalid",
            ) from cause
        registrars.append((ep.name, registrar))
        status["loaded"] = True
        status["healthy"] = True
        statuses.append(status)
    return registrars, statuses


__all__ = ["PluginRegistrarDiscoveryError", "discover_plugin_registrars"]
