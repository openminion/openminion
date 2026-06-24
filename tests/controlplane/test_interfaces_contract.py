from __future__ import annotations

from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ensure_controlplane_component_compatibility,
)
from openminion.modules.controlplane.contracts.models import (
    DeliveryContext,
    InboundMessage,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime import EchoBrain, RuntimeCoordinator
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore
from openminion.modules.controlplane.channels.telegram.events import (
    NoopSessionEventSink,
)


class _OutboundSink:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.payloads.append(payload)


class _NoopRegistry:
    def execute(self, command, ctx):  # pragma: no cover - command path not used
        raise AssertionError("command path should not be used in this test")


class _AccessPolicyStub:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def evaluate(self, inbound, *, bot_username=None):
        del inbound, bot_username
        return {"allowed": True, "reason": "ok"}


class _IdentityAPIStub:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def resolve(self, *, channel: str, subject_id: str) -> str | None:
        del channel, subject_id
        return "principal:stub"

    def bind(
        self,
        *,
        principal_id: str,
        channel: str,
        subject_id: str,
        scopes=None,
        status: str = "active",
        note: str | None = None,
        meta=None,
    ) -> None:
        del principal_id, channel, subject_id, scopes, status, note, meta


def test_controlplane_components_satisfy_contracts() -> None:
    store = InMemoryControlPlaneStore()
    router = Router(store)
    parser = SlashCommandParser()
    brain = EchoBrain()
    sink = _OutboundSink()

    ensure_controlplane_component_compatibility(store, component_type="session_store")
    ensure_controlplane_component_compatibility(router, component_type="router")
    ensure_controlplane_component_compatibility(parser, component_type="command_parser")
    ensure_controlplane_component_compatibility(brain, component_type="brain_client")
    ensure_controlplane_component_compatibility(sink, component_type="outbound_sender")
    ensure_controlplane_component_compatibility(
        _AccessPolicyStub(),
        component_type="access_policy",
    )
    ensure_controlplane_component_compatibility(
        _IdentityAPIStub(),
        component_type="identity_api",
    )
    ensure_controlplane_component_compatibility(
        NoopSessionEventSink(),
        component_type="session_event_sink",
    )


def test_runtime_coordinator_contract_path_runs_chat_turn() -> None:
    store = InMemoryControlPlaneStore()
    runtime = RuntimeCoordinator(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=_NoopRegistry(),
        brain_client=EchoBrain(),
        outbound=_OutboundSink(),
    )
    payload = runtime.handle_inbound(
        InboundMessage(user_key="u1", chat_key="c1", text="hello")
    )
    assert payload["type"] == "chat"
    assert payload["text"]


def test_delivery_context_contract_shape() -> None:
    ctx = DeliveryContext(
        channel="telegram",
        chat_id="telegram:100",
        thread_id="77",
        reply_to="123",
        outbox_id="out-1",
    )
    assert ctx.channel == "telegram"
    assert ctx.chat_id == "telegram:100"
    assert ctx.thread_id == "77"
    assert ctx.reply_to == "123"
    assert ctx.outbox_id == "out-1"
