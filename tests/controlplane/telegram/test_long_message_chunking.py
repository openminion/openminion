from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
from openminion.modules.controlplane.channels.telegram.config import (
    DeliveryConfig,
    ReplyConfig,
    RetryConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
    split_text,
)
from openminion.modules.controlplane.channels.telegram.models import TelegramReplyTarget


def _send_result(chunk_text: str) -> dict[str, Any]:
    return {
        "message_id": 1,
        "chat": {"id": 123, "type": "private"},
        "text": chunk_text,
    }


def _service(
    api: Any, *, chunk_limit: int, parse_mode: str = "plain"
) -> TelegramDeliveryService:
    return TelegramDeliveryService(
        api=api,
        delivery_config=DeliveryConfig(
            parse_mode=parse_mode,
            chunk_limit=chunk_limit,
            retry=RetryConfig(max_attempts=1, backoff_ms=[1]),
        ),
        reply_config=ReplyConfig(mode="reply_to_user"),
        sleep_fn=lambda _s: None,
    )


def test_long_text_splits_into_multiple_chunks_under_limit() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    svc = _service(api, chunk_limit=4096, parse_mode="plain")
    body = "a" * 10_000  # single paragraph, forces _split_hard
    svc.send_text(
        text=body,
        target=TelegramReplyTarget(chat_id=123, message_id=1, topic_id=None),
    )

    assert api.send_message.call_count >= 3
    for call in api.send_message.call_args_list:
        payload = call.args[0]
        assert len(payload["text"]) <= 4096


def test_long_text_preserves_paragraph_boundaries_when_possible() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    svc = _service(api, chunk_limit=50, parse_mode="plain")
    paragraphs = [f"para-{i}-{'x' * 20}" for i in range(10)]
    body = "\n\n".join(paragraphs)
    svc.send_text(
        text=body,
        target=TelegramReplyTarget(chat_id=123, message_id=1, topic_id=None),
    )

    assert api.send_message.call_count >= 2
    for call in api.send_message.call_args_list:
        assert len(call.args[0]["text"]) <= 50


def test_split_text_hard_splits_at_exact_limit_for_single_paragraph() -> None:
    body = "a" * 10_000
    chunks = split_text(body, limit=4096)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert len(chunk) <= 4096
    assert "".join(chunks) == body


def test_markdown_v2_code_fence_across_boundary_preserves_entities() -> None:
    api = MagicMock(spec=TelegramBotAPI)
    api.send_message.side_effect = lambda payload: _send_result(payload["text"])

    body = "```\n" + ("x" * 120) + "\n```"
    svc = _service(api, chunk_limit=50, parse_mode="MarkdownV2")
    svc.send_text(
        text=body,
        target=TelegramReplyTarget(chat_id=123, message_id=1, topic_id=None),
    )

    assert api.send_message.call_count >= 2
    for call in api.send_message.call_args_list:
        payload = call.args[0]
        assert payload.get("parse_mode") == "MarkdownV2"
        assert len(payload["text"]) <= 50
        assert payload["text"].count("```") % 2 == 0, (
            f"chunk has unbalanced ```: {payload['text']!r}"
        )
