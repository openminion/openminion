from __future__ import annotations

import threading
import time
from typing import Any

from openminion.base.channel import ChannelRegistry
from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION
from openminion.services.runtime.channel_supervisor import ChannelRuntimeSupervisor


class _FakeChannel:
    contract_version = CONTROLPLANE_INTERFACE_VERSION
    channel_id: str

    def __init__(self, channel_id: str, *, fail: bool = False) -> None:
        self.channel_id = channel_id
        self.started = False
        self.stopped = False
        self.fail = fail
        self.stop_event: threading.Event | None = None

    def start(self, stop_event: threading.Event | None = None) -> None:
        self.started = True
        self.stop_event = stop_event
        if self.fail:
            raise RuntimeError("xoxb-hidden-token failed")
        while stop_event is not None and not stop_event.is_set():
            stop_event.wait(0.01)

    def stop(self) -> None:
        self.stopped = True

    def health(self) -> dict[str, Any]:
        return {"mode": "fake", "connected": self.started and not self.stopped}

    def deliver(self, _payload: dict[str, Any], _ctx: Any) -> None:
        return None


class _FakeOutboxWorker:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> None:
        self.calls += 1
        time.sleep(0.01)
        return None


class _TelemetrySpy:
    def __init__(self) -> None:
        self.events = []

    def record_event_sync(self, event) -> None:
        self.events.append(event)


def test_supervisor_starts_and_stops_channels_and_outbox() -> None:
    telegram = _FakeChannel("telegram")
    slack = _FakeChannel("slack")
    outbox = _FakeOutboxWorker()
    closed: list[str] = []
    registry = ChannelRegistry()
    registry.register(telegram)
    registry.register(slack)
    supervisor = ChannelRuntimeSupervisor(
        channels=registry,
        outbox_worker=outbox,  # type: ignore[arg-type]
        close_runtime=lambda: closed.append("closed"),
    )

    supervisor.start()
    wait_until(lambda: telegram.started and slack.started and outbox.calls > 0)
    running = supervisor.status()

    assert running.state == "running"
    assert running.outbox_worker_alive is True
    assert telegram._outbox_managed_by_supervisor is True
    assert slack._outbox_managed_by_supervisor is True

    result = supervisor.stop()
    supervisor.stop()

    assert result["telegram"]["ok"] is True
    assert result["slack"]["ok"] is True
    assert telegram.stopped is True
    assert slack.stopped is True
    assert closed == ["closed"]
    assert supervisor.status().state == "stopped"


def test_supervisor_redacts_channel_failure_details() -> None:
    failing = _FakeChannel("telegram", fail=True)
    registry = ChannelRegistry()
    registry.register(failing)
    supervisor = ChannelRuntimeSupervisor(channels=registry)

    supervisor.start()
    wait_until(lambda: supervisor.status().state == "degraded")
    status = supervisor.status()

    assert status.channels["telegram"].state == "failed"
    assert "xoxb-" not in (status.last_error or "")
    assert "<redacted>" in (status.last_error or "")


def test_supervisor_emits_redacted_lifecycle_telemetry() -> None:
    failing = _FakeChannel("telegram", fail=True)
    telemetry = _TelemetrySpy()
    registry = ChannelRegistry()
    registry.register(failing)
    supervisor = ChannelRuntimeSupervisor(
        channels=registry,
        telemetry_service=telemetry,
    )

    supervisor.start()
    wait_until(lambda: supervisor.status().state == "degraded")
    supervisor.stop()

    event_types = [event.event_type for event in telemetry.events]
    failed = [
        event
        for event in telemetry.events
        if event.event_type == "controlplane.channel.failed"
    ][0]

    assert "controlplane.channel.started" in event_types
    assert "controlplane.channel.failed" in event_types
    assert "controlplane.channel.degraded" in event_types
    assert "xoxb-" not in str(failed.data)
    assert failed.data["error"] == "<redacted>"


def wait_until(predicate, *, timeout_seconds: float = 1.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")
