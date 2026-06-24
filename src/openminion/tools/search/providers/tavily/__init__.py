"""Public exports for tools search providers tavily."""

from typing import Any

from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

from .registrar import REGISTRAR

REGISTRAR: ToolModuleRegistrar


def register(*args: Any, **kwargs: Any):
    from .plugin import register as register_impl

    return register_impl(*args, **kwargs)


def register_search_provider(*args: Any, **kwargs: Any):
    from .plugin import register_search_provider as register_search_provider_impl

    return register_search_provider_impl(*args, **kwargs)


def __getattr__(name: str):
    if name in {"TavilySearchPlugin", "TavilySearchProvider"}:
        from .plugin import TavilySearchPlugin, TavilySearchProvider

        exports = {
            "TavilySearchPlugin": TavilySearchPlugin,
            "TavilySearchProvider": TavilySearchProvider,
        }
        return exports[name]
    if name == "TavilySearchTool":
        from .search import TavilySearchTool

        return TavilySearchTool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "REGISTRAR",
    "TavilySearchPlugin",
    "TavilySearchProvider",
    "TavilySearchTool",
    "register",
    "register_search_provider",
]
