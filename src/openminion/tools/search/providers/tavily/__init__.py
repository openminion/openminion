from typing import Any

from .registrar import REGISTRAR


def register(*args: Any, **kwargs: Any) -> None:
    from .plugin import register as register_impl

    return register_impl(*args, **kwargs)


def register_search_provider(*args: Any, **kwargs: Any) -> None:
    from .plugin import register_search_provider as register_search_provider_impl

    return register_search_provider_impl(*args, **kwargs)


def __getattr__(name: str) -> Any:
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
