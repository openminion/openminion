from __future__ import annotations

from openminion.modules.controlplane.channels.telegram.models import (
    ControlEvent,
    TelegramInboundEnvelope,
    TelegramUser,
)
from openminion.modules.controlplane.channels.telegram.normalization import (
    to_inbound_message,
)
from openminion.modules.controlplane.channels.telegram.pairing_adapter import (
    TelegramPairingAdapter,
)


def _inbound(text: str, *, chat_type: str = "private"):
    envelope = TelegramInboundEnvelope(
        update_id=1,
        raw_type="message",
        chat_id=22,
        message_id=10,
        text=text,
        from_user=TelegramUser(id=11, username="u", display="U"),
        chat_type=chat_type,
    )
    event = ControlEvent(
        channel="telegram",
        conversation_id="22",
        thread_id=None,
        message_id="10",
        from_user={"id": "11"},
        text=text,
        attachments=[],
        metadata={},
    )
    return to_inbound_message(envelope, normalized_text=text, control_event=event)


def test_telegram_pairing_adapter_extracts_start_token() -> None:
    attempt = TelegramPairingAdapter().extract_pairing_attempt(
        _inbound("/start token_123"),
        channel_context={"bot_username": "bot"},
    )
    assert attempt is not None
    assert attempt.channel == "telegram"
    assert attempt.token == "token_123"
    assert attempt.account_id == "telegram-bot:user:11"
    assert attempt.chat_key == "telegram-bot:chat:22"
    assert attempt.extra["subject_id"] == "22"
    assert attempt.extra["session_chat_key"] == "telegram:22"


def test_telegram_pairing_adapter_ignores_other_bot_mention() -> None:
    attempt = TelegramPairingAdapter().extract_pairing_attempt(
        _inbound("/start@otherbot token_123"),
        channel_context={"bot_username": "mybot"},
    )
    assert attempt is None


def test_telegram_pairing_adapter_non_start_returns_none() -> None:
    attempt = TelegramPairingAdapter().extract_pairing_attempt(_inbound("/help"))
    assert attempt is None
