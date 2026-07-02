import threading

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.socket_mode import (
    SlackSocketModeRunner,
)


class FakeSocket:
    def __init__(self, envelopes):
        self.envelopes = list(envelopes)
        self.acks = []
        self.closed = False

    def connect(self):
        pass

    def recv(self, timeout=1.0):
        if self.envelopes:
            return self.envelopes.pop(0)
        return None

    def ack(self, envelope_id):
        self.acks.append(envelope_id)

    def close(self):
        self.closed = True


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


def test_socket_mode_acks_before_dispatch_and_stops() -> None:
    stop = threading.Event()
    socket = FakeSocket(
        [
            {
                "envelope_id": "env1",
                "payload": {
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
                },
            }
        ]
    )
    runtime = FakeRuntime()
    delivery = FakeDelivery()
    runner = SlackSocketModeRunner(
        config=SlackChannelConfig(),
        runtime=runtime,
        delivery=delivery,
        socket_client=socket,
    )

    original_recv = socket.recv

    def stop_after_recv(timeout=1.0):
        value = original_recv(timeout=timeout)
        stop.set()
        return value

    socket.recv = stop_after_recv
    runner.start(stop_event=stop)

    assert socket.acks == ["env1"]
    assert runtime.inbounds[0].text == "hi"
    assert socket.closed is True
