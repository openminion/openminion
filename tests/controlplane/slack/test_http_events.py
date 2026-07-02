import json
import time
from hashlib import sha256
import hmac

import pytest

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.listener import (
    SlackSignatureError,
    verify_slack_signature,
)
from openminion.modules.controlplane.channels.slack.webhook import SlackHttpEventsRunner


class FakeRuntime:
    def __init__(self) -> None:
        self.inbounds = []

    def handle_inbound(self, inbound):
        self.inbounds.append(inbound)
        return {"text": "ok"}


class FakeDelivery:
    def __init__(self) -> None:
        self.sent = []

    def deliver(self, payload, ctx):
        self.sent.append((payload, ctx))


def _headers(secret: str, body: bytes, *, ts: int | None = None) -> dict[str, str]:
    timestamp = str(ts or int(time.time()))
    sig = "v0=" + hmac.new(
        secret.encode(), b"v0:" + timestamp.encode() + b":" + body, sha256
    ).hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": sig}


def test_signature_rejects_stale_timestamp() -> None:
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret="secret",
            timestamp="1",
            body=b"{}",
            signature="bad",
            now=1000,
        )


def test_http_runner_handles_challenge_and_event() -> None:
    runtime = FakeRuntime()
    delivery = FakeDelivery()
    runner = SlackHttpEventsRunner(
        config=SlackChannelConfig(signing_secret="secret"),
        runtime=runtime,
        delivery=delivery,
    )

    challenge = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    assert runner.handle_http_event(challenge, headers=_headers("secret", challenge)) == {
        "status": 200,
        "body": "abc",
    }

    body = json.dumps(
        {
            "type": "event_callback",
            "team_id": "T1",
            "event_id": "Ev1",
            "event": {
                "type": "message",
                "channel": "D1",
                "channel_type": "im",
                "user": "U1",
                "text": "hi",
                "ts": "1.0",
            },
        }
    ).encode()
    assert runner.handle_http_event(body, headers=_headers("secret", body))["status"] == 200
    assert runtime.inbounds[0].text == "hi"
    assert delivery.sent[0][0] == {"text": "ok"}
