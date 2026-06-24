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

from .fixtures import skill_flow_fixture
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


class TestConcreteSkillFlowIntegration:
    def test_full_flow_create_account_post_share(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                text='create account and publish/share "hello world"',
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1
            outbound = outbounds[0]

            data = outbound.get("data", {})
            assert "account_id" in data.get("skill_result", {})
            assert "post_id" in data.get("skill_result", {})
            assert "share_url" in data.get("skill_result", {})

            skill_result = data["skill_result"]
            assert skill_result["account_id"].startswith("acc_")
            assert skill_result["post_id"].startswith("post_")
            assert skill_result["share_url"].startswith("http")

    def test_create_account_only(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text="create account named testuser",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            skill_result = outbounds[0].get("data", {}).get("skill_result", {})
            assert "account_id" in skill_result
            assert skill_result["account_id"].startswith("acc_")

    def test_create_post_only(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text="create post with content hello",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            skill_result = outbounds[0].get("data", {}).get("skill_result", {})
            assert "post_id" in skill_result
            assert skill_result["post_id"].startswith("post_")

    def test_share_post_only(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text="share the post",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            skill_result = outbounds[0].get("data", {}).get("skill_result", {})
            assert "share_url" in skill_result
            assert skill_result["share_url"].startswith("http")

    def test_session_id_persists_across_flow(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text="create account named testuser",
                message_id=1,
            )
            runner.run_once()

            first_session = (
                fixture.captured_outbounds[0].get("data", {}).get("session_id")
            )

            transport.inject_message(
                chat_id=123,
                user_id=456,
                text="create post with content hello",
                message_id=2,
            )
            runner.run_once()

            second_session = (
                fixture.captured_outbounds[1].get("data", {}).get("session_id")
            )

            assert first_session != second_session

    def test_redaction_in_skill_result(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text="create account named testuser",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            text = outbounds[0].get("data", {}).get("text", "")

            assert "sk_test***" in text
            assert "sk_live_" not in text

    def test_skill_call_history_tracked(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text="create account and share post",
                message_id=1,
            )
            runner.run_once()

            history = fixture.get_skill_call_history()
            assert len(history) == 1
            assert "create account" in history[0]["user_text"].lower()
            assert "share" in history[0]["user_text"].lower()


class TestSkillFlowContractParity:
    def test_contract_has_required_fields(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text='create account and publish/share "test"',
                message_id=1,
            )
            runner.run_once()

            result = fixture.get_skill_result()
            assert result is not None

            assert "account_id" in result
            assert "post_id" in result
            assert "share_url" in result

            assert result["account_id"].startswith("acc_")
            assert result["post_id"].startswith("post_")
            assert result["share_url"].startswith("http")

    def test_outbound_has_session_metadata(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with skill_flow_fixture() as fixture:
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
                chat_id=123,
                user_id=456,
                text="create account",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1
            outbound = outbounds[0]
            data = outbound.get("data", {})

            assert "session_id" in data
            assert data["session_id"].startswith("test-session-")

            assert "agent_id" in outbound
            assert outbound["agent_id"] == "test-agent"
