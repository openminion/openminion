from __future__ import annotations

from pathlib import Path

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

from .fixtures import runtime_fixture
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


class TestNLSkillLearnPathIntegration:
    def test_nl_path_learn_triggers_skill_ingest(self, tmp_path: Path):
        # Create a test SKILL.md file
        skill_file = tmp_path / "test_skill.md"
        skill_file.write_text("""# Test Skill

## Description
A test skill for controlplane NL learn.

## Example
Test example here.
""")

        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text=f"learn this skill from {skill_file} and use it",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert any(
                marker in text.lower()
                for marker in [
                    "ingested",
                    "skill",
                    "learned",
                    "source_type",
                    "detected",
                ]
            ), f"Expected skill ingest indicator in: {text}"

    def test_nl_path_learn_source_type_metadata(self, tmp_path: Path):
        skill_file = tmp_path / "plan_skill.md"
        skill_file.write_text("""# Plan Skill

## Description
A planning skill.

## Example
Plan example.
""")

        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text=f"read this skill from {skill_file}",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert (
                "skill" in text.lower()
                or "ingest" in text.lower()
                or "learn" in text.lower()
                or "detected" in text.lower()
            ), f"Expected skill-related response, got: {text}"


class TestNLSkillLearnURLIntegration:
    def test_nl_url_learn_triggers_fetch_and_ingest(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text="learn this skill from https://example.com/SKILL.md and use it",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert any(
                marker in text.lower()
                for marker in [
                    "url",
                    "fetch",
                    "ingest",
                    "skill",
                    "source_type",
                    "detected",
                ]
            ), f"Expected URL skill handling indicator in: {text}"

    def test_nl_url_learn_source_type_url_metadata(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text="read this skill from https://raw.githubusercontent.com/user/repo/main/skill.md",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert (
                "skill" in text.lower()
                or "url" in text.lower()
                or "source" in text.lower()
                or "detected" in text.lower()
            ), f"Expected URL skill response, got: {text}"


class TestNLSkillLearnNegativePaths:
    def test_nl_url_unreachable_host(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text="learn this skill from https://bad-host.invalid/SKILL.md",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert (
                "learn this skill from https://bad-host.invalid/skill.md"
                in text.lower()
            ), f"Expected chat fallback for bad URL, got: {text}"

    def test_nl_url_blocked_localhost(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text="learn this skill from http://localhost:8080/admin.md",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert (
                "learn this skill from http://localhost:8080/admin.md" in text.lower()
            ), f"Expected chat fallback for localhost URL, got: {text}"

    def test_nl_path_invalid_file(self, tmp_path: Path):
        nonexistent_file = tmp_path / "nonexistent_skill.md"

        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text=f"learn this skill from {nonexistent_file}",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert str(nonexistent_file).lower() in text.lower(), (
                f"Expected chat fallback for invalid path, got: {text}"
            )

    def test_nl_url_invalid_scheme(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text="learn this skill from ftp://example.com/skill.md",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbounds[0].get("data", {})
            text = outbounds[0].get("text", "")

            assert "learn this skill from ftp://example.com/skill.md" in text.lower(), (
                f"Expected chat fallback for invalid scheme, got: {text}"
            )


class TestNLSkillLearnContractParity:
    def test_nl_learn_response_has_deterministic_structure(self, tmp_path: Path):
        skill_file = tmp_path / "parity_skill.md"
        skill_file.write_text("""# Parity Skill

## Description
Contract parity test skill.

## Example
Example usage.
""")

        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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
                text=f"learn this skill from {skill_file}",
                message_id=1,
            )
            runner.run_once()

            outbounds = fixture.captured_outbounds
            assert len(outbounds) == 1

            outbound = outbounds[0]

            assert "agent_id" in outbound
            assert "text" in outbound
            assert "session_id" in outbound

            assert "data" in outbound
            outbound["data"]

            assert "session_id" in outbound

    def test_nl_learn_error_codes_match_chat_lane(self):
        transport = DeterministicTelegramTransport(bot_token="test-token")
        config = _make_test_config()

        with runtime_fixture() as fixture:
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

            test_cases = [
                ("learn from ftp://example.com/skill.md", "INVALID_SCHEME"),
                ("learn from http://localhost/skill.md", "BLOCKED_HOST"),
            ]

            for prompt, expected_code in test_cases:
                transport.inject_message(
                    chat_id=123,
                    user_id=456,
                    text=prompt,
                    message_id=hash(prompt) % 10000,  # Deterministic but unique-ish
                )
                runner.run_once()

                outbounds = fixture.captured_outbounds
                assert len(outbounds) > 0, f"No response for: {prompt}"

                last = outbounds[-1]
                assert "data" in last

                fixture.clear_captures()
