from openminion.modules.controlplane.channels.slack.bot_api import SlackAPIError
from openminion.modules.controlplane.channels.slack.config import (
    SlackDeliveryConfig,
    SlackRetryConfig,
)
from openminion.modules.controlplane.channels.slack.delivery import SlackDeliveryService
from openminion.modules.controlplane.channels.slack.models import SlackReplyTarget


class FakeAPI:
    def __init__(self) -> None:
        self.calls = []
        self.fail_once = False

    def chat_post_message(self, payload):
        self.calls.append(payload)
        if self.fail_once and len(self.calls) == 1:
            raise SlackAPIError(
                "limited",
                error_code="ratelimited",
                retryable=True,
                retry_after_seconds=0,
            )
        return {"ok": True, "ts": f"{len(self.calls)}.0"}


class FakeAudit:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event_type, **kwargs):
        self.events.append((event_type, kwargs))


def test_delivery_posts_threaded_message() -> None:
    api = FakeAPI()
    delivery = SlackDeliveryService(api=api)

    result = delivery.send_text(
        "hello", SlackReplyTarget(channel_id="C1", thread_ts="1.0")
    )

    assert result.ok is True
    assert api.calls == [{"channel": "C1", "text": "hello", "thread_ts": "1.0"}]


def test_delivery_chunks_and_retries_rate_limit() -> None:
    api = FakeAPI()
    api.fail_once = True
    audit = FakeAudit()
    delivery = SlackDeliveryService(
        api=api,
        delivery_config=SlackDeliveryConfig(
            max_message_chars=3,
            retry=SlackRetryConfig(max_attempts=2, backoff_seconds=0),
        ),
        audit_logger=audit,
        sleep_fn=lambda _: None,
    )

    result = delivery.send_text("abcdef", {"channel_id": "C1"})

    assert result.chunks_sent == 2
    assert [call["text"] for call in api.calls] == ["abc", "abc", "def"]
    assert audit.events[0][0] == "cp.delivery.failed"
