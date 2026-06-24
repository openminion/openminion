from __future__ import annotations


from openminion.modules.controlplane.channels.telegram.config import (
    AccessConfig,
    ActionsConfig,
    PairingConfig,
    PollingConfig,
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


class TestPollingModeIntegration:
    def test_inbound_message_triggers_runtime_dispatch(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
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
                session_sink=None,
                pairing_service=None,
            )
            runner._initialized = True
            runner._bot_username = "testbot"
            runner._account_id = "telegram-bot:123456789"

            transport.inject_message(
                chat_id=123,
                user_id=456,
                text="Hello bot",
                message_id=1,
            )

            processed = runner.run_once()

            assert processed == 1

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1
            assert outbounds[0]["type"] == "chat"
            assert "Hello bot" in outbounds[0].get("text", "")

            assert "session_id" in outbounds[0]
            assert outbounds[0]["session_id"].startswith("test-session-")

    def test_session_id_persists_across_messages(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
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
                chat_id=123, user_id=456, text="First", message_id=1
            )
            runner.run_once()

            first_session = fixture.captured_outbounds[0]["session_id"]

            transport.inject_message(
                chat_id=123, user_id=456, text="Second", message_id=2
            )
            runner.run_once()

            second_session = fixture.captured_outbounds[1]["session_id"]

            assert first_session != second_session

    def test_metadata_assertions_session_channel_user(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
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
                chat_id=999,
                user_id=456,
                text="Test message",
                message_id=42,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1
            outbound = outbounds[0]

            assert "session_id" in outbound
            assert outbound["session_id"].startswith("test-session-")

            assert "agent_id" in outbound
            assert outbound["agent_id"] == "test-agent"

    def test_audit_events_record_dispatch_path(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with ControlplaneRuntimeFixture(enable_audit=True) as fixture:
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
                chat_id=123, user_id=456, text="Audit test", message_id=1
            )
            runner.run_once()

            events = fixture.audit_events
            assert len(events) > 0

            event_types = [e.get("event") for e in events]
            assert "inbound.received" in event_types
            assert "inbound.resolved" in event_types
            assert "outbound.sent" in event_types

    def test_multiple_updates_in_single_poll(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
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
                chat_id=123, user_id=456, text="Message 1", message_id=1
            )
            transport.inject_message(
                chat_id=123, user_id=456, text="Message 2", message_id=2
            )
            transport.inject_message(
                chat_id=123, user_id=456, text="Message 3", message_id=3
            )

            processed = runner.run_once()

            assert processed == 3
            assert len(fixture.captured_outbounds) == 3
