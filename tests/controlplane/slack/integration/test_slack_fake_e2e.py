import json

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.state import SlackStateStore
from openminion.modules.controlplane.channels.slack.webhook import SlackHttpEventsRunner


class FakeRuntime:
    def __init__(self) -> None:
        self.inbounds = []

    def handle_inbound(self, inbound):
        self.inbounds.append(inbound)
        return {
            "text": f"[{inbound.chat_key}] {inbound.text}",
            "session_id": "sess-1",
        }


class FakeDelivery:
    def __init__(self) -> None:
        self.sent = []

    def deliver(self, payload, ctx):
        self.sent.append((payload, ctx))


def test_fake_slack_event_roundtrip_and_dedup(tmp_path) -> None:
    runtime = FakeRuntime()
    delivery = FakeDelivery()
    runner = SlackHttpEventsRunner(
        config=SlackChannelConfig(),
        runtime=runtime,
        delivery=delivery,
        state_store=SlackStateStore(tmp_path / "slack.db"),
    )
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "app_mention",
            "channel": "C1",
            "channel_type": "channel",
            "user": "U1",
            "text": "<@B1> status",
            "ts": "10.0",
            "thread_ts": "9.0",
        },
    }

    assert runner.handle_http_event(json.dumps(payload))["status"] == 200
    assert runner.handle_http_event(json.dumps(payload))["status"] == 200

    assert len(runtime.inbounds) == 1
    assert runtime.inbounds[0].chat_key == "slack:T1:channel:C1:thread:9.0"
    assert len(delivery.sent) == 1
    assert delivery.sent[0][1].thread_ts == "9.0"
