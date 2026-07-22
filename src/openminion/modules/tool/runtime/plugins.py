import inspect
from importlib.metadata import EntryPoint, entry_points
from typing import Any
from collections.abc import Iterable

from ..interfaces import validate_plugin_contract
from ..registry import ToolRegistry


def _plugin_entry_points() -> list[EntryPoint]:
    try:
        eps = entry_points(group="openminion.modules.tool.runtime.plugins")
        return sorted(eps, key=lambda ep: ep.name)
    except TypeError:
        all_eps = entry_points()
        fallback_eps = all_eps.get("openminion.modules.tool.runtime.plugins", [])
        return sorted(fallback_eps, key=lambda ep: ep.name)


def load_plugins(registry: ToolRegistry, policy: Any) -> list[dict[str, Any]]:
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
            plugin = loaded() if inspect.isclass(loaded) else loaded

            # Enforce plugin contract for class-based plugin instances.
            if inspect.isclass(loaded):
                validate_plugin_contract(plugin)

            manifest = getattr(plugin, "TOOL_MANIFEST", None)
            if manifest is None:
                manifest = getattr(loaded, "TOOL_MANIFEST", None)
            if manifest is not None:
                if not isinstance(manifest, Iterable) or isinstance(
                    manifest, (str, bytes, bytearray)
                ):
                    raise TypeError(  # allow-bare-raise: defensive type guard on plugin TOOL_MANIFEST
                        f"Plugin '{ep.name}' has invalid TOOL_MANIFEST type"
                    )
                status["manifest_count"] = len(list(manifest))

            register = getattr(plugin, "register", None)
            if register is None and hasattr(loaded, "register"):
                register = getattr(loaded, "register")
            if register is None or not callable(register):
                raise TypeError(  # allow-bare-raise: defensive type guard on plugin register attribute
                    f"Plugin '{ep.name}' must expose callable register(registry)"
                )

            register(registry)
            status["loaded"] = True

            health = {"ok": True}
            if hasattr(plugin, "healthcheck"):
                maybe_health = plugin.healthcheck()
                if isinstance(maybe_health, dict):
                    health = maybe_health
            status["healthy"] = bool(health.get("ok", True))
            status["health"] = health
        except Exception as exc:
            status["loaded"] = False
            status["healthy"] = False
            status["error"] = f"{type(exc).__name__}: {exc}"
        statuses.append(status)

    if not statuses:
        statuses.append(
            {
                "name": "openminion_tool",
                "module": "openminion_tool",
                "installed": True,
                "enabled": True,
                "loaded": True,
                "healthy": True,
            }
        )

    return statuses
