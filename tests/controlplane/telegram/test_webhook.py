from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from openminion.modules.controlplane.channels.telegram.webhook import (
    TelegramWebhookRunner,
)
from openminion.modules.controlplane.channels.telegram.config import (
    ClarifyConfig,
    WebhookConfig,
)


class MockConfig:
    def __init__(self, secret: str | None = "test-default-secret"):
        self.webhook = WebhookConfig(enabled=True, secret=secret)
        self.clarify = ClarifyConfig()
        self.pairing = MagicMock()
        self.pairing.auto_send_pairing_hint = False
        self.access = MagicMock()
        self.actions = MagicMock()
        self.actions.send_message = True


class TelegramWebhookRunnerTests(unittest.TestCase):
    def setUp(self):
        self.mock_api = MagicMock()
        self.mock_runtime = MagicMock()
        self.mock_delivery = MagicMock()
        self.mock_state_store = MagicMock()

        self.config = MockConfig()
        self.runner = TelegramWebhookRunner(
            config=self.config,
            api=self.mock_api,
            runtime=self.mock_runtime,
            delivery=self.mock_delivery,
            state_store=self.mock_state_store,
        )

    def test_webhook_runner_initializes(self):
        self.mock_api.get_me.return_value = {"id": "123456789", "username": "testbot"}
        self.runner.initialize()

        self.assertEqual(self.runner._bot_username, "testbot")
        self.assertEqual(self.runner._account_id, "telegram-bot:123456789")

    def test_construction_rejects_enabled_without_secret(self):
        from openminion.base.config.base import ConfigError

        with self.assertRaises(ConfigError):
            WebhookConfig(enabled=True, secret=None)
        with self.assertRaises(ConfigError):
            WebhookConfig(enabled=True, secret="")

    def test_handle_webhook_update_with_missing_secret(self):
        self.config = MockConfig(secret="test-secret")
        self.runner = TelegramWebhookRunner(
            config=self.config,
            api=self.mock_api,
            runtime=self.mock_runtime,
            delivery=self.mock_delivery,
            state_store=self.mock_state_store,
        )

        update = {"update_id": 123, "message": {"message_id": 1}}

        result = self.runner.handle_webhook_update(update, secret_token=None)

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "missing_secret_token")

    def test_handle_webhook_update_with_invalid_secret(self):
        self.config = MockConfig(secret="test-secret")
        self.runner = TelegramWebhookRunner(
            config=self.config,
            api=self.mock_api,
            runtime=self.mock_runtime,
            delivery=self.mock_delivery,
            state_store=self.mock_state_store,
        )

        update = {"update_id": 123, "message": {"message_id": 1}}

        result = self.runner.handle_webhook_update(update, secret_token="wrong-secret")

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "invalid_secret_token")

    def test_handle_webhook_update_with_valid_secret(self):
        self.config = MockConfig(secret="test-secret")
        self.runner = TelegramWebhookRunner(
            config=self.config,
            api=self.mock_api,
            runtime=self.mock_runtime,
            delivery=self.mock_delivery,
            state_store=self.mock_state_store,
        )
        self.mock_api.get_me.return_value = {"id": "123456789", "username": "testbot"}
        self.runner.initialize()

        update = {
            "update_id": 123,
            "message": {
                "message_id": 1,
                "chat": {"id": 111, "type": "private"},
                "from": {"id": 111, "first_name": "Test"},
                "text": "hello",
            },
        }

        result = self.runner.handle_webhook_update(update, secret_token="test-secret")

        self.assertTrue(result["success"])

    def test_duplicate_update_detection(self):
        self.mock_api.get_me.return_value = {"id": "123456789", "username": "testbot"}
        self.runner.initialize()

        update = {
            "update_id": 456,
            "message": {
                "message_id": 1,
                "chat": {"id": 111, "type": "private"},
                "from": {"id": 111, "first_name": "Test"},
                "text": "hello",
            },
        }

        result1 = self.runner.handle_webhook_update(
            update, secret_token="test-default-secret"
        )
        self.assertTrue(result1["success"])

        result2 = self.runner.handle_webhook_update(
            update, secret_token="test-default-secret"
        )
        self.assertTrue(result2.get("duplicate", False))

    def test_get_debug_info(self):
        self.mock_api.get_me.return_value = {"id": "123456789", "username": "testbot"}
        self.runner.initialize()

        debug_info = self.runner.get_debug_info()

        self.assertEqual(debug_info["mode"], "webhook")
        self.assertEqual(debug_info["bot_username"], "testbot")
        self.assertFalse(debug_info["webhook_configured"])


class WebhookSecretVerificationTests(unittest.TestCase):
    def test_verify_secret_with_matching_token(self):
        config = MockConfig(secret="my-secret")
        runner = TelegramWebhookRunner(
            config=config,
            api=MagicMock(),
            runtime=MagicMock(),
            delivery=MagicMock(),
        )

        result = runner._verify_secret("my-secret")
        self.assertTrue(result)

    def test_verify_secret_with_non_matching_token(self):
        config = MockConfig(secret="my-secret")
        runner = TelegramWebhookRunner(
            config=config,
            api=MagicMock(),
            runtime=MagicMock(),
            delivery=MagicMock(),
        )

        result = runner._verify_secret("wrong-secret")
        self.assertFalse(result)

    def test_verify_secret_with_configured_secret_round_trip(self):
        config = MockConfig(secret="known-secret-abc")
        runner = TelegramWebhookRunner(
            config=config,
            api=MagicMock(),
            runtime=MagicMock(),
            delivery=MagicMock(),
        )

        self.assertTrue(runner._verify_secret("known-secret-abc"))
        self.assertFalse(runner._verify_secret("other-secret"))
        self.assertFalse(runner._verify_secret(None))
