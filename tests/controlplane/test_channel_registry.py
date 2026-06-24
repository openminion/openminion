from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openminion.modules.controlplane.runtime.channels import ChannelRegistry
from openminion.modules.controlplane.interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ensure_controlplane_component_compatibility,
)
from openminion.modules.controlplane.contracts.models import DeliveryContext


@dataclass
class _AdapterStub:
    channel_id: str
    contract_version: str = CONTROLPLANE_INTERFACE_VERSION
    started: bool = False
    stopped: bool = False
    delivered: list[tuple[dict[str, Any], DeliveryContext]] = field(
        default_factory=list
    )

    def start(self, stop_event: Any | None = None) -> None:
        del stop_event
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def deliver(self, payload: dict[str, Any], ctx: DeliveryContext) -> dict[str, Any]:
        self.delivered.append((dict(payload), ctx))
        return {"ok": True}

    def health(self) -> dict[str, Any]:
        return {"ok": True, "channel": self.channel_id}


def test_channel_registry_contract_surface() -> None:
    registry = ChannelRegistry()
    ensure_controlplane_component_compatibility(
        registry,
        component_type="channel_registry",
    )


def test_channel_registry_register_get_list() -> None:
    registry = ChannelRegistry()
    tg = _AdapterStub(channel_id="telegram")
    cli = _AdapterStub(channel_id="cli")
    registry.register(tg)
    registry.register(cli)

    assert registry.list() == ["cli", "telegram"]
    assert registry.get("telegram") is tg
    assert registry.get("cli") is cli


def test_channel_registry_duplicate_registration_fails_deterministically() -> None:
    registry = ChannelRegistry()
    registry.register(_AdapterStub(channel_id="telegram"))
    with pytest.raises(ValueError, match="already registered: telegram"):
        registry.register(_AdapterStub(channel_id="telegram"))


def test_channel_registry_start_stop_health() -> None:
    registry = ChannelRegistry()
    tg = _AdapterStub(channel_id="telegram")
    registry.register(tg)

    start_result = registry.start_all()
    assert start_result["telegram"]["ok"] is True
    assert tg.started is True

    health = registry.health()
    assert health["telegram"]["ok"] is True
    assert health["telegram"]["channel"] == "telegram"

    stop_result = registry.stop_all()
    assert stop_result["telegram"]["ok"] is True
    assert stop_result["telegram"]["stopped"] is True
    assert tg.stopped is True
