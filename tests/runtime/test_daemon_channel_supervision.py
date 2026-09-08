from __future__ import annotations

import logging
from types import SimpleNamespace
from threading import RLock

from openminion.api.core.lifecycle import (
    close_runtime_components,
    close_runtime_session,
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


class _Gateway:
    def __init__(self, agent_id: str = "default") -> None:
        self.agent_id = agent_id
        self.closed = 0
        self.closed_sessions: list[tuple[str, str]] = []
        self.released_sessions: list[str] = []

    def close(self) -> None:
        self.closed += 1

    def close_session(self, session_id: str, *, reason: str) -> None:
        self.closed_sessions.append((session_id, reason))

    def release_session(self, session_id: str) -> None:
        self.released_sessions.append(session_id)


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
    gateway = _Gateway()

    close_runtime_components(
        retrieve_ctl=None,
        action_policy=None,
        runtime_manager=None,
        lifecycle_bridge=None,
        tools=None,
        runtime_storage=None,
        agent_services={"default": service},
        gateways={"default": gateway},
    )

    assert gateway.closed == 1
    assert service.closed == 1


def test_runtime_session_close_uses_owner_and_releases_other_gateways() -> None:
    owner = _Gateway("alpha")
    other = _Gateway("beta")
    session = SimpleNamespace(active_agent_id="alpha", owner_agent_id="alpha")
    sessions = SimpleNamespace(get_session=lambda _session_id: session)
    runtime = SimpleNamespace(
        sessions=sessions,
        _gateways={"alpha": owner, "beta": other},
    )

    close_runtime_session(runtime, "session-1", reason="test_close")

    assert owner.closed_sessions == [("session-1", "test_close")]
    assert other.released_sessions == ["session-1"]


def test_agent_runtime_eviction_closes_its_provider_client() -> None:
    service = _AgentService()
    gateway = _Gateway()
    runtime = SimpleNamespace(
        _agent_runtime_lock=RLock(),
        _gateways={"alpha||default": gateway},
        _agent_services={"alpha||default": service},
        _memory_assemblies={},
        logger=logging.getLogger("test.runtime"),
    )

    RuntimeProfilesMixin.evict_agent_runtime(
        runtime,
        agent_id="alpha",
        reason="test",
    )

    assert gateway.closed == 1
    assert service.closed == 1
    assert runtime._gateways == {}
    assert runtime._agent_services == {}
