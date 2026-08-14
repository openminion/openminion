from __future__ import annotations

import logging
from types import SimpleNamespace
from threading import RLock

from openminion.api.core.lifecycle import (
    close_runtime_components,
    initialize_runtime_components,
)
from openminion.api.core.profiles import RuntimeProfilesMixin


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


class _AgentService:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


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


def test_runtime_lifecycle_closes_agent_services() -> None:
    service = _AgentService()

    close_runtime_components(
        retrieve_ctl=None,
        action_policy=None,
        runtime_manager=None,
        lifecycle_bridge=None,
        tools=None,
        runtime_storage=None,
        agent_services={"default": service},
    )

    assert service.closed == 1


def test_agent_runtime_eviction_closes_its_provider_client() -> None:
    service = _AgentService()
    runtime = SimpleNamespace(
        _agent_runtime_lock=RLock(),
        _gateways={"alpha||default": object()},
        _agent_services={"alpha||default": service},
        logger=logging.getLogger("test.runtime"),
    )

    RuntimeProfilesMixin.evict_agent_runtime(
        runtime,
        agent_id="alpha",
        reason="test",
    )

    assert service.closed == 1
    assert runtime._gateways == {}
    assert runtime._agent_services == {}
