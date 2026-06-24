from __future__ import annotations

import pytest

from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    PairingConfig,
    TelegramChannelConfig,
    WebhookConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)
from openminion.modules.controlplane.runtime.audit import AuditLogger

from .fixtures import ControlplaneRuntimeFixture
from .transports import DeterministicTelegramTransport


_TEST_WEBHOOK_SECRET = "test-secret-token-12345"


def _make_test_config(with_secret: bool = True) -> TelegramChannelConfig:
    secret = _TEST_WEBHOOK_SECRET  # always non-empty post-CSH-03
    del with_secret  # parameter retained for caller-source readability only
    return TelegramChannelConfig(
        enabled=True,
        bot_token="test-token",
        mode="webhook",
        webhook=WebhookConfig(
            enabled=True,
            url="https://example.com/webhook",
            secret=secret,
            drop_pending_updates=True,
        ),
        access=AccessConfig(
            dm_policy="allowlist",
            allow_from_user_ids=[456],
            group_policy="deny",
        ),
        pairing=PairingConfig(
            enabled=False,
            mode="off",
        ),
        actions=ActionsConfig(
            send_message=True,
            edit_message=False,
            reactions=False,
            inline_buttons=False,
        ),
    )


class TestWebhookModeIntegration:
    def test_webhook_valid_secret_passes(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=True)

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "from": {"id": 456, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 123, "type": "private"},
                    "date": 1234567890,
                    "text": "Hello via webhook",
                },
            }

            result = runner.handle_webhook_update(
                update, secret_token="test-secret-token-12345"
            )

            assert result["success"] is True
            assert "error" not in result

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1
            assert "Hello via webhook" in outbounds[0].get("text", "")

    def test_webhook_invalid_secret_fails(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=True)

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "from": {"id": 456, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 123, "type": "private"},
                    "date": 1234567890,
                    "text": "Should not process",
                },
            }

            result = runner.handle_webhook_update(update, secret_token="wrong-secret")

            assert result["success"] is False
            assert result.get("reason") == "invalid_secret_token"
            assert result.get("error") == "unauthorized"

            assert len(fixture.captured_outbounds) == 0

    def test_webhook_missing_secret_fails(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=True)

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "from": {"id": 456, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 123, "type": "private"},
                    "date": 1234567890,
                    "text": "No secret",
                },
            }

            result = runner.handle_webhook_update(update, secret_token=None)

            assert result["success"] is False
            assert result.get("reason") == "missing_secret_token"

    def test_webhook_config_rejects_enabled_without_secret(self):
        from openminion.base.config.base import ConfigError

        with pytest.raises(ConfigError):
            WebhookConfig(enabled=True, secret=None, url="https://example.com/webhook")
        with pytest.raises(ConfigError):
            WebhookConfig(enabled=True, secret="", url="https://example.com/webhook")

    def test_webhook_duplicate_update_replay(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=False)

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            update = {
                "update_id": 42,
                "message": {
                    "message_id": 1,
                    "from": {"id": 456, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 123, "type": "private"},
                    "date": 1234567890,
                    "text": "First attempt",
                },
            }

            result1 = runner.handle_webhook_update(
                update, secret_token=_TEST_WEBHOOK_SECRET
            )
            assert result1["success"] is True
            assert "duplicate" not in result1
            assert len(fixture.captured_outbounds) == 1

            fixture.clear_captures()

            result2 = runner.handle_webhook_update(
                update, secret_token=_TEST_WEBHOOK_SECRET
            )
            assert result2["success"] is True
            assert result2.get("duplicate") is True
            assert result2.get("update_id") == 42

            assert len(fixture.captured_outbounds) == 0

    def test_webhook_metadata_matches_polling(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=False)

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            update = {
                "update_id": 1,
                "message": {
                    "message_id": 42,
                    "from": {
                        "id": 456,
                        "is_bot": False,
                        "first_name": "Test",
                    },
                    "chat": {"id": 999, "type": "private"},
                    "date": 1234567890,
                    "text": "Test message",
                },
            }

            runner.handle_webhook_update(update, secret_token=_TEST_WEBHOOK_SECRET)

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1
            outbound = outbounds[0]

            assert "session_id" in outbound
            assert outbound["session_id"].startswith("test-session-")

            assert "agent_id" in outbound
            assert outbound["agent_id"] == "test-agent"

            assert "Test message" in outbound.get("text", "")

    def test_webhook_dispatch_failure_mapping(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=False)
        audit = AuditLogger()

        class BrokenRuntime:
            def handle_inbound(self, inbound):
                raise RuntimeError("Simulated dispatch failure")

        with ControlplaneRuntimeFixture():
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=BrokenRuntime(),  # Use broken runtime
                delivery=delivery,
                state_store=None,
                audit_logger=audit,
            )

            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "from": {"id": 456, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 123, "type": "private"},
                    "date": 1234567890,
                    "text": "This will fail",
                },
            }

            result = runner.handle_webhook_update(
                update, secret_token=_TEST_WEBHOOK_SECRET
            )

            assert result["success"] is False
            assert "error" in result
            assert result["error_code"] == "runtime_dispatch_failed"
            assert result["reason"] == "runtime_dispatch_failed"
            assert "Simulated dispatch failure" in result["error"]
            assert any(
                event.event_type == "cp.route.runtime_failed"
                and event.details
                == {
                    "update_id": 1,
                    "chat_id": "123",
                    "error_code": "runtime_dispatch_failed",
                    "error_type": "RuntimeError",
                    "reason": "runtime_dispatch_failed",
                }
                for event in audit.events
            )

    def test_webhook_callback_query_handling(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=False)

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            update = {
                "update_id": 1,
                "callback_query": {
                    "id": "callback_123",
                    "from": {"id": 456, "is_bot": False, "first_name": "Test"},
                    "chat_instance": "123",
                    "data": "button_click",
                    "message": {
                        "message_id": 42,
                        "chat": {"id": 123, "type": "private"},
                        "date": 1234567890,
                        "text": "Click me",
                    },
                },
            }

            result = runner.handle_webhook_update(
                update, secret_token=_TEST_WEBHOOK_SECRET
            )

            assert result["success"] is True

            outbounds = fixture.captured_outbounds
            assert len(outbounds) >= 1

    def test_webhook_debug_info(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config(with_secret=True)

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramWebhookRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            debug_info = runner.get_debug_info()

            assert debug_info["mode"] == "webhook"
            assert debug_info["webhook_configured"] is True
            assert debug_info["webhook_secret_set"] is True
            assert "account_id" in debug_info
