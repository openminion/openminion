from __future__ import annotations

from dataclasses import replace
from typing import Any

from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    DeliveryConfig,
    PairingConfig,
    ReplyConfig,
    TelegramChannelConfig,
    WebhookConfig,
)
from openminion.modules.controlplane.channels.telegram.models import DeliveryResult
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)


class _StubAPI:
    def __init__(self) -> None:
        self.get_me_calls = 0

    def get_me(self) -> dict[str, Any]:
        self.get_me_calls += 1
        return {"id": "123", "username": "testbot"}

    def answer_callback_query(self, callback_query_id: str) -> dict[str, Any]:
        return {"ok": True}


class _StubDelivery:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    def send_text(self, *, text: str, target: Any) -> DeliveryResult:
        self.texts.append(text)
        return DeliveryResult(
            ok=True,
            sent_messages=[
                {
                    "message_id": 1,
                    "chat": {"id": target.chat_id},
                    "message_thread_id": target.topic_id,
                }
            ],
        )

    def send_payload(self, payload: dict[str, Any], target: Any) -> DeliveryResult:
        self.payloads.append(payload)
        return DeliveryResult(
            ok=True,
            sent_messages=[
                {
                    "message_id": 2,
                    "chat": {"id": target.chat_id},
                    "message_thread_id": target.topic_id,
                }
            ],
        )


class _StubRuntime:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def handle_inbound(self, inbound: Any) -> dict[str, Any]:
        self.calls.append(inbound)
        return {
            "type": "chat",
            "text": "ok",
            "session_id": "sess-1",
            "agent_id": "agent:default",
        }


def _config(secret: str | None) -> TelegramChannelConfig:
    return TelegramChannelConfig(
        enabled=True,
        bot_token="x",
        mode="webhook",
        webhook=WebhookConfig(enabled=True, secret=secret),
        access=AccessConfig(
            dm_policy="allow",
            group_policy="allow",
            mention_only_in_groups=False,
        ),
        pairing=PairingConfig(enabled=False, mode="off"),
        actions=ActionsConfig(
            send_message=True,
            edit_message=True,
            reactions=False,
            inline_buttons=True,
        ),
        reply=ReplyConfig(mode="reply_to_user"),
        delivery=DeliveryConfig(parse_mode="plain", chunk_limit=500),
    )


def _private_update(update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 10,
            "text": "hello",
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 111, "username": "alice", "first_name": "Alice"},
        },
    }


def _make_runner() -> tuple[TelegramWebhookRunner, _StubAPI]:
    api = _StubAPI()
    runner = TelegramWebhookRunner(
        config=_config("old"),
        api=api,  # type: ignore[arg-type]
        runtime=_StubRuntime(),  # type: ignore[arg-type]
        delivery=_StubDelivery(),  # type: ignore[arg-type]
        state_store=None,
    )
    runner.initialize()
    return runner, api


def test_valid_secret_is_accepted_before_rotation() -> None:
    runner, _api = _make_runner()
    result = runner.handle_webhook_update(
        _private_update(update_id=1), secret_token="old"
    )
    assert result.get("success") is True
    assert result.get("error") is None


def test_old_secret_rejected_after_rotation_to_new() -> None:
    runner, _api = _make_runner()

    # Baseline: old secret works
    ok = runner.handle_webhook_update(_private_update(update_id=1), secret_token="old")
    assert ok.get("success") is True

    # Rotate secret on the runner's config
    runner._config = replace(
        runner._config,
        webhook=replace(runner._config.webhook, secret="new"),
    )

    # Old secret now rejected
    rejected = runner.handle_webhook_update(
        _private_update(update_id=2), secret_token="old"
    )
    assert rejected.get("success") is False
    assert rejected.get("error") == "unauthorized"
    assert rejected.get("reason") == "invalid_secret_token"

    # New secret accepted
    accepted = runner.handle_webhook_update(
        _private_update(update_id=3), secret_token="new"
    )
    assert accepted.get("success") is True
    assert accepted.get("error") is None


def test_missing_secret_token_rejected_when_configured() -> None:
    runner, _api = _make_runner()
    rejected = runner.handle_webhook_update(
        _private_update(update_id=4), secret_token=None
    )
    assert rejected.get("success") is False
    assert rejected.get("error") == "unauthorized"
    assert rejected.get("reason") == "missing_secret_token"
