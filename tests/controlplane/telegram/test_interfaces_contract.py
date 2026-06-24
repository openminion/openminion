from __future__ import annotations

from openminion.modules.controlplane.interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ensure_controlplane_component_compatibility,
)
from openminion.modules.controlplane.contracts.models import DeliveryContext
from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
from openminion.modules.controlplane.channels.telegram.config import (
    DeliveryConfig,
    ReplyConfig,
    TelegramChannelConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.interfaces import (
    TELEGRAM_INTERFACE_VERSION,
    ensure_telegram_component_compatibility,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)


class _RuntimeStub:
    contract_version = TELEGRAM_INTERFACE_VERSION

    def handle_inbound(self, inbound):
        return {"type": "chat", "text": "ok", "session_id": "s1", "agent_id": "a1"}


class _ControlplaneSessionSink:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(self) -> None:
        self.inbound = []
        self.outbound = []

    def record_inbound(self, event, raw_update: dict) -> None:
        self.inbound.append((event, raw_update))

    def record_outbound(self, **kwargs) -> None:
        self.outbound.append(dict(kwargs))


def _fake_http_post(url: str, payload: dict, timeout: float) -> dict:
    del url, payload, timeout
    return {"ok": True, "result": {"id": 1, "username": "bot"}}


def _build_channel_stack(tmp_path, http_post):
    cfg = TelegramChannelConfig(enabled=True, bot_token="token")
    api = TelegramBotAPI("token", http_post=http_post)
    delivery = TelegramDeliveryService(
        api=api,
        delivery_config=DeliveryConfig(),
        reply_config=ReplyConfig(),
    )
    state_store = TelegramPollStateStore(str(tmp_path / "poll-state.db"))
    runtime = _RuntimeStub()
    sink = _ControlplaneSessionSink()
    polling = TelegramPollingRunner(
        config=cfg,
        api=api,
        runtime=runtime,
        delivery=delivery,
        state_store=state_store,
        session_sink=sink,
    )
    webhook = TelegramWebhookRunner(
        config=cfg,
        api=api,
        runtime=runtime,
        delivery=delivery,
        state_store=state_store,
        session_sink=sink,
    )
    return api, delivery, state_store, runtime, sink, polling, webhook


def test_telegram_components_satisfy_contracts(tmp_path) -> None:
    api, delivery, state_store, runtime, _sink, polling, webhook = _build_channel_stack(
        tmp_path, _fake_http_post
    )

    ensure_telegram_component_compatibility(api, component_type="bot_api")
    ensure_telegram_component_compatibility(delivery, component_type="delivery_service")
    ensure_telegram_component_compatibility(state_store, component_type="state_store")
    ensure_telegram_component_compatibility(runtime, component_type="runtime_handler")
    ensure_controlplane_component_compatibility(
        _ControlplaneSessionSink(),
        component_type="session_event_sink",
    )

    assert polling.contract_version == TELEGRAM_INTERFACE_VERSION
    assert webhook.contract_version == TELEGRAM_INTERFACE_VERSION
    assert polling.channel_id == "telegram"
    assert webhook.channel_id == "telegram"
    ensure_controlplane_component_compatibility(
        polling, component_type="channel_adapter"
    )
    ensure_controlplane_component_compatibility(
        webhook, component_type="channel_adapter"
    )


def test_channel_adapters_deliver_with_typed_delivery_context(tmp_path) -> None:
    captured: list[tuple[str, dict]] = []

    def _recording_post(url: str, payload: dict, timeout: float) -> dict:
        del timeout
        captured.append((url, dict(payload)))
        if url.endswith("/getMe"):
            return {"ok": True, "result": {"id": 1, "username": "bot"}}
        if url.endswith("/sendMessage"):
            return {"ok": True, "result": {"message_id": 10}}
        return {"ok": True, "result": {}}

    _api, _delivery, _state_store, _runtime, _sink, polling, webhook = (
        _build_channel_stack(tmp_path, _recording_post)
    )
    ctx = DeliveryContext(
        channel="telegram",
        chat_id="telegram:100",
        thread_id="77",
        reply_to="55",
        outbox_id="out-1",
    )
    polling.deliver({"text": "hello"}, ctx)
    webhook.deliver({"text": "hello"}, ctx)

    send_payloads = [
        payload for url, payload in captured if url.endswith("/sendMessage")
    ]
    assert len(send_payloads) >= 2
    assert send_payloads[0]["chat_id"] == 100
    assert send_payloads[0]["message_thread_id"] == 77
    assert send_payloads[0]["reply_to_message_id"] == 55
