from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
from openminion.modules.controlplane.channels.telegram.config import (
    DeliveryConfig,
    ReplyConfig,
    RetryConfig,
)
from openminion.modules.controlplane.channels.telegram.constants import (
    REPLY_MODE_TO_USER,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.models import TelegramReplyTarget
from openminion.modules.controlplane.channels.telegram.reactions import (
    TelegramReactionsAdapter,
)


def _service(
    api: Any, *, reply_mode: str = REPLY_MODE_TO_USER
) -> TelegramDeliveryService:
    return TelegramDeliveryService(
        api=api,
        delivery_config=DeliveryConfig(
            parse_mode="plain",
            chunk_limit=500,
            retry=RetryConfig(max_attempts=1, backoff_ms=[1]),
        ),
        reply_config=ReplyConfig(mode=reply_mode),
        sleep_fn=lambda _s: None,
    )


def _default_send_result() -> dict[str, Any]:
    return {
        "message_id": 99,
        "chat": {"id": 123, "type": "private"},
        "text": "ok",
    }


def test_send_message_includes_reply_to_message_id_on_first_chunk() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.return_value = _default_send_result()

    svc = _service(api)
    result = svc.send_text(
        text="hello",
        target=TelegramReplyTarget(chat_id=123, message_id=42, topic_id=None),
    )

    assert result.ok is True
    assert api.send_message.call_count == 1
    payload = api.send_message.call_args.args[0]
    assert payload["chat_id"] == 123
    assert payload["text"] == "hello"
    assert payload["reply_to_message_id"] == 42


def test_send_message_multi_chunk_only_first_has_reply_to() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.return_value = _default_send_result()

    svc = TelegramDeliveryService(
        api=api,
        delivery_config=DeliveryConfig(parse_mode="plain", chunk_limit=5),
        reply_config=ReplyConfig(mode=REPLY_MODE_TO_USER),
        sleep_fn=lambda _s: None,
    )
    svc.send_text(
        text="1234567890",
        target=TelegramReplyTarget(chat_id=123, message_id=42, topic_id=None),
    )

    assert api.send_message.call_count == 2
    first_payload = api.send_message.call_args_list[0].args[0]
    second_payload = api.send_message.call_args_list[1].args[0]
    assert first_payload["reply_to_message_id"] == 42
    assert "reply_to_message_id" not in second_payload


def test_send_message_includes_message_thread_id_when_topic_present() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.return_value = _default_send_result()

    svc = _service(api)
    svc.send_text(
        text="hi",
        target=TelegramReplyTarget(chat_id=123, message_id=42, topic_id=77),
    )

    payload = api.send_message.call_args.args[0]
    assert payload["message_thread_id"] == 77


def test_edit_text_invokes_edit_message_text_with_payload() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.edit_message_text.return_value = {
        "message_id": 55,
        "chat": {"id": 123},
        "text": "updated",
    }

    svc = _service(api)
    svc.edit_text(chat_id=123, message_id=55, text="updated")

    assert api.edit_message_text.call_count == 1
    payload = api.edit_message_text.call_args.args[0]
    assert payload["chat_id"] == 123
    assert payload["message_id"] == 55
    assert payload["text"] == "updated"
    assert payload["disable_web_page_preview"] is False


def test_reactions_adapter_invokes_set_message_reaction_with_emoji() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.set_message_reaction.return_value = {"ok": True}

    adapter = TelegramReactionsAdapter(api)
    adapter.react_add({"conversation_id": "chat-123", "message_id": 7}, emoji="👍")

    assert api.set_message_reaction.call_count == 1
    kwargs = api.set_message_reaction.call_args.kwargs
    assert kwargs["chat_id"] == "chat-123"
    assert kwargs["message_id"] == 7
    assert kwargs["emoji"] == "👍"
    assert kwargs["remove_all"] is False


def test_reactions_adapter_remove_all_passes_remove_flag() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.set_message_reaction.return_value = {"ok": True}

    adapter = TelegramReactionsAdapter(api)
    adapter.react_remove_all_bot({"conversation_id": "chat-123", "message_id": 7})

    assert api.set_message_reaction.call_count == 1
    kwargs = api.set_message_reaction.call_args.kwargs
    assert kwargs["remove_all"] is True


def test_send_message_inline_keyboard_forwarded_verbatim_through_bot_api() -> None:
    captured: dict[str, Any] = {}

    def _http_post(
        url: str, payload: dict[str, Any], _timeout: float
    ) -> dict[str, Any]:
        captured["url"] = url
        captured["payload"] = dict(payload)
        return {
            "ok": True,
            "result": {"message_id": 1, "chat": {"id": 123}, "text": "hi"},
        }

    api = TelegramBotAPI(token="tok", http_post=_http_post)
    reply_markup = {
        "inline_keyboard": [[{"text": "Yes", "callback_data": "yes"}]],
    }
    api.send_message(
        {
            "chat_id": 123,
            "text": "pick one",
            "reply_markup": reply_markup,
        }
    )

    assert captured["payload"]["reply_markup"] == reply_markup
    assert captured["payload"]["chat_id"] == 123
