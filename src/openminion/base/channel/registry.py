from __future__ import annotations

from typing import Any, Iterable
from .console import ConsoleChannel


class ChannelRegistry:
    def __init__(self, channels: Iterable[Any] = ()) -> None:
        self._channels: dict[str, Any] = {}
        for channel in channels:
            self.register(channel)

    def register(self, channel: Any) -> None:
        self._channels[_channel_name(channel)] = channel

    def get(self, name: str) -> Any:
        if name not in self._channels:
            available = ", ".join(sorted(self._channels)) or "none"
            raise KeyError(f"Unknown channel '{name}'. Available channels: {available}")
        return self._channels[name]

    def names(self) -> list[str]:
        return sorted(self._channels)


def build_default_channel_registry() -> ChannelRegistry:
    return ChannelRegistry([ConsoleChannel()])


def _channel_name(channel: Any) -> str:
    name = str(
        getattr(channel, "name", "") or getattr(channel, "channel_id", "")
    ).strip()
    if not name:
        raise ValueError("channel objects must define `name` or `channel_id`")
    return name
