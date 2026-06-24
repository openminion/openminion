from __future__ import annotations


from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    PairingConfig,
    PollingConfig,
    ReplyConfig,
    TelegramChannelConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)

from .fixtures import ControlplaneRuntimeFixture
from .transports import DeterministicTelegramTransport


def _make_test_config() -> TelegramChannelConfig:
    return TelegramChannelConfig(
        enabled=True,
        bot_token="test-token",
        mode="polling",
        polling=PollingConfig(
            timeout_seconds=1,
            limit=100,
            persist_offset=False,
            drop_pending_on_start=False,
        ),
        access=AccessConfig(
            dm_policy="allowlist",
            allow_from_user_ids=[456],
            group_policy="deny",
        ),
        pairing=PairingConfig(enabled=False, mode="off"),
        reply=ReplyConfig(),
        actions=ActionsConfig(send_message=True),
    )


class TestPollingSimple:
    def test_transport_inject_and_retrieve(self):
        transport = DeterministicTelegramTransport("test-token")

        update_id = transport.inject_message(
            chat_id=123, user_id=456, text="Hello", message_id=1
        )

        updates = transport.api.get_updates(
            offset=0, timeout=30, limit=100, allowed_updates=["message"]
        )

        assert len(updates) == 1
        assert updates[0]["message"]["text"] == "Hello"
        assert updates[0]["update_id"] == update_id

    def test_fixture_runtime_dispatch(self):
        with ControlplaneRuntimeFixture() as fixture:
            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "from": {"id": 456, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 123, "type": "private"},
                    "date": 1234567890,
                    "text": "Hello",
                },
            }

            result = fixture.inject_update(update)

            assert len(fixture.captured_outbounds) == 1
            assert "text" in result

    def test_full_polling_cycle(self):
        transport = DeterministicTelegramTransport("test-token")
        config = _make_test_config()

        with ControlplaneRuntimeFixture() as fixture:
            delivery = TelegramDeliveryService(
                api=transport.api,
                delivery_config=config.delivery,
                reply_config=config.reply,
            )

            runner = TelegramPollingRunner(
                config=config,
                api=transport.api,
                runtime=fixture.coordinator,
                delivery=delivery,
                state_store=None,
            )

            runner._initialized = True
            runner._bot_username = "testbot"
            runner._account_id = "telegram-bot:123456789"

            transport.inject_message(
                chat_id=123, user_id=456, text="Hello bot", message_id=1
            )

            processed = runner.run_once()

            assert processed == 1, (
                f"Expected 1, got {processed}. Outbounds: {len(fixture.captured_outbounds)}"
            )
            assert len(fixture.captured_outbounds) == 1
