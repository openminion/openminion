from __future__ import annotations

import os
import time
import uuid

import pytest

from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI


@pytest.mark.telegram_live
@pytest.mark.skipif(
    not (os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_LIVE_CHAT_ID")),
    reason="requires live Telegram credentials (TELEGRAM_BOT_TOKEN + TELEGRAM_LIVE_CHAT_ID)",
)
def test_live_bot_send_and_echo() -> None:
    api = TelegramBotAPI(os.environ["TELEGRAM_BOT_TOKEN"])
    marker = f"cpe-08 smoke {uuid.uuid4()}"
    chat_id = int(os.environ["TELEGRAM_LIVE_CHAT_ID"])

    sent = api.send_message({"chat_id": chat_id, "text": marker})
    assert isinstance(sent, dict)
    assert "message_id" in sent, f"expected message_id in response, got {sent!r}"

    deadline = time.monotonic() + 30.0
    seen = False
    last_update_id = 0
    while time.monotonic() < deadline:
        updates = api.get_updates(
            offset=(last_update_id + 1) if last_update_id else None,
            timeout=1,
            limit=100,
            allowed_updates=["message"],
        )
        for upd in updates:
            uid = upd.get("update_id")
            if isinstance(uid, int):
                last_update_id = max(last_update_id, uid)
            msg = upd.get("message") or {}
            if msg.get("text") == marker:
                seen = True
                break
        if seen:
            break

    assert seen, f"did not observe sent message {marker!r} in get_updates within 30s"
