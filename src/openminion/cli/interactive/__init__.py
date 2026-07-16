from importlib import import_module
from typing import Any

__all__ = ["FocusApp"]

_LAZY_SUBMODULES = frozenset({"runtime", "terminal"})


def __getattr__(name: str) -> Any:
    if name == "FocusApp":
        return getattr(import_module(".app", __name__), name)
    if name in _LAZY_SUBMODULES:
        return import_module(f".{name}", __name__)
    raise AttributeError(name)
