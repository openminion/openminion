from typing import TypedDict

TIME_PLUGIN_INTERFACE_VERSION = "v1"


class TimeInstant(TypedDict, total=False):
    utc: str
    unix_seconds: int
    unix_millis: int
    timezone: str
    local: str
    offset_seconds: int


__all__ = [
    "TIME_PLUGIN_INTERFACE_VERSION",
    "TimeInstant",
]
