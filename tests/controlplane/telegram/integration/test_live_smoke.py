from __future__ import annotations

import os
import uuid

import pytest

from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI


@pytest.mark.telegram_live
@pytest.mark.skipif(
    not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_LIVE_CHAT_ID")),
    reason="requires live Telegram credentials (TELEGRAM_BOT_TOKEN + TELEGRAM_LIVE_CHAT_ID)",
)
def test_live_bot_send_message() -> None:
    api = TelegramBotAPI(os.environ["TELEGRAM_BOT_TOKEN"])
    marker = f"cpe-08 smoke {uuid.uuid4()}"
    chat_id = int(os.environ["TELEGRAM_LIVE_CHAT_ID"])

    sent = api.send_message({"chat_id": chat_id, "text": marker})
    assert isinstance(sent, dict)
    assert "message_id" in sent, f"expected message_id in response, got {sent!r}"
    assert sent.get("text") == marker
