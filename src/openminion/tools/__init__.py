"""Lazy public tool and local-plugin authoring exports."""

from importlib import import_module
from typing import Any

_PLUGIN_AUTHOR_EXPORTS = {
    "PLUGIN_MANIFEST_CONTRACT_VERSION": "openminion.services.runtime.plugins.manifests",
    "Plugin": "openminion.services.runtime.plugins.hooks",
    "PluginContext": "openminion.services.runtime.plugins.hooks",
    "ToolDecl": "openminion.modules.tool.framework",
    "ToolDependencyDecl": "openminion.modules.tool.contracts.dependencies",
    "ToolDependencyProbeContext": "openminion.modules.tool.contracts.dependencies",
    "ToolDependencySetupHint": "openminion.modules.tool.contracts.dependencies",
    "ToolDependencyStatus": "openminion.modules.tool.contracts.dependencies",
    "ToolFamilySpec": "openminion.modules.tool.framework",
    "build_registrar": "openminion.modules.tool.framework",
}


def build_default_tool_registry(*args: Any, **kwargs: Any):
    from openminion.modules import tool as tool_module

    return tool_module.build_default_tool_registry(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name == "tool":
        from openminion.tools.decorator import tool as _tool

        return _tool
    module_name = _PLUGIN_AUTHOR_EXPORTS.get(name)
    if module_name is not None:
        return getattr(import_module(module_name), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    *_PLUGIN_AUTHOR_EXPORTS,
    "build_default_tool_registry",
    "tool",
]
