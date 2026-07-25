from __future__ import annotations

from typing import Any

from .exports import LAZY_EXPORTS, PUBLIC_EXPORTS, resolve_lazy_export

__all__ = PUBLIC_EXPORTS


def __getattr__(name: str) -> Any:  # pragma: no cover
    value = resolve_lazy_export(package_name=__name__, name=name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(list(globals().keys()) + list(LAZY_EXPORTS.keys())))
