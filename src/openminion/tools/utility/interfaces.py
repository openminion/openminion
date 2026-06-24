from typing import TypedDict

UTILITY_PLUGIN_INTERFACE_VERSION = "v1"


class UtilityResult(TypedDict, total=False):
    ok: bool
    error: str


__all__ = [
    "UTILITY_PLUGIN_INTERFACE_VERSION",
    "UtilityResult",
]
