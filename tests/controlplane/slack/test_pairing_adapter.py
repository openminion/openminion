from openminion.modules.controlplane.channels.slack.pairing_adapter import (
    SlackPairingAdapter,
)
from openminion.modules.controlplane.contracts.models import InboundMessage


def test_slack_pairing_adapter_extracts_slash_pair_attempt() -> None:
    attempt = SlackPairingAdapter().extract_pairing_attempt(
        InboundMessage(
            channel="slack",
            user_key="slack:T1:user:U1",
            chat_key="slack:T1:channel:C1",
            text="/openminion pair tok123",
            chat_id="C1",
            user_id="U1",
            metadata={
                "team_id": "T1",
                "channel_id": "C1",
                "slack_interaction": "slash_command",
            },
        )
    )

    assert attempt is not None
    assert attempt.channel == "slack"
    assert attempt.token == "tok123"
    assert attempt.account_id == "slack:T1:user:U1"
    assert attempt.chat_key == "slack:T1:channel:C1"
    assert attempt.chat_type == "private"
    assert attempt.extra["subject_id"] == "C1"
    assert attempt.extra["session_chat_key"] == "slack:T1:channel:C1"


def test_slack_pairing_adapter_keeps_public_message_as_channel_attempt() -> None:
    attempt = SlackPairingAdapter().extract_pairing_attempt(
        InboundMessage(
            channel="slack",
            user_key="slack:T1:user:U1",
            chat_key="slack:T1:channel:C1",
            text="/openminion pair tok123",
            chat_id="C1",
            user_id="U1",
            metadata={
                "team_id": "T1",
                "channel_id": "C1",
                "channel_type": "channel",
            },
        )
    )

    assert attempt is not None
    assert attempt.chat_type == "channel"


def test_slack_pairing_adapter_ignores_non_pairing_text() -> None:
    attempt = SlackPairingAdapter().extract_pairing_attempt(
        InboundMessage(
            channel="slack",
            user_key="slack:T1:user:U1",
            chat_key="slack:T1:channel:C1",
            text="/status",
            chat_id="C1",
            user_id="U1",
            metadata={"team_id": "T1", "channel_id": "C1"},
        )
    )

    assert attempt is None
