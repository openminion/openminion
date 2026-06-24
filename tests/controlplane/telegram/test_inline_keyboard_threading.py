from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
from openminion.modules.controlplane.channels.telegram.config import (
    ActionsConfig,
    DeliveryConfig,
    ReplyConfig,
    RetryConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.models import TelegramReplyTarget


def _send_result(text: str, chat_id: int = 123) -> dict[str, Any]:
    return {
        "message_id": 1,
        "chat": {"id": chat_id, "type": "private"},
        "text": text,
    }


def _service(
    api: Any,
    *,
    inline_buttons: bool = True,
    chunk_limit: int = 500,
) -> TelegramDeliveryService:
    return TelegramDeliveryService(
        api=api,
        delivery_config=DeliveryConfig(
            parse_mode="plain",
            chunk_limit=chunk_limit,
            retry=RetryConfig(max_attempts=1, backoff_ms=[1]),
        ),
        reply_config=ReplyConfig(mode="reply_to_user"),
        actions_config=ActionsConfig(inline_buttons=inline_buttons),
        sleep_fn=lambda _s: None,
    )


def test_send_payload_threads_reply_markup_when_inline_buttons_enabled() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    svc = _service(api, inline_buttons=True)
    payload = {
        "text": "pick one",
        "ui": {
            "inline_buttons": [[{"text": "OK", "callback_data": "ok"}]],
        },
    }
    target = TelegramReplyTarget(chat_id=123, message_id=42, topic_id=None)
    result = svc.send_payload(payload, target)

    assert result.ok is True
    assert api.send_message.call_count == 1
    sent = api.send_message.call_args.args[0]
    assert "reply_markup" in sent
    assert sent["reply_markup"] == {
        "inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]],
    }


def test_send_payload_accepts_direct_reply_markup() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    svc = _service(api, inline_buttons=True)
    direct_markup = {
        "inline_keyboard": [[{"text": "Yes", "callback_data": "yes"}]],
    }
    payload = {"text": "go?", "reply_markup": direct_markup}
    target = TelegramReplyTarget(chat_id=123, message_id=42, topic_id=None)
    svc.send_payload(payload, target)

    sent = api.send_message.call_args.args[0]
    assert sent["reply_markup"] == direct_markup


def test_send_payload_strips_reply_markup_when_inline_buttons_disabled(
    caplog: Any,
) -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    svc = _service(api, inline_buttons=False)
    payload = {
        "text": "pick one",
        "ui": {
            "inline_buttons": [[{"text": "OK", "callback_data": "ok"}]],
        },
    }
    target = TelegramReplyTarget(chat_id=123, message_id=42, topic_id=None)

    with caplog.at_level(logging.WARNING):
        svc.send_payload(payload, target)

    sent = api.send_message.call_args.args[0]
    assert "reply_markup" not in sent
    assert sent["text"] == "pick one"
    assert any(
        "channel.inline_buttons.disabled.skipping_keyboard" in record.getMessage()
        for record in caplog.records
    )


def test_reply_markup_only_on_first_chunk() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    svc = _service(api, inline_buttons=True, chunk_limit=5)
    payload = {
        "text": "1234567890",
        "ui": {"inline_buttons": [[{"text": "OK", "callback_data": "ok"}]]},
    }
    target = TelegramReplyTarget(chat_id=123, message_id=42, topic_id=None)
    svc.send_payload(payload, target)

    assert api.send_message.call_count == 2
    first = api.send_message.call_args_list[0].args[0]
    second = api.send_message.call_args_list[1].args[0]
    assert "reply_markup" in first
    assert "reply_markup" not in second


def test_send_payload_no_markup_when_payload_omits_it() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    svc = _service(api, inline_buttons=True)
    payload = {"text": "plain message"}
    target = TelegramReplyTarget(chat_id=123, message_id=42, topic_id=None)
    svc.send_payload(payload, target)

    sent = api.send_message.call_args.args[0]
    assert "reply_markup" not in sent
