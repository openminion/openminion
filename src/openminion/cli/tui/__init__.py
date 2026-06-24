from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "OpenMinionApp",
    "DemoRuntime",
    "run_demo",
    "presentation",
    "screen",
    "tabs",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    if name in {"presentation", "screen", "tabs"}:
        return import_module(f".{name}", __name__)
    module = import_module(".app", __name__)
    return getattr(module, name)
