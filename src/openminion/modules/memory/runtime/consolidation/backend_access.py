"""Memory backend access helper for consolidation flows."""

from typing import Any


def memory_backend(memory_api: Any) -> Any | None:
    if memory_api is None:
        return None
    for attr_name in ("_backend", "store"):
        value = getattr(memory_api, attr_name, None)
        if value is not None:
            return value
    return memory_api


__all__ = ["memory_backend"]
