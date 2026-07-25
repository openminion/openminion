from __future__ import annotations

from importlib import import_module
from typing import Any

from .exports import PUBLIC_EXPORTS

_EXPORT_MODULES = (
    "active",
    "budgets",
    "build",
    "common",
    "evidence",
    "manifest",
    "memory",
    "segments",
    "session",
)

__all__ = PUBLIC_EXPORTS


def __getattr__(name: str) -> Any:
    if name not in PUBLIC_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    for module_name in _EXPORT_MODULES:
        module = import_module(f"{__name__}.{module_name}")
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no exported attribute {name!r}")
