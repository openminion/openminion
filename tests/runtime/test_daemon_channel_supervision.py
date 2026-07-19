from __future__ import annotations

from openminion.api.core.lifecycle import (
    close_runtime_components,
    initialize_runtime_components,
)


class _ExposureService:
    def __init__(self) -> None:
        self.bound = False

    def bind_event_sink(self, _sink) -> None:
        self.bound = True


class _Tools:
    def __init__(self) -> None:
        self.exposure_service = _ExposureService()


class _ChannelSupervisor:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> dict[str, object]:
        self.started += 1
        return {"telegram": {"ok": True}}

    def stop(self) -> dict[str, object]:
        self.stopped += 1
        return {"telegram": {"ok": True}}


class _Runtime:
    def __init__(self) -> None:
        self.tools = _Tools()
        self.channel_supervisor = _ChannelSupervisor()


def test_runtime_lifecycle_starts_and_stops_channel_supervisor() -> None:
    runtime = _Runtime()

    finalizer = initialize_runtime_components(
        runtime,
        tool_exposure_event_sink=lambda _event: None,
    )

    assert runtime.tools.exposure_service.bound is True
    assert runtime.channel_supervisor.started == 1

    finalizer.detach()
    close_runtime_components(
        channel_supervisor=runtime.channel_supervisor,
        retrieve_ctl=None,
        action_policy=None,
        runtime_manager=None,
        lifecycle_bridge=None,
        tools=runtime.tools,
        runtime_storage=None,
        telemetry_service=None,
    )

    assert runtime.channel_supervisor.stopped == 1
