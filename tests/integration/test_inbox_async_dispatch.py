from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.socket_mode import (
    SlackSocketModeRunner,
)
from openminion.modules.controlplane.channels.slack.webhook import SlackHttpEventsRunner
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine
from tests.integration.test_unified_config_bootstrap import _close_runtime, _make_config
from tests.controlplane.telegram.integration.transports import (
    DeterministicTelegramTransport,
)


WEBHOOK_SECRET = "cus-webhook-secret"


class _FakeRuntime:
    def __init__(self) -> None:
        self.inbounds = []

    def handle_inbound(self, inbound):  # noqa: ANN001
        self.inbounds.append(inbound)
        return {"text": inbound.text}


class _FakeDelivery:
    def __init__(self) -> None:
        self.sent = []

    def deliver(self, payload, ctx):  # noqa: ANN001
        self.sent.append((payload, ctx))


class _FakeSocket:
    def __init__(self, envelope: dict[str, object]) -> None:
        self.envelope = envelope
        self.acks: list[str] = []

    def ack(self, envelope_id: str) -> None:
        self.acks.append(envelope_id)


def test_telegram_polling_enqueues_before_runtime_dispatch(tmp_path: Path) -> None:
    runtime = _build_telegram_runtime(tmp_path, mode="polling")
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)
        transport = DeterministicTelegramTransport(bot_token="token")
        runner._api = transport.api
        runner._delivery._api = transport.api
        transport.inject_message(
            chat_id=123,
            user_id=456,
            text="queued polling",
            message_id=10,
        )

        assert runner.run_once() == 1
        assert runtime.controlplane_components is not None
        row = runtime.controlplane_components.store.claim_inbox(lock_owner="test")
        assert row is not None
        assert json.loads(row["payload_json"])["text"] == "queued polling"
        assert transport.get_outbound_texts() == []
    finally:
        _close_runtime(runtime)


def test_telegram_webhook_enqueues_before_runtime_dispatch(tmp_path: Path) -> None:
    runtime = _build_telegram_runtime(tmp_path, mode="webhook")
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramWebhookRunner)
        transport = DeterministicTelegramTransport(bot_token="token")
        runner._api = transport.api
        runner._delivery._api = transport.api
        runner.initialize()

        result = runner.handle_webhook_update(
            _telegram_update("queued webhook", update_id=1),
            secret_token=WEBHOOK_SECRET,
        )

        assert result["success"] is True
        assert runtime.controlplane_components is not None
        row = runtime.controlplane_components.store.claim_inbox(lock_owner="test")
        assert row is not None
        assert json.loads(row["payload_json"])["text"] == "queued webhook"
        assert transport.get_outbound_texts() == []
    finally:
        _close_runtime(runtime)


def test_slack_http_event_enqueues_idempotently_before_runtime_dispatch(
    tmp_path: Path,
) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    runtime = _FakeRuntime()
    delivery = _FakeDelivery()
    runner = SlackHttpEventsRunner(
        config=SlackChannelConfig(),
        runtime=runtime,
        delivery=delivery,
        store=store,
    )
    payload = _slack_event_payload(event_id="Ev1", text="queued slack event")

    assert runner.handle_http_event(json.dumps(payload))["status"] == 200
    assert runner.handle_http_event(json.dumps(payload))["status"] == 200

    row = store.claim_inbox(lock_owner="test")
    assert row is not None
    assert json.loads(row["payload_json"])["text"] == "queued slack event"
    assert store.claim_inbox(lock_owner="test-2") is None
    assert runtime.inbounds == []
    assert delivery.sent == []
    store.close()


def test_slack_socket_slash_enqueues_before_runtime_dispatch(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    runtime = _FakeRuntime()
    delivery = _FakeDelivery()
    socket = _FakeSocket(
        {
            "envelope_id": "env1",
            "type": "slash_commands",
            "payload": {
                "team_id": "T1",
                "channel_id": "C1",
                "user_id": "U1",
                "command": "/openminion",
                "text": "status",
                "trigger_id": "trig1",
            },
        }
    )
    runner = SlackSocketModeRunner(
        config=SlackChannelConfig(),
        runtime=runtime,
        delivery=delivery,
        socket_client=socket,  # type: ignore[arg-type]
        store=store,
    )

    runner._handle_socket_envelope(socket.envelope)

    row = store.claim_inbox(lock_owner="test")
    assert row is not None
    assert json.loads(row["payload_json"])["text"] == "/status"
    assert socket.acks == ["env1"]
    assert runtime.inbounds == []
    assert delivery.sent == []
    store.close()


def _build_telegram_runtime(tmp_path: Path, *, mode: str):
    config = _make_config(tmp_path, mode=mode)
    telegram = config.channels["telegram"]
    telegram["access"] = {
        "dmPolicy": "allowlist",
        "allowFromUserIds": [456],
        "groupPolicy": "deny",
    }
    telegram["pairing"] = {"enabled": False, "mode": "off"}
    if mode == "webhook":
        telegram["webhook"] = {
            "enabled": True,
            "url": "https://example.test/webhook",
            "secret": WEBHOOK_SECRET,
            "dropPendingUpdates": True,
        }
    lifecycle = LifecycleService.from_config(
        config,
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    return lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )


def _telegram_update(text: str, *, update_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 100,
            "from": {"id": 456, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 456, "type": "private"},
            "date": 1_700_000_000,
            "text": text,
        },
    }


def _slack_event_payload(*, event_id: str, text: str) -> dict[str, object]:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": event_id,
        "event": {
            "type": "message",
            "channel": "D1",
            "channel_type": "im",
            "user": "U1",
            "text": text,
            "ts": "1.0",
        },
    }
