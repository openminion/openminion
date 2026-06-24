from __future__ import annotations

import pytest

from openminion.base.config.base import ConfigError
from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    DeliveryConfig,
    PairingConfig,
    ReplyConfig,
    TelegramChannelConfig,
    WebhookConfig,
    load_config,
)
from openminion.modules.controlplane.channels.telegram.models import DeliveryResult
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)


class _API:
    def get_me(self) -> dict[str, object]:
        return {"id": "123", "username": "testbot"}


class _Runtime:
    def handle_inbound(self, inbound: object) -> dict[str, object]:
        return {
            "type": "chat",
            "text": "ok",
            "session_id": "sess-1",
            "agent_id": "agent:default",
        }


class _Delivery:
    def send_payload(
        self, payload: dict[str, object], target: object
    ) -> DeliveryResult:
        return DeliveryResult(ok=True, sent_messages=[])

    def send_text(self, *, text: str, target: object) -> DeliveryResult:
        return DeliveryResult(ok=True, sent_messages=[])


def _config(secret: str | None) -> TelegramChannelConfig:
    return TelegramChannelConfig(
        enabled=True,
        bot_token="token",
        access=AccessConfig(
            dm_policy="allow",
            group_policy="allow",
            mention_only_in_groups=False,
        ),
        webhook=WebhookConfig(
            enabled=True,
            url="https://example.test/webhook",
            secret=secret,
            drop_pending_updates=True,
        ),
        pairing=PairingConfig(enabled=False),
        actions=ActionsConfig(
            send_message=True,
            edit_message=True,
            reactions=False,
            inline_buttons=True,
        ),
        reply=ReplyConfig(mode="reply_to_user"),
        delivery=DeliveryConfig(parse_mode="plain", chunk_limit=500),
    )


def _runner(secret: str) -> TelegramWebhookRunner:
    return TelegramWebhookRunner(
        config=_config(secret),
        api=_API(),  # type: ignore[arg-type]
        runtime=_Runtime(),  # type: ignore[arg-type]
        delivery=_Delivery(),  # type: ignore[arg-type]
        state_store=None,
    )


def _private_update(update_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1},
            "text": "hi",
        },
    }


def test_webhook_enabled_requires_non_empty_secret_at_config_load() -> None:
    with pytest.raises(
        ConfigError, match="webhook.enabled=True requires non-empty webhook.secret"
    ):
        load_config(
            {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "botToken": "token",
                        "webhook": {
                            "enabled": True,
                            "url": "https://example.test/webhook",
                            "secret": "",
                        },
                    }
                }
            }
        )

    with pytest.raises(
        ConfigError, match="webhook.enabled=True requires non-empty webhook.secret"
    ):
        load_config(
            {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "botToken": "token",
                        "webhook": {
                            "enabled": True,
                            "url": "https://example.test/webhook",
                            "secret": None,
                        },
                    }
                }
            }
        )


def test_webhook_disabled_allows_empty_secret() -> None:
    cfg = load_config(
        {
            "channels": {
                "telegram": {
                    "enabled": True,
                    "botToken": "token",
                    "webhook": {
                        "enabled": False,
                        "url": "https://example.test/webhook",
                        "secret": "",
                    },
                }
            }
        }
    ).telegram

    assert cfg.webhook.enabled is False
    assert cfg.webhook.secret in {"", None}


def test_webhook_secret_verification_accepts_bytes_header() -> None:
    runner = _runner("abc123")

    assert (
        runner.handle_webhook_update(
            _private_update(1),
            secret_token=b"abc123",  # type: ignore[arg-type]
        )["success"]
        is True
    )


def test_webhook_secret_verification_reports_type_mismatch_clearly() -> None:
    runner = _runner("abc123")

    result = runner.handle_webhook_update(
        _private_update(2),
        secret_token=object(),  # type: ignore[arg-type]
    )

    assert result["success"] is False
    assert result["error"] == "unauthorized"
    assert result["reason"] != "missing_secret_token"
