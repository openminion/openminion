from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openminion.modules.controlplane.runtime.channels import ChannelRegistry
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.modules.controlplane.contracts.models import (
    DeliveryContext,
    InboundMessage,
)
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore
from openminion.modules.controlplane.channels.telegram.access import (
    TelegramAccessPolicy,
)
from openminion.modules.controlplane.channels.telegram.config import AccessConfig
from openminion.modules.controlplane.channels.telegram.models import (
    TelegramInboundEnvelope,
    TelegramUser,
)
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker


class _RecordingAdapter:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id
        self.calls: list[tuple[dict[str, object], DeliveryContext]] = []

    def start(self, stop_event=None) -> None:
        del stop_event

    def deliver(
        self, payload: dict[str, object], ctx: DeliveryContext
    ) -> dict[str, object]:
        self.calls.append((dict(payload), ctx))
        return {"ok": True, "channel": self.channel_id, "outbox_id": ctx.outbox_id}


@dataclass(frozen=True)
class _AccessDecision:
    allowed: bool
    reason: str = "ok"


class _SecondAccessPolicy:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def evaluate(self, inbound, *, bot_username=None):
        del bot_username
        if getattr(inbound, "channel", "") != "second":
            return _AccessDecision(False, "wrong_channel")
        return _AccessDecision(True, "ok")


def _telegram_envelope(*, text: str) -> TelegramInboundEnvelope:
    return TelegramInboundEnvelope(
        update_id=1,
        raw_type="message",
        chat_id=111,
        message_id=10,
        text=text,
        from_user=TelegramUser(id=111, username="alice", display="Alice"),
        chat_type="private",
        raw_update={},
    )


def test_p3a_matrix_routes_outbox_by_channel_for_telegram_and_second(
    tmp_path: Path,
) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    registry = ChannelRegistry()
    telegram = _RecordingAdapter("telegram")
    second = _RecordingAdapter("second")
    registry.register(telegram)
    registry.register(second)

    first = store.enqueue_outbox(
        channel="telegram", chat_id="tg:100", payload={"text": "hello tg"}
    )
    second_id = store.enqueue_outbox(
        channel="second", chat_id="sc:200", payload={"text": "hello second"}
    )
    worker = OutboxWorker(store=store, registry=registry)

    r1 = worker.run_once()
    r2 = worker.run_once()

    assert r1 is not None and r1["status"] == "sent"
    assert r2 is not None and r2["status"] == "sent"
    assert len(telegram.calls) == 1
    assert len(second.calls) == 1
    assert telegram.calls[0][1].channel == "telegram"
    assert telegram.calls[0][1].outbox_id == first
    assert second.calls[0][1].channel == "second"
    assert second.calls[0][1].outbox_id == second_id
    store.close()


def test_p3a_matrix_access_policy_is_pre_auth_and_scope_authorizer_is_post_auth() -> (
    None
):
    telegram_policy = TelegramAccessPolicy(
        access=AccessConfig(dm_policy="allowlist", allow_from_user_ids=[]),
    )
    second_policy = _SecondAccessPolicy()

    tg_decision = telegram_policy.evaluate(
        _telegram_envelope(text="hello"),
        bot_username="bot",
    )
    assert tg_decision.allowed is False
    assert tg_decision.reason == "dm_allowlist_miss"

    second_inbound = InboundMessage(
        user_key="second:user-1",
        chat_key="second:chat-1",
        channel="second",
        text="hello",
    )
    second_decision = second_policy.evaluate(second_inbound, bot_username=None)
    assert second_decision.allowed is True
    assert second_decision.reason == "ok"

    # ScopeAuthorizer remains independent (post-auth) and still resolves auth from pairing store.
    auth_store = InMemoryControlPlaneStore()
    auth = ScopeAuthorizer(store=auth_store).auth_for_inbound(second_inbound)
    assert auth.role == "unpaired"


def test_p3a_matrix_unknown_channel_is_explicit_negative_path(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    registry = ChannelRegistry()
    registry.register(_RecordingAdapter("telegram"))
    outbox_id = store.enqueue_outbox(
        channel="unknown",
        chat_id="unknown:1",
        payload={"text": "should fail"},
    )
    worker = OutboxWorker(
        store=store, registry=registry, max_attempts=2, max_backoff_s=1
    )

    result = worker.run_once()

    assert result is not None
    assert result["status"] in {"retry", "failed"}
    assert result["outbox_id"] == outbox_id
    assert "unknown channel adapter" in str(result["error"])
    store.close()
