"""Postprocess helpers for adaptive tool-loop execution."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AdaptiveLoopRunnerPostprocessMixin",
    "finalize_iteration_state",
]

_LAZY_EXPORTS = {
    "AdaptiveLoopRunnerPostprocessMixin": (
        ".engine",
        "AdaptiveLoopRunnerPostprocessMixin",
    ),
    "finalize_iteration_state": (".loop", "finalize_iteration_state"),
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    target = _LAZY_EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr_name = target
    module = __import__(__name__ + module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(list(globals().keys()) + list(_LAZY_EXPORTS.keys())))
