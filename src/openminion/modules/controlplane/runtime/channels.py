from dataclasses import dataclass, field
from typing import Any

from ..interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ChannelAdapterAPI,
    ensure_controlplane_component_compatibility,
)


@dataclass
class ChannelRegistry:
    contract_version: str = field(default=CONTROLPLANE_INTERFACE_VERSION, init=False)
    _adapters: dict[str, ChannelAdapterAPI] = field(default_factory=dict, init=False)

    def register(self, adapter: ChannelAdapterAPI) -> None:
        ensure_controlplane_component_compatibility(
            adapter, component_type="channel_adapter"
        )
        channel_id = str(getattr(adapter, "channel_id", "")).strip()
        if not channel_id:
            raise ValueError("channel_id is required for channel adapter registration")
        if channel_id in self._adapters:
            raise ValueError(f"channel adapter already registered: {channel_id}")
        self._adapters[channel_id] = adapter

    def get(self, channel_id: str) -> ChannelAdapterAPI:
        key = str(channel_id or "").strip()
        if key not in self._adapters:
            raise KeyError(f"unknown channel adapter: {key}")
        return self._adapters[key]

    def list(self) -> list[str]:
        return sorted(self._adapters.keys())

    def start_all(self, stop_event: Any | None = None) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for channel_id in self.list():
            adapter = self._adapters[channel_id]
            try:
                adapter.start(stop_event=stop_event)
                results[channel_id] = {"ok": True}
            except Exception as exc:  # pragma: no cover - exercised by tests
                results[channel_id] = {"ok": False, "error": str(exc)}
        return results

    def stop_all(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for channel_id in self.list():
            adapter = self._adapters[channel_id]
            stop = getattr(adapter, "stop", None)
            if not callable(stop):
                results[channel_id] = {"ok": True, "stopped": False}
                continue
            try:
                stop()
                results[channel_id] = {"ok": True, "stopped": True}
            except Exception as exc:  # pragma: no cover - exercised by tests
                results[channel_id] = {"ok": False, "error": str(exc), "stopped": False}
        return results

    def health(self) -> dict[str, dict[str, Any]]:
        status: dict[str, dict[str, Any]] = {}
        for channel_id in self.list():
            adapter = self._adapters[channel_id]
            probe = getattr(adapter, "health", None)
            if not callable(probe):
                status[channel_id] = {"ok": True, "source": "default"}
                continue
            try:
                raw = probe()
            except Exception as exc:  # pragma: no cover - exercised by tests
                status[channel_id] = {"ok": False, "error": str(exc)}
                continue
            if isinstance(raw, dict):
                status[channel_id] = {"ok": bool(raw.get("ok", True)), **raw}
            else:
                status[channel_id] = {"ok": bool(raw)}
        return status
