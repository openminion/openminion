from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.services.runtime.plugins.registry import Plugin


def plugin_label(plugin: "Plugin") -> str:
    module = getattr(plugin, "__module__", None)
    name = getattr(plugin, "name", None)
    if module:
        return module
    if name:
        return str(name)
    return type(plugin).__name__


__all__ = ["plugin_label"]
