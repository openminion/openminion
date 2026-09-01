from __future__ import annotations

import logging

from openminion.base.config import OpenMinionConfig
from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.memory.smoke import EphemeralMemorySmokeProvider
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
)
from openminion.services.runtime.memory import (
    RuntimeMemoryAssembly,
    active_runtime_memory_assembly,
    build_memory_v2_runtime_assembly,
)


class _Service:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("service.close")


class _Scheduler:
    def __init__(self, events: list[str], *, live_after_stop: bool = False) -> None:
        self.events = events
        self.live = live_after_stop
        self.record_source = None

    def bind_record_source(self, record_source) -> None:
        self.events.append("scheduler.bind")
        self.record_source = record_source

    def start(self) -> None:
        self.events.append("scheduler.start")

    def stop(self) -> None:
        self.events.append("scheduler.stop")

    def is_alive(self) -> bool:
        return self.live


def test_active_assembly_owns_one_non_duplicated_runtime(tmp_path) -> None:
    config = OpenMinionConfig()
    config.runtime.memory_enabled = True
    config.runtime.memory_provider = "memory_v2"

    assembly = build_memory_v2_runtime_assembly(
        config=config,
        agent_id="alpha",
        memory_root=tmp_path,
        logger=logging.getLogger("test.memory.assembly"),
        config_manager=None,
        home_root=None,
        data_root=None,
        session_context=None,
        retrieve_ctl=None,
        storage_path=None,
        artifactctl_factory=lambda: None,
    )

    assert assembly.gateway is not None
    assert assembly.service is not None
    assert isinstance(assembly.memctl, MemctlAdapter)
    assert assembly.memctl._backend is assembly.service
    assert assembly.gateway._service is assembly.service
    assert assembly.close().closed is True


def test_disabled_assembly_keeps_gateway_without_runtime_owners() -> None:
    for gateway in (
        DisabledMemoryGatewayAdapter(agent_id="alpha"),
        EphemeralMemorySmokeProvider(agent_id="alpha"),
    ):
        assembly = RuntimeMemoryAssembly(gateway=gateway)

        assert assembly.gateway is gateway
        assert assembly.service is None
        assert assembly.memctl is None
        assert assembly.scheduler is None
        assert assembly.close().closed is True


def test_backend_none_builds_gateway_only_assembly(tmp_path) -> None:
    assembly = build_memory_v2_runtime_assembly(
        config=OpenMinionConfig(),
        agent_id="alpha",
        memory_root=tmp_path,
        logger=logging.getLogger("test.memory.none"),
        config_manager=None,
        home_root=None,
        data_root=None,
        session_context=None,
        retrieve_ctl=None,
        storage_path=None,
        resolve_runtime_memory_config_fn=lambda **kwargs: {
            "backend": {"provider": "none"}
        },
    )

    assert isinstance(assembly.gateway, DisabledMemoryGatewayAdapter)
    assert assembly.service is None
    assert assembly.memctl is None
    assert assembly.scheduler is None


def test_assembly_starts_and_closes_scheduler_before_service() -> None:
    events: list[str] = []
    service = _Service(events)
    scheduler = _Scheduler(events)
    assembly = active_runtime_memory_assembly(
        gateway=object(),
        service=service,  # type: ignore[arg-type]
        agent_id="alpha",
        scheduler=scheduler,
    )

    assembly.start()
    first = assembly.close()
    second = assembly.close()

    assert scheduler.record_source is assembly.memctl
    assert events == [
        "scheduler.bind",
        "scheduler.start",
        "scheduler.stop",
        "service.close",
    ]
    assert first is second
    assert first.closed is True


def test_assembly_leaves_service_open_while_scheduler_is_live() -> None:
    events: list[str] = []
    service = _Service(events)
    scheduler = _Scheduler(events, live_after_stop=True)
    assembly = active_runtime_memory_assembly(
        gateway=object(),
        service=service,  # type: ignore[arg-type]
        agent_id="alpha",
        scheduler=scheduler,
    )

    blocked = assembly.close()

    assert blocked.closed is False
    assert blocked.reason_code == "scheduler_still_running"
    assert events == ["scheduler.stop"]

    scheduler.live = False
    assert assembly.close().closed is True
    assert events == ["scheduler.stop", "scheduler.stop", "service.close"]
