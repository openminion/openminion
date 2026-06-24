"""Lazy compatibility exports for the tool package."""

from typing import Any


def build_default_tool_registry(*args: Any, **kwargs: Any):
    from openminion.modules import tool as tool_module

    return tool_module.build_default_tool_registry(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name == "tool":
        from openminion.tools.decorator import tool as _tool

        return _tool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "build_default_tool_registry",
    "tool",
]
