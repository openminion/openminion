from __future__ import annotations

from openminion.modules.controlplane.channels.telegram.bot_api import (
    TelegramTransportError,
)
from openminion.modules.controlplane.channels.telegram.config import (
    DeliveryConfig,
    ReplyConfig,
    RetryConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
    escape_markdown_v2,
    split_text,
)
from openminion.modules.controlplane.channels.telegram.models import TelegramReplyTarget


class _FakeAPI:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0
        self.payloads: list[dict] = []

    def send_message(self, payload: dict) -> dict:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise TelegramTransportError("temporary network")
        self.payloads.append(payload)
        return {
            "message_id": 100 + self.calls,
            "chat": {"id": payload["chat_id"]},
            "message_thread_id": payload.get("message_thread_id"),
        }

    def edit_message_text(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return payload


def _service(
    api: _FakeAPI, *, chunk_limit: int, max_attempts: int = 1
) -> TelegramDeliveryService:
    return TelegramDeliveryService(
        api=api,  # type: ignore[arg-type]
        delivery_config=DeliveryConfig(
            parse_mode="plain",
            chunk_limit=chunk_limit,
            retry=RetryConfig(max_attempts=max_attempts, backoff_ms=[1, 1]),
        ),
        reply_config=ReplyConfig(mode="reply_to_user"),
        sleep_fn=lambda _seconds: None,
    )


def test_split_text_preserves_paragraph_boundaries() -> None:
    text = "para-1\n\npara-2\n\npara-3"
    chunks = split_text(text, limit=10)
    assert len(chunks) >= 2
    assert all(chunk for chunk in chunks)


def test_escape_markdown_v2() -> None:
    assert escape_markdown_v2("hello_world") == "hello\\_world"


def test_send_text_retries_transient_failure() -> None:
    api = _FakeAPI(fail_first=True)
    service = _service(api, chunk_limit=100, max_attempts=3)
    result = service.send_text(
        text="hello", target=TelegramReplyTarget(chat_id=1, message_id=2, topic_id=3)
    )
    assert result.ok is True
    assert len(result.sent_messages) == 1
    assert api.calls == 2


def test_send_text_chunks_and_replies_first_chunk_only() -> None:
    api = _FakeAPI()
    service = _service(api, chunk_limit=5)
    service.send_text(
        text="1234567890",
        target=TelegramReplyTarget(chat_id=1, message_id=2, topic_id=None),
    )

    assert len(api.payloads) == 2
    assert api.payloads[0]["reply_to_message_id"] == 2
    assert "reply_to_message_id" not in api.payloads[1]
