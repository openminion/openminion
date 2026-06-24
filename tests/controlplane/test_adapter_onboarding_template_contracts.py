from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openminion.modules.controlplane.runtime.channels import ChannelRegistry
from openminion.modules.controlplane.interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ensure_controlplane_component_compatibility,
)
from openminion.modules.controlplane.contracts.models import DeliveryContext
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker


@dataclass(frozen=True)
class _AccessDecision:
    allowed: bool
    reason: str = "ok"


class _SecondChannelAccessPolicy:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def evaluate(self, inbound, *, bot_username=None):
        del bot_username
        if getattr(inbound, "channel", "") != "second":
            return _AccessDecision(False, "channel_mismatch")
        return _AccessDecision(True, "ok")


class _SecondChannelAdapter:
    contract_version = CONTROLPLANE_INTERFACE_VERSION
    channel_id = "second"

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], DeliveryContext]] = []

    def start(self, stop_event=None) -> None:
        del stop_event

    def deliver(
        self, payload: dict[str, object], ctx: DeliveryContext
    ) -> dict[str, object]:
        self.calls.append((dict(payload), ctx))
        return {
            "ok": True,
            "channel": ctx.channel,
            "chat_id": ctx.chat_id,
            "outbox_id": ctx.outbox_id,
        }


def test_onboarding_template_contracts_match_controlplane_interfaces() -> None:
    policy = _SecondChannelAccessPolicy()
    adapter = _SecondChannelAdapter()
    registry = ChannelRegistry()

    ensure_controlplane_component_compatibility(policy, component_type="access_policy")
    ensure_controlplane_component_compatibility(
        adapter, component_type="channel_adapter"
    )

    registry.register(adapter)
    ensure_controlplane_component_compatibility(
        registry, component_type="channel_registry"
    )
    assert registry.list() == ["second"]


def test_onboarding_template_registry_path_routes_outbox_delivery(
    tmp_path: Path,
) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    adapter = _SecondChannelAdapter()
    registry = ChannelRegistry()
    registry.register(adapter)
    outbox_id = store.enqueue_outbox(
        channel="second",
        chat_id="second:chat-1",
        payload={"type": "chat", "text": "hello"},
    )

    worker = OutboxWorker(store=store, registry=registry)
    result = worker.run_once()

    assert result is not None
    assert result["status"] == "sent"
    assert result["outbox_id"] == outbox_id
    assert len(adapter.calls) == 1
    payload, ctx = adapter.calls[0]
    assert payload["text"] == "hello"
    assert ctx.channel == "second"
    assert ctx.chat_id == "second:chat-1"
    assert ctx.outbox_id == outbox_id
    store.close()
